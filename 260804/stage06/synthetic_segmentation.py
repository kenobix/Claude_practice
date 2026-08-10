"""セグメンテーション用の合成データセット: 図形1つ+その二値マスクを生成する
(オフライン完結、Pillowのみ使用)。U-Netの学習に使う。
"""
import numpy as np
from PIL import Image, ImageDraw

CLASS_NAMES = ["circle", "square", "triangle", "cross"]
IMAGE_SIZE = 64


def _draw_shape(draw: ImageDraw.ImageDraw, cls: int, cx: int, cy: int, size: int, fill) -> None:
    if cls == 0:
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=fill)
    elif cls == 1:
        draw.rectangle([cx - size, cy - size, cx + size, cy + size], fill=fill)
    elif cls == 2:
        draw.polygon([(cx, cy - size), (cx - size, cy + size), (cx + size, cy + size)], fill=fill)
    elif cls == 3:
        w = max(2, size // 2)
        draw.rectangle([cx - size, cy - w, cx + size, cy + w], fill=fill)
        draw.rectangle([cx - w, cy - size, cx + w, cy + size], fill=fill)


def generate_segmentation_sample(seed: int, image_size: int = IMAGE_SIZE):
    rng = np.random.RandomState(seed)
    cls = rng.randint(0, len(CLASS_NAMES))
    bg_color = tuple(rng.randint(180, 256, size=3).tolist())
    fg_color = tuple(rng.randint(0, 120, size=3).tolist())

    img = Image.new("RGB", (image_size, image_size), bg_color)
    mask = Image.new("L", (image_size, image_size), 0)  # 0=背景, 255=物体
    draw_img = ImageDraw.Draw(img)
    draw_mask = ImageDraw.Draw(mask)

    size = rng.randint(image_size // 5, image_size // 3)
    cx = rng.randint(size + 2, image_size - size - 2)
    cy = rng.randint(size + 2, image_size - size - 2)

    _draw_shape(draw_img, cls, cx, cy, size, fg_color)
    _draw_shape(draw_mask, cls, cx, cy, size, 255)

    img_arr = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) / 255.0
    img_arr += rng.normal(0, 0.03, size=img_arr.shape).astype(np.float32)
    img_arr = np.clip(img_arr, 0.0, 1.0)
    mask_arr = (np.asarray(mask, dtype=np.float32) / 255.0)[None, :, :]  # (1,H,W)

    return img_arr, mask_arr, cls


def generate_segmentation_dataset(n_samples: int, seed_offset: int = 0, image_size: int = IMAGE_SIZE):
    images = np.zeros((n_samples, 3, image_size, image_size), dtype=np.float32)
    masks = np.zeros((n_samples, 1, image_size, image_size), dtype=np.float32)
    labels = np.zeros(n_samples, dtype=np.int64)
    for i in range(n_samples):
        img, mask, cls = generate_segmentation_sample(seed=i + seed_offset, image_size=image_size)
        images[i], masks[i], labels[i] = img, mask, cls
    return images, masks, labels
