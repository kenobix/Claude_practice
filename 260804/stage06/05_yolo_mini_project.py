"""ミニプロジェクト: YOLOで手元の画像を物体検出する

YOLO(You Only Look Once)は一発で(1回のネットワーク推論で)画像全体から
物体の位置とクラスを予測する1段階検出器の代表格。SSDと同じ「1段階」の
系譜だが、より新しい設計と学習の工夫でSSDより高精度・高速な傾向がある。
02で試したFaster R-CNN(2段階)・SSD(1段階)に、YOLOを加えて3系統を
同じ画像・同じ土俵で比較し、Stage6の締めくくりとする。
"""
import importlib
import time

from ultralytics import YOLO
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image
import torchvision

import _mpl_ja  # noqa: F401

two_stage = importlib.import_module("02_two_stage_vs_one_stage")


def run_yolo(model, image_path, conf_threshold=0.5):
    t0 = time.perf_counter()
    results = model.predict(image_path, conf=conf_threshold, verbose=False)
    elapsed = time.perf_counter() - t0
    r = results[0]
    boxes = r.boxes.xyxy.numpy()
    labels = r.boxes.cls.numpy().astype(int)
    scores = r.boxes.conf.numpy()
    return {"boxes": boxes, "labels": labels, "scores": scores, "time": elapsed}


def main() -> None:
    print("=== YOLO11n(nano, 軽量版)を手元の画像に適用 ===")
    model = YOLO("yolo11n.pt")
    print(f"クラス数: {len(model.names)}種類(COCOと同じ80クラス体系)")

    image_paths = ["assets/bus.jpg", "assets/zidane.jpg"]

    # 比較用に02と同じFaster R-CNN・SSDも読み込む(すでにキャッシュ済みのためダウンロード不要)
    from torchvision.models.detection import (
        fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights,
        ssdlite320_mobilenet_v3_large, SSDLite320_MobileNet_V3_Large_Weights,
    )
    faster_rcnn = fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.COCO_V1)
    ssd = ssdlite320_mobilenet_v3_large(weights=SSDLite320_MobileNet_V3_Large_Weights.COCO_V1)
    coco_names = FasterRCNN_ResNet50_FPN_Weights.COCO_V1.meta["categories"]

    fig, axes = plt.subplots(2, 3, figsize=(17, 11))
    summary_rows = []

    for row, path in enumerate(image_paths):
        print(f"\n=== 画像: {path} ===")
        img_pil = Image.open(path).convert("RGB")
        img_t = torchvision.transforms.functional.to_tensor(img_pil)

        yolo_result = run_yolo(model, path, conf_threshold=0.5)
        fr_result = two_stage.run_detection(faster_rcnn, img_t, score_threshold=0.7)
        ssd_result = two_stage.run_detection(ssd, img_t, score_threshold=0.4)

        for name, result, names in [
            ("YOLO11n(1段階)", yolo_result, model.names),
            ("Faster R-CNN(2段階)", fr_result, coco_names),
            ("SSDLite(1段階)", ssd_result, coco_names),
        ]:
            print(f"{name}: 検出{len(result['boxes'])}件, 推論時間={result['time']:.3f}秒")
            summary_rows.append((path, name, len(result["boxes"]), result["time"]))

        axes[row, 0].imshow(img_pil)
        for box, label, score in zip(yolo_result["boxes"], yolo_result["labels"], yolo_result["scores"]):
            x1, y1, x2, y2 = box
            axes[row, 0].add_patch(patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2,
                                                       edgecolor="yellow", facecolor="none"))
            axes[row, 0].text(x1, y1 - 5, f"{model.names[label]} {score:.2f}", color="yellow", fontsize=9,
                               bbox=dict(facecolor="black", alpha=0.5, pad=1))
        axes[row, 0].set_title(f"YOLO11n\n検出{len(yolo_result['boxes'])}件, {yolo_result['time']:.3f}秒")
        axes[row, 0].axis("off")

        two_stage.draw_detections(axes[row, 1], img_t, fr_result, coco_names, "Faster R-CNN")
        two_stage.draw_detections(axes[row, 2], img_t, ssd_result, coco_names, "SSDLite")

    fig.tight_layout()
    out_path = "yolo_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print("\n=== まとめ: 3手法の推論時間比較(2画像の合計) ===")
    totals = {}
    for path, name, n_det, t in summary_rows:
        totals.setdefault(name, {"time": 0.0, "det": 0})
        totals[name]["time"] += t
        totals[name]["det"] += n_det
    for name, v in totals.items():
        print(f"{name:20s}: 合計推論時間={v['time']:.3f}秒, 合計検出数={v['det']}件")

    print(
        "\nYOLOはSSDと同じ1段階検出器の系譜でありながら、マルチスケール予測・"
        "アンカーフリー設計(バージョンによる)・強力なデータ拡張などの改良を重ねており、"
        "実務でリアルタイム物体検出が必要な場面(監視カメラ・自動運転の補助・"
        "ロボティクス等)で標準的な選択肢になっている。"
        "Stage6を通して、①IoU/NMSという共通の土台→②2段階/1段階という設計思想の違い"
        "→③実際のファインチューニングによるタスク適応→④検出の発展形であるセグメンテーション"
        "→⑤最新の1段階検出器という流れで、物体検出・セグメンテーションの全体像を一通り体験できた。"
    )


if __name__ == "__main__":
    main()
