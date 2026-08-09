"""PyTorchでDCGAN(Deep Convolutional GAN)を実装し、Fashion-MNISTで画像生成を試す

01のVAEは「再構成誤差+KLダイバージェンス」という明示的な損失を最小化する形で
生成を学んだが、GAN(敵対的生成ネットワーク)は全く違う発想を取る:
  - Generator(生成器): ランダムノイズから、本物らしい画像を作ろうとする
  - Discriminator(識別器): 画像が本物(訓練データ)か偽物(Generatorの生成物)かを見分けようとする
この2つを「贋作者 vs 鑑定士」のように敵対的に競わせながら同時に学習させることで、
Generatorは損失関数を明示的に設計しなくても、人間の目に自然に見える画像を生成する
ことを学んでいく。DCGANは、この枠組みに畳み込み層を組み合わせた代表的な構成。

GANの学習はVAEと違い、GeneratorとDiscriminatorの力関係のバランスが崩れると
うまく学習が進まない(mode collapse等)ことで知られ、学習の不安定さも体感する。
"""
import time

import numpy as np
import torch
import torch.nn as nn
import torchvision
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)
LATENT_DIM = 64


class Generator(nn.Module):
    """ノイズベクトル(1x1)から、転置畳み込みで段階的に7x7→14x14→28x28へ拡大していく"""

    def __init__(self, latent_dim=LATENT_DIM):
        super().__init__()
        self.net = nn.Sequential(
            nn.ConvTranspose2d(latent_dim, 128, kernel_size=7, stride=1, padding=0),  # 1x1 -> 7x7
            nn.BatchNorm2d(128), nn.ReLU(True),
            nn.ConvTranspose2d(128, 64, kernel_size=4, stride=2, padding=1),  # 7x7 -> 14x14
            nn.BatchNorm2d(64), nn.ReLU(True),
            nn.ConvTranspose2d(64, 1, kernel_size=4, stride=2, padding=1),  # 14x14 -> 28x28
            nn.Tanh(),  # 出力を[-1, 1]にする(訓練データ側もそれに合わせて正規化する)
        )

    def forward(self, z):
        return self.net(z.view(z.size(0), -1, 1, 1))


class Discriminator(nn.Module):
    """畳み込みで段階的に28x28→14x14→7x7へ縮小し、最後に本物らしさを1つのスカラーで出力する"""

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=4, stride=2, padding=1),  # 28x28 -> 14x14
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 128, kernel_size=4, stride=2, padding=1),  # 14x14 -> 7x7
            nn.BatchNorm2d(128), nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(128, 1, kernel_size=7, stride=1, padding=0),  # 7x7 -> 1x1
        )

    def forward(self, x):
        return self.net(x).view(-1)


def main() -> None:
    print("=== 1. データ準備(Fashion-MNIST, [-1,1]に正規化) ===")
    transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.5,), (0.5,)),
    ])
    train_ds = torchvision.datasets.FashionMNIST(root="data", train=True, download=True, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=128, shuffle=True, drop_last=True)
    print(f"訓練{len(train_ds)}枚")

    G = Generator()
    D = Discriminator()
    criterion = nn.BCEWithLogitsLoss()
    opt_G = torch.optim.Adam(G.parameters(), lr=2e-4, betas=(0.5, 0.999))
    opt_D = torch.optim.Adam(D.parameters(), lr=2e-4, betas=(0.5, 0.999))

    fixed_noise = torch.randn(64, LATENT_DIM)
    snapshot_epochs = [1, 3, 6, 10, 15, 20]
    snapshots = {}
    g_losses, d_losses = [], []

    print("\n=== 2. GeneratorとDiscriminatorを交互に学習(敵対的学習) ===")
    t0 = time.perf_counter()
    n_epochs = 20
    for epoch in range(n_epochs):
        g_loss_sum, d_loss_sum, n_batches = 0.0, 0.0, 0
        for real, _ in train_loader:
            batch_size = real.size(0)
            real_labels = torch.ones(batch_size)
            fake_labels = torch.zeros(batch_size)

            # --- Discriminatorの学習: 本物は本物、偽物は偽物と見分けられるようにする ---
            opt_D.zero_grad()
            d_loss_real = criterion(D(real), real_labels)
            noise = torch.randn(batch_size, LATENT_DIM)
            fake = G(noise)
            d_loss_fake = criterion(D(fake.detach()), fake_labels)
            d_loss = d_loss_real + d_loss_fake
            d_loss.backward()
            opt_D.step()

            # --- Generatorの学習: Discriminatorを『本物』と誤認させられるようにする ---
            opt_G.zero_grad()
            g_loss = criterion(D(fake), real_labels)  # 「これは本物だ」とDに思わせたい
            g_loss.backward()
            opt_G.step()

            g_loss_sum += g_loss.item()
            d_loss_sum += d_loss.item()
            n_batches += 1

        g_losses.append(g_loss_sum / n_batches)
        d_losses.append(d_loss_sum / n_batches)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"epoch{epoch + 1:2d}: G_loss={g_losses[-1]:.3f}  D_loss={d_losses[-1]:.3f}")

        if (epoch + 1) in snapshot_epochs:
            with torch.no_grad():
                G.eval()
                snapshots[epoch + 1] = G(fixed_noise).view(64, 28, 28).numpy()
                G.train()

    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 3. 学習曲線と生成画像の推移を可視化 ===")
    fig, axes = plt.subplots(2, 4, figsize=(18, 9.5))
    axes[0, 0].plot(g_losses, label="Generator loss")
    axes[0, 0].plot(d_losses, label="Discriminator loss")
    axes[0, 0].set_xlabel("epoch")
    axes[0, 0].set_ylabel("loss")
    axes[0, 0].set_title("学習曲線")
    axes[0, 0].legend()

    axes[0, 1].axis("off")
    axes[0, 2].axis("off")
    axes[0, 3].axis("off")

    for ax, epoch in zip(axes[1], snapshot_epochs[:4]):
        if epoch in snapshots:
            grid = np.zeros((28 * 4, 28 * 4))
            for i in range(4):
                for j in range(4):
                    grid[i * 28:(i + 1) * 28, j * 28:(j + 1) * 28] = snapshots[epoch][i * 4 + j]
            ax.imshow(grid, cmap="gray")
            ax.set_title(f"epoch{epoch}時点の生成画像")
            ax.axis("off")

    fig.tight_layout()
    out_path = "dcgan_fashionmnist.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\n学習曲線を見ると、Generator lossとDiscriminator lossは(VAEの単調減少するlossとは"
        "対照的に)互いに反応し合いながら上下し、どちらか一方が完全に『勝つ』ことなく"
        "拮抗した状態を保っている。これはGANの学習が本質的に『2つのネットワークの"
        "せめぎ合い』であり、片方が強くなりすぎるともう片方が学習できなくなる"
        "(Discriminatorが強すぎるとGeneratorへの勾配が消え、Generatorが強すぎると"
        "Discriminatorが機能しなくなる)という不安定さと表裏一体であることを示している。"
        "epochごとの生成画像を見ると、学習初期はほぼノイズだった生成画像が、"
        "epochを重ねるごとに衣類らしいシルエットへと変化していく様子を確認できる。"
    )


if __name__ == "__main__":
    main()
