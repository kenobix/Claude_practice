"""2段階検出器(Faster R-CNN)と1段階検出器(SSD)の比較

物体検出の手法は大きく2系統に分かれる:
  - 2段階(Two-stage): まず『物体らしき領域』を提案(Region Proposal)し、
    その領域それぞれに対してクラス分類と座標の微調整を行う。Faster R-CNNが代表格。
    精度は高いが、2段階の処理を順番に行うため比較的遅い。
  - 1段階(One-stage): 画像全体から一度に(領域提案なしで)クラスと座標を直接予測する。
    YOLO・SSDが代表格。処理が速く、リアルタイム用途に向く。
このスクリプトでは事前学習済み(COCO)のFaster R-CNNとSSDで同じ画像を推論し、
検出結果・推論時間・パラメータ数を比較する。
"""
import time

import torch
import torchvision
from torchvision.models.detection import (
    fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights,
    ssdlite320_mobilenet_v3_large, SSDLite320_MobileNet_V3_Large_Weights,
)
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def load_image(path: str) -> torch.Tensor:
    img = Image.open(path).convert("RGB")
    return torchvision.transforms.functional.to_tensor(img)


def run_detection(model, image_tensor, score_threshold=0.6):
    model.eval()
    t0 = time.perf_counter()
    with torch.no_grad():
        pred = model([image_tensor])[0]
    elapsed = time.perf_counter() - t0
    keep = pred["scores"] >= score_threshold
    return {
        "boxes": pred["boxes"][keep].numpy(),
        "labels": pred["labels"][keep].numpy(),
        "scores": pred["scores"][keep].numpy(),
        "time": elapsed,
        "n_raw": len(pred["scores"]),  # NMS後もモデルが出す候補の総数
    }


def draw_detections(ax, image_tensor, result, class_names, title):
    ax.imshow(image_tensor.permute(1, 2, 0).numpy())
    for box, label, score in zip(result["boxes"], result["labels"], result["scores"]):
        x1, y1, x2, y2 = box
        ax.add_patch(patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2,
                                        edgecolor="lime", facecolor="none"))
        ax.text(x1, y1 - 5, f"{class_names[label]} {score:.2f}", color="lime", fontsize=9,
                bbox=dict(facecolor="black", alpha=0.5, pad=1))
    ax.set_title(f"{title}\n検出数={len(result['boxes'])}, 推論時間={result['time']:.2f}秒")
    ax.axis("off")


def main() -> None:
    class_names = FasterRCNN_ResNet50_FPN_Weights.COCO_V1.meta["categories"]

    print("=== 事前学習済みモデルの読み込み(COCOデータセットで学習済み、80クラス) ===")
    faster_rcnn = fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.COCO_V1)
    ssd = ssdlite320_mobilenet_v3_large(weights=SSDLite320_MobileNet_V3_Large_Weights.COCO_V1)

    fr_params = sum(p.numel() for p in faster_rcnn.parameters())
    ssd_params = sum(p.numel() for p in ssd.parameters())
    print(f"Faster R-CNN(ResNet50-FPN): パラメータ数={fr_params:,}")
    print(f"SSDLite(MobileNetV3): パラメータ数={ssd_params:,} (Faster R-CNNの約{fr_params/ssd_params:.1f}分の1)")

    image_paths = ["assets/bus.jpg", "assets/zidane.jpg"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 11))

    for row, path in enumerate(image_paths):
        img = load_image(path)
        print(f"\n=== 画像: {path} ({img.shape[2]}x{img.shape[1]}) ===")

        fr_result = run_detection(faster_rcnn, img, score_threshold=0.7)
        ssd_result = run_detection(ssd, img, score_threshold=0.4)  # SSDはスコアが控えめに出る傾向があるため閾値を下げる

        print(f"Faster R-CNN: 検出{len(fr_result['boxes'])}件, 推論{fr_result['time']:.2f}秒, モデル出力候補総数={fr_result['n_raw']}")
        print(f"SSD:          検出{len(ssd_result['boxes'])}件, 推論{ssd_result['time']:.2f}秒, モデル出力候補総数={ssd_result['n_raw']}")

        draw_detections(axes[row, 0], img, fr_result, class_names, "Faster R-CNN(2段階)")
        draw_detections(axes[row, 1], img, ssd_result, class_names, "SSDLite(1段階)")

    fig.tight_layout()
    out_path = "two_stage_vs_one_stage.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nFaster R-CNNはSSDLiteよりパラメータ数が多く(2段階で領域提案とクラス分類を別々に"
        "行う分、計算コストも高い)、CPU上でも推論時間が長くなる傾向がある。"
        "一方SSD/YOLOのような1段階検出器は『領域候補の生成』を省き、画像全体から"
        "格子状に配置した多数のアンカーボックスに対して直接クラス・座標を予測するため、"
        "計算が1回で済み高速。ただし一般に小さい物体の検出精度は2段階検出器の方が"
        "有利とされる、というトレードオフがある。"
    )


if __name__ == "__main__":
    main()
