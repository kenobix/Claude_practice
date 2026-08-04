"""
Stage 1 - Step A: 線形回帰のスクラッチ実装（numpyのみ）

単回帰・重回帰を「最小二乗法（正規方程式）」と「勾配降下法」の
2通りで実装し、両者が同じ解に収束することを確認する。
"""
import numpy as np


def generate_regression_data(n_samples=200, n_features=1, noise=1.0, seed=42):
    """y = Xw + b + noise となる人工データを生成する"""
    rng = np.random.default_rng(seed)
    X = rng.uniform(-5, 5, size=(n_samples, n_features))
    true_w = rng.uniform(-3, 3, size=n_features)
    true_b = rng.uniform(-2, 2)
    noise_term = rng.normal(0, noise, size=n_samples)
    y = X @ true_w + true_b + noise_term
    return X, y, true_w, true_b


def add_bias_column(X):
    """X の先頭に定数項(バイアス)用の1の列を追加する"""
    ones = np.ones((X.shape[0], 1))
    return np.hstack([ones, X])


def fit_normal_equation(X, y):
    """最小二乗法（正規方程式）: w = (X^T X)^-1 X^T y"""
    Xb = add_bias_column(X)
    # 逆行列を陽に計算するより数値的に安定な最小二乗解法を使う
    theta, *_ = np.linalg.lstsq(Xb, y, rcond=None)
    return theta[0], theta[1:]  # bias, weights


def fit_gradient_descent(X, y, lr=0.05, n_iters=2000):
    """勾配降下法による線形回帰。MSE損失を最小化する"""
    n_samples, n_features = X.shape
    w = np.zeros(n_features)
    b = 0.0
    history = []

    for i in range(n_iters):
        y_pred = X @ w + b
        error = y_pred - y

        # MSE = mean(error^2) の勾配
        grad_w = (2.0 / n_samples) * (X.T @ error)
        grad_b = (2.0 / n_samples) * np.sum(error)

        w -= lr * grad_w
        b -= lr * grad_b

        mse = np.mean(error ** 2)
        history.append(mse)

    return b, w, history


def mse(y_true, y_pred):
    return np.mean((y_true - y_pred) ** 2)


def r2_score(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return 1 - ss_res / ss_tot


if __name__ == "__main__":
    print("=== 単回帰 (n_features=1) ===")
    X, y, true_w, true_b = generate_regression_data(n_features=1, seed=0)

    b_ne, w_ne = fit_normal_equation(X, y)
    b_gd, w_gd, history = fit_gradient_descent(X, y, lr=0.05, n_iters=2000)

    print(f"真の係数     : w={true_w}, b={true_b:.4f}")
    print(f"正規方程式   : w={w_ne}, b={b_ne:.4f}")
    print(f"勾配降下法   : w={w_gd}, b={b_gd:.4f}  (最終MSE={history[-1]:.6f})")

    y_pred_ne = X @ w_ne + b_ne
    print(f"正規方程式 R^2: {r2_score(y, y_pred_ne):.4f}")

    print("\n=== 重回帰 (n_features=3) ===")
    X3, y3, true_w3, true_b3 = generate_regression_data(n_features=3, seed=1)
    b3_ne, w3_ne = fit_normal_equation(X3, y3)
    b3_gd, w3_gd, history3 = fit_gradient_descent(X3, y3, lr=0.01, n_iters=5000)

    print(f"真の係数     : w={true_w3}, b={true_b3:.4f}")
    print(f"正規方程式   : w={w3_ne}, b={b3_ne:.4f}")
    print(f"勾配降下法   : w={w3_gd}, b={b3_gd:.4f}  (最終MSE={history3[-1]:.6f})")

    # 正規方程式と勾配降下法がほぼ一致することを確認
    assert np.allclose(w_ne, w_gd, atol=1e-2), "単回帰: 正規方程式と勾配降下法の解が乖離しています"
    assert np.allclose(w3_ne, w3_gd, atol=1e-2), "重回帰: 正規方程式と勾配降下法の解が乖離しています"
    print("\n[OK] 正規方程式と勾配降下法の解はほぼ一致しました")
