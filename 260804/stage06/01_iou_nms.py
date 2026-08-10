"""物体検出の共通の土台: IoU(Intersection over Union) と NMS(Non-Max Suppression)

分類は「画像1枚に1つのラベル」を当てるだけだったが、物体検出は
「どこに(バウンディングボックス)」「何が(クラス)」あるかを同時に当てる必要がある。
YOLO/SSD/Faster R-CNNなど手法が違っても、必ず次の2つの部品を土台として使う:
  1. IoU: 2つの矩形がどれだけ重なっているかを測る指標(予測精度の評価・学習時の割当に使う)
  2. NMS: 同じ物体に対して大量に出てくる重複した予測ボックスを1つに絞り込む後処理
"""
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches

import _mpl_ja  # noqa: F401


def iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """box = [x1, y1, x2, y2](左上・右下座標)"""
    xa1, ya1, xa2, ya2 = box_a
    xb1, yb1, xb2, yb2 = box_b

    inter_x1, inter_y1 = max(xa1, xb1), max(ya1, yb1)
    inter_x2, inter_y2 = min(xa2, xb2), min(ya2, yb2)
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)

    area_a = (xa2 - xa1) * (ya2 - ya1)
    area_b = (xb2 - xb1) * (yb2 - yb1)
    union_area = area_a + area_b - inter_area

    return inter_area / union_area if union_area > 0 else 0.0


def demo_iou() -> None:
    print("=== 1. IoU(Intersection over Union) ===")
    box_gt = np.array([50, 50, 150, 150])  # 正解ボックス(ground truth)
    candidates = {
        "完全一致": np.array([50, 50, 150, 150]),
        "少しずれた予測": np.array([70, 70, 170, 170]),
        "大きくずれた予測": np.array([120, 120, 220, 220]),
        "全く重ならない予測": np.array([200, 200, 300, 300]),
    }

    fig, axes = plt.subplots(1, len(candidates), figsize=(16, 4.5))
    for ax, (name, box_pred) in zip(axes, candidates.items()):
        score = iou(box_gt, box_pred)
        print(f"{name}: IoU={score:.3f}")

        ax.add_patch(patches.Rectangle((box_gt[0], box_gt[1]), box_gt[2] - box_gt[0], box_gt[3] - box_gt[1],
                                        linewidth=2, edgecolor="tab:blue", facecolor="none", label="正解"))
        ax.add_patch(patches.Rectangle((box_pred[0], box_pred[1]), box_pred[2] - box_pred[0], box_pred[3] - box_pred[1],
                                        linewidth=2, edgecolor="tab:red", facecolor="none", linestyle="--", label="予測"))
        ax.set_xlim(0, 320)
        ax.set_ylim(320, 0)
        ax.set_title(f"{name}\nIoU={score:.3f}")
        ax.set_aspect("equal")
        if ax is axes[0]:
            ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    out_path = "iou_examples.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")
    print(
        "\nIoU = 重なっている面積 ÷ 2つの矩形を合わせた面積(和集合)。0(全く重ならない)〜1(完全一致)の値を取る。"
        "物体検出では『IoU >= 0.5の予測を正解とみなす』のように、評価や学習時のボックス割当の"
        "しきい値として使われる(検出タスクの共通言語)。"
    )


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.5) -> list[int]:
    """スコアの高い順にボックスを採用し、採用済みボックスとIoUが高い(重複している)
    ボックスを除外していく、という貪欲法。残ったボックスのインデックスを返す"""
    order = scores.argsort()[::-1]  # スコアが高い順
    keep = []
    while len(order) > 0:
        current = order[0]
        keep.append(current)
        rest = order[1:]
        ious = np.array([iou(boxes[current], boxes[i]) for i in rest])
        order = rest[ious < iou_threshold]  # 重複度が高いものは間引く
    return keep


def demo_nms() -> None:
    print("\n=== 2. NMS(Non-Max Suppression): 重複した予測ボックスを1つに絞る ===")
    # 同じ物体1つに対し、検出モデルが出しがちな『重複した予測ボックス群』を模擬
    boxes = np.array([
        [50, 50, 150, 150],
        [55, 52, 155, 148],
        [48, 55, 145, 152],
        [200, 60, 280, 140],  # 別の物体
        [205, 65, 275, 135],
    ])
    scores = np.array([0.95, 0.88, 0.75, 0.90, 0.70])

    keep_idx = nms(boxes, scores, iou_threshold=0.5)
    print(f"NMS適用前: {len(boxes)}個の予測ボックス")
    print(f"NMS適用後: {len(keep_idx)}個に絞り込み(残したインデックス: {keep_idx})")

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))
    for ax, (title, idx_list) in zip(axes, [("NMS適用前(全予測)", range(len(boxes))), ("NMS適用後", keep_idx)]):
        for i in idx_list:
            x1, y1, x2, y2 = boxes[i]
            ax.add_patch(patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2,
                                            edgecolor="tab:red", facecolor="none"))
            ax.text(x1, y1 - 5, f"{scores[i]:.2f}", color="tab:red", fontsize=9)
        ax.set_xlim(0, 320)
        ax.set_ylim(200, 0)
        ax.set_title(title)
        ax.set_aspect("equal")
    fig.tight_layout()
    out_path = "nms_example.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")
    print(
        "\nNMSは①最もスコアが高いボックスを採用②採用したボックスとIoUがしきい値以上"
        "(重複しすぎている)の他の候補を捨てる③残った候補で①に戻る、を繰り返す。"
        "これにより『同じ物体に対する重複予測』を1個に絞り込みつつ、"
        "『別の物体に対する予測』はIoUが低いため両方とも生き残る。"
        "YOLO/SSD/Faster R-CNNいずれも、モデルの生出力(大量の候補ボックス)から"
        "最終的な検出結果を得る後処理としてNMS(またはその改良版)を使っている。"
    )


def main() -> None:
    demo_iou()
    demo_nms()


if __name__ == "__main__":
    main()
