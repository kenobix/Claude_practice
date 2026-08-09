"""PyTorchの量子化APIで、CNNを32bit浮動小数点→8bit整数に変換し、実際のサイズ・速度変化を測る

01のプルーニングは(非構造化のままでは)重みを0にするだけでファイルサイズは変わらなかった。
量子化は、重みや活性化の表現形式そのものを32bit浮動小数点(float32)から8bit整数(int8)に
変換する手法で、理論上はパラメータ1個あたりのサイズが1/4になり、対応するCPU命令セット
(x86のfbgemm等)を使えば整数演算の方が浮動小数点演算より高速に処理できる場合がある。
ここでは训練時は通常通りfloat32で学習し、学習後に量子化する「训練後の静的量子化
(Post-Training Static Quantization)」を使う。キャリブレーション(少量データを流して
活性化の値域を推定する処理)が必要な点が、動的量子化(推論時にその都度スケールを計算する
手法)との違い。

実際に (1) モデルファイルサイズ (2) CPU推論時間 (3) 精度 の3つを量子化前後で比較する。
"""
import os
import time
import warnings

import torch
import torch.nn as nn
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="torch.ao")

from synthetic_shapes import generate_shapes_dataset
import _mpl_ja  # noqa: F401

torch.manual_seed(42)
torch.backends.quantized.engine = "fbgemm"  # x86 CPU向けの量子化バックエンド


class QuantizableCNN(nn.Module):
    """量子化のためQuantStub/DeQuantStubで入出力を挟み、Conv+ReLUを融合(fuse)できる形にする"""

    def __init__(self, n_classes=4):
        super().__init__()
        self.quant = torch.ao.quantization.QuantStub()
        self.conv1 = nn.Conv2d(3, 32, 3, padding=1)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv2d(32, 64, 3, padding=1)
        self.relu2 = nn.ReLU()
        self.conv3 = nn.Conv2d(64, 64, 3, padding=1)
        self.relu3 = nn.ReLU()
        self.pool = nn.MaxPool2d(2)
        self.fc1 = nn.Linear(64 * 4 * 4, 128)
        self.relu4 = nn.ReLU()
        self.fc2 = nn.Linear(128, n_classes)
        self.dequant = torch.ao.quantization.DeQuantStub()

    def forward(self, x):
        x = self.quant(x)
        x = self.pool(self.relu1(self.conv1(x)))
        x = self.pool(self.relu2(self.conv2(x)))
        x = self.pool(self.relu3(self.conv3(x)))
        x = x.flatten(1)
        x = self.relu4(self.fc1(x))
        x = self.fc2(x)
        return self.dequant(x)


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


def get_model_size_kb(model, path="_tmp_model.pt"):
    torch.save(model.state_dict(), path)
    size_kb = os.path.getsize(path) / 1024
    os.remove(path)
    return size_kb


def measure_latency(model, X, n_repeats=30):
    model.eval()
    with torch.no_grad():
        for _ in range(3):  # ウォームアップ
            model(X[:1])
        t0 = time.perf_counter()
        for _ in range(n_repeats):
            model(X)
        elapsed = time.perf_counter() - t0
    return elapsed / n_repeats * 1000  # ミリ秒/バッチ


def main() -> None:
    print("=== 1. データ準備・float32モデルの学習 ===")
    X_train, y_train = generate_shapes_dataset(2000, seed=0)
    X_test, y_test = generate_shapes_dataset(400, seed=1000)
    X_train_t, y_train_t = torch.tensor(X_train).float(), torch.tensor(y_train).long()
    X_test_t, y_test_t = torch.tensor(X_test).float(), torch.tensor(y_test).long()
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(X_train_t, y_train_t), batch_size=64, shuffle=True)

    model_fp32 = QuantizableCNN()
    train_model(model_fp32, train_loader)
    acc_fp32 = evaluate(model_fp32, X_test_t, y_test_t)
    size_fp32 = get_model_size_kb(model_fp32)
    latency_fp32 = measure_latency(model_fp32, X_test_t)
    print(f"float32モデル: 精度={acc_fp32:.3f}, サイズ={size_fp32:.1f}KB, "
          f"推論時間(400枚バッチ)={latency_fp32:.2f}ms")

    print("\n=== 2. Conv+ReLUの融合(fuse)、キャリブレーション、int8への変換 ===")
    model_fp32.eval()
    model_fused = torch.ao.quantization.fuse_modules(
        model_fp32,
        [["conv1", "relu1"], ["conv2", "relu2"], ["conv3", "relu3"]],
    )
    model_fused.qconfig = torch.ao.quantization.get_default_qconfig("fbgemm")
    model_prepared = torch.ao.quantization.prepare(model_fused)

    print("キャリブレーション中(訓練データの一部を流して活性化の値域を推定)...")
    with torch.no_grad():
        for X, _ in train_loader:
            model_prepared(X)

    model_int8 = torch.ao.quantization.convert(model_prepared)
    print("int8への変換完了")

    acc_int8 = evaluate(model_int8, X_test_t, y_test_t)
    size_int8 = get_model_size_kb(model_int8)
    latency_int8 = measure_latency(model_int8, X_test_t)
    print(f"\nint8量子化モデル: 精度={acc_int8:.3f}, サイズ={size_int8:.1f}KB, "
          f"推論時間(400枚バッチ)={latency_int8:.2f}ms")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    labels = ["float32", "int8量子化"]

    axes[0].bar(labels, [size_fp32, size_int8], color=["tab:blue", "tab:orange"])
    axes[0].set_ylabel("モデルサイズ(KB)")
    axes[0].set_title(f"モデルサイズ({size_fp32 / size_int8:.2f}倍)")
    for i, v in enumerate([size_fp32, size_int8]):
        axes[0].text(i, v, f"{v:.0f}KB", ha="center", va="bottom")

    axes[1].bar(labels, [latency_fp32, latency_int8], color=["tab:blue", "tab:orange"])
    axes[1].set_ylabel("推論時間(ms/400枚バッチ)")
    axes[1].set_title(f"推論速度({latency_fp32 / latency_int8:.2f}倍)")
    for i, v in enumerate([latency_fp32, latency_int8]):
        axes[1].text(i, v, f"{v:.2f}ms", ha="center", va="bottom")

    axes[2].bar(labels, [acc_fp32, acc_int8], color=["tab:blue", "tab:orange"])
    axes[2].set_ylabel("テスト精度")
    axes[2].set_title("精度")
    axes[2].set_ylim(0, 1.05)
    for i, v in enumerate([acc_fp32, acc_int8]):
        axes[2].text(i, v, f"{v:.3f}", ha="center", va="bottom")

    fig.tight_layout()
    out_path = "quantization.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\nモデルサイズはfloat32の{size_fp32:.1f}KBからint8量子化後は{size_int8:.1f}KBまで"
        f"縮小し、{size_fp32 / size_int8:.2f}倍の圧縮を達成した(32bit→8bitで理論上4倍のはずだが、"
        "量子化パラメータ(スケール・ゼロ点)の保存や、量子化されない層が残ることなどにより"
        "理論値とは多少ずれる)。推論時間は"
        f"{latency_fp32:.2f}ms→{latency_int8:.2f}ms({latency_fp32 / latency_int8:.2f}倍)"
        + ("に高速化した" if latency_int8 < latency_fp32 else "、今回はむしろ遅くなった"
           "(小さなモデル・小さなバッチでは、int8演算の恩恵よりも量子化・逆量子化の"
           "オーバーヘッドの方が大きくなることがある)")
        + f"。精度は{acc_fp32:.3f}→{acc_int8:.3f}となり、"
        + ("ほぼ劣化なく" if abs(acc_fp32 - acc_int8) < 0.02 else "多少の劣化を伴いつつ")
        + "軽量化できることを確認した。プルーニングと異なり、量子化は表現形式そのものを"
        "変えるため、特別な疎行列ライブラリなどを使わなくても『保存して普通に読み込むだけ』で"
        "サイズ削減の恩恵をそのまま受けられる点が実務上のメリットである。"
    )


if __name__ == "__main__":
    main()
