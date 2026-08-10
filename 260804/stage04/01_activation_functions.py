"""活性化関数とその導関数（誤差逆伝播法で使う勾配の元になる）"""
import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def relu(x):
    return np.maximum(0, x)


def relu_grad(x):
    return (x > 0).astype(float)


def leaky_relu(x, alpha=0.1):
    return np.where(x > 0, x, alpha * x)


def leaky_relu_grad(x, alpha=0.1):
    return np.where(x > 0, 1.0, alpha)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def sigmoid_grad(x):
    s = sigmoid(x)
    return s * (1 - s)


def tanh(x):
    return np.tanh(x)


def tanh_grad(x):
    return 1 - np.tanh(x) ** 2


def identity(x):
    return x


def identity_grad(x):
    return np.ones_like(x)


def softmax(x):
    x_shifted = x - np.max(x)  # オーバーフロー防止（結果は変わらない）
    exp_x = np.exp(x_shifted)
    return exp_x / exp_x.sum()


def main() -> None:
    x = np.linspace(-5, 5, 400)

    functions = [
        ("ReLU", relu, relu_grad),
        ("Leaky ReLU (alpha=0.1)", leaky_relu, leaky_relu_grad),
        ("シグモイド", sigmoid, sigmoid_grad),
        ("tanh", tanh, tanh_grad),
        ("恒等関数", identity, identity_grad),
    ]

    fig, axes = plt.subplots(2, 5, figsize=(20, 7))
    for col, (name, f, g) in enumerate(functions):
        axes[0, col].plot(x, f(x), color="tab:blue")
        axes[0, col].set_title(name)
        axes[0, col].axhline(0, color="gray", linewidth=0.5)
        axes[0, col].axvline(0, color="gray", linewidth=0.5)
        axes[0, col].set_ylim(-1.5, 5)

        axes[1, col].plot(x, g(x), color="tab:orange")
        axes[1, col].set_title(f"{name}の導関数")
        axes[1, col].axhline(0, color="gray", linewidth=0.5)
        axes[1, col].axvline(0, color="gray", linewidth=0.5)
        axes[1, col].set_ylim(-0.2, 1.2)
        axes[1, col].set_xlabel("x")
    axes[0, 0].set_ylabel("f(x)")
    axes[1, 0].set_ylabel("f'(x)")

    fig.tight_layout()
    out_path = "activation_functions.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print("\n=== 各活性化関数の特徴と勾配消失問題 ===")
    print(f"シグモイドの勾配の最大値: {sigmoid_grad(np.array([0.0]))[0]:.4f}（x=0の時が最大）")
    print(f"x=5の時のシグモイドの勾配: {sigmoid_grad(np.array([5.0]))[0]:.6f}（ほぼ0）")
    print(f"x=5の時のReLUの勾配: {relu_grad(np.array([5.0]))[0]:.4f}（xが大きくても勾配は1のまま）")
    print(
        "\nシグモイド/tanhは入力の絶対値が大きくなると勾配がほぼ0になる『勾配消失』が起きやすい。"
        "層を重ねるたびに勾配同士が掛け算されるため、深いネットワークでは勾配がどんどん"
        "小さくなり学習が進まなくなる。ReLUは正の領域で勾配が常に1のため、この問題が"
        "起きにくく、現在のディープラーニングで主流の活性化関数になっている。"
        "ただしReLUは負の領域で勾配が完全に0になる『死んだReLU』問題があり、"
        "これを緩和するためにLeaky ReLU（負の領域にも小さな傾きを残す）が使われることがある。"
    )

    print("\n=== ソフトマックス関数（出力層で確率分布に変換する。他の活性化関数と異なりベクトル全体を1つの分布に変換） ===")
    logits = np.array([2.0, 1.0, 0.1])
    probs = softmax(logits)
    print(f"入力(ロジット): {logits}")
    print(f"出力(確率):     {probs.round(4)}  合計={probs.sum():.4f}")
    print(
        "ソフトマックスはReLU等と違い『各要素を独立に変換する』のではなく、"
        "ベクトル全体を使って正規化する（全出力の合計が必ず1になる）。"
        "多クラス分類の出力層で『各クラスである確率』を表現するために使われる。"
    )


if __name__ == "__main__":
    main()
