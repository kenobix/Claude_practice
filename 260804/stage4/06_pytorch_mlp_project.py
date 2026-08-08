"""ミニプロジェクト: numpyスクラッチMLP(02) と PyTorch MLP を同条件で比較する

同じデータ分割・同じアーキテクチャ(64→32→10)・同じ学習率で、
「自分で書いたforward/backward」と「フレームワークのautograd」がどれだけ
同じ結果に辿り着くか、コード量と学習時間はどう違うかを比較する。
"""
import importlib
import time

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

scratch = importlib.import_module("02_scratch_mlp_backprop")  # 02のScratchMLP等を再利用


class TorchMLP(nn.Module):
    def __init__(self, n_in=64, n_hidden=32, n_out=10):
        super().__init__()
        self.fc1 = nn.Linear(n_in, n_hidden)
        self.fc2 = nn.Linear(n_hidden, n_out)

    def forward(self, x):
        return self.fc2(torch.relu(self.fc1(x)))


def main() -> None:
    digits = load_digits()
    X, y = digits.data, digits.target
    X = StandardScaler().fit_transform(X)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    y_train_oh = scratch.one_hot(y_train, 10)
    y_test_oh = scratch.one_hot(y_test, 10)
    epochs = 300
    lr = 0.5
    print(f"データ: 手書き数字 訓練{len(X_train)}件 / テスト{len(X_test)}件, epochs={epochs}, lr={lr}, アーキテクチャ=64→32(ReLU)→10")

    # --- 1. numpyスクラッチMLP(02と同一実装) ---
    print("\n=== 1. numpyスクラッチMLP(フルバッチ勾配降下法) ===")
    np_model = scratch.ScratchMLP(n_in=64, n_hidden=32, n_out=10, seed=42)
    np_losses = []
    t0 = time.perf_counter()
    for epoch in range(epochs):
        cache = np_model.forward(X_train)
        loss = scratch.cross_entropy(cache["a2"], y_train_oh)
        grads = np_model.backward(cache, y_train_oh)
        np_model.step(grads, lr)
        np_losses.append(loss)
    np_time = time.perf_counter() - t0
    np_test_acc = (np_model.predict(X_test) == y_test).mean()
    print(f"学習時間: {np_time:.3f}秒  最終テスト精度: {np_test_acc:.4f}")

    # --- 2. PyTorch MLP(同じ学習率・同じフルバッチ勾配降下法) ---
    print("\n=== 2. PyTorch MLP(同条件: フルバッチ勾配降下法, autogradによる自動微分) ===")
    torch.manual_seed(42)
    torch_model = TorchMLP()
    # PyTorchのLinear層はデフォルトで一様分布初期化のため、numpy版(He初期化)と厳密には異なる。
    # 初期値の違いによる差を無くすため、ScratchMLPと全く同じ乱数ストリーム(1つのRandomStateを
    # W1→W2の順に連続使用)から生成した重みをコピーする。
    rng = np.random.RandomState(42)
    W1_init = rng.randn(64, 32) * np.sqrt(2.0 / 64)  # ScratchMLP.__init__のW1と同一の値
    W2_init = rng.randn(32, 10) * np.sqrt(2.0 / 32)  # 続けて同じrngから生成 = W2と同一の値
    with torch.no_grad():
        torch_model.fc1.weight.copy_(torch.tensor(W1_init.T, dtype=torch.float32))  # nn.Linearは(out,in)の形状
        torch_model.fc1.bias.zero_()
        torch_model.fc2.weight.copy_(torch.tensor(W2_init.T, dtype=torch.float32))
        torch_model.fc2.bias.zero_()

    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.long)
    X_test_t = torch.tensor(X_test, dtype=torch.float32)
    y_test_t = torch.tensor(y_test, dtype=torch.long)

    optimizer = optim.SGD(torch_model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()  # 内部でsoftmax+交差エントロピーをまとめて計算(02の手書き導出と同じ処理)

    torch_losses = []
    t0 = time.perf_counter()
    for epoch in range(epochs):
        optimizer.zero_grad()
        out = torch_model(X_train_t)
        loss = criterion(out, y_train_t)
        loss.backward()  # ここがautograd: 02で手書きしたbackward()を自動でやってくれる
        optimizer.step()
        torch_losses.append(loss.item())
    torch_time = time.perf_counter() - t0
    with torch.no_grad():
        torch_test_acc = (torch_model(X_test_t).argmax(dim=1) == y_test_t).float().mean().item()
    print(f"学習時間: {torch_time:.3f}秒  最終テスト精度: {torch_test_acc:.4f}")

    print("\n=== 3. 比較まとめ ===")
    print(f"{'':20s} {'テスト精度':>10s} {'学習時間':>10s} {'コアの学習部分の行数':>16s}")
    print(f"{'numpyスクラッチ':20s} {np_test_acc:>10.4f} {np_time:>9.3f}秒 {'約15行(forward+backward+step)':>16s}")
    print(f"{'PyTorch':20s} {torch_test_acc:>10.4f} {torch_time:>9.3f}秒 {'約5行(forward+backward()+step())':>16s}")

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(np_losses, label="numpyスクラッチMLP")
    ax.plot(torch_losses, label="PyTorch MLP", linestyle="--")
    ax.set_xlabel("epoch")
    ax.set_ylabel("訓練loss(交差エントロピー)")
    ax.set_title("numpyスクラッチ実装 vs PyTorch実装 の学習曲線比較")
    ax.legend()
    fig.tight_layout()
    out_path = "scratch_vs_pytorch.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\n同じ初期値・同じ学習率・同じフルバッチ勾配降下法で学習させると、numpyスクラッチ実装と"
        "PyTorch実装はほぼ同じ学習曲線・同等の最終精度に到達する。これは02で手書きした"
        "forward/backwardの数式が、PyTorchのautograd(自動微分)が裏側でやっている計算と"
        "本質的に同じものだったことの裏付けになる。両者の違いは主に"
        "①コード量(PyTorchはbackward()の中身を書かなくてよい)、②GPU対応・自動微分による"
        "任意の複雑なネットワーク構造への拡張性、③畳み込み層やAttention等の既製レイヤー・"
        "最適化アルゴリズムが揃っている実用性、にある。"
        "『仕組みを理解するためにまず手で書き、実践では信頼できるフレームワークに任せる』"
        "というStage 4の狙いがここで一周する。"
    )


if __name__ == "__main__":
    main()
