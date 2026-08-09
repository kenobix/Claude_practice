"""PyTorchでVAE(Variational Autoencoder)を実装し、Fashion-MNISTで画像生成を試す

Stage4で扱った通常のオートエンコーダは「入力を再構成できる潜在表現」を学ぶだけで、
潜在空間の途中の点をデコードしても意味のある画像になる保証がない
(学習データが通っていない場所は何にデコードされるか分からない)。

VAEは、各入力を「潜在空間の1点」ではなく「潜在空間上の確率分布(平均μ・分散σ²)」に
エンコードし、その分布が標準正規分布に近づくように正則化(KLダイバージェンス)する。
これにより潜在空間全体が「なめらかに意味を持つ」空間になり、ランダムに点をサンプリングして
デコードするだけで新しい画像を生成できるようになる——これが生成モデルとしての核心。

損失関数 = 再構成誤差 + KLダイバージェンス
勾配が確率的サンプリングを通り抜けられるようにする「reparameterization trick」
(z = μ + σ*ε, ε〜N(0,1)) も実装する。
"""
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)
DEVICE = "cpu"


class VAE(nn.Module):
    def __init__(self, latent_dim=2, hidden_dim=256):
        super().__init__()
        self.latent_dim = latent_dim
        self.enc1 = nn.Linear(28 * 28, hidden_dim)
        self.enc_mu = nn.Linear(hidden_dim, latent_dim)
        self.enc_logvar = nn.Linear(hidden_dim, latent_dim)
        self.dec1 = nn.Linear(latent_dim, hidden_dim)
        self.dec2 = nn.Linear(hidden_dim, 28 * 28)

    def encode(self, x):
        h = F.relu(self.enc1(x))
        return self.enc_mu(h), self.enc_logvar(h)

    def reparameterize(self, mu, logvar):
        # z = μ + σ*ε (ε〜N(0,1)) と変形することで、確率的サンプリングの「乱数部分」を
        # 外に切り出し、μ・σへの勾配だけを通常の誤差逆伝播で計算できるようにする
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return mu + std * eps

    def decode(self, z):
        h = F.relu(self.dec1(z))
        return torch.sigmoid(self.dec2(h))

    def forward(self, x):
        mu, logvar = self.encode(x)
        z = self.reparameterize(mu, logvar)
        return self.decode(z), mu, logvar


def vae_loss(recon_x, x, mu, logvar):
    recon_loss = F.binary_cross_entropy(recon_x, x, reduction="sum")
    # KLダイバージェンス: N(μ,σ²)とN(0,1)の間のKL距離の解析解
    kl_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    return recon_loss + kl_loss, recon_loss, kl_loss


def main() -> None:
    print("=== 1. データ準備(Fashion-MNIST) ===")
    transform = torchvision.transforms.ToTensor()
    train_ds = torchvision.datasets.FashionMNIST(root="data", train=True, download=True, transform=transform)
    test_ds = torchvision.datasets.FashionMNIST(root="data", train=False, download=True, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=128, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_ds, batch_size=256, shuffle=False)
    class_names = ["Tシャツ", "ズボン", "プルオーバー", "ドレス", "コート",
                   "サンダル", "シャツ", "スニーカー", "バッグ", "ブーツ"]
    print(f"訓練{len(train_ds)}枚 / テスト{len(test_ds)}枚")

    print("\n=== 2. VAEを学習(潜在次元=2、可視化のため低次元にする) ===")
    model = VAE(latent_dim=2).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    t0 = time.perf_counter()
    n_epochs = 15
    for epoch in range(n_epochs):
        model.train()
        total_loss, total_recon, total_kl = 0.0, 0.0, 0.0
        for x, _ in train_loader:
            x = x.view(x.size(0), -1).to(DEVICE)
            optimizer.zero_grad()
            recon_x, mu, logvar = model(x)
            loss, recon_loss, kl_loss = vae_loss(recon_x, x, mu, logvar)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            total_recon += recon_loss.item()
            total_kl += kl_loss.item()
        n = len(train_ds)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"epoch{epoch + 1:2d}: 平均loss={total_loss / n:.2f} "
                  f"(再構成={total_recon / n:.2f}, KL={total_kl / n:.2f})")
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 3. 潜在空間・再構成・生成の可視化 ===")
    model.eval()
    fig, axes = plt.subplots(2, 3, figsize=(16, 10.5))

    # (a) テストデータを2次元潜在空間にエンコードし、クラスごとに色分けして散布図に
    all_mu, all_labels = [], []
    with torch.no_grad():
        for x, y in test_loader:
            mu, _ = model.encode(x.view(x.size(0), -1))
            all_mu.append(mu.numpy())
            all_labels.append(y.numpy())
    all_mu = np.concatenate(all_mu)
    all_labels = np.concatenate(all_labels)
    scatter = axes[0, 0].scatter(all_mu[:, 0], all_mu[:, 1], c=all_labels, cmap="tab10", s=3, alpha=0.5)
    axes[0, 0].set_title("テストデータの潜在空間(2次元)への埋め込み")
    axes[0, 0].set_xlabel("z1")
    axes[0, 0].set_ylabel("z2")
    handles, _ = scatter.legend_elements()
    legend = axes[0, 0].legend(handles, class_names, title="クラス", fontsize=6, loc="upper right")
    axes[0, 0].add_artist(legend)

    # (b) 元画像 vs 再構成画像
    x_sample, y_sample = next(iter(test_loader))
    x_sample = x_sample[:8]
    with torch.no_grad():
        recon, _, _ = model(x_sample.view(8, -1))
    axes[0, 1].imshow(np.hstack([x_sample[i, 0].numpy() for i in range(8)]), cmap="gray")
    axes[0, 1].set_title("元画像(8枚)")
    axes[0, 1].axis("off")
    axes[0, 2].imshow(np.hstack([recon[i].view(28, 28).numpy() for i in range(8)]), cmap="gray")
    axes[0, 2].set_title("VAEによる再構成")
    axes[0, 2].axis("off")

    # (c) 潜在空間を格子状にサンプリングして生成(潜在空間の「なめらかさ」を確認)
    grid_n = 10
    grid_x = np.linspace(-3, 3, grid_n)
    grid_y = np.linspace(-3, 3, grid_n)
    canvas = np.zeros((28 * grid_n, 28 * grid_n))
    with torch.no_grad():
        for i, yi in enumerate(grid_y):
            for j, xi in enumerate(grid_x):
                z = torch.tensor([[xi, yi]], dtype=torch.float32)
                img = model.decode(z).view(28, 28).numpy()
                canvas[i * 28:(i + 1) * 28, j * 28:(j + 1) * 28] = img
    axes[1, 0].imshow(canvas, cmap="gray")
    axes[1, 0].set_title("潜在空間を格子状に走査して生成\n(z1,z2を-3〜3で均等にサンプリング)")
    axes[1, 0].axis("off")

    # (d) 標準正規分布からランダムサンプリングして生成(実際の生成利用シーン)
    with torch.no_grad():
        z_random = torch.randn(64, 2)
        gen = model.decode(z_random).view(64, 28, 28).numpy()
    grid_gen = np.zeros((28 * 8, 28 * 8))
    for i in range(8):
        for j in range(8):
            grid_gen[i * 28:(i + 1) * 28, j * 28:(j + 1) * 28] = gen[i * 8 + j]
    axes[1, 1].imshow(grid_gen, cmap="gray")
    axes[1, 1].set_title("z〜N(0,1)からランダムサンプリングして生成(64枚)")
    axes[1, 1].axis("off")

    axes[1, 2].axis("off")

    fig.tight_layout()
    out_path = "vae_fashionmnist.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\n潜在空間の散布図を見ると、ズボン(class1)やバッグ(class8)のような形が"
        "独特なクラスは比較的まとまった領域を作る一方、シャツ/Tシャツ/コート/プルオーバー"
        "のような上半身の衣類同士は潜在空間上でも領域が重なり合っており、見た目が似ている"
        "クラス同士は潜在表現も近くなることが分かる。潜在次元をわずか2次元に絞ったことで"
        "再構成画像はやや輪郭がぼやけるが、格子状サンプリングでは、あるクラスの見た目から"
        "別のクラスの見た目へなめらかに変化していく様子が確認でき、通常のオートエンコーダには"
        "ない『潜在空間のなめらかさ』というVAEの特徴を視覚的に確認できた。"
    )


if __name__ == "__main__":
    main()
