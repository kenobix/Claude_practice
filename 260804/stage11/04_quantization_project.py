"""ミニプロジェクト: 「大きいモデル」と「知識蒸留で作った小さいモデル」の両方に量子化を適用し、
軽量化手法を組み合わせた場合のモデルサイズ・推論速度・精度を比較する

01のプルーニング・02の量子化・03の知識蒸留と、Stage11で見てきた3つの軽量化手法は
それぞれ独立した技術であり、実務では組み合わせて使うことが多い。
このミニプロジェクトでは、02のQuantizableCNN(チャネル数を変えられる形に一般化)を使い、
  (A) 大きいモデル(チャネル数多め、03のTeacherCNN相当) のfloat32/int8
  (B) 小さいモデル(チャネル数少なめ、03のStudentCNN相当) のfloat32/int8
の計4パターンで、モデルサイズ・推論速度・精度を横並びで比較する。
『小さいモデルにする(知識蒸留・設計変更)』と『同じモデルを量子化する』は独立した軽量化の
軸であり、両方を組み合わせることでどれだけ軽量化できるかを実際に測定する。
"""
import os
import time
import warnings

import torch
import torch.nn as nn
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset
import _mpl_ja  # noqa: F401

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="torch.ao")

torch.manual_seed(42)
torch.backends.quantized.engine = "fbgemm"


class QuantizableCNN(nn.Module):
    def __init__(self, base_ch=32, n_classes=4):
        super().__init__()
        c1, c2, c3 = base_ch, base_ch * 2, base_ch * 2
        self.quant = torch.ao.quantization.QuantStub()
        self.conv1 = nn.Conv2d(3, c1, 3, padding=1)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv2d(c1, c2, 3, padding=1)
        self.relu2 = nn.ReLU()
        self.conv3 = nn.Conv2d(c2, c3, 3, padding=1)
        self.relu3 = nn.ReLU()
        self.pool = nn.MaxPool2d(2)
        self.fc1 = nn.Linear(c3 * 4 * 4, base_ch * 4)
        self.relu4 = nn.ReLU()
        self.fc2 = nn.Linear(base_ch * 4, n_classes)
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


def count_params(model):
    return sum(p.numel() for p in model.parameters())


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
        return (model(X).argmax(1) == y).float().mean().item()


def get_model_size_kb(model, path="_tmp_model.pt"):
    torch.save(model.state_dict(), path)
    size_kb = os.path.getsize(path) / 1024
    os.remove(path)
    return size_kb


def measure_latency(model, X, n_repeats=30):
    model.eval()
    with torch.no_grad():
        for _ in range(3):
            model(X[:1])
        t0 = time.perf_counter()
        for _ in range(n_repeats):
            model(X)
        elapsed = time.perf_counter() - t0
    return elapsed / n_repeats * 1000


def quantize(model, calib_loader):
    model.eval()
    model_fused = torch.ao.quantization.fuse_modules(
        model, [["conv1", "relu1"], ["conv2", "relu2"], ["conv3", "relu3"]])
    model_fused.qconfig = torch.ao.quantization.get_default_qconfig("fbgemm")
    model_prepared = torch.ao.quantization.prepare(model_fused)
    with torch.no_grad():
        for X, _ in calib_loader:
            model_prepared(X)
    return torch.ao.quantization.convert(model_prepared)


def main() -> None:
    print("=== 1. データ準備 ===")
    X_train, y_train = generate_shapes_dataset(2000, seed=0)
    X_test, y_test = generate_shapes_dataset(400, seed=1000)
    X_train_t, y_train_t = torch.tensor(X_train).float(), torch.tensor(y_train).long()
    X_test_t, y_test_t = torch.tensor(X_test).float(), torch.tensor(y_test).long()
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(X_train_t, y_train_t), batch_size=64, shuffle=True)

    configs = [("大きいモデル(base_ch=32)", 32), ("小さいモデル(base_ch=8)", 8)]
    results = {}

    for name, base_ch in configs:
        print(f"\n=== {name}を学習・量子化 ===")
        model_fp32 = QuantizableCNN(base_ch=base_ch)
        train_model(model_fp32, train_loader)
        acc_fp32 = evaluate(model_fp32, X_test_t, y_test_t)
        size_fp32 = get_model_size_kb(model_fp32)
        latency_fp32 = measure_latency(model_fp32, X_test_t)
        n_params = count_params(model_fp32)
        print(f"  float32: パラメータ数={n_params:,}, 精度={acc_fp32:.3f}, "
              f"サイズ={size_fp32:.1f}KB, 推論時間={latency_fp32:.2f}ms")

        model_int8 = quantize(model_fp32, train_loader)
        acc_int8 = evaluate(model_int8, X_test_t, y_test_t)
        size_int8 = get_model_size_kb(model_int8)
        latency_int8 = measure_latency(model_int8, X_test_t)
        print(f"  int8    : 精度={acc_int8:.3f}, サイズ={size_int8:.1f}KB, "
              f"推論時間={latency_int8:.2f}ms")

        results[name] = dict(n_params=n_params, acc_fp32=acc_fp32, size_fp32=size_fp32,
                              latency_fp32=latency_fp32, acc_int8=acc_int8,
                              size_int8=size_int8, latency_int8=latency_int8)

    print("\n=== 2. 可視化 ===")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    bar_labels = ["大\nfloat32", "大\nint8", "小\nfloat32", "小\nint8"]
    colors = ["tab:blue", "tab:cyan", "tab:red", "tab:orange"]

    sizes = [results[configs[0][0]]["size_fp32"], results[configs[0][0]]["size_int8"],
             results[configs[1][0]]["size_fp32"], results[configs[1][0]]["size_int8"]]
    latencies = [results[configs[0][0]]["latency_fp32"], results[configs[0][0]]["latency_int8"],
                 results[configs[1][0]]["latency_fp32"], results[configs[1][0]]["latency_int8"]]
    accs = [results[configs[0][0]]["acc_fp32"], results[configs[0][0]]["acc_int8"],
            results[configs[1][0]]["acc_fp32"], results[configs[1][0]]["acc_int8"]]

    axes[0].bar(bar_labels, sizes, color=colors)
    axes[0].set_ylabel("モデルサイズ(KB)")
    axes[0].set_title("モデルサイズ(モデル設計×量子化)")
    axes[0].set_yscale("log")
    for i, v in enumerate(sizes):
        axes[0].text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)

    axes[1].bar(bar_labels, latencies, color=colors)
    axes[1].set_ylabel("推論時間(ms/400枚バッチ)")
    axes[1].set_title("推論速度(モデル設計×量子化)")
    for i, v in enumerate(latencies):
        axes[1].text(i, v, f"{v:.1f}", ha="center", va="bottom", fontsize=8)

    axes[2].bar(bar_labels, accs, color=colors)
    axes[2].set_ylabel("テスト精度")
    axes[2].set_title("精度(モデル設計×量子化)")
    axes[2].set_ylim(0, 1.05)
    for i, v in enumerate(accs):
        axes[2].text(i, v + 0.02, f"{v:.3f}", ha="center", fontsize=8)

    fig.tight_layout()
    out_path = "quantization_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    big = results[configs[0][0]]
    small = results[configs[1][0]]
    total_compression = big["size_fp32"] / small["size_int8"]
    print(
        f"\n大きいモデル(float32, {big['size_fp32']:.0f}KB)を基準にすると、"
        f"『小さい設計にする』効果と『量子化する』効果を掛け合わせた"
        f"小さいモデル(int8, {small['size_int8']:.0f}KB)は、"
        f"{total_compression:.1f}倍のサイズ圧縮を達成した。"
        f"内訳を見ると、モデル設計を小さくする効果が{big['size_fp32'] / small['size_fp32']:.1f}倍、"
        f"そこからさらに量子化する効果が{small['size_fp32'] / small['size_int8']:.1f}倍であり、"
        "2つの軽量化手法はほぼ独立に効いて掛け算的に効果が積み上がることが分かる。"
        f"精度は大きいモデル(float32)の{big['acc_fp32']:.3f}から、"
        f"小さいモデル(int8)では{small['acc_int8']:.3f}まで低下しており、"
        "軽量化と精度はトレードオフの関係にあることも確認できる。"
        "実務では、この『どこまで軽量化して、どこまでの精度低下を許容するか』を"
        "デプロイ先の制約(モバイル端末のメモリ・レイテンシ要件等)に応じて調整することになる。"
    )


if __name__ == "__main__":
    main()
