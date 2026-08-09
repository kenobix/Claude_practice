"""オフラインで完結する画像分類データセット(合成図形)

インターネット接続が不安定な環境のため、CIFAR-10の代わりにPillowで
その場生成する32x32のRGB図形画像(円・四角・三角・十字の4クラス)を使う。
位置・サイズ・回転角・色・背景ノイズをランダム化することで、単純な
ルールベースでは解けない程度の難易度を持たせている。
"""
import numpy as np
from PIL import Image, ImageDraw

CLASS_NAMES = ["circle", "square", "triangle", "cross"]
IMAGE_SIZE = 32


def _draw_shape(draw: ImageDraw.ImageDraw, cls: int, cx: int, cy: int, size: int, color: tuple) -> None:
    if cls == 0:  # circle
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=color)
    elif cls == 1:  # square
        draw.rectangle([cx - size, cy - size, cx + size, cy + size], fill=color)
    elif cls == 2:  # triangle
        draw.polygon(
            [(cx, cy - size), (cx - size, cy + size), (cx + size, cy + size)], fill=color
        )
    elif cls == 3:  # cross(プラス記号)
        w = max(2, size // 2)
        draw.rectangle([cx - size, cy - w, cx + size, cy + w], fill=color)
        draw.rectangle([cx - w, cy - size, cx + w, cy + size], fill=color)


def generate_shapes_dataset(n_samples: int, seed: int, image_size: int = IMAGE_SIZE, noise_std: float = 0.05):
    """戻り値: X(float32, N,3,H,W, 値域[0,1]), y(int64, N,)"""
    rng = np.random.RandomState(seed)
    X = np.zeros((n_samples, 3, image_size, image_size), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.int64)

    for i in range(n_samples):
        cls = rng.randint(0, len(CLASS_NAMES))
        bg_color = tuple(rng.randint(180, 256, size=3).tolist())  # 明るい背景
        fg_color = tuple(rng.randint(0, 120, size=3).tolist())  # 暗めの図形色(背景とのコントラスト確保)

        canvas = Image.new("RGB", (image_size * 2, image_size * 2), bg_color)
        draw = ImageDraw.Draw(canvas)
        cx, cy = image_size, image_size
        size = rng.randint(image_size // 4, image_size // 2)
        _draw_shape(draw, cls, cx, cy, size, fg_color)

        angle = rng.uniform(0, 360)
        canvas = canvas.rotate(angle, fillcolor=bg_color, resample=Image.BILINEAR)

        # ランダムな位置ずれを与えつつ中央image_size x image_sizeを切り出す
        max_shift = image_size // 4
        shift_x = rng.randint(-max_shift, max_shift + 1)
        shift_y = rng.randint(-max_shift, max_shift + 1)
        left = image_size // 2 + shift_x
        top = image_size // 2 + shift_y
        crop = canvas.crop((left, top, left + image_size, top + image_size))

        arr = np.asarray(crop, dtype=np.float32).transpose(2, 0, 1) / 255.0  # (3,H,W)
        arr += rng.normal(0, noise_std, size=arr.shape).astype(np.float32)  # 撮影ノイズ相当
        arr = np.clip(arr, 0.0, 1.0)

        X[i] = arr
        y[i] = cls

    return X, y
