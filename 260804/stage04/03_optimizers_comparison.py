"""最適化アルゴリズムの比較: 勾配降下法・最急降下法・ニュートン法・SGD・Adam

前半: 楕円形の谷（曲率が方向によって大きく異なる、条件数の悪い関数）で
      勾配降下法(固定ステップ)・最急降下法(毎回最適なステップ幅を線探索)・
      ニュートン法(2階微分=曲率の情報も使う)の軌跡を比較する
後半: 02で作ったMLPと同じアーキテクチャで、フルバッチGD・ミニバッチSGD・Adamの
      収束の速さを手書き数字分類で比較する
"""
import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


# ============================================================
# 前半: 楕円形の谷での GD / 最急降下法 / ニュートン法の比較
# ============================================================
def f(xy: np.ndarray) -> float:
    x, y = xy
    return x**2 + 10 * y**2  # x方向より y方向の壁が10倍急な「細長い谷」


def grad_f(xy: np.ndarray) -> np.ndarray:
    x, y = xy
    return np.array([2 * x, 20 * y])


def hessian_f(xy: np.ndarray) -> np.ndarray:
    return np.array([[2.0, 0.0], [0.0, 20.0]])  # この関数では定数(2階微分=曲率が一定)


def gradient_descent(start, lr=0.09, steps=30):
    path = [start.copy()]
    xy = start.copy()
    for _ in range(steps):
        xy = xy - lr * grad_f(xy)
        path.append(xy.copy())
    return np.array(path)


def steepest_descent(start, steps=30):
    """毎回のステップで『その方向に沿って一番loss が下がる歩幅』を解析的に求める(線探索)"""
    path = [start.copy()]
    xy = start.copy()
    for _ in range(steps):
        g = grad_f(xy)
        H = hessian_f(xy)
        # 2次関数 f(xy - t*g) を t で最小化する解析解: t* = (g・g) / (g・H・g)
        t_star = (g @ g) / (g @ H @ g)
        xy = xy - t_star * g
        path.append(xy.copy())
    return np.array(path)


def newton_method(start, steps=30):
    path = [start.copy()]
    xy = start.copy()
    for _ in range(steps):
        g = grad_f(xy)
        H = hessian_f(xy)
        xy = xy - np.linalg.inv(H) @ g
        path.append(xy.copy())
        if np.linalg.norm(grad_f(xy)) < 1e-10:  # 更新後の勾配で収束判定する
            break
    return np.array(path)


def compare_toy_optimizers() -> None:
    print("=== 1. 楕円形の谷(条件数の悪い関数)での比較: f(x,y) = x^2 + 10*y^2 ===")
    start = np.array([8.0, 4.0])

    gd_path = gradient_descent(start, lr=0.09, steps=30)
    sd_path = steepest_descent(start, steps=30)
    newton_path = newton_method(start, steps=5)

    print(f"開始点: {start}")
    print(f"勾配降下法(固定lr=0.09, 30ステップ後): {gd_path[-1].round(4)}  loss={f(gd_path[-1]):.6f}")
    print(f"最急降下法(線探索, 30ステップ後):      {sd_path[-1].round(4)}  loss={f(sd_path[-1]):.6f}")
    print(f"ニュートン法({len(newton_path)-1}ステップで収束):        {newton_path[-1].round(4)}  loss={f(newton_path[-1]):.6f}")
    print(
        "\nニュートン法は2階微分(曲率)の情報を使い、2次関数であればたった1ステップで"
        "厳密な最小値に到達する。勾配降下法・最急降下法は1階微分(勾配の向き)しか使わないため、"
        "谷の壁が急な方向(y軸)で振動しながらゆっくり収束する『ジグザグ現象』が起きる"
        "（最急降下法は毎回最適な歩幅を選んでいるにもかかわらずジグザグする点に注目。"
        "『その場では最適』でも『進むべき方向自体』が谷の形に対して悪いため）。"
    )
    print(
        "ではニュートン法が常に最強かというと、そうではない。ニューラルネットのパラメータは"
        "数千〜数億個あり、そのヘッセ行列は「パラメータ数の2乗」のサイズになる上、逆行列の計算は"
        "さらに重い。ディープラーニングでニュートン法がほぼ使われず勾配降下法系が主流なのは、"
        "この計算コストの現実的な制約のため。"
    )

    x_range = np.linspace(-9, 9, 200)
    y_range = np.linspace(-5, 5, 200)
    X, Y = np.meshgrid(x_range, y_range)
    Z = X**2 + 10 * Y**2

    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax, path, title in zip(
        axes, [gd_path, sd_path, newton_path], ["勾配降下法(固定ステップ)", "最急降下法(線探索)", "ニュートン法"]
    ):
        ax.contour(X, Y, Z, levels=20, cmap="Greys", alpha=0.5)
        ax.plot(path[:, 0], path[:, 1], "o-", color="tab:red", markersize=3, linewidth=1)
        ax.plot(0, 0, "*", color="tab:blue", markersize=15, label="最小値")
        ax.set_title(f"{title}\n({len(path)-1}ステップ)")
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.legend()
    fig.tight_layout()
    out_path = "optimizer_trajectories.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")


# ============================================================
# 後半: 実データ(MLP)でのフルバッチGD / ミニバッチSGD / Adamの比較
# ============================================================
def relu(z):
    return np.maximum(0, z)


def relu_grad(z):
    return (z > 0).astype(float)


def softmax_batch(z):
    z_shifted = z - z.max(axis=1, keepdims=True)
    exp_z = np.exp(z_shifted)
    return exp_z / exp_z.sum(axis=1, keepdims=True)


def one_hot(y, n_classes):
    out = np.zeros((len(y), n_classes))
    out[np.arange(len(y)), y] = 1
    return out


def cross_entropy(a2, y_onehot):
    eps = 1e-12
    return -np.mean(np.sum(y_onehot * np.log(a2 + eps), axis=1))


class MLPParams:
    def __init__(self, n_in, n_hidden, n_out, seed=42):
        rng = np.random.RandomState(seed)
        self.W1 = rng.randn(n_in, n_hidden) * np.sqrt(2.0 / n_in)
        self.b1 = np.zeros(n_hidden)
        self.W2 = rng.randn(n_hidden, n_out) * np.sqrt(2.0 / n_hidden)
        self.b2 = np.zeros(n_out)

    def forward(self, X):
        z1 = X @ self.W1 + self.b1
        a1 = relu(z1)
        z2 = a1 @ self.W2 + self.b2
        a2 = softmax_batch(z2)
        return {"X": X, "z1": z1, "a1": a1, "z2": z2, "a2": a2}

    def backward(self, cache, y_onehot):
        N = cache["X"].shape[0]
        dz2 = (cache["a2"] - y_onehot) / N
        dW2 = cache["a1"].T @ dz2
        db2 = dz2.sum(axis=0)
        da1 = dz2 @ self.W2.T
        dz1 = da1 * relu_grad(cache["z1"])
        dW1 = cache["X"].T @ dz1
        db1 = dz1.sum(axis=0)
        return {"W1": dW1, "b1": db1, "W2": dW2, "b2": db2}

    def params(self):
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    def predict(self, X):
        return self.forward(X)["a2"].argmax(axis=1)


class SGDUpdater:
    """フルバッチGD/ミニバッチSGD共通: 単純に学習率×勾配だけ引く"""

    def __init__(self, lr):
        self.lr = lr

    def update(self, model: MLPParams, grads: dict) -> None:
        for name, grad in grads.items():
            setattr(model, name, getattr(model, name) - self.lr * grad)


class AdamUpdater:
    """各パラメータごとに勾配の移動平均(m)と分散の移動平均(v)を保持し、
    バイアス補正した上で学習率を自動調整する"""

    def __init__(self, lr=0.01, beta1=0.9, beta2=0.999, eps=1e-8):
        self.lr, self.beta1, self.beta2, self.eps = lr, beta1, beta2, eps
        self.m, self.v, self.t = {}, {}, 0

    def update(self, model: MLPParams, grads: dict) -> None:
        self.t += 1
        for name, grad in grads.items():
            if name not in self.m:
                self.m[name] = np.zeros_like(grad)
                self.v[name] = np.zeros_like(grad)
            self.m[name] = self.beta1 * self.m[name] + (1 - self.beta1) * grad
            self.v[name] = self.beta2 * self.v[name] + (1 - self.beta2) * (grad**2)
            m_hat = self.m[name] / (1 - self.beta1**self.t)
            v_hat = self.v[name] / (1 - self.beta2**self.t)
            new_val = getattr(model, name) - self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
            setattr(model, name, new_val)


def train(model, updater, X_train, y_train_oh, X_test, y_test, y_test_oh, epochs, batch_size, rng):
    n = len(X_train)
    history = []
    for epoch in range(epochs):
        if batch_size is None:  # フルバッチ
            batches = [(X_train, y_train_oh)]
        else:  # ミニバッチ(毎エポックシャッフル)
            idx = rng.permutation(n)
            batches = [
                (X_train[idx[i : i + batch_size]], y_train_oh[idx[i : i + batch_size]])
                for i in range(0, n, batch_size)
            ]
        for X_batch, y_batch in batches:
            cache = model.forward(X_batch)
            grads = model.backward(cache, y_batch)
            updater.update(model, grads)

        test_cache = model.forward(X_test)
        test_loss = cross_entropy(test_cache["a2"], y_test_oh)
        test_acc = (test_cache["a2"].argmax(axis=1) == y_test).mean()
        history.append((epoch, test_loss, test_acc))
    return history


def compare_real_optimizers() -> None:
    print("\n=== 2. 手書き数字MLPでのフルバッチGD / ミニバッチSGD / Adamの比較(20epoch) ===")
    digits = load_digits()
    X, y = digits.data, digits.target
    X = StandardScaler().fit_transform(X)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    y_train_oh, y_test_oh = one_hot(y_train, 10), one_hot(y_test, 10)

    epochs = 20
    rng = np.random.RandomState(0)

    model_gd = MLPParams(64, 32, 10, seed=42)
    hist_gd = train(model_gd, SGDUpdater(lr=0.5), X_train, y_train_oh, X_test, y_test, y_test_oh, epochs, None, rng)

    model_sgd = MLPParams(64, 32, 10, seed=42)
    hist_sgd = train(model_sgd, SGDUpdater(lr=0.5), X_train, y_train_oh, X_test, y_test, y_test_oh, epochs, 32, rng)

    model_adam = MLPParams(64, 32, 10, seed=42)
    hist_adam = train(model_adam, AdamUpdater(lr=0.01), X_train, y_train_oh, X_test, y_test, y_test_oh, epochs, 32, rng)

    for name, hist in [("フルバッチGD", hist_gd), ("ミニバッチSGD", hist_sgd), ("Adam", hist_adam)]:
        print(f"{name}: epoch5 test_acc={hist[4][2]:.3f}  epoch{epochs} test_acc={hist[-1][2]:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for name, hist in [("フルバッチGD", hist_gd), ("ミニバッチSGD", hist_sgd), ("Adam", hist_adam)]:
        epochs_arr, losses, accs = zip(*hist)
        axes[0].plot(epochs_arr, losses, label=name)
        axes[1].plot(epochs_arr, accs, label=name)
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("テスト損失")
    axes[0].set_title("テスト損失の推移")
    axes[0].legend()
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("テスト精度")
    axes[1].set_title("テスト精度の推移")
    axes[1].legend()
    fig.tight_layout()
    out_path = "optimizer_training_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nフルバッチGDは1エポックにつき1回しかパラメータを更新しないため、同じエポック数では"
        "更新回数が最も少なく収束が遅い。ミニバッチSGDは1エポック内で複数回(データ数/バッチサイズ回)"
        "更新するため、同じエポック数でもより多く学習が進む。Adamはミニバッチ勾配に加えて"
        "勾配の移動平均(モーメンタム)と大きさに応じた学習率の自動調整を行うため、"
        "同じエポック数の中で最も速く収束する傾向がある。"
    )


def main() -> None:
    compare_toy_optimizers()
    compare_real_optimizers()


if __name__ == "__main__":
    main()
