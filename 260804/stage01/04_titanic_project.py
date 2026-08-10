"""
Stage 1 - ミニプロジェクト: タイタニック生存予測

OpenML経由でタイタニックデータセットを取得し、前処理 → ロジスティック回帰 →
評価指標一式（Accuracy/Precision/Recall/F値/混同行列/ROC-AUC）+ 交差検証まで一通り行う。
"""
import numpy as np
import pandas as pd
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import fetch_openml
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
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


def load_titanic():
    """OpenMLからタイタニックデータセットを取得する（要インターネット接続）"""
    data = fetch_openml("titanic", version=1, as_frame=True, parser="auto")
    df = data.frame.copy()
    # 使用する特徴量: 乗客クラス/性別/年齢/兄弟配偶者数/親子数/運賃/乗船港
    features = ["pclass", "sex", "age", "sibsp", "parch", "fare", "embarked"]
    df = df[features + ["survived"]].copy()
    df["survived"] = df["survived"].astype(int)
    return df


def main():
    df = load_titanic()
    print(f"データ形状: {df.shape}")
    print(f"欠損値:\n{df.isna().sum()}")
    print(f"\n生存率: {df['survived'].mean():.3f}")

    X = df.drop(columns=["survived"])
    y = df["survived"]

    numeric_features = ["age", "sibsp", "parch", "fare"]
    categorical_features = ["pclass", "sex", "embarked"]

    # 数値: 欠損を中央値で補完→標準化 / カテゴリ: 欠損を最頻値で補完→one-hot化
    preprocess = ColumnTransformer([
        ("num", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), numeric_features),
        ("cat", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), categorical_features),
    ])

    pipeline = Pipeline([
        ("preprocess", preprocess),
        ("clf", LogisticRegression(max_iter=5000)),
    ])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    print("\n=== 学習 ===")
    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    print("\n=== 評価指標 ===")
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
    print(f"\nclassification_report:\n{classification_report(y_test, y_pred, target_names=['死亡', '生存'])}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))

    im = axes[0].imshow(cm, cmap="Blues")
    axes[0].set_title("混同行列")
    axes[0].set_xlabel("予測ラベル")
    axes[0].set_ylabel("正解ラベル")
    axes[0].set_xticks([0, 1], ["死亡", "生存"])
    axes[0].set_yticks([0, 1], ["死亡", "生存"])
    for i in range(2):
        for j in range(2):
            axes[0].text(j, i, str(cm[i, j]), ha="center", va="center",
                         color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=axes[0])

    fpr, tpr, _ = roc_curve(y_test, y_proba)
    axes[1].plot(fpr, tpr, label=f"ロジスティック回帰 (AUC={auc:.3f})")
    axes[1].plot([0, 1], [0, 1], "--", color="gray", label="ランダム")
    axes[1].set_xlabel("偽陽性率 (FPR)")
    axes[1].set_ylabel("真陽性率 (TPR)")
    axes[1].set_title("ROC曲線")
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/titanic_evaluation.png", dpi=120)
    print(f"\n混同行列・ROC曲線の図を保存: {OUT_DIR}/titanic_evaluation.png")

    print("\n=== 交差検証 (5-fold) ===")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(pipeline, X, y, cv=skf, scoring="accuracy")
    print(f"5-fold CV Accuracy: {cv_scores}")
    print(f"平均Accuracy: {cv_scores.mean():.4f} (std={cv_scores.std():.4f})")

    # 係数から特徴量の影響を確認（性別・客室クラスが効くはず）
    clf = pipeline.named_steps["clf"]
    feature_names = pipeline.named_steps["preprocess"].get_feature_names_out()
    coefs = pd.Series(clf.coef_[0], index=feature_names).sort_values()
    print("\n=== 係数（生存への影響、正=生存に有利 負=不利）===")
    print(coefs.to_string())


if __name__ == "__main__":
    main()
