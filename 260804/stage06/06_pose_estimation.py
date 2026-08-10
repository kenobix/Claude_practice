"""姿勢推定: 事前学習済みKeypoint R-CNNによる人物の関節位置推定

これまでのStage 6では「物体はどこにあるか」を矩形(検出)や画素単位のマスク
(セグメンテーション)で表現してきた。姿勢推定はこれらとは異なる第3の出力形式で、
「人物の関節(キーポイント)がどこにあるか」を、あらかじめ決められた17点
(鼻・目・耳・肩・肘・手首・腰・膝・足首)の座標として出力する。
Keypoint R-CNNはMask R-CNNと同じFaster R-CNNの構造(ROIごとに矩形とクラスを予測)に、
マスクブランチの代わりにキーポイントブランチを追加したモデルで、
複数人物が写っていてもROIごとに個別のキーポイント集合を予測できる
(OpenPoseのように全キーポイントを先に検出してから人物ごとに割り当てる
ボトムアップ方式とは逆の、トップダウン方式)。
"""
import time

import torch
import torchvision
from torchvision.models.detection import keypointrcnn_resnet50_fpn, KeypointRCNN_ResNet50_FPN_Weights
from PIL import Image
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)

# COCOの17キーポイントの定義順
KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
# 関節同士のつながり(骨格)。indexはKEYPOINT_NAMESに対応
SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),  # 肩-肩, 肩-肘-手首
    (5, 11), (6, 12), (11, 12),  # 肩-腰, 腰-腰
    (11, 13), (13, 15), (12, 14), (14, 16),  # 腰-膝-足首
    (0, 1), (0, 2), (1, 3), (2, 4),  # 鼻-目-耳
]


def run_pose_estimation(image_path, score_threshold=0.9):
    model = keypointrcnn_resnet50_fpn(weights=KeypointRCNN_ResNet50_FPN_Weights.COCO_V1)
    model.eval()

    img = Image.open(image_path).convert("RGB")
    img_t = torchvision.transforms.functional.to_tensor(img)

    t0 = time.perf_counter()
    with torch.no_grad():
        pred = model([img_t])[0]
    elapsed = time.perf_counter() - t0

    keep = pred["scores"] >= score_threshold
    keypoints = pred["keypoints"][keep].numpy()  # (N, 17, 3) = x, y, visibility
    boxes = pred["boxes"][keep].numpy()
    scores = pred["scores"][keep].numpy()
    return img, keypoints, boxes, scores, elapsed


def draw_pose(ax, img, keypoints, boxes, title):
    ax.imshow(img)
    for person_kp, box in zip(keypoints, boxes):
        x1, y1, x2, y2 = box
        ax.add_patch(plt.Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, edgecolor="lime", linewidth=1.5))
        xs, ys = person_kp[:, 0], person_kp[:, 1]
        ax.scatter(xs, ys, c="red", s=15, zorder=5)
        for i, j in SKELETON:
            ax.plot([xs[i], xs[j]], [ys[i], ys[j]], c="cyan", linewidth=1.5)
    ax.set_title(title)
    ax.axis("off")


def main() -> None:
    print("=== 事前学習済みKeypoint R-CNNで人物の姿勢を推定 ===")
    image_paths = ["assets/zidane.jpg", "assets/bus.jpg"]

    fig, axes = plt.subplots(1, len(image_paths), figsize=(14, 7))
    for ax, path in zip(axes, image_paths):
        img, keypoints, boxes, scores, elapsed = run_pose_estimation(path)
        print(f"\n[{path}] 推論時間={elapsed:.2f}秒, 検出人数={len(keypoints)}人 (score>=0.9)")
        for i, (kp, score) in enumerate(zip(keypoints, scores)):
            visible = int((kp[:, 2] > 0).sum())
            print(f"  人物{i+1}: score={score:.3f}, 可視キーポイント数={visible}/17")
        draw_pose(ax, img, keypoints, boxes, f"{path}\n({len(keypoints)}人検出)")

    fig.tight_layout()
    out_path = "pose_estimation.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nKeypoint R-CNNの出力は、これまで見てきた物体検出(矩形+クラス)・"
        "セグメンテーション(画素単位のマスク)とは異なり、人物ごとに17点の"
        "座標(x, y, 可視性)という構造化されたデータになる。同じFaster R-CNNの"
        "『まず矩形候補を出し、その中身を詳しく見る』という2段階の設計を土台にしつつ、"
        "ROIの中身として『クラス』でも『マスク』でもなく『関節座標』を予測するブランチを"
        "追加するだけで、全く異なるタスクに転用できることが分かる。"
        "OpenPose(G検定で頻出)はこれとは逆に、まず画像全体からキーポイント候補と"
        "関節同士のつながり(Part Affinity Fields)を先に求め、そのあとで人物ごとに"
        "割り当てる『ボトムアップ方式』を取る点が対照的で、人数が多い画像では"
        "人物ごとに検出をやり直さない分ボトムアップ方式の方が高速になりやすいとされる。"
    )


if __name__ == "__main__":
    main()
