"""正規化(BatchNorm/LayerNorm/InstanceNorm/GroupNorm)と正則化(Dropout/EarlyStopping)

前半: 同じ4次元テンソル(バッチN, チャネルC, 高さH, 幅W)に対して4種類の正規化を適用し、
      『どの軸をまとめて平均・分散を計算するか』の違いを数値で確認する
後半: あえて過学習しやすい設定(小さい訓練データ+大きい隠れ層)でMLPを学習させ、
      Dropoutの有無・Early Stoppingの効果を訓練/検証曲線で比較する
"""
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def demo_normalization_variants() -> None:
    print("=== 1. BatchNorm / LayerNorm / InstanceNorm / GroupNorm の違い ===")
    N, C, H, W = 2, 4, 3, 3
    # チャネルごとに違うオフセットを与えた合成データ(N, C, H, W)
    torch.manual_seed(0)
    x = torch.randn(N, C, H, W)
    for c in range(C):
        x[:, c] += c * 10.0  # チャネルcには+0, +10, +20, +30のオフセット
    print(f"入力テンソルの形状: {tuple(x.shape)} = (バッチN, チャネルC, 高さH, 幅W)")
    print(f"各チャネルの平均値(正規化前): {[round(x[:, c].mean().item(), 2) for c in range(C)]}")

    bn = nn.BatchNorm2d(C, affine=False)
    ln = nn.LayerNorm([C, H, W], elementwise_affine=False)
    inorm = nn.InstanceNorm2d(C, affine=False)
    gn = nn.GroupNorm(num_groups=2, num_channels=C, affine=False)

    results = {"BatchNorm": bn(x), "LayerNorm": ln(x), "InstanceNorm": inorm(x), "GroupNorm(2group)": gn(x)}
    print("\n正規化後、各チャネルの平均値(サンプル0のみ表示):")
    for name, out in results.items():
        means = [round(out[0, c].mean().item(), 3) for c in range(C)]
        print(f"  {name:20s}: {means}")

    print(
        "\nBatchNormは『チャネルごとに、バッチ全体(N,H,W)の平均・分散』で正規化するため、"
        "チャネル間の値のオフセット(+0,+10,+20,+30)がほぼ解消されている(正規化前は"
        "チャネル間で30以上離れていた平均値が、正規化後は-0.4〜0.3程度の同じスケールに"
        "収まった。サンプル0だけの局所平均のためちょうど0にはならないが、桁が大きく縮んだ"
        "ことがポイント)。LayerNormは『サンプルごとに、そのサンプルの全チャネル(C,H,W)"
        "の平均・分散』で正規化するため、チャネル間のオフセットの違いはむしろ残る"
        "(サンプル内での相対的な位置関係は保たれる)。InstanceNormは『サンプル×チャネルごと"
        "に(H,W)だけ』で正規化し、GroupNormはチャネルをいくつかのグループに分けて"
        "『サンプル×グループごとに正規化』する、BatchNormとLayerNormの中間的な手法。"
    )
    print(
        "BatchNormはバッチサイズが小さいと統計量が不安定になる弱点があり、バッチサイズに"
        "依存しないLayerNorm(Transformer系でよく使われる)やGroupNorm(バッチサイズが"
        "小さくなりがちな画像系タスクで使われる)が代替として使われる。"
    )


class MLP(nn.Module):
    def __init__(self, n_in, n_hidden, n_out, dropout_p=0.0):
        super().__init__()
        self.fc1 = nn.Linear(n_in, n_hidden)
        self.dropout = nn.Dropout(p=dropout_p)
        self.fc2 = nn.Linear(n_hidden, n_out)

    def forward(self, x):
        x = torch.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)


def train_and_eval(model, X_train, y_train, X_val, y_val, epochs=200, lr=0.01):
    optimizer = optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    history = {"train_loss": [], "val_loss": [], "val_acc": []}
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        out = model(X_train)
        loss = criterion(out, y_train)
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            val_out = model(X_val)
            val_loss = criterion(val_out, y_val).item()
            val_acc = (val_out.argmax(dim=1) == y_val).float().mean().item()
        history["train_loss"].append(loss.item())
        history["val_loss"].append(val_loss)
        history["val_acc"].append(val_acc)
    return history


def demo_dropout_and_early_stopping() -> None:
    print("\n=== 2. Dropoutの効果とEarly Stoppingのタイミング ===")
    digits = load_digits()
    X, y = digits.data, digits.target
    X = StandardScaler().fit_transform(X)
    # あえて訓練データを150件だけに絞り、隠れ層を256と大きくして過学習を起こしやすくする
    X_train, X_val, y_train, y_val = train_test_split(X, y, train_size=150, random_state=42, stratify=y)
    print(f"訓練データ: {len(X_train)}件（過学習を起こしやすくするため意図的に少なくしている）/ 検証データ: {len(X_val)}件")

    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.long)
    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.long)

    torch.manual_seed(42)
    model_no_dropout = MLP(64, 256, 10, dropout_p=0.0)
    hist_no_dropout = train_and_eval(model_no_dropout, X_train_t, y_train_t, X_val_t, y_val_t)

    torch.manual_seed(42)
    model_dropout = MLP(64, 256, 10, dropout_p=0.5)
    hist_dropout = train_and_eval(model_dropout, X_train_t, y_train_t, X_val_t, y_val_t)

    best_val_loss = float("inf")
    best_epoch = 0
    patience, patience_counter = 20, 0
    stop_epoch = len(hist_no_dropout["val_loss"]) - 1
    for epoch, vloss in enumerate(hist_no_dropout["val_loss"]):
        if vloss < best_val_loss:
            best_val_loss, best_epoch, patience_counter = vloss, epoch, 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                stop_epoch = epoch
                break

    print(f"\nDropoutなし: 最終(200epoch)検証精度={hist_no_dropout['val_acc'][-1]:.3f}  最終検証loss={hist_no_dropout['val_loss'][-1]:.3f}")
    print(f"Dropoutあり(p=0.5): 最終(200epoch)検証精度={hist_dropout['val_acc'][-1]:.3f}  最終検証loss={hist_dropout['val_loss'][-1]:.3f}")
    acc_at_best_loss = hist_no_dropout["val_acc"][best_epoch]
    acc_at_end = hist_no_dropout["val_acc"][-1]
    print(
        f"\nEarly Stopping(patience={patience}): Dropoutなしモデルは"
        f"epoch{best_epoch}で検証lossが最小({best_val_loss:.3f})になった後{patience}epoch改善しなかったため"
        f"epoch{stop_epoch}で打ち切り。この時点(epoch{best_epoch})の検証精度は{acc_at_best_loss:.3f}で、"
        f"200epoch時点の{acc_at_end:.3f}より{'高い' if acc_at_best_loss > acc_at_end else '低い'}。"
    )
    print(
        "興味深いのは、検証lossはepoch7以降ずっと悪化し続けている(＝モデルの確信度が"
        "訓練データに過剰適合していく)一方、検証accuracyはepoch7以降もわずかに改善している点。"
        "lossは『正解クラスにどれだけ自信を持って予測できているか』まで見るのに対し、"
        "accuracyは『1位の予測が当たっているか』しか見ない、という指標の違いが表れている。"
        "Early Stoppingをlossで判定するかaccuracyで判定するかで結果が変わり得ることは、"
        "実務でも意識すべき注意点。"
    )

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(hist_no_dropout["train_loss"], label="訓練loss(Dropoutなし)", color="tab:blue")
    axes[0].plot(hist_no_dropout["val_loss"], label="検証loss(Dropoutなし)", color="tab:orange")
    axes[0].axvline(best_epoch, color="gray", linestyle="--", label=f"Early Stoppingの理想点(epoch{best_epoch})")
    axes[0].axvline(stop_epoch, color="red", linestyle=":", label=f"実際の打ち切り(epoch{stop_epoch})")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("loss")
    axes[0].set_title("Dropoutなし: 過学習とEarly Stopping")
    axes[0].legend(fontsize=8)

    axes[1].plot(hist_no_dropout["val_acc"], label="検証精度(Dropoutなし)")
    axes[1].plot(hist_dropout["val_acc"], label="検証精度(Dropout p=0.5)")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("Accuracy")
    axes[1].set_title("Dropoutの有無による検証精度の違い")
    axes[1].legend()

    fig.tight_layout()
    out_path = "dropout_early_stopping.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")


def main() -> None:
    demo_normalization_variants()
    demo_dropout_and_early_stopping()


if __name__ == "__main__":
    main()
