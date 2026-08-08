"""
Stage 2 - Step B: 決定木（Decision Tree）

- load_wine（ワインの化学成分13種類から3品種を分類、178件）を使用
- max_depthを変えて train/test 精度がどう変化するか（過学習の可視化）
- 決定木の構造そのものを可視化（plot_tree）
- 2特徴量だけを使った決定境界の可視化（深さによる境界の複雑さの違い）
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import load_wine
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier, plot_tree
from sklearn.metrics import accuracy_score

OUT_DIR = __file__.rsplit("/", 1)[0]


def main():
    data = load_wine()
    X, y = data.data, data.target
    print(f"データ形状: X={X.shape}, y={y.shape}")
    print(f"クラス分布: {np.bincount(y)} (品種: {list(data.target_names)})")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )

    print("\n=== 1. max_depthによる過学習の観察 ===")
    depths = list(range(1, 11)) + [None]
    train_accs, test_accs = [], []
    for depth in depths:
        clf = DecisionTreeClassifier(max_depth=depth, random_state=42)
        clf.fit(X_train, y_train)
        train_accs.append(accuracy_score(y_train, clf.predict(X_train)))
        test_accs.append(accuracy_score(y_test, clf.predict(X_test)))
        label = depth if depth is not None else "None(無制限)"
        print(f"max_depth={label!s:>10}: train={train_accs[-1]:.3f}  test={test_accs[-1]:.3f}")

    depth_labels = [str(d) if d is not None else "None" for d in depths]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    axes[0].plot(depth_labels, train_accs, marker="o", label="訓練精度")
    axes[0].plot(depth_labels, test_accs, marker="o", label="テスト精度")
    axes[0].set_xlabel("max_depth（木の深さの上限）")
    axes[0].set_ylabel("Accuracy")
    axes[0].set_title("木の深さと過学習の関係")
    axes[0].legend()

    print("\n=== 2. 決定木の構造を可視化（max_depth=3） ===")
    shallow_clf = DecisionTreeClassifier(max_depth=3, random_state=42)
    shallow_clf.fit(X_train, y_train)
    plot_tree(
        shallow_clf,
        feature_names=data.feature_names,
        class_names=data.target_names,
        filled=True,
        fontsize=7,
        ax=axes[1],
    )
    axes[1].set_title("決定木の構造 (max_depth=3)")

    print("\n=== 3. 2特徴量での決定境界（浅い木 vs 深い木） ===")
    # 可視化のため flavanoids（フラボノイド量）と color_intensity（色の濃さ）の2特徴量に絞る
    f1_idx = list(data.feature_names).index("flavanoids")
    f2_idx = list(data.feature_names).index("color_intensity")
    X2 = X[:, [f1_idx, f2_idx]]
    X2_train, X2_test, y2_train, y2_test = train_test_split(
        X2, y, test_size=0.3, random_state=42, stratify=y
    )

    overfit_clf = DecisionTreeClassifier(max_depth=None, random_state=42)
    overfit_clf.fit(X2_train, y2_train)
    shallow2_clf = DecisionTreeClassifier(max_depth=3, random_state=42)
    shallow2_clf.fit(X2_train, y2_train)

    x_min, x_max = X2[:, 0].min() - 0.5, X2[:, 0].max() + 0.5
    y_min, y_max = X2[:, 1].min() - 1, X2[:, 1].max() + 1
    xx, yy = np.meshgrid(np.linspace(x_min, x_max, 300), np.linspace(y_min, y_max, 300))
    Z = overfit_clf.predict(np.c_[xx.ravel(), yy.ravel()]).reshape(xx.shape)

    axes[2].contourf(xx, yy, Z, alpha=0.3, cmap="viridis")
    axes[2].scatter(X2_test[:, 0], X2_test[:, 1], c=y2_test, cmap="viridis", edgecolors="k", s=30)
    axes[2].set_xlabel("flavanoids（フラボノイド量）")
    axes[2].set_ylabel("color_intensity（色の濃さ）")
    test_acc_overfit = accuracy_score(y2_test, overfit_clf.predict(X2_test))
    test_acc_shallow = accuracy_score(y2_test, shallow2_clf.predict(X2_test))
    axes[2].set_title(
        f"決定境界 (max_depth=None)\nテスト精度={test_acc_overfit:.3f}"
        f"（参考: max_depth=3 は {test_acc_shallow:.3f}）"
    )

    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/decision_tree.png", dpi=120)
    print(f"\n図を保存: {OUT_DIR}/decision_tree.png")
    print(
        "\n2特徴量だけの決定境界は、13特徴量を使った場合よりギザギザ（過学習気味）に"
        "なりやすい点に注意。可視化のためにあえて情報量を減らしている。"
    )


if __name__ == "__main__":
    main()
