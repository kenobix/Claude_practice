"""オートエンコーダ: 非線形の次元圧縮としてPCAと比較する

前半: 8次元に圧縮するオートエンコーダ vs 8主成分のPCA で、再構成誤差と
      復元画像の見た目を比較する(圧縮サイズを揃えて『非線形 vs 線形』を比べる)
後半: 2次元まで圧縮するオートエンコーダの潜在空間を可視化し、
      ラベルを一切使わずに数字ごとの塊がどれだけ分かれるかを見る
"""
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


class Autoencoder(nn.Module):
    def __init__(self, n_in: int, bottleneck: int, hidden: int = 64):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(n_in, hidden), nn.ReLU(),
            nn.Linear(hidden, bottleneck),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, hidden), nn.ReLU(),
            nn.Linear(hidden, n_in), nn.Sigmoid(),  # 画素値を[0,1]に収めるため出力層はsigmoid
        )

    def forward(self, x):
        z = self.encoder(x)
        x_hat = self.decoder(z)
        return x_hat, z


def train_autoencoder(model, X_train, epochs=300, lr=1e-3):
    optimizer = optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    losses = []
    for epoch in range(epochs):
        optimizer.zero_grad()
        x_hat, _ = model(X_train)
        loss = criterion(x_hat, X_train)
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
    return losses


def main() -> None:
    digits = load_digits()
    X, y = digits.data, digits.target
    X = X / 16.0  # 元の画素値は0〜16。sigmoid出力に合わせて0〜1に正規化
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print(f"データ: 手書き数字(load_digits, 画素値0〜1に正規化) 訓練{len(X_train)}件 / テスト{len(X_test)}件")

    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    X_test_t = torch.tensor(X_test, dtype=torch.float32)

    # --- 1. bottleneck=8で、オートエンコーダ vs PCA(8主成分) の再構成誤差を比較 ---
    print("\n=== 1. 8次元に圧縮: オートエンコーダ vs PCA ===")
    ae8 = Autoencoder(n_in=64, bottleneck=8)
    losses8 = train_autoencoder(ae8, X_train_t, epochs=800)
    with torch.no_grad():
        X_test_hat_ae, _ = ae8(X_test_t)
    mse_ae = ((X_test_hat_ae.numpy() - X_test) ** 2).mean()

    pca8 = PCA(n_components=8, random_state=42).fit(X_train)
    X_test_hat_pca = pca8.inverse_transform(pca8.transform(X_test))
    mse_pca = ((X_test_hat_pca - X_test) ** 2).mean()

    print(f"オートエンコーダ(8次元)のテスト再構成MSE: {mse_ae:.5f}")
    print(f"PCA(8主成分)のテスト再構成MSE:          {mse_pca:.5f}")
    print(
        f"→ オートエンコーダの方が誤差が{'小さい(良い)' if mse_ae < mse_pca else '大きい(悪い)'}。"
        "PCAは『線形変換』しか行えないのに対し、オートエンコーダは中間層にReLUという"
        "非線形関数を挟んでいるため、より複雑な(線形では表現できない)圧縮の仕方を学習でき、"
        "同じ圧縮サイズでもより情報を保持しやすい。"
    )

    # 元画像・PCA復元・AE復元を並べて比較
    n_show = 8
    fig, axes = plt.subplots(3, n_show, figsize=(14, 5.5))
    rng = np.random.RandomState(1)
    show_idx = rng.choice(len(X_test), n_show, replace=False)
    for col, idx in enumerate(show_idx):
        axes[0, col].imshow(X_test[idx].reshape(8, 8), cmap="gray_r")
        axes[1, col].imshow(X_test_hat_pca[idx].reshape(8, 8), cmap="gray_r")
        axes[2, col].imshow(X_test_hat_ae.numpy()[idx].reshape(8, 8), cmap="gray_r")
        for row in range(3):
            axes[row, col].set_xticks([])
            axes[row, col].set_yticks([])
    axes[0, 0].set_ylabel("元画像", fontsize=11)
    axes[1, 0].set_ylabel("PCA復元\n(8主成分)", fontsize=11)
    axes[2, 0].set_ylabel("AE復元\n(8次元潜在)", fontsize=11)
    fig.suptitle(f"元画像 / PCA復元(MSE={mse_pca:.4f}) / オートエンコーダ復元(MSE={mse_ae:.4f})")
    fig.tight_layout()
    out_path1 = "autoencoder_vs_pca.png"
    fig.savefig(out_path1, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path1}")

    # --- 2. bottleneck=2で潜在空間を直接可視化 ---
    print("\n=== 2. 2次元に圧縮した潜在空間の可視化(ラベルは学習に一切使っていない) ===")
    ae2 = Autoencoder(n_in=64, bottleneck=2)
    train_autoencoder(ae2, X_train_t, epochs=800)
    with torch.no_grad():
        _, z_test = ae2(X_test_t)
    z_test = z_test.numpy()

    pca2_test = PCA(n_components=2, random_state=42).fit(X_train).transform(X_test)

    fig2, axes2 = plt.subplots(1, 2, figsize=(12, 5.5))
    scatter1 = axes2[0].scatter(pca2_test[:, 0], pca2_test[:, 1], c=y_test, cmap="tab10", s=15)
    axes2[0].set_title("PCA(2次元)")
    axes2[0].set_xlabel("次元1")
    axes2[0].set_ylabel("次元2")

    scatter2 = axes2[1].scatter(z_test[:, 0], z_test[:, 1], c=y_test, cmap="tab10", s=15)
    axes2[1].set_title("オートエンコーダの潜在空間(2次元)")
    axes2[1].set_xlabel("潜在変数1")
    axes2[1].set_ylabel("潜在変数2")
    fig2.colorbar(scatter2, ax=axes2, label="正解の数字ラベル(可視化のためだけに使用)", fraction=0.03)

    out_path2 = "autoencoder_latent_space.png"
    fig2.savefig(out_path2, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path2}")

    print(
        "\nオートエンコーダは『正解ラベルを一切見ずに、入力を再構成できるように圧縮する』"
        "という自己教師あり(教師なし)の学習だけで、結果として数字の種類ごとに"
        "潜在空間上でまとまりやすい表現を獲得する。これはStage3で学んだPCAが"
        "『分散を最大化する線形方向』を探すのに対し、オートエンコーダは"
        "『再構成誤差を最小化する非線形な圧縮』を探す、という目的関数の違いに由来する。"
    )


if __name__ == "__main__":
    main()
