"""物体検出用の合成データセット: 128x128キャンバスに1〜3個の図形を配置し、
バウンディングボックス付きで生成する(オフライン完結、Pillowのみ使用)。

torchvision.models.detectionのAPI規約に合わせ、クラスIDは0を背景として
予約し、円=1・四角=2・三角=3・十字=4とする。
"""
import numpy as np
import torch
from PIL import Image, ImageDraw

CLASS_NAMES = ["background", "circle", "square", "triangle", "cross"]
CANVAS_SIZE = 128


def _draw_shape(draw: ImageDraw.ImageDraw, cls: int, cx: int, cy: int, size: int, color: tuple) -> None:
    if cls == 1:  # circle
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=color)
    elif cls == 2:  # square
        draw.rectangle([cx - size, cy - size, cx + size, cy + size], fill=color)
    elif cls == 3:  # triangle
        draw.polygon([(cx, cy - size), (cx - size, cy + size), (cx + size, cy + size)], fill=color)
    elif cls == 4:  # cross
        w = max(2, size // 2)
        draw.rectangle([cx - size, cy - w, cx + size, cy + w], fill=color)
        draw.rectangle([cx - w, cy - size, cx + w, cy + size], fill=color)


def _bbox_for_shape(cls: int, cx: int, cy: int, size: int) -> tuple:
    # cross(プラス記号)は外接矩形が正方形からはみ出ないため、他の形と同じ扱いでよい
    return (cx - size, cy - size, cx + size, cy + size)


def generate_detection_sample(seed: int, canvas_size: int = CANVAS_SIZE, max_objects: int = 3):
    rng = np.random.RandomState(seed)
    n_objects = rng.randint(1, max_objects + 1)
    bg_color = tuple(rng.randint(200, 256, size=3).tolist())
    img = Image.new("RGB", (canvas_size, canvas_size), bg_color)
    draw = ImageDraw.Draw(img)

    boxes, labels = [], []
    attempts = 0
    while len(boxes) < n_objects and attempts < n_objects * 20:
        attempts += 1
        cls = rng.randint(1, 5)
        size = rng.randint(12, 22)
        cx = rng.randint(size + 2, canvas_size - size - 2)
        cy = rng.randint(size + 2, canvas_size - size - 2)
        new_box = _bbox_for_shape(cls, cx, cy, size)

        # 既存のボックスと大きく重ならないようにする(簡易チェック)
        overlap = False
        for b in boxes:
            ix1, iy1 = max(new_box[0], b[0]), max(new_box[1], b[1])
            ix2, iy2 = min(new_box[2], b[2]), min(new_box[3], b[3])
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter > 0:
                overlap = True
                break
        if overlap:
            continue

        color = tuple(rng.randint(0, 120, size=3).tolist())
        _draw_shape(draw, cls, cx, cy, size, color)
        boxes.append(new_box)
        labels.append(cls)

    arr = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) / 255.0
    arr += rng.normal(0, 0.03, size=arr.shape).astype(np.float32)
    arr = np.clip(arr, 0.0, 1.0)

    return {
        "image": torch.tensor(arr, dtype=torch.float32),
        "boxes": torch.tensor(boxes, dtype=torch.float32),
        "labels": torch.tensor(labels, dtype=torch.int64),
    }


class SyntheticDetectionDataset(torch.utils.data.Dataset):
    def __init__(self, n_samples: int, seed_offset: int = 0, **kwargs):
        self.n_samples = n_samples
        self.seed_offset = seed_offset
        self.kwargs = kwargs

    def __len__(self):
        return self.n_samples

    def __getitem__(self, idx):
        sample = generate_detection_sample(seed=idx + self.seed_offset, **self.kwargs)
        target = {"boxes": sample["boxes"], "labels": sample["labels"]}
        return sample["image"], target


def collate_fn(batch):
    images, targets = zip(*batch)
    return list(images), list(targets)
