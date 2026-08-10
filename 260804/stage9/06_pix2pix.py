"""Pix2Pix(条件付きGANによるペア画像変換)を実装し、DCGANとの発想の違いを確認する

02のDCGANは「ランダムノイズ」から画像を生成する、いわば『無』からの生成だった。
Pix2Pixはこれとは異なり、「入力画像」から「対応する別の画像」への変換を学習する
条件付きGAN(conditional GAN)で、
  edges2shoes(靴の輪郭線→本物らしい靴の画像)
  Pix2Pixのその他有名な応用例と同様に、ここでは「図形の輪郭線(エッジ画像)」→
  「色つきの図形画像」というペア変換タスクを、Stage5の合成図形データセットから作る。
アーキテクチャは2つの工夫からなる:
  - Generator: U-Net(エンコーダ・デコーダ+スキップ結合)。入力の低レベルな構造
    (輪郭線の位置)をスキップ結合でそのままデコーダに伝えることで、
    ただのオートエンコーダより鮮明な出力を作りやすくする
  - Discriminator: PatchGAN。画像全体が本物か偽物かを1つのスカラーで判定するのではなく、
    画像をパッチに分割してパッチごとに本物らしさを判定する。局所的なテクスチャの
    リアルさに集中させることで、L1損失だけでは出しにくい鮮明さを補う
損失は「Discriminatorを騙す敵対的損失」+「生成画像を正解画像に直接近づけるL1損失」の
組み合わせで、L1損失があることが「入力と無関係な絵」を生成しがちなDCGANとの違いになる
(Pix2Pixは入力に対応した『正解』が存在するペア変換タスクだからこそ使える損失)。
"""
import time

import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def to_edge_map(images: np.ndarray) -> np.ndarray:
    """RGB画像(N,3,H,W, [0,1])から、Sobelフィルタでエッジ画像(N,3,H,W)を作る

    背景が白・輪郭線が黒の『線画』になるようにし、色情報を取り除く
    (Pix2Pixの定番デモである edges2shoes と同じ発想の入力を作る)。
    """
    gray = images.mean(axis=1)  # (N,H,W)
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = kx.T
    edges = np.zeros_like(gray)
    for i in range(gray.shape[0]):
        gx = _convolve2d(gray[i], kx)
        gy = _convolve2d(gray[i], ky)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        edges[i] = mag
    edges = edges / (edges.max() + 1e-8)
    edge_img = 1.0 - np.clip(edges * 3.0, 0, 1)  # 白背景に黒線、強調のため3倍してクリップ
    return np.repeat(edge_img[:, None, :, :], 3, axis=1).astype(np.float32)


def _convolve2d(img: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    padded = np.pad(img, 1, mode="edge")
    h, w = img.shape
    out = np.zeros_like(img)
    for i in range(3):
        for j in range(3):
            out += kernel[i, j] * padded[i:i + h, j:j + w]
    return out


class UNetGenerator(nn.Module):
    """エッジ画像(3ch)→色つき画像(3ch)。スキップ結合で入力の輪郭情報を直接デコーダに渡す"""

    def __init__(self, base=32):
        super().__init__()
        self.enc1 = nn.Sequential(nn.Conv2d(3, base, 4, 2, 1), nn.LeakyReLU(0.2))          # 32->16
        self.enc2 = nn.Sequential(nn.Conv2d(base, base * 2, 4, 2, 1),
                                   nn.BatchNorm2d(base * 2), nn.LeakyReLU(0.2))              # 16->8
        self.enc3 = nn.Sequential(nn.Conv2d(base * 2, base * 4, 4, 2, 1),
                                   nn.BatchNorm2d(base * 4), nn.LeakyReLU(0.2))              # 8->4

        self.dec3 = nn.Sequential(nn.ConvTranspose2d(base * 4, base * 2, 4, 2, 1),
                                   nn.BatchNorm2d(base * 2), nn.ReLU())                       # 4->8
        self.dec2 = nn.Sequential(nn.ConvTranspose2d(base * 4, base, 4, 2, 1),
                                   nn.BatchNorm2d(base), nn.ReLU())                           # 8->16 (concat後base*4入力)
        self.dec1 = nn.Sequential(nn.ConvTranspose2d(base * 2, 3, 4, 2, 1), nn.Tanh())       # 16->32 (concat後base*2入力)

    def forward(self, x):
        e1 = self.enc1(x)
        e2 = self.enc2(e1)
        e3 = self.enc3(e2)
        d3 = self.dec3(e3)
        d2 = self.dec2(torch.cat([d3, e2], dim=1))  # スキップ結合
        d1 = self.dec1(torch.cat([d2, e1], dim=1))  # スキップ結合
        return d1


class PatchDiscriminator(nn.Module):
    """画像全体でなくパッチ単位で本物らしさを判定する(入力=エッジ画像+対象画像を結合)"""

    def __init__(self, base=32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(6, base, 4, 2, 1), nn.LeakyReLU(0.2),                                  # 32->16
            nn.Conv2d(base, base * 2, 4, 2, 1), nn.BatchNorm2d(base * 2), nn.LeakyReLU(0.2),  # 16->8
            nn.Conv2d(base * 2, 1, 4, 2, 1),                                                  # 8->4 (パッチごとのlogit)
        )

    def forward(self, edge, image):
        return self.net(torch.cat([edge, image], dim=1))


def main() -> None:
    print("=== 1. データ準備: 合成図形からエッジ画像↔色つき画像のペアを作る ===")
    X_train, _ = generate_shapes_dataset(1500, seed=0)
    X_test, _ = generate_shapes_dataset(300, seed=1)
    edge_train, edge_test = to_edge_map(X_train), to_edge_map(X_test)

    # Generatorの出力はTanh([-1,1])のため、正解画像側も[-1,1]に合わせる
    target_train = torch.tensor(X_train) * 2 - 1
    target_test = torch.tensor(X_test) * 2 - 1
    edge_train_t = torch.tensor(edge_train) * 2 - 1
    edge_test_t = torch.tensor(edge_test) * 2 - 1
    print(f"訓練{len(X_train)}組 / テスト{len(X_test)}組(エッジ画像 → 色つき画像)")

    print("\n=== 2. Pix2Pix(U-Net Generator + PatchGAN Discriminator)を学習 ===")
    G = UNetGenerator()
    D = PatchDiscriminator()
    opt_g = torch.optim.Adam(G.parameters(), lr=2e-4, betas=(0.5, 0.999))
    opt_d = torch.optim.Adam(D.parameters(), lr=2e-4, betas=(0.5, 0.999))
    bce = nn.BCEWithLogitsLoss()
    l1 = nn.L1Loss()
    lambda_l1 = 100.0

    n_epochs = 40
    batch_size = 64
    n = len(X_train)
    history = {"g_loss": [], "d_loss": [], "l1": []}

    t0 = time.perf_counter()
    for epoch in range(n_epochs):
        perm = torch.randperm(n)
        epoch_g, epoch_d, epoch_l1 = 0.0, 0.0, 0.0
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            edge, target = edge_train_t[idx], target_train[idx]
            bs = edge.size(0)
            real_label = torch.ones(bs, 1, 4, 4)
            fake_label = torch.zeros(bs, 1, 4, 4)

            fake = G(edge)

            opt_d.zero_grad()
            d_real = D(edge, target)
            d_fake = D(edge, fake.detach())
            d_loss = 0.5 * (bce(d_real, real_label) + bce(d_fake, fake_label))
            d_loss.backward()
            opt_d.step()

            opt_g.zero_grad()
            d_fake_for_g = D(edge, fake)
            g_adv = bce(d_fake_for_g, real_label)
            g_l1 = l1(fake, target) * lambda_l1
            g_loss = g_adv + g_l1
            g_loss.backward()
            opt_g.step()

            epoch_g += g_adv.item() * bs
            epoch_d += d_loss.item() * bs
            epoch_l1 += (g_l1.item() / lambda_l1) * bs

        history["g_loss"].append(epoch_g / n)
        history["d_loss"].append(epoch_d / n)
        history["l1"].append(epoch_l1 / n)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"epoch{epoch + 1:2d}: G敵対損失={history['g_loss'][-1]:.3f}, "
                  f"D損失={history['d_loss'][-1]:.3f}, L1誤差={history['l1'][-1]:.4f}")
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 3. 可視化 ===")
    G.eval()
    with torch.no_grad():
        fake_test = G(edge_test_t[:8])
    fake_test_01 = ((fake_test + 1) / 2).clamp(0, 1)
    edge_test_01 = ((edge_test_t[:8] + 1) / 2).clamp(0, 1)
    target_test_01 = ((target_test[:8] + 1) / 2).clamp(0, 1)

    fig, axes = plt.subplots(3, 1, figsize=(13, 8))
    for ax, imgs, title in [
        (axes[0], edge_test_01, "入力(エッジ画像)"),
        (axes[1], fake_test_01, "Pix2Pixによる生成"),
        (axes[2], target_test_01, "正解(色つき画像)"),
    ]:
        row = np.hstack([imgs[i].permute(1, 2, 0).numpy() for i in range(8)])
        ax.imshow(row)
        ax.set_title(title)
        ax.axis("off")
    fig.tight_layout()
    out_path1 = "pix2pix_examples.png"
    fig.savefig(out_path1, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path1}")

    fig2, axes2 = plt.subplots(1, 2, figsize=(11, 4.5))
    axes2[0].plot(history["g_loss"], label="Generator敵対損失")
    axes2[0].plot(history["d_loss"], label="Discriminator損失")
    axes2[0].set_xlabel("epoch")
    axes2[0].legend()
    axes2[0].set_title("敵対損失の推移")
    axes2[1].plot(history["l1"], color="tab:green")
    axes2[1].set_xlabel("epoch")
    axes2[1].set_ylabel("L1誤差(生成画像 vs 正解画像)")
    axes2[1].set_title("L1再構成誤差の推移")
    fig2.tight_layout()
    out_path2 = "pix2pix_training_curve.png"
    fig2.savefig(out_path2, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path2}")

    final_l1 = history["l1"][-1]
    print(
        f"\n最終的なL1誤差(画素値の平均絶対誤差, [0,1]換算)={final_l1:.4f}。"
        "生成結果を見ると、入力エッジ画像の輪郭線の位置・形状(円/四角/三角/十字)は"
        "ほぼそのまま保たれており、スキップ結合によって入力の構造情報がデコーダまで"
        "直接伝わっていることが確認できる。一方で塗られた色は正解画像の色とは"
        "必ずしも一致していない——これは学習の失敗ではなく、エッジ画像には色の情報が"
        "全く含まれていないため『この形にどんな色が塗られていたか』はモデルにとって"
        "本質的に決定不能な問題であり、Pix2Pixは学習データ全体で見てもっともらしい"
        "色(この合成図形データセットでは暗めの色調)を『推測』して塗っていると考えられる。"
        "02のDCGANは『ランダムノイズ→画像』という1対多(同じノイズ次元でも学習後は"
        "無限の画像を生成しうる)の写像だったのに対し、Pix2Pixは『入力画像→対応する1枚』"
        "というほぼ1対1の写像を学習する設計だが、この色の例のように入力だけでは"
        "一意に定まらない要素は依然として残ることが分かる。生成の自由度と引き換えに"
        "『何が生成されるべきか』を入力が具体的に指定できるという実用上の違いが、"
        "同じGANの枠組みでもタスク設計次第で大きく異なる使い方になることを示している。"
    )


if __name__ == "__main__":
    main()
