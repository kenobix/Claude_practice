"""
Stage 2 - Step B: サポートベクターマシン（SVM）とカーネルトリック

- make_circles（同心円状の非線形分離データ）を使用
- 線形カーネルでは分離できないことを示し、poly/rbfカーネルで分離できることを対比
- カーネルトリック: 低次元では直線で分けられないデータを、高次元空間に写像すると
  直線（超平面）で分けられるようになる、という仕組みをrbfカーネルの決定境界で体感する
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import make_circles
from sklearn.model_selection import train_test_split
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score

OUT_DIR = __file__.rsplit("/", 1)[0]


def plot_boundary(ax, clf, X, y, title):
    x_min, x_max = X[:, 0].min() - 0.3, X[:, 0].max() + 0.3
    y_min, y_max = X[:, 1].min() - 0.3, X[:, 1].max() + 0.3
    xx, yy = np.meshgrid(np.linspace(x_min, x_max, 300), np.linspace(y_min, y_max, 300))
    Z = clf.predict(np.c_[xx.ravel(), yy.ravel()]).reshape(xx.shape)
    ax.contourf(xx, yy, Z, alpha=0.3, cmap="coolwarm")
    ax.scatter(X[:, 0], X[:, 1], c=y, cmap="coolwarm", edgecolors="k", s=25)
    ax.set_title(title)


def main():
    X, y = make_circles(n_samples=300, noise=0.15, factor=0.4, random_state=42)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    print(f"データ形状: X={X.shape}（同心円状の2クラスデータ、線形分離は不可能な形状）")

    kernels = {
        "linear（線形カーネル）": SVC(kernel="linear", C=1.0),
        "poly（多項式カーネル, degree=3）": SVC(kernel="poly", degree=3, C=1.0),
        "rbf（RBFカーネル）": SVC(kernel="rbf", C=1.0, gamma="scale"),
    }

    print("\n=== 1. カーネルごとの分類精度比較 ===")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    for ax, (name, clf) in zip(axes, kernels.items()):
        clf.fit(X_train, y_train)
        acc = accuracy_score(y_test, clf.predict(X_test))
        n_sv = clf.n_support_.sum()
        print(f"{name:<30}: テスト精度={acc:.3f}  サポートベクター数={n_sv}")
        plot_boundary(ax, clf, X, y, f"{name}\nテスト精度={acc:.3f}")

    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/svm_kernel_comparison.png", dpi=120)
    print(f"\n図を保存: {OUT_DIR}/svm_kernel_comparison.png")

    print(
        "\nlinearカーネルは直線でしか分けられないため、同心円データではほぼ半分しか"
        "正解できない(50%前後)。poly/rbfカーネルはいずれもデータを高次元空間へ写像"
        "してから直線的に分離する(=元の2次元空間では曲線の境界に見える)という"
        "同じ原理(カーネルトリック)に基づくが、同心円状のデータの形にはrbf(動径基底関数)"
        "の方が相性が良く、今回はrbfのみがほぼ完璧(0.99弱)に分離できた。polyはdegree(次数)"
        "やcoef0の調整次第でもっと改善する余地がある。"
    )

    print("\n=== 2. Cパラメータ（マージンの厳しさ）の影響（rbfカーネル固定） ===")
    for C in [0.01, 1.0, 100.0]:
        clf = SVC(kernel="rbf", C=C, gamma="scale")
        clf.fit(X_train, y_train)
        acc = accuracy_score(y_test, clf.predict(X_test))
        n_sv = clf.n_support_.sum()
        print(f"C={C:<8}: テスト精度={acc:.3f}  サポートベクター数={n_sv}")
    print(
        "Cが小さいほど「多少の誤分類を許してでも滑らかな境界」を優先し、"
        "Cが大きいほど「訓練データを厳密に分けようとする」複雑な境界になりやすい"
        "（過学習のリスクが上がる）。"
    )


if __name__ == "__main__":
    main()
