"""深度別分離畳み込み(MobileNet) と 拡張畳み込み(Dilated Convolution)

前半: 通常の畳み込みと深度別分離畳み込み(Depthwise Separable Convolution)で、
      同じ入出力チャネル数でもパラメータ数・計算量がどれだけ変わるかを比較する
後半: カーネル内の間隔(dilation)を広げることで、パラメータ数を増やさずに
      受容野(1つの出力が『見ている』入力範囲)がどれだけ広がるかを可視化する
"""
import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def count_params(module: nn.Module) -> int:
    return sum(p.numel() for p in module.parameters())


def demo_depthwise_separable() -> None:
    print("=== 1. 通常の畳み込み vs 深度別分離畳み込み(Depthwise Separable Conv) ===")
    in_ch, out_ch, k = 64, 128, 3

    standard_conv = nn.Conv2d(in_ch, out_ch, kernel_size=k, padding=1)

    # 深度別分離畳み込み = ①チャネルごとに個別のフィルタをかける(depthwise)
    #                     + ②1x1畳み込みでチャネル間の情報を混ぜる(pointwise)
    depthwise = nn.Conv2d(in_ch, in_ch, kernel_size=k, padding=1, groups=in_ch)  # groups=in_chで「チャネルごと」に分離
    pointwise = nn.Conv2d(in_ch, out_ch, kernel_size=1)

    standard_params = count_params(standard_conv)
    dw_params = count_params(depthwise) + count_params(pointwise)

    print(f"入力チャネル={in_ch}, 出力チャネル={out_ch}, カーネル={k}x{k}")
    print(f"通常の畳み込み:         パラメータ数={standard_params:,}")
    print(f"深度別分離畳み込み:     パラメータ数={dw_params:,} (depthwise={count_params(depthwise):,} + pointwise={count_params(pointwise):,})")
    print(f"削減率: {(1 - dw_params/standard_params)*100:.1f}%削減 (パラメータ数が約{standard_params/dw_params:.1f}分の1)")

    x = torch.randn(1, in_ch, 16, 16)
    out_std = standard_conv(x)
    out_dw = pointwise(depthwise(x))
    print(f"出力形状: 通常={tuple(out_std.shape)}  深度別分離={tuple(out_dw.shape)} (形状は同じ)")

    print(
        "\n通常の畳み込みは『出力チャネル1つにつき、入力の全チャネル×カーネルサイズ分の"
        "重みを持つ』ため、チャネル数が増えるとパラメータ数が掛け算的に増える。"
        "深度別分離畳み込みは①各入力チャネルを独立に畳み込む(空間方向のパターン検出)と"
        "②1x1畳み込みでチャネルを混ぜる(チャネル間の情報統合)を分離することで、"
        "同じ入出力チャネル数でもパラメータ数を大きく減らせる。MobileNetはこの仕組みを"
        "多用することで、スマートフォン等の限られた計算資源でも動くCNNを実現している。"
    )


def demo_dilated_convolution() -> None:
    print("\n=== 2. 拡張畳み込み(Dilated Convolution)による受容野の拡大 ===")
    # 入力の中心に1だけを置いたインパルス画像を使い、「出力の中心1マスがどこまで入力を見ているか」を可視化
    size = 15
    center = size // 2

    dilations = [1, 2, 3]
    fig, axes = plt.subplots(1, len(dilations), figsize=(13, 4.5))

    for ax, dilation in zip(axes, dilations):
        conv = nn.Conv2d(1, 1, kernel_size=3, padding=dilation, dilation=dilation, bias=False)
        with torch.no_grad():
            conv.weight.fill_(1.0)  # 全ての重みを1にし、「どこを参照したか」だけを見る

        # 受容野を調べるため、入力全体を1にした画像を通し、出力の中心マスへの勾配で寄与範囲を特定
        x = torch.ones(1, 1, size, size, requires_grad=True)
        out = conv(x)
        out_center = out[0, 0, center, center]
        out_center.backward()
        receptive_field = (x.grad[0, 0].numpy() != 0).astype(int)
        n_referenced = receptive_field.sum()  # 実際に重みがかかる点の数(常に9個)

        # 受容野の「広がり」は点の数ではなく、参照点が占める空間的な範囲(バウンディングボックス)で測る
        ys, xs = np.nonzero(receptive_field)
        span_h = ys.max() - ys.min() + 1
        span_w = xs.max() - xs.min() + 1

        print(
            f"dilation={dilation}: 3x3カーネル(重みを持つ点は{n_referenced}個で共通)だが、"
            f"参照点が広がる範囲は{span_h}x{span_w}マス"
        )

        ax.imshow(receptive_field, cmap="Greys")
        ax.set_title(f"dilation={dilation}\n参照点は9個のまま、広がりは{span_h}x{span_w}")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.plot(center, center, "r+", markersize=15, markeredgewidth=2)

    fig.tight_layout()
    out_path = "dilated_convolution.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\n通常の3x3畳み込み(dilation=1)は隣接する9マスしか見ないが、dilation=2にすると"
        "カーネルの中身の間隔を1マスずつ空けて配置するため、パラメータ数(9個)を増やさずに"
        "参照範囲(受容野)を大きく広げられる。層を重ねるほど受容野の拡大効果は指数的に"
        "大きくなるため、画像全体の広い文脈を少ない層数・パラメータで捉えたいセグメンテーション"
        "タスク(例: DeepLab等)でよく使われる。"
    )


def main() -> None:
    demo_depthwise_separable()
    demo_dilated_convolution()


if __name__ == "__main__":
    main()
