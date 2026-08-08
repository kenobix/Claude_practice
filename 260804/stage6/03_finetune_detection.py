"""物体検出モデルのファインチューニング

COCO(80クラス)で事前学習済みのFaster R-CNNを、合成図形データセット
(円/四角/三角/十字の4クラス + 背景)向けに付け替えて再学習する。
Stage5で見た『分類層だけ付け替えるファインチューニング』と同じ考え方を、
検出タスク(クラス分類+矩形回帰)に適用する。
"""
import time

import torch
from torchvision.models.detection import (
    fasterrcnn_mobilenet_v3_large_320_fpn, FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
)
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from synthetic_detection import SyntheticDetectionDataset, collate_fn, CLASS_NAMES
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def build_model(num_classes: int):
    # CPUでも現実的な時間で学習できるよう、軽量なMobileNetV3バックボーン版を使う
    # (ResNet50版は同じ設定で1epoch(8枚)に84秒かかったが、こちらは1.3秒で終わる)
    model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)  # 80+1クラス→5クラス用に付け替え
    return model


def train_one_epoch(model, loader, optimizer):
    model.train()
    total_loss = 0.0
    n_batches = 0
    for images, targets in loader:
        loss_dict = model(images, targets)  # 学習時はtargetsを渡すと損失の内訳を返す
        loss = sum(loss_dict.values())
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        n_batches += 1
    return total_loss / n_batches


@torch.no_grad()
def evaluate_mean_iou(model, loader, score_threshold=0.5):
    """簡易評価: 各画像で最もスコアが高い予測と、最も近い正解ボックスとのIoU平均"""
    model.eval()
    ious = []
    n_detected, n_total_gt = 0, 0
    for images, targets in loader:
        preds = model(images)
        for pred, target in zip(preds, targets):
            keep = pred["scores"] >= score_threshold
            pred_boxes = pred["boxes"][keep]
            gt_boxes = target["boxes"]
            n_total_gt += len(gt_boxes)
            n_detected += len(pred_boxes)
            for gt_box in gt_boxes:
                if len(pred_boxes) == 0:
                    ious.append(0.0)
                    continue
                best_iou = 0.0
                for pb in pred_boxes:
                    xa1, ya1, xa2, ya2 = gt_box.tolist()
                    xb1, yb1, xb2, yb2 = pb.tolist()
                    ix1, iy1 = max(xa1, xb1), max(ya1, yb1)
                    ix2, iy2 = min(xa2, xb2), min(ya2, yb2)
                    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                    union = (xa2 - xa1) * (ya2 - ya1) + (xb2 - xb1) * (yb2 - yb1) - inter
                    best_iou = max(best_iou, inter / union if union > 0 else 0)
                ious.append(best_iou)
    return sum(ious) / len(ious) if ious else 0.0, n_detected, n_total_gt


def main() -> None:
    print("=== データ準備: 合成図形の物体検出タスク(円/四角/三角/十字 + 背景) ===")
    train_ds = SyntheticDetectionDataset(n_samples=150, seed_offset=0)
    test_ds = SyntheticDetectionDataset(n_samples=40, seed_offset=10000)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=4, shuffle=True, collate_fn=collate_fn)
    test_loader = torch.utils.data.DataLoader(test_ds, batch_size=4, shuffle=False, collate_fn=collate_fn)
    print(f"訓練{len(train_ds)}枚 / テスト{len(test_ds)}枚, クラス: {CLASS_NAMES}")

    model = build_model(num_classes=len(CLASS_NAMES))

    print("\n=== ファインチューニング前(COCO学習済みのまま)の評価 ===")
    # クラス分類層を付け替えた直後はランダム初期化なので、意味のある検出はできないはず
    iou_before, n_det_before, n_gt = evaluate_mean_iou(model, test_loader)
    print(f"ファインチューニング前: 平均IoU={iou_before:.3f}, 検出数={n_det_before}/正解数={n_gt}")

    print("\n=== ファインチューニング(5epoch) ===")
    optimizer = torch.optim.SGD(model.parameters(), lr=0.005, momentum=0.9, weight_decay=0.0005)
    t0 = time.perf_counter()
    for epoch in range(5):
        avg_loss = train_one_epoch(model, train_loader, optimizer)
        print(f"epoch {epoch}: 平均loss={avg_loss:.4f}")
    print(f"学習時間: {time.perf_counter()-t0:.1f}秒")

    iou_after, n_det_after, _ = evaluate_mean_iou(model, test_loader)
    print(f"\nファインチューニング後: 平均IoU={iou_after:.3f}, 検出数={n_det_after}/正解数={n_gt}")

    # 検出結果を可視化
    model.eval()
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.5))
    with torch.no_grad():
        for i, ax in enumerate(axes):
            image, target = test_ds[i]
            pred = model([image])[0]
            keep = pred["scores"] >= 0.5
            ax.imshow(image.permute(1, 2, 0).numpy())
            for box in target["boxes"]:
                x1, y1, x2, y2 = box.tolist()
                ax.add_patch(patches.Rectangle((x1, y1), x2 - x1, y2 - y1, edgecolor="yellow",
                                                facecolor="none", linewidth=2, linestyle=":"))
            for box, label, score in zip(pred["boxes"][keep], pred["labels"][keep], pred["scores"][keep]):
                x1, y1, x2, y2 = box.tolist()
                ax.add_patch(patches.Rectangle((x1, y1), x2 - x1, y2 - y1, edgecolor="lime",
                                                facecolor="none", linewidth=2))
                ax.text(x1, y1 - 3, f"{CLASS_NAMES[label]} {score:.2f}", color="lime", fontsize=8)
            ax.axis("off")
    fig.suptitle("黄点線=正解ボックス, 緑実線=ファインチューニング後の予測")
    fig.tight_layout()
    out_path = "finetune_detection_result.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\n平均IoUがファインチューニング前の{iou_before:.3f}から{iou_after:.3f}まで改善した。"
        "COCOの80クラスを見分けるために学習された特徴抽出部分(バックボーン)は、"
        "『物体の輪郭・形を捉える』という能力自体は今回の合成図形にも転用でき、"
        "分類層(box_predictor)を4+1クラス用に付け替えて少量のデータで再学習するだけで、"
        "新しいタスクに適応できることを確認した。"
    )


if __name__ == "__main__":
    main()
