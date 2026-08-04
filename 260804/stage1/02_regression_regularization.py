"""
Stage 1 - Step B (回帰編): scikit-learnによる重回帰・正則化・評価の実践

- load_diabetes（糖尿病進行度データ、回帰用の組み込みデータセット）を使用
- 重回帰(LinearRegression) / Ridge(L2) / Lasso(L1) の比較
- AIC（赤池情報量規準）を手計算で算出
- k分割交差検証・グリッドサーチによるハイパーパラメータ探索
"""
import numpy as np
import _mpl_ja  # noqa: F401  matplotlibの日本語フォント設定
import matplotlib.pyplot as plt

from sklearn.datasets import load_diabetes
from sklearn.model_selection import train_test_split, KFold, cross_val_score, GridSearchCV
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

OUT_DIR = __file__.rsplit("/", 1)[0]


def compute_aic(y_true, y_pred, n_params):
    """AIC = n * ln(RSS / n) + 2k  （回帰モデルの簡易AIC計算）"""
    n = len(y_true)
    rss = np.sum((y_true - y_pred) ** 2)
    aic = n * np.log(rss / n) + 2 * n_params
    return aic


def main():
    data = load_diabetes()
    X, y = data.data, data.target
    feature_names = data.feature_names
    print(f"データ形状: X={X.shape}, y={y.shape}, 特徴量={feature_names}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # 特徴量ごとにスケールが違う正則化モデルのため標準化する
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    print("\n=== 1. 重回帰 (LinearRegression) ===")
    lin = LinearRegression()
    lin.fit(X_train_s, y_train)
    y_pred = lin.predict(X_test_s)
    print(f"係数: {dict(zip(feature_names, np.round(lin.coef_, 2)))}")
    print(f"テストMSE: {mean_squared_error(y_test, y_pred):.2f}")
    print(f"テストR^2: {r2_score(y_test, y_pred):.4f}")

    print("\n=== 2. AIC（モデル複雑度と当てはまりのトレードオフ）===")
    # 説明変数を1本ずつ増やしながらAICの変化を見る
    for k in [1, 3, 5, 10]:
        model = LinearRegression().fit(X_train_s[:, :k], y_train)
        pred = model.predict(X_train_s[:, :k])
        aic = compute_aic(y_train, pred, n_params=k + 1)  # +1 は切片分
        print(f"  特徴量数={k:2d}  AIC={aic:.2f}")

    print("\n=== 3. 正則化: Ridge(L2) と Lasso(L1) の比較 ===")
    alphas = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]
    ridge_coefs, lasso_coefs = [], []
    for alpha in alphas:
        ridge = Ridge(alpha=alpha).fit(X_train_s, y_train)
        lasso = Lasso(alpha=alpha, max_iter=10000).fit(X_train_s, y_train)
        ridge_coefs.append(ridge.coef_)
        lasso_coefs.append(lasso.coef_)
        print(
            f"  alpha={alpha:8.3f}  "
            f"Ridge R^2={r2_score(y_test, ridge.predict(X_test_s)):.4f}  "
            f"Lasso R^2={r2_score(y_test, lasso.predict(X_test_s)):.4f}  "
            f"Lassoの非ゼロ係数数={np.sum(lasso.coef_ != 0)}/{len(feature_names)}"
        )

    ridge_coefs = np.array(ridge_coefs)
    lasso_coefs = np.array(lasso_coefs)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for i, name in enumerate(feature_names):
        axes[0].plot(alphas, ridge_coefs[:, i], marker="o", label=name)
        axes[1].plot(alphas, lasso_coefs[:, i], marker="o", label=name)
    for ax, title in zip(axes, ["Ridge (L2)", "Lasso (L1)"]):
        ax.set_xscale("log")
        ax.set_xlabel("alpha")
        ax.set_ylabel("coefficient")
        ax.set_title(f"{title}: 正則化強度と係数の変化")
        ax.axhline(0, color="gray", linewidth=0.5)
    axes[1].legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/regularization_paths.png", dpi=120)
    print(f"\n係数パスの図を保存: {OUT_DIR}/regularization_paths.png")

    print("\n=== 4. k分割交差検証 ===")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(LinearRegression(), X, y, cv=kf, scoring="r2")
    print(f"5-fold CV R^2: {scores}")
    print(f"平均R^2: {scores.mean():.4f} (std={scores.std():.4f})")

    print("\n=== 5. グリッドサーチ（Ridgeのalpha探索）===")
    param_grid = {"alpha": [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]}
    grid = GridSearchCV(Ridge(), param_grid, cv=kf, scoring="r2")
    grid.fit(X_train_s, y_train)
    print(f"最良alpha: {grid.best_params_}")
    print(f"最良CV R^2: {grid.best_score_:.4f}")
    best_ridge = grid.best_estimator_
    print(f"テストR^2 (最良モデル): {r2_score(y_test, best_ridge.predict(X_test_s)):.4f}")


if __name__ == "__main__":
    main()
