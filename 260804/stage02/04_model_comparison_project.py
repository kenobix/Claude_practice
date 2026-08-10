"""
Stage 2 - ミニプロジェクト: 決定木 / ランダムフォレスト / SVM の決定境界比較

同じデータセット（make_moons: 三日月型に絡み合った2クラスの非線形データ）に対して
決定木・ランダムフォレスト・SVM(rbfカーネル)を学習させ、
決定境界の形・テスト精度・5-fold交差検証の平均精度を横並びで比較する。
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import make_moons
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score

OUT_DIR = __file__.rsplit("/", 1)[0]


def plot_boundary(ax, clf, X, y, title):
    x_min, x_max = X[:, 0].min() - 0.5, X[:, 0].max() + 0.5
    y_min, y_max = X[:, 1].min() - 0.5, X[:, 1].max() + 0.5
    xx, yy = np.meshgrid(np.linspace(x_min, x_max, 300), np.linspace(y_min, y_max, 300))
    Z = clf.predict(np.c_[xx.ravel(), yy.ravel()]).reshape(xx.shape)
    ax.contourf(xx, yy, Z, alpha=0.3, cmap="coolwarm")
    ax.scatter(X[:, 0], X[:, 1], c=y, cmap="coolwarm", edgecolors="k", s=25)
    ax.set_title(title)


def main():
    X, y = make_moons(n_samples=300, noise=0.3, random_state=42)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    print(f"データ: make_moons（三日月型2クラス、ノイズ0.3で絡み合いあり）, X={X.shape}")

    models = {
        "決定木 (max_depth=5)": DecisionTreeClassifier(max_depth=5, random_state=42),
        "RandomForest (n=100)": RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42),
        "SVM (rbf, C=1.0)": SVC(kernel="rbf", C=1.0, gamma="scale"),
    }

    print(f"\n{'モデル':<24}{'テストAcc':>10}{'5-fold CV平均':>16}{'CV標準偏差':>12}")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    for ax, (name, clf) in zip(axes, models.items()):
        clf.fit(X_train, y_train)
        test_acc = accuracy_score(y_test, clf.predict(X_test))
        cv_scores = cross_val_score(clf, X, y, cv=5)
        print(f"{name:<24}{test_acc:>10.3f}{cv_scores.mean():>16.3f}{cv_scores.std():>12.3f}")
        plot_boundary(ax, clf, X, y, f"{name}\nテスト精度={test_acc:.3f}")

    fig.suptitle("同じデータ(make_moons)に対する決定境界の違い")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/model_comparison.png", dpi=120)
    print(f"\n図を保存: {OUT_DIR}/model_comparison.png")

    print(
        "\n決定木は軸に平行な直線を組み合わせた「階段状」の境界になりやすく、"
        "RandomForestは複数の決定木の多数決でその階段がなめらかに平均化される。"
        "SVM(rbf)は元々曲線的な境界を作れるため、三日月の絡み合った形に最も自然にフィットする。"
        "このデータではノイズが大きい(0.3)ため、どのモデルも完璧(1.0)にはならない点にも注目。"
    )


if __name__ == "__main__":
    main()
