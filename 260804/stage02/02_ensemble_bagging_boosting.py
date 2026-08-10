"""
Stage 2 - Step B: アンサンブル学習（バギング・ランダムフォレスト・ブースティング）

- load_wine を使用（01と同じデータで、単体の決定木からの改善幅を見る）
- 単体決定木 / Bagging / RandomForest / GradientBoosting(ブースティング) を比較
- RandomForestの特徴量重要度(feature_importances_)を可視化
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import load_wine
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import BaggingClassifier, RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import accuracy_score

OUT_DIR = __file__.rsplit("/", 1)[0]


def main():
    data = load_wine()
    X, y = data.data, data.target
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )

    models = {
        "決定木(単体)": DecisionTreeClassifier(max_depth=3, random_state=42),
        "Bagging": BaggingClassifier(
            DecisionTreeClassifier(max_depth=3, random_state=42),
            n_estimators=50, random_state=42,
        ),
        "RandomForest": RandomForestClassifier(n_estimators=50, max_depth=3, random_state=42),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42
        ),
    }

    print("=== 1. 各モデルの精度比較（同条件: max_depth=3, n_estimators=50） ===")
    print(f"{'モデル':<18}{'訓練Acc':>10}{'テストAcc':>10}{'5-fold CV平均':>16}")
    results = {}
    for name, clf in models.items():
        clf.fit(X_train, y_train)
        train_acc = accuracy_score(y_train, clf.predict(X_train))
        test_acc = accuracy_score(y_test, clf.predict(X_test))
        cv_scores = cross_val_score(clf, X, y, cv=5)
        results[name] = {"train": train_acc, "test": test_acc, "cv_mean": cv_scores.mean()}
        print(f"{name:<18}{train_acc:>10.3f}{test_acc:>10.3f}{cv_scores.mean():>16.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    names = list(results.keys())
    test_accs = [results[n]["test"] for n in names]
    cv_means = [results[n]["cv_mean"] for n in names]
    x_pos = np.arange(len(names))
    width = 0.35
    axes[0].bar(x_pos - width / 2, test_accs, width, label="テスト精度")
    axes[0].bar(x_pos + width / 2, cv_means, width, label="5-fold CV平均")
    axes[0].set_xticks(x_pos, names, rotation=15)
    axes[0].set_ylim(0.7, 1.02)
    axes[0].set_ylabel("Accuracy")
    axes[0].set_title("単体決定木 vs アンサンブル手法の精度比較")
    axes[0].legend()

    print("\n=== 2. RandomForestの特徴量重要度 ===")
    rf = models["RandomForest"]
    importances = rf.feature_importances_
    order = np.argsort(importances)[::-1]
    for idx in order[:5]:
        print(f"{data.feature_names[idx]:<25}: {importances[idx]:.3f}")

    axes[1].barh(
        [data.feature_names[i] for i in order[::-1]],
        importances[order[::-1]],
        color="teal",
    )
    axes[1].set_xlabel("重要度")
    axes[1].set_title("RandomForestの特徴量重要度")

    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/ensemble_comparison.png", dpi=120)
    print(f"\n図を保存: {OUT_DIR}/ensemble_comparison.png")

    print(
        "\nBagging/RandomForestは「複数の弱い決定木の多数決」で分散を下げ(過学習を抑え)、"
        "GradientBoostingは「前の木の間違いを次の木が補正する」逐次学習でバイアスを下げる。"
        "アプローチが異なる点に注意。"
    )


if __name__ == "__main__":
    main()
