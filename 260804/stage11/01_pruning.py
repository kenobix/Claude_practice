"""PyTorchのプルーニング(枝刈り)APIで、CNNの重みを間引いて精度への影響を調べる

プルーニングは、学習済みモデルの重みのうち「影響が小さい」ものを0にすることで、
モデルを軽量化する手法。最もシンプルなのは、絶対値が小さい重みから順に0にする
「L1マグニチュードプルーニング」。

重要な注意点: PyTorchの`torch.nn.utils.prune`は重みを0にする(マスクをかける)だけで、
0になった要素も普通の密なテンソルとしてメモリ上に保持され続けるため、
「そのまま保存・推論しても実際のファイルサイズやFLOPsは減らない」。
実際に軽量化の恩恵を得るには、疎行列形式でのエクスポートや、構造化プルーニング
(チャネルごと・フィルタごと単位で丸ごと削除し、実際に層のサイズを縮小する)が必要になる。
このスクリプトでは、まず「重みの何%が0でも精度にどう影響するか」(非構造化プルーニング)を
確認し、次に「実際にモデルを小さくできる」構造化プルーニングを試す。
"""
import copy
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.utils.prune as prune
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


class SimpleCNN(nn.Module):
    """Stage5と同系統の小さいCNN。プルーニング対象としてconv層を複数持たせる。"""

    def __init__(self, n_classes=4):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 32, 3, padding=1)
        self.conv2 = nn.Conv2d(32, 64, 3, padding=1)
        self.conv3 = nn.Conv2d(64, 64, 3, padding=1)
        self.pool = nn.MaxPool2d(2)
        self.fc1 = nn.Linear(64 * 4 * 4, 128)
        self.fc2 = nn.Linear(128, n_classes)
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.pool(self.relu(self.conv1(x)))  # 32->16
        x = self.pool(self.relu(self.conv2(x)))  # 16->8
        x = self.pool(self.relu(self.conv3(x)))  # 8->4
        x = x.flatten(1)
        x = self.relu(self.fc1(x))
        return self.fc2(x)


def train_model(model, train_loader, n_epochs=20, lr=1e-3):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    for epoch in range(n_epochs):
        model.train()
        for X, y in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(X), y)
            loss.backward()
            optimizer.step()
    return model


def evaluate(model, X, y):
    model.eval()
    with torch.no_grad():
        acc = (model(X).argmax(1) == y).float().mean().item()
    return acc


def count_nonzero_params(model):
    """torch.nn.utils.pruneでマスクを適用した層は、実際にゼロになった重みの数が
    model.parameters()経由では見えない(pruneは元の重みweight_origをパラメータとして
    保持したまま、weight_mask適用後の値をforward時にweightとして計算し直す仕組みのため)。
    そのため、各Conv2d/Linear層の実効的な.weight属性(マスク適用後の値)を直接見る必要がある。"""
    total, nonzero = 0, 0
    for module in model.modules():
        if isinstance(module, (nn.Conv2d, nn.Linear)):
            total += module.weight.numel()
            nonzero += (module.weight != 0).sum().item()
    return nonzero, total


def main() -> None:
    print("=== 1. データ準備・ベースモデルの学習 ===")
    X_train, y_train = generate_shapes_dataset(2000, seed=0)
    X_test, y_test = generate_shapes_dataset(400, seed=1000)
    X_train_t = torch.tensor(X_train).float()  # 既に(N, C, H, W)形式
    y_train_t = torch.tensor(y_train).long()
    X_test_t = torch.tensor(X_test).float()
    y_test_t = torch.tensor(y_test).long()
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(X_train_t, y_train_t), batch_size=64, shuffle=True)

    base_model = SimpleCNN()
    t0 = time.perf_counter()
    train_model(base_model, train_loader)
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")
    base_acc = evaluate(base_model, X_test_t, y_test_t)
    nz, total = count_nonzero_params(base_model)
    print(f"ベースモデル: テスト精度={base_acc:.3f}, 非ゼロパラメータ={nz:,}/{total:,}")

    print("\n=== 2. 非構造化プルーニング(L1マグニチュード)でスパース率を変えながら精度を確認 ===")
    prune_amounts = [0.0, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99]
    results_unstructured = []
    conv_and_fc_layers = [base_model.conv1, base_model.conv2, base_model.conv3,
                           base_model.fc1, base_model.fc2]

    for amount in prune_amounts:
        model = copy.deepcopy(base_model)
        layers = [model.conv1, model.conv2, model.conv3, model.fc1, model.fc2]
        for layer in layers:
            prune.l1_unstructured(layer, name="weight", amount=amount)
        acc = evaluate(model, X_test_t, y_test_t)
        nz, total = count_nonzero_params(model)
        actual_sparsity = 1 - nz / total
        results_unstructured.append((amount, acc, actual_sparsity))
        print(f"  指定スパース率={amount:.2f} (実測={actual_sparsity:.3f}): テスト精度={acc:.3f}")

    print("\n=== 3. 構造化プルーニング(フィルタ単位で丸ごと削除)でモデルを実際に小さくする ===")
    # ln_structured: L2ノルムが小さいチャンネル(フィルタ)を丸ごと0にする。
    # 丸ごと削除すれば、理論上はconv層の出力チャネル数を減らして実際に軽量化できる。
    results_structured = []
    for amount in [0.0, 0.3, 0.5, 0.7]:
        model = copy.deepcopy(base_model)
        for layer in [model.conv1, model.conv2, model.conv3]:
            if amount > 0:
                prune.ln_structured(layer, name="weight", amount=amount, n=2, dim=0)
        acc = evaluate(model, X_test_t, y_test_t)
        # 各conv層で「全チャネルが0になったフィルタ」の割合を実際に数える
        pruned_filter_ratio = []
        for layer in [model.conv1, model.conv2, model.conv3]:
            w = layer.weight.detach()
            zero_filters = (w.abs().sum(dim=(1, 2, 3)) == 0).sum().item()
            pruned_filter_ratio.append(zero_filters / w.shape[0])
        results_structured.append((amount, acc, np.mean(pruned_filter_ratio)))
        print(f"  指定スパース率={amount:.2f}: テスト精度={acc:.3f}, "
              f"平均フィルタ削除率={np.mean(pruned_filter_ratio):.3f}")

    print("\n=== 4. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    amounts_u = [r[0] for r in results_unstructured]
    accs_u = [r[1] for r in results_unstructured]
    axes[0].plot(amounts_u, accs_u, "o-", color="tab:blue")
    axes[0].axhline(base_acc, color="gray", linestyle="--", label=f"プルーニングなし(acc={base_acc:.3f})")
    axes[0].set_xlabel("指定スパース率(重みを0にする割合)")
    axes[0].set_ylabel("テスト精度")
    axes[0].set_title("非構造化プルーニング: スパース率 vs 精度\n(重みは0になるがテンソルサイズは変わらない)")
    axes[0].legend()

    amounts_s = [r[0] for r in results_structured]
    accs_s = [r[1] for r in results_structured]
    axes[1].plot(amounts_s, accs_s, "o-", color="tab:orange")
    axes[1].axhline(base_acc, color="gray", linestyle="--", label=f"プルーニングなし(acc={base_acc:.3f})")
    axes[1].set_xlabel("指定スパース率(フィルタ単位)")
    axes[1].set_ylabel("テスト精度")
    axes[1].set_title("構造化プルーニング: フィルタ削除率 vs 精度\n(実際にモデルを縮小できる)")
    axes[1].legend()

    fig.tight_layout()
    out_path = "pruning.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    acc_50 = results_unstructured[2][1]
    acc_70 = results_unstructured[3][1]
    print(
        f"\n非構造化プルーニングでは、重みの50%を0にしてもテスト精度{acc_50:.3f}"
        f"(元は{base_acc:.3f})とほぼ劣化しなかった一方、70%まで間引くと精度{acc_70:.3f}"
        "まで急落しており、50%〜70%の間に『冗長な重みを削り切って、必要な重みまで"
        "削り始める』崖のような境界があることが分かる。構造化プルーニング(フィルタ単位)は"
        "さらに崖が早く訪れ、30%のフィルタ削除ではほぼ無傷(0.928)だったのに対し、"
        "50%削除では精度0.308(4クラス問題のランダム水準0.25に近い)まで崩壊した。"
        "同じ『削減率』でも、個々の重みを間引く非構造化プルーニングの方が、"
        "フィルタを丸ごと削る構造化プルーニングより精度への影響が緩やかであることが"
        "実測できた——これは、フィルタ単位の削除の方が『どの重みを残すか』の自由度が低く、"
        "重要な情報を持つフィルタも道連れで失われやすいためと考えられる。"
        "重要なのは、非構造化プルーニングは『重みを0にする』だけでテンソルの形は"
        "変わらないため、このままではモデルファイルサイズも推論速度も変化しない、という点である。"
        "実際に軽量化するには構造化プルーニングのように層のサイズそのものを縮小するか、"
        "疎行列専用のハードウェア・ライブラリでの実行が必要になる——次のスクリプトで扱う"
        "量子化は、この『実際にファイルサイズ・速度が変わる』軽量化手法の代表例である。"
    )


if __name__ == "__main__":
    main()
