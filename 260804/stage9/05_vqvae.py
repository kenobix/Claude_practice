"""PyTorchでVQ-VAE(Vector Quantized VAE)を実装し、離散潜在空間を01の連続潜在空間と比較する

01のVAEは、各画像を潜在空間上の「連続的な確率分布」(平均μ・分散σ²)にエンコードし、
KLダイバージェンスで標準正規分布に近づけることで、潜在空間全体をなめらかにしていた。
VQ-VAEはこれとは異なる発想で、潜在表現を「あらかじめ用意した有限個のベクトル
(コードブック)のうちどれに一番近いか」というインデックス(離散値)として表現する:
  1) エンコーダが画像を連続的な特徴マップz_eに変換する(ここでは7x7の特徴マップ)
  2) 各位置のベクトルを、コードブック中で最も近いベクトルに置き換える(量子化)
  3) デコーダが量子化後のベクトルz_qから画像を再構成する
「最も近いベクトルを選ぶ」という操作は微分不可能なため、逆伝播時だけ量子化前後の
勾配をそのまま素通りさせるstraight-through estimatorを使う。また、コードブック自体を
z_eに近づける項(codebook loss)と、エンコーダの出力をコードブックに近づける項
(commitment loss)を損失に加える。KLダイバージェンスによる「なめらかな連続空間」ではなく、
離散的な「どの原型(コード)を使うか」という表現を学ぶ点がVAEとの最大の違いになる。
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
NUM_EMBEDDINGS = 32   # コードブックのサイズ(離散コードの種類数)
EMBEDDING_DIM = 16    # 各コードベクトルの次元数


class VectorQuantizer(nn.Module):
    def __init__(self, num_embeddings, embedding_dim, commitment_cost=0.25):
        super().__init__()
        self.embedding_dim = embedding_dim
        self.embedding = nn.Embedding(num_embeddings, embedding_dim)
        self.embedding.weight.data.uniform_(-1 / num_embeddings, 1 / num_embeddings)
        self.commitment_cost = commitment_cost

    def forward(self, z_e):
        # (B, C, H, W) -> (B, H, W, C) -> (B*H*W, C) にして、各ベクトルごとに最近傍コードを探す
        z_e_perm = z_e.permute(0, 2, 3, 1).contiguous()
        flat = z_e_perm.view(-1, self.embedding_dim)

        distances = (
            flat.pow(2).sum(1, keepdim=True)
            - 2 * flat @ self.embedding.weight.t()
            + self.embedding.weight.pow(2).sum(1)
        )
        indices = distances.argmin(dim=1)
        quantized = self.embedding(indices).view(z_e_perm.shape)

        codebook_loss = F.mse_loss(quantized, z_e_perm.detach())     # コードブックをz_eに近づける
        commitment_loss = F.mse_loss(quantized.detach(), z_e_perm)   # z_eをコードブックに近づける
        vq_loss = codebook_loss + self.commitment_cost * commitment_loss

        # straight-through estimator: 順伝播は量子化後の値を使い、逆伝播の勾配はz_eにそのまま流す
        quantized_st = z_e_perm + (quantized - z_e_perm).detach()
        quantized_st = quantized_st.permute(0, 3, 1, 2).contiguous()

        indices = indices.view(z_e.size(0), z_e.size(2), z_e.size(3))
        return quantized_st, vq_loss, indices


class Encoder(nn.Module):
    def __init__(self, embedding_dim, hidden=32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(1, hidden, 4, stride=2, padding=1), nn.ReLU(),   # 28x28 -> 14x14
            nn.Conv2d(hidden, hidden, 4, stride=2, padding=1), nn.ReLU(),  # 14x14 -> 7x7
            nn.Conv2d(hidden, embedding_dim, 3, padding=1),
        )

    def forward(self, x):
        return self.net(x)


class Decoder(nn.Module):
    def __init__(self, embedding_dim, hidden=32):
        super().__init__()
        self.net = nn.Sequential(
            nn.ConvTranspose2d(embedding_dim, hidden, 4, stride=2, padding=1), nn.ReLU(),  # 7x7 -> 14x14
            nn.ConvTranspose2d(hidden, hidden, 4, stride=2, padding=1), nn.ReLU(),  # 14x14 -> 28x28
            nn.Conv2d(hidden, 1, 3, padding=1), nn.Sigmoid(),
        )

    def forward(self, z):
        return self.net(z)


class VQVAE(nn.Module):
    def __init__(self, num_embeddings=NUM_EMBEDDINGS, embedding_dim=EMBEDDING_DIM):
        super().__init__()
        self.encoder = Encoder(embedding_dim)
        self.vq = VectorQuantizer(num_embeddings, embedding_dim)
        self.decoder = Decoder(embedding_dim)

    def forward(self, x):
        z_e = self.encoder(x)
        z_q, vq_loss, indices = self.vq(z_e)
        recon = self.decoder(z_q)
        return recon, vq_loss, indices


def main() -> None:
    print("=== 1. データ準備(Fashion-MNIST, 01と共通) ===")
    transform = torchvision.transforms.ToTensor()
    train_ds = torchvision.datasets.FashionMNIST(root="data", train=True, download=True, transform=transform)
    test_ds = torchvision.datasets.FashionMNIST(root="data", train=False, download=True, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=128, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_ds, batch_size=256, shuffle=False)
    print(f"訓練{len(train_ds)}枚 / テスト{len(test_ds)}枚, コードブックサイズ={NUM_EMBEDDINGS}, "
          f"コード次元={EMBEDDING_DIM}, 潜在マップ=7x7({7*7}箇所×コード1つ)")

    print("\n=== 2. VQ-VAEを学習 ===")
    model = VQVAE().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    t0 = time.perf_counter()
    n_epochs = 15
    for epoch in range(n_epochs):
        model.train()
        total_recon, total_vq = 0.0, 0.0
        for x, _ in train_loader:
            x = x.to(DEVICE)
            optimizer.zero_grad()
            recon, vq_loss, _ = model(x)
            recon_loss = F.mse_loss(recon, x)
            loss = recon_loss + vq_loss
            loss.backward()
            optimizer.step()
            total_recon += recon_loss.item() * x.size(0)
            total_vq += vq_loss.item() * x.size(0)
        n = len(train_ds)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"epoch{epoch + 1:2d}: 再構成MSE={total_recon / n:.5f}, VQ損失={total_vq / n:.5f}")
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 3. コードブックの使用状況・再構成・可視化 ===")
    model.eval()
    all_indices = []
    total_test_mse = 0.0
    with torch.no_grad():
        for x, _ in test_loader:
            recon, _, indices = model(x)
            all_indices.append(indices.numpy().reshape(-1))
            total_test_mse += F.mse_loss(recon, x, reduction="sum").item()
    all_indices = np.concatenate(all_indices)
    test_mse = total_test_mse / (len(test_ds) * 28 * 28)
    code_usage = np.bincount(all_indices, minlength=NUM_EMBEDDINGS)
    n_used_codes = int((code_usage > 0).sum())
    print(f"テストMSE(画素あたり)={test_mse:.5f}")
    print(f"実際に使われたコード数={n_used_codes}/{NUM_EMBEDDINGS}"
          f"(最頻コードの使用率={code_usage.max() / code_usage.sum():.1%})")

    fig, axes = plt.subplots(2, 2, figsize=(11, 10))

    x_sample, _ = next(iter(test_loader))
    x_sample = x_sample[:8]
    with torch.no_grad():
        recon_sample, _, idx_sample = model(x_sample)
    axes[0, 0].imshow(np.hstack([x_sample[i, 0].numpy() for i in range(8)]), cmap="gray")
    axes[0, 0].set_title("元画像(8枚)")
    axes[0, 0].axis("off")
    axes[0, 1].imshow(np.hstack([recon_sample[i, 0].numpy() for i in range(8)]), cmap="gray")
    axes[0, 1].set_title(f"VQ-VAEによる再構成(テストMSE={test_mse:.4f})")
    axes[0, 1].axis("off")

    axes[1, 0].bar(range(NUM_EMBEDDINGS), code_usage)
    axes[1, 0].set_xlabel("コードブックのインデックス")
    axes[1, 0].set_ylabel("使用回数(テストデータ全体, 7x7箇所×画像数)")
    axes[1, 0].set_title(f"コードブックの使用頻度({n_used_codes}/{NUM_EMBEDDINGS}個が使用された)")

    # 1枚のサンプルについて、7x7の各位置がどのコードを選んだかをヒートマップ表示
    im = axes[1, 1].imshow(idx_sample[0].numpy(), cmap="tab20", vmin=0, vmax=NUM_EMBEDDINGS - 1)
    axes[1, 1].set_title("1枚のサンプルの7x7潜在マップ\n(色=選ばれたコードのインデックス)")
    fig.colorbar(im, ax=axes[1, 1], fraction=0.046)

    fig.tight_layout()
    out_path = "vqvae_fashionmnist.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\n{NUM_EMBEDDINGS}個用意したコードブックのうち実際に使われたのは{n_used_codes}個で、"
        f"{'ほぼ全てのコードが活用された' if n_used_codes >= NUM_EMBEDDINGS * 0.8 else '一部のコードは使われずに終わった'}。"
        "01のVAEが潜在空間全体を連続的・なめらかにする(z1,z2をわずかにずらすと生成画像も"
        "わずかに変化する)のに対し、VQ-VAEは各位置の表現を有限個の離散コードのどれかに"
        "強制的に割り当てるため、『潜在空間を連続的に走査して中間的な画像を作る』"
        "01のような生成の仕方はできない(コードの間には『中間』が存在しない)。"
        "その代わり、この離散コードの系列(7x7=49個のインデックス)をさらに別の系列モデル"
        "(PixelCNNやTransformer等)で学習させれば、より高品質な画像を離散コードの組み合わせとして"
        "生成できるようになる、というのがVQ-VAEが後続の研究(VQ-GAN、DALL-E等)で"
        "採用され続けている理由である。"
    )


if __name__ == "__main__":
    main()
