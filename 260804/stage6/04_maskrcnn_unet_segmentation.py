"""セグメンテーション: 事前学習済みMask R-CNNの推論 + 自作U-Netの学習

物体検出が「矩形(バウンディングボックス)」で物体の位置を示すのに対し、
セグメンテーションは「画素単位」でどこが物体かを示す。
前半: 事前学習済みMask R-CNN(Faster R-CNN + マスク予測ブランチ)で
      インスタンスセグメンテーション(物体ごとに個別のマスク)を試す
後半: U-Net(エンコーダ・デコーダ+スキップ結合)を自作し、合成図形データで
      セマンティックセグメンテーション(画素ごとに物体か背景かを2値分類)を学習する
"""
import time

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
from torchvision.models.detection import maskrcnn_resnet50_fpn, MaskRCNN_ResNet50_FPN_Weights
from PIL import Image
import matplotlib.pyplot as plt

from synthetic_segmentation import generate_segmentation_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def demo_maskrcnn() -> None:
    print("=== 1. 事前学習済みMask R-CNNによるインスタンスセグメンテーション ===")
    class_names = MaskRCNN_ResNet50_FPN_Weights.COCO_V1.meta["categories"]
    model = maskrcnn_resnet50_fpn(weights=MaskRCNN_ResNet50_FPN_Weights.COCO_V1)
    model.eval()

    img = Image.open("assets/bus.jpg").convert("RGB")
    img_t = torchvision.transforms.functional.to_tensor(img)

    t0 = time.perf_counter()
    with torch.no_grad():
        pred = model([img_t])[0]
    elapsed = time.perf_counter() - t0

    keep = pred["scores"] >= 0.7
    masks = pred["masks"][keep, 0].numpy()  # (N, H, W), 0〜1の確率マップ
    labels = pred["labels"][keep].numpy()
    print(f"推論時間={elapsed:.2f}秒, 検出数={keep.sum().item()}件")
    for label in labels:
        print(f"  検出: {class_names[label]}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    axes[0].imshow(img)
    axes[0].set_title("元画像")
    axes[0].axis("off")

    overlay = np.array(img).astype(float) / 255.0
    rng = np.random.RandomState(0)
    for m in masks:
        color = rng.uniform(0.3, 1.0, size=3)
        binary = m > 0.5
        overlay[binary] = overlay[binary] * 0.4 + color * 0.6
    axes[1].imshow(overlay)
    axes[1].set_title(f"Mask R-CNNのインスタンスマスク({len(masks)}個, 色分け)")
    axes[1].axis("off")
    fig.tight_layout()
    out_path = "maskrcnn_result.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")
    print(
        "\nMask R-CNNはFaster R-CNN(矩形検出)に、ROI(検出した領域)ごとに"
        "画素単位のマスクを予測する小さなネットワーク(マスクブランチ)を追加した構造。"
        "『物体ごとに』マスクが分かれる(インスタンスセグメンテーション)ため、"
        "同じ種類の物体(例: 人が複数)が重なっていても個別に分離して認識できる。"
    )


class DoubleConv(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1), nn.BatchNorm2d(out_ch), nn.ReLU(),
            nn.Conv2d(out_ch, out_ch, 3, padding=1), nn.BatchNorm2d(out_ch), nn.ReLU(),
        )

    def forward(self, x):
        return self.block(x)


class SmallUNet(nn.Module):
    """エンコーダで縮小しながら特徴を抽出し、デコーダで元の解像度に戻す。
    スキップ結合(enc特徴をdecに直接連結)で、ダウンサンプリングで失われる
    位置情報の細かさを補う、これがU-Netの名前の由来(U字型の構造)。"""

    def __init__(self, in_ch=3, out_ch=1, base=16):
        super().__init__()
        self.enc1 = DoubleConv(in_ch, base)
        self.pool1 = nn.MaxPool2d(2)
        self.enc2 = DoubleConv(base, base * 2)
        self.pool2 = nn.MaxPool2d(2)
        self.bottleneck = DoubleConv(base * 2, base * 4)
        self.up2 = nn.ConvTranspose2d(base * 4, base * 2, 2, stride=2)
        self.dec2 = DoubleConv(base * 4, base * 2)  # スキップ結合でチャネル数が2倍になる
        self.up1 = nn.ConvTranspose2d(base * 2, base, 2, stride=2)
        self.dec1 = DoubleConv(base * 2, base)
        self.out_conv = nn.Conv2d(base, out_ch, 1)

    def forward(self, x):
        e1 = self.enc1(x)
        e2 = self.enc2(self.pool1(e1))
        b = self.bottleneck(self.pool2(e2))
        d2 = self.dec2(torch.cat([self.up2(b), e2], dim=1))  # スキップ結合
        d1 = self.dec1(torch.cat([self.up1(d2), e1], dim=1))  # スキップ結合
        return self.out_conv(d1)


def dice_score(pred_mask: torch.Tensor, true_mask: torch.Tensor, eps=1e-6) -> float:
    pred_bin = (pred_mask > 0.5).float()
    inter = (pred_bin * true_mask).sum()
    return ((2 * inter + eps) / (pred_bin.sum() + true_mask.sum() + eps)).item()


def demo_unet() -> None:
    print("\n=== 2. 自作U-Netによるセマンティックセグメンテーション ===")
    X_train, M_train, _ = generate_segmentation_dataset(400, seed_offset=0)
    X_test, M_test, _ = generate_segmentation_dataset(80, seed_offset=10000)
    X_train_t = torch.tensor(X_train)
    M_train_t = torch.tensor(M_train)
    X_test_t = torch.tensor(X_test)
    M_test_t = torch.tensor(M_test)
    print(f"訓練{len(X_train_t)}枚 / テスト{len(X_test_t)}枚 (64x64, 図形 vs 背景の2値マスク)")

    model = SmallUNet()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.BCEWithLogitsLoss()

    t0 = time.perf_counter()
    batch_size = 16
    n = len(X_train_t)
    for epoch in range(15):
        model.train()
        perm = torch.randperm(n)
        for i in range(0, n, batch_size):
            idx = perm[i : i + batch_size]
            optimizer.zero_grad()
            logits = model(X_train_t[idx])
            loss = criterion(logits, M_train_t[idx])
            loss.backward()
            optimizer.step()
    train_time = time.perf_counter() - t0

    model.eval()
    with torch.no_grad():
        test_logits = model(X_test_t)
        test_probs = torch.sigmoid(test_logits)
        dice = dice_score(test_probs, M_test_t)
        # 画素単位のAccuracy(背景/物体の2クラス)も参考値として算出
        pixel_acc = ((test_probs > 0.5).float() == M_test_t).float().mean().item()
    print(f"学習時間={train_time:.1f}秒  テストDiceスコア={dice:.3f}  画素Accuracy={pixel_acc:.3f}")

    fig, axes = plt.subplots(3, 6, figsize=(15, 7.5))
    with torch.no_grad():
        for col in range(6):
            img = X_test_t[col]
            true_mask = M_test_t[col, 0]
            pred_mask = torch.sigmoid(model(img.unsqueeze(0)))[0, 0]

            axes[0, col].imshow(img.permute(1, 2, 0).numpy())
            axes[0, col].axis("off")
            axes[1, col].imshow(true_mask.numpy(), cmap="gray")
            axes[1, col].axis("off")
            axes[2, col].imshow((pred_mask > 0.5).float().numpy(), cmap="gray")
            axes[2, col].axis("off")
    axes[0, 0].set_title("入力画像", loc="left", fontsize=10)
    for ax, name in zip([axes[0, 0], axes[1, 0], axes[2, 0]], ["入力画像", "正解マスク", "U-Net予測マスク"]):
        ax.set_ylabel(name, fontsize=10)
        ax.axis("on")
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle(f"U-Netのセグメンテーション結果(テストDice={dice:.3f})")
    fig.tight_layout()
    out_path = "unet_segmentation_result.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nDiceスコアは予測マスクと正解マスクの重なり具合を測る指標(IoUに似ているが"
        "重なり部分を2倍で数える分、やや高めの値が出やすい)で、1.0が完全一致。"
        f"{dice:.3f}という値は、U-Netが物体の形をかなり正確に画素単位で捉えられている"
        "ことを示す。畳み込みだけのシンプルな構造でも、エンコーダ・デコーダ+"
        "スキップ結合という設計だけでセグメンテーションが学習できることを確認した。"
    )


def main() -> None:
    demo_maskrcnn()
    demo_unet()


if __name__ == "__main__":
    main()
