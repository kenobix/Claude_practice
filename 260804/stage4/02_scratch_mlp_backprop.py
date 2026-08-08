"""numpyのみで多層パーセプトロン(MLP)を実装し、誤差逆伝播法を手書きで導出する

ネットワーク構成: 入力64 → 隠れ層(ReLU) → 出力10(ソフトマックス)
損失関数: 交差エントロピー誤差

--- 逆伝播の導出（ここが本題） ---
記号: X=入力, W1,b1=1層目の重み/バイアス, z1=W1の出力(活性化前), a1=ReLU(z1)
      W2,b2=2層目の重み/バイアス, z2=W2の出力(活性化前), a2=softmax(z2)=予測確率
      y=正解のone-hotベクトル, N=バッチサイズ
      L = -(1/N) * sum(y * log(a2))  (交差エントロピー誤差)

1. 出力層の勾配 dL/dz2:
   ソフトマックス+交差エントロピーの組み合わせは、驚くほど綺麗な式に落ちる。
   dL/dz2 = (a2 - y) / N
   （softmaxのヤコビ行列と交差エントロピーの勾配を連鎖律で掛け合わせると、
     お互いの複雑な項が打ち消し合ってこの単純な形になる。これが「交差エントロピー」を
     softmaxとセットで使う最大の理由）

2. 2層目の重み・バイアスの勾配（連鎖律: dL/dW2 = dL/dz2 * dz2/dW2、z2=a1@W2+b2なのでdz2/dW2=a1）:
   dL/dW2 = a1.T @ dL/dz2
   dL/db2 = sum(dL/dz2, axis=0)

3. 1層目への逆伝播（z2=a1@W2+b2なのでdz2/da1=W2）:
   dL/da1 = dL/dz2 @ W2.T
   dL/dz1 = dL/da1 * ReLU'(z1)   （ReLUの導関数: z1>0で1、z1<=0で0）

4. 1層目の重み・バイアスの勾配:
   dL/dW1 = X.T @ dL/dz1
   dL/db1 = sum(dL/dz1, axis=0)

この「出力側から入力側に向かって連鎖律を順番に適用していく」計算手順が誤差逆伝播法。
"""
import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


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


class ScratchMLP:
    def __init__(self, n_in: int, n_hidden: int, n_out: int, seed: int = 42) -> None:
        rng = np.random.RandomState(seed)
        # He初期化: ReLUを使う層は分散2/n_inで初期化すると学習が安定しやすい
        self.W1 = rng.randn(n_in, n_hidden) * np.sqrt(2.0 / n_in)
        self.b1 = np.zeros(n_hidden)
        self.W2 = rng.randn(n_hidden, n_out) * np.sqrt(2.0 / n_hidden)
        self.b2 = np.zeros(n_out)

    def forward(self, X: np.ndarray) -> dict:
        z1 = X @ self.W1 + self.b1
        a1 = relu(z1)
        z2 = a1 @ self.W2 + self.b2
        a2 = softmax_batch(z2)
        return {"X": X, "z1": z1, "a1": a1, "z2": z2, "a2": a2}

    def backward(self, cache: dict, y_onehot: np.ndarray) -> dict:
        N = cache["X"].shape[0]
        dz2 = (cache["a2"] - y_onehot) / N  # 導出の手順1
        dW2 = cache["a1"].T @ dz2  # 手順2
        db2 = dz2.sum(axis=0)
        da1 = dz2 @ self.W2.T  # 手順3
        dz1 = da1 * relu_grad(cache["z1"])
        dW1 = cache["X"].T @ dz1  # 手順4
        db1 = dz1.sum(axis=0)
        return {"dW1": dW1, "db1": db1, "dW2": dW2, "db2": db2}

    def step(self, grads: dict, lr: float) -> None:
        self.W1 -= lr * grads["dW1"]
        self.b1 -= lr * grads["db1"]
        self.W2 -= lr * grads["dW2"]
        self.b2 -= lr * grads["db2"]

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.forward(X)["a2"].argmax(axis=1)


def cross_entropy(a2: np.ndarray, y_onehot: np.ndarray) -> float:
    eps = 1e-12  # log(0)によるnan回避
    return -np.mean(np.sum(y_onehot * np.log(a2 + eps), axis=1))


def numerical_gradient_check(model: ScratchMLP, X: np.ndarray, y_onehot: np.ndarray) -> None:
    """手書きで導出した勾配(解析的勾配)が、数値微分の近似値と一致するか検証する"""
    cache = model.forward(X)
    analytic_grads = model.backward(cache, y_onehot)

    eps = 1e-5
    # W1の最初の3要素だけ抜き取り検証(全要素を数値微分すると遅いため)
    max_diff = 0.0
    flat_idx = [(0, 0), (1, 2), (5, 10)]
    for i, j in flat_idx:
        orig = model.W1[i, j]

        model.W1[i, j] = orig + eps
        loss_plus = cross_entropy(model.forward(X)["a2"], y_onehot)
        model.W1[i, j] = orig - eps
        loss_minus = cross_entropy(model.forward(X)["a2"], y_onehot)
        model.W1[i, j] = orig

        numeric_grad = (loss_plus - loss_minus) / (2 * eps)
        analytic_grad = analytic_grads["dW1"][i, j]
        diff = abs(numeric_grad - analytic_grad)
        max_diff = max(max_diff, diff)
        print(f"  W1[{i},{j}]: 解析的勾配={analytic_grad:.6f}  数値微分={numeric_grad:.6f}  差={diff:.2e}")
    print(f"  最大誤差: {max_diff:.2e}（1e-6程度以下なら手書きの逆伝播の実装は正しいとみなせる）")


def main() -> None:
    digits = load_digits()
    X, y = digits.data, digits.target
    X = StandardScaler().fit_transform(X)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    y_train_oh = one_hot(y_train, 10)
    y_test_oh = one_hot(y_test, 10)
    print(f"データ: 手書き数字(load_digits) 訓練{len(X_train)}件 / テスト{len(X_test)}件")

    model = ScratchMLP(n_in=64, n_hidden=32, n_out=10, seed=42)

    print("\n=== 0. 勾配チェック（手書きの逆伝播が正しいかを数値微分で検証） ===")
    numerical_gradient_check(model, X_train[:16], y_train_oh[:16])

    print("\n=== 1. フルバッチ勾配降下法で学習 ===")
    lr = 0.5
    epochs = 300
    train_losses, test_losses, train_accs, test_accs = [], [], [], []
    for epoch in range(epochs):
        cache = model.forward(X_train)
        loss = cross_entropy(cache["a2"], y_train_oh)
        grads = model.backward(cache, y_train_oh)
        model.step(grads, lr)

        if epoch % 10 == 0 or epoch == epochs - 1:
            train_pred = cache["a2"].argmax(axis=1)
            train_acc = (train_pred == y_train).mean()
            test_cache = model.forward(X_test)
            test_loss = cross_entropy(test_cache["a2"], y_test_oh)
            test_acc = (test_cache["a2"].argmax(axis=1) == y_test).mean()
            train_losses.append((epoch, loss))
            test_losses.append((epoch, test_loss))
            train_accs.append((epoch, train_acc))
            test_accs.append((epoch, test_acc))
            if epoch % 50 == 0 or epoch == epochs - 1:
                print(f"epoch {epoch:3d}: train_loss={loss:.4f} train_acc={train_acc:.3f}  test_loss={test_loss:.4f} test_acc={test_acc:.3f}")

    final_test_acc = (model.predict(X_test) == y_test).mean()
    print(f"\n最終テスト精度: {final_test_acc:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(*zip(*train_losses), label="訓練loss")
    axes[0].plot(*zip(*test_losses), label="テストloss")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("交差エントロピー誤差")
    axes[0].set_title("損失の推移")
    axes[0].legend()

    axes[1].plot(*zip(*train_accs), label="訓練精度")
    axes[1].plot(*zip(*test_accs), label="テスト精度")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("Accuracy")
    axes[1].set_title("精度の推移")
    axes[1].legend()

    fig.tight_layout()
    out_path = "scratch_mlp_training.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nnumpyの行列演算だけで、フレームワークを一切使わずに手書き数字分類が学習できた。"
        "ここで実装したforward/backward/stepの3ステップこそが、PyTorch等のフレームワークが"
        "自動微分(autograd)で肩代わりしてくれている処理そのものであり、"
        "次のミニプロジェクトでPyTorch実装と比較する際の土台になる。"
    )


if __name__ == "__main__":
    main()
