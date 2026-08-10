"""CNNの基礎: 畳み込み演算・プーリング(最大値/平均値/グローバルアベレージ)

全結合層と違い、CNNは「小さなフィルタ(カーネル)を画像全体にスライドさせながら
掛け算・足し算する」ことで、位置によらず同じパターン(エッジ・模様など)を検出する。
このスクリプトでは畳み込みとプーリングをnumpyで手書きし、何をしているかを可視化する。
"""
import numpy as np
from sklearn.datasets import load_digits
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def conv2d(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """パディングなし・ストライド1の2次元畳み込み(相互相関)"""
    kh, kw = kernel.shape
    h, w = image.shape
    out_h, out_w = h - kh + 1, w - kw + 1
    out = np.zeros((out_h, out_w))
    for i in range(out_h):
        for j in range(out_w):
            patch = image[i : i + kh, j : j + kw]
            out[i, j] = np.sum(patch * kernel)
    return out


def max_pool2d(image: np.ndarray, size: int = 2, stride: int = 2) -> np.ndarray:
    h, w = image.shape
    out_h, out_w = (h - size) // stride + 1, (w - size) // stride + 1
    out = np.zeros((out_h, out_w))
    for i in range(out_h):
        for j in range(out_w):
            patch = image[i * stride : i * stride + size, j * stride : j * stride + size]
            out[i, j] = patch.max()
    return out


def avg_pool2d(image: np.ndarray, size: int = 2, stride: int = 2) -> np.ndarray:
    h, w = image.shape
    out_h, out_w = (h - size) // stride + 1, (w - size) // stride + 1
    out = np.zeros((out_h, out_w))
    for i in range(out_h):
        for j in range(out_w):
            patch = image[i * stride : i * stride + size, j * stride : j * stride + size]
            out[i, j] = patch.mean()
    return out


def global_avg_pool(image: np.ndarray) -> float:
    return image.mean()


def main() -> None:
    digits = load_digits()
    image = digits.images[3]  # 8x8の手書き数字1枚をサンプルに使う
    print(f"サンプル画像の形状: {image.shape}（正解ラベル: {digits.target[3]}）")

    # --- 1. 畳み込みで何が起きるか: エッジ検出フィルタを適用してみる ---
    print("\n=== 1. 畳み込み演算(エッジ検出カーネルの例) ===")
    vertical_edge_kernel = np.array([[-1, 0, 1], [-1, 0, 1], [-1, 0, 1]])  # 縦方向の輝度変化を検出
    horizontal_edge_kernel = vertical_edge_kernel.T  # 横方向の輝度変化を検出

    v_edges = conv2d(image, vertical_edge_kernel)
    h_edges = conv2d(image, horizontal_edge_kernel)
    print(f"入力画像: {image.shape} → 畳み込み後(3x3カーネル, パディングなし): {v_edges.shape}")
    print(
        "カーネルの重みが『左が暗く右が明るい境界』に強く反応するように設計されているため、"
        "縦方向のエッジ(輪郭の左右の境目)が強調される。CNNではこのカーネルの中身自体を"
        "手で設計するのではなく、学習によって『どんなパターンを検出すれば分類に役立つか』を"
        "自動的に獲得する点が重要（今回は仕組みを見せるために意図的に手で設計した固定カーネルを使用）。"
    )

    # --- 2. プーリング: 最大値・平均値・グローバルアベレージ ---
    print("\n=== 2. プーリング(ダウンサンプリング) ===")
    max_pooled = max_pool2d(image, size=2, stride=2)
    avg_pooled = avg_pool2d(image, size=2, stride=2)
    gap = global_avg_pool(image)
    print(f"最大値プーリング(2x2, stride2): {image.shape} → {max_pooled.shape}")
    print(f"平均値プーリング(2x2, stride2): {image.shape} → {avg_pooled.shape}")
    print(f"グローバルアベレージプーリング(GAP): {image.shape} → スカラー1個 (値={gap:.3f})")
    print(
        "最大値プーリングは『その領域で一番強く反応した特徴』だけを残すため、多少の位置ずれに"
        "強く(頑健に)なる。平均値プーリングは領域全体をなだらかに要約するため情報の急激な"
        "欠落は少ないが、強い特徴が薄まりやすい。GAPはチャネルごとに画像全体を1つの値に"
        "要約する特殊なプーリングで、CNN終盤の全結合層をGAPに置き換えると『画像サイズに"
        "依存しない』『パラメータ数を大幅に減らせる』というメリットがあり、"
        "GoogLeNet以降の多くのアーキテクチャで採用されている。"
    )

    fig, axes = plt.subplots(1, 5, figsize=(18, 4))
    for ax, img, title in zip(
        axes,
        [image, v_edges, h_edges, max_pooled, avg_pooled],
        ["元画像(8x8)", "縦エッジ検出後", "横エッジ検出後", "最大値プーリング後", "平均値プーリング後"],
    ):
        im = ax.imshow(img, cmap="gray_r")
        ax.set_title(f"{title}\n{img.shape}")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    out_path = "conv_pooling_basics.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")


if __name__ == "__main__":
    main()
