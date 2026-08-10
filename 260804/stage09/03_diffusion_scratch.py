"""PyTorchでDDPM(Denoising Diffusion Probabilistic Model)の簡易版をスクラッチ実装する

01のVAE・02のGANは「1回の順伝播で画像を生成する」枠組みだった。
Diffusion Modelは全く異なるアプローチを取る:
  1) 前向き過程(forward process): 訓練画像に、T ステップかけて少しずつガウスノイズを
     加えていき、最終的に完全なノイズ(標準正規分布)にする。これは学習不要で式で書ける。
  2) 逆向き過程(reverse process): 「完全なノイズ」から「1ステップ前のノイズが少し
     減った状態」を予測するニューラルネットワークを学習する。これを繰り返し適用すれば、
     完全なノイズから徐々に画像を復元(生成)できる。

学習時にネットワークが直接予測するのは「画像」そのものではなく、
「そのステップで加えられたノイズε」であることが多い(ノイズ予測モデル)。
ここではFashion-MNISTの中の1クラス(スニーカー)だけを使い、小さいCNNで
ノイズ予測ネットワークを学習し、生成過程を可視化する。
"""
import time

import numpy as np
import torch
import torch.nn as nn
import torchvision
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)
T = 200  # 拡散のステップ数(論文では1000前後が一般的だが、CPUでの実行時間を考え簡略化)


def cosine_beta_schedule(T, s=0.008):
    """ステップtでどれだけノイズを加えるかのスケジュール(cosine schedule, Nichol&Dhariwal 2021)。
    線形スケジュールより、学習・サンプリングの質が安定することが知られている。"""
    steps = torch.arange(T + 1, dtype=torch.float64) / T
    alphas_cumprod = torch.cos((steps + s) / (1 + s) * torch.pi * 0.5) ** 2
    alphas_cumprod = alphas_cumprod / alphas_cumprod[0]
    betas = 1 - (alphas_cumprod[1:] / alphas_cumprod[:-1])
    return torch.clip(betas, 0.0001, 0.999).float()


betas = cosine_beta_schedule(T)
alphas = 1.0 - betas
alphas_cumprod = torch.cumprod(alphas, dim=0)  # ᾱ_t = α_1*α_2*...*α_t


def forward_diffusion(x0, t, noise=None):
    """x0(元画像)にtステップぶんのノイズを『1回で』加える式:
    x_t = √ᾱ_t * x0 + √(1-ᾱ_t) * noise
    これは各ステップでガウスノイズを逐次加えた場合と数学的に等価(ガウス分布の再生性)で、
    Tステップ分の逐次計算をせずにいきなりx_tを作れるのが前向き過程の便利な点。"""
    if noise is None:
        noise = torch.randn_like(x0)
    sqrt_acp = alphas_cumprod[t].sqrt().view(-1, 1, 1, 1)
    sqrt_one_minus_acp = (1 - alphas_cumprod[t]).sqrt().view(-1, 1, 1, 1)
    return sqrt_acp * x0 + sqrt_one_minus_acp * noise, noise


class SinusoidalTimeEmbedding(nn.Module):
    """時刻tを、Transformerの位置エンコーディングと同じ発想でベクトルに変換する。
    ネットワークに『今どのくらいノイズが乗っているステップか』を教えるために必要。"""

    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, t):
        half = self.dim // 2
        freqs = torch.exp(-np.log(10000) * torch.arange(half, dtype=torch.float32) / half)
        args = t.float().unsqueeze(1) * freqs.unsqueeze(0)
        return torch.cat([torch.sin(args), torch.cos(args)], dim=1)


class SmallUNet(nn.Module):
    """01・02より小さいCNN。時刻埋め込みを各畳み込みブロックに加算して、
    『今のノイズレベル』に応じた適切なノイズ予測ができるようにする。"""

    def __init__(self, ch=32, time_dim=64):
        super().__init__()
        self.time_embed = nn.Sequential(
            SinusoidalTimeEmbedding(time_dim), nn.Linear(time_dim, time_dim), nn.ReLU(),
        )
        self.enc1 = nn.Conv2d(1, ch, 3, padding=1)
        self.enc2 = nn.Conv2d(ch, ch * 2, 3, stride=2, padding=1)  # 28->14
        self.time_proj1 = nn.Linear(time_dim, ch)
        self.time_proj2 = nn.Linear(time_dim, ch * 2)
        self.mid = nn.Conv2d(ch * 2, ch * 2, 3, padding=1)
        self.dec2 = nn.ConvTranspose2d(ch * 2, ch, 4, stride=2, padding=1)  # 14->28
        self.out = nn.Conv2d(ch * 2, 1, 3, padding=1)
        self.act = nn.ReLU()

    def forward(self, x, t):
        temb = self.time_embed(t)
        h1 = self.act(self.enc1(x) + self.time_proj1(temb).unsqueeze(-1).unsqueeze(-1))
        h2 = self.act(self.enc2(h1) + self.time_proj2(temb).unsqueeze(-1).unsqueeze(-1))
        m = self.act(self.mid(h2))
        d = self.act(self.dec2(m))
        return self.out(torch.cat([d, h1], dim=1))


@torch.no_grad()
def sample(model, n_samples, save_every=None):
    """完全なノイズx_Tから出発し、t=T-1,...,0の順にノイズ予測モデルで少しずつ脱ノイズしていく"""
    model.eval()
    x = torch.randn(n_samples, 1, 28, 28)
    snapshots = {}
    for t in reversed(range(T)):
        t_batch = torch.full((n_samples,), t, dtype=torch.long)
        pred_noise = model(x, t_batch)
        alpha_t = alphas[t]
        alpha_cumprod_t = alphas_cumprod[t]
        beta_t = betas[t]
        # 予測したノイズを使って、x_tから平均的なx_{t-1}を求める(DDPMの逆向き過程の式)
        mean = (1 / alpha_t.sqrt()) * (x - (beta_t / (1 - alpha_cumprod_t).sqrt()) * pred_noise)
        if t > 0:
            noise = torch.randn_like(x)
            x = mean + beta_t.sqrt() * noise
        else:
            x = mean
        if save_every is not None and (t % save_every == 0 or t == T - 1):
            snapshots[t] = x.clone()
    model.train()
    return x, snapshots


def main() -> None:
    print("=== 1. データ準備(Fashion-MNISTの『スニーカー』クラスのみ使用) ===")
    transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.5,), (0.5,)),  # [-1, 1]に正規化
    ])
    full_ds = torchvision.datasets.FashionMNIST(root="data", train=True, download=True, transform=transform)
    sneaker_idx = [i for i, (_, y) in enumerate(full_ds) if y == 7]  # 7=スニーカー
    sneaker_ds = torch.utils.data.Subset(full_ds, sneaker_idx)
    train_loader = torch.utils.data.DataLoader(sneaker_ds, batch_size=64, shuffle=True)
    print(f"スニーカー画像: {len(sneaker_ds)}枚を使用(全クラスだと学習に時間がかかるため単純化)")

    print("\n=== 2. 前向き過程(ノイズを加えていく様子)を可視化 ===")
    x0, _ = next(iter(train_loader))
    x0_sample = x0[:1]
    show_steps = [0, 20, 50, 100, 150, 199]
    fig0, axes0 = plt.subplots(1, len(show_steps), figsize=(15, 3))
    for ax, t in zip(axes0, show_steps):
        xt, _ = forward_diffusion(x0_sample, torch.tensor([t]))
        ax.imshow(xt[0, 0].numpy(), cmap="gray")
        ax.set_title(f"t={t}")
        ax.axis("off")
    fig0.suptitle("前向き過程: 元画像に段階的にノイズを加える(式で計算、学習不要)")
    fig0.tight_layout()
    fig0.savefig("diffusion_forward_process.png", dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/diffusion_forward_process.png")

    print("\n=== 3. ノイズ予測ネットワークを学習 ===")
    model = SmallUNet()
    optimizer = torch.optim.Adam(model.parameters(), lr=2e-4)
    criterion = nn.MSELoss()

    t0 = time.perf_counter()
    n_epochs = 30
    losses = []
    for epoch in range(n_epochs):
        epoch_loss, n_batches = 0.0, 0
        for x0_batch, _ in train_loader:
            batch_size = x0_batch.size(0)
            t_batch = torch.randint(0, T, (batch_size,))
            xt, noise = forward_diffusion(x0_batch, t_batch)
            pred_noise = model(xt, t_batch)
            loss = criterion(pred_noise, noise)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1
        losses.append(epoch_loss / n_batches)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"epoch{epoch + 1:2d}: 平均loss(ノイズ予測のMSE)={losses[-1]:.4f}")
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 4. 逆向き過程(サンプリング)で新しいスニーカー画像を生成 ===")
    t0 = time.perf_counter()
    generated, snapshots = sample(model, n_samples=8, save_every=40)
    print(f"サンプリング時間(1枚あたり{T}ステップ×8枚)={time.perf_counter() - t0:.1f}秒")

    fig, axes = plt.subplots(2, 5, figsize=(15, 6.5))
    axes[0, 0].plot(losses)
    axes[0, 0].set_xlabel("epoch")
    axes[0, 0].set_ylabel("MSE loss(ノイズ予測)")
    axes[0, 0].set_title("学習曲線")

    snapshot_ts = sorted(snapshots.keys(), reverse=True)
    snapshot_axes = list(axes[0, 1:]) + list(axes[1, :4])
    for ax, t in zip(snapshot_axes, snapshot_ts):
        ax.imshow(snapshots[t][0, 0].numpy(), cmap="gray")
        ax.set_title(f"サンプリング t={t}")
        ax.axis("off")
    for ax in snapshot_axes[len(snapshot_ts):]:
        ax.axis("off")

    grid = np.zeros((28 * 2, 28 * 4))
    for i in range(8):
        r, c = divmod(i, 4)
        grid[r * 28:(r + 1) * 28, c * 28:(c + 1) * 28] = generated[i, 0].numpy()
    axes[1, 4].imshow(grid, cmap="gray")
    axes[1, 4].set_title("最終生成結果(8枚)")
    axes[1, 4].axis("off")

    fig.tight_layout()
    out_path = "diffusion_scratch.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\n前向き過程の可視化では、t=0の元画像がステップを追うごとに徐々にノイズに"
        "埋もれていき、t=199ではほぼ完全なノイズになる様子を確認できた。逆向き過程の"
        "サンプリングでは、t=200近くの完全なノイズから出発し、tが小さくなるにつれて"
        "ノイズの中から明るい塊(スニーカーのシルエットのおおまかな位置・輪郭)が"
        "浮かび上がってくる過程を可視化できた。ただし最終的な生成画像は、01のVAEや"
        "02のGANほど輪郭がくっきりしたスニーカーの形にはならず、ぼんやりした明るい"
        "塊にとどまった。これはCPUでの実行時間を優先して、モデルを小さく"
        "(2段のみのミニUNet、チャンネル数32)・学習も単一クラス6000枚×30epochという"
        "小規模構成にしたためで、ノイズ予測のMSE lossも0.09台で下げ止まっており、"
        "十分に収束しきっていないことがうかがえる。実際のDDPM論文やStable Diffusionでは"
        "はるかに大きなUNet・大量のデータ・数千〜数万epoch相当の学習量を使っており、"
        "『仕組みは同じでもスケールが結果の質を大きく左右する』ことを、逆に小規模実装の"
        "限界として体感する結果になった。一方で、01のVAE・02のGANが『1回のネットワーク"
        f"呼び出し』で画像を生成するのに対し、Diffusion Modelは1枚生成するのに{T}回も"
        "ネットワークを呼び出す必要があり、生成の仕組みが本質的に異なることは確認できた。"
        "この生成コストの高さが、実際のStable Diffusion等で高速化"
        "(ステップ数の削減、潜在空間での拡散等)が重要な研究テーマになっている理由である。"
    )


if __name__ == "__main__":
    main()
