"""
Stage 1 - Step B (分類編): scikit-learnによるロジスティック回帰と評価指標一式

- load_breast_cancer（乳がん診断データ、分類用の組み込みデータセット）を使用
- ロジスティック回帰で二値分類
- 評価指標: Accuracy / Precision / Recall / F値 / 混同行列 / ROC曲線とAUC
- StratifiedKFoldによる交差検証 / GridSearchCVによるハイパーパラメータ探索
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import load_breast_cancer
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score, GridSearchCV
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    roc_curve,
    roc_auc_score,
    classification_report,
)

OUT_DIR = __file__.rsplit("/", 1)[0]


def main():
    data = load_breast_cancer()
    X, y = data.data, data.target  # 0=malignant(悪性), 1=benign(良性)
    print(f"データ形状: X={X.shape}, y={y.shape}")
    print(f"クラス分布: {np.bincount(y)} (0=悪性, 1=良性)")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    print("\n=== 1. ロジスティック回帰の学習 ===")
    clf = LogisticRegression(max_iter=5000)
    clf.fit(X_train_s, y_train)
    y_pred = clf.predict(X_test_s)
    y_proba = clf.predict_proba(X_test_s)[:, 1]  # クラス1(良性)の確率

    print("\n=== 2. 評価指標 ===")
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred)
    rec = recall_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred)
    auc = roc_auc_score(y_test, y_proba)
    print(f"Accuracy : {acc:.4f}")
    print(f"Precision: {prec:.4f}")
    print(f"Recall   : {rec:.4f}")
    print(f"F1値     : {f1:.4f}")
    print(f"ROC-AUC  : {auc:.4f}")

    cm = confusion_matrix(y_test, y_pred)
    print(f"\n混同行列:\n{cm}")
    print(f"\nclassification_report:\n{classification_report(y_test, y_pred, target_names=data.target_names)}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))

    # 混同行列のヒートマップ
    im = axes[0].imshow(cm, cmap="Blues")
    axes[0].set_title("混同行列")
    axes[0].set_xlabel("予測ラベル")
    axes[0].set_ylabel("正解ラベル")
    axes[0].set_xticks([0, 1], data.target_names)
    axes[0].set_yticks([0, 1], data.target_names)
    for i in range(2):
        for j in range(2):
            axes[0].text(j, i, str(cm[i, j]), ha="center", va="center",
                         color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=axes[0])

    # ROC曲線
    fpr, tpr, _ = roc_curve(y_test, y_proba)
    axes[1].plot(fpr, tpr, label=f"ロジスティック回帰 (AUC={auc:.3f})")
    axes[1].plot([0, 1], [0, 1], "--", color="gray", label="ランダム")
    axes[1].set_xlabel("偽陽性率 (FPR)")
    axes[1].set_ylabel("真陽性率 (TPR)")
    axes[1].set_title("ROC曲線")
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/logistic_evaluation.png", dpi=120)
    print(f"\n混同行列・ROC曲線の図を保存: {OUT_DIR}/logistic_evaluation.png")

    print("\n=== 3. 交差検証 (StratifiedKFold) ===")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(LogisticRegression(max_iter=5000), X, y, cv=skf, scoring="accuracy")
    print(f"5-fold CV Accuracy: {cv_scores}")
    print(f"平均Accuracy: {cv_scores.mean():.4f} (std={cv_scores.std():.4f})")

    print("\n=== 4. グリッドサーチ（正則化強度Cの探索）===")
    param_grid = {"C": [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]}
    grid = GridSearchCV(
        LogisticRegression(max_iter=5000), param_grid, cv=skf, scoring="f1"
    )
    grid.fit(X_train_s, y_train)
    print(f"最良C: {grid.best_params_}")
    print(f"最良CV F1: {grid.best_score_:.4f}")
    best_clf = grid.best_estimator_
    y_pred_best = best_clf.predict(X_test_s)
    print(f"テストAccuracy (最良モデル): {accuracy_score(y_test, y_pred_best):.4f}")


if __name__ == "__main__":
    main()
