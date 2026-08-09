"""numpyでLoRA(Low-Rank Adaptation)をスクラッチ実装し、フルファインチューニングと比較する

LLMのような巨大なモデルを新しいタスクに適応させたい時、全パラメータを再学習する
「フルファインチューニング」は計算コスト・メモリコストが非常に大きい。
LoRAは、重み行列Wを直接更新する代わりに、Wは凍結したまま
  W' = W + ΔW,  ΔW = B @ A  (A: r×d_in, B: d_out×r, r ≪ d_in, d_out)
という低ランク(rank r)の差分ΔWだけを学習する。rを小さくすることで、
学習対象のパラメータ数を「元の重み行列のパラメータ数」から「r×(d_in+d_out)」まで
劇的に減らせる。Stage8で見たTransformerの各層の重み行列(Q/K/V/出力の線形変換等)に
LoRAを適用するのが、LLMのパラメータ効率的ファインチューニング(PEFT)の代表的な手法。

ここでは「タスクA(元のタスク)で学習済みの重み」を持つ小さなMLPを、
「タスクB(新しいタスク)」に適応させる場面を想定し、
(1) フルファインチューニング (2) LoRA
の2通りで適応させ、学習対象パラメータ数と最終的な精度を比較する。
"""
import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

rng = np.random.RandomState(42)


def make_task_data(axis, n_samples=300, seed=0):
    """2クラスの2次元データ。axis='x'ならx座標で、axis='y'ならy座標で2クラスを分ける
    (単なる平行移動ではなく、決定境界の『向き』そのものが変わるようにする)。"""
    r = np.random.RandomState(seed)
    if axis == "x":
        centers = (np.array([-1.5, 0]), np.array([1.5, 0]))
    else:
        centers = (np.array([0, -1.5]), np.array([0, 1.5]))
    X0 = r.randn(n_samples // 2, 2) * 0.5 + centers[0]
    X1 = r.randn(n_samples // 2, 2) * 0.5 + centers[1]
    X = np.vstack([X0, X1])
    y = np.concatenate([np.zeros(n_samples // 2), np.ones(n_samples // 2)])
    perm = r.permutation(len(X))
    return X[perm], y[perm]


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -30, 30)))


class BaseMLP:
    """『事前学習済み』を模した、2層MLP。W1(隠れ層への重み)を後でLoRA適応の対象にする。"""

    def __init__(self, in_dim=2, hidden=32, seed=0):
        r = np.random.RandomState(seed)
        self.W1 = r.randn(in_dim, hidden) * np.sqrt(2.0 / in_dim)
        self.b1 = np.zeros(hidden)
        self.W2 = r.randn(hidden, 1) * np.sqrt(2.0 / hidden)
        self.b2 = np.zeros(1)

    def forward(self, X, delta_W1=None):
        W1_eff = self.W1 if delta_W1 is None else self.W1 + delta_W1
        h = np.maximum(0, X @ W1_eff + self.b1)  # ReLU
        y_hat = sigmoid(h @ self.W2 + self.b2)
        return y_hat, h


def pretrain_on_task_A(n_iters=2000, lr=0.5):
    """タスクAで一からMLPを学習し、『事前学習済みモデル』を作る"""
    model = BaseMLP(seed=1)
    X, y = make_task_data(axis="x", seed=1)
    y = y.reshape(-1, 1)
    for _ in range(n_iters):
        y_hat, h = model.forward(X)
        dW2 = h.T @ (y_hat - y) / len(X)
        db2 = (y_hat - y).mean(axis=0)
        dh = (y_hat - y) @ model.W2.T
        dh_relu = dh * (h > 0)
        dW1 = X.T @ dh_relu / len(X)
        db1 = dh_relu.mean(axis=0)
        model.W1 -= lr * dW1
        model.b1 -= lr * db1
        model.W2 -= lr * dW2
        model.b2 -= lr * db2
    acc = ((model.forward(X)[0] > 0.5).astype(float) == y).mean()
    return model, acc


def full_finetune(model, X, y, n_iters=500, lr=0.5):
    """W1を含む全パラメータをタスクBのデータで再学習(フルファインチューニング)"""
    import copy
    m = copy.deepcopy(model)
    y = y.reshape(-1, 1)
    for _ in range(n_iters):
        y_hat, h = m.forward(X)
        dW2 = h.T @ (y_hat - y) / len(X)
        db2 = (y_hat - y).mean(axis=0)
        dh = (y_hat - y) @ m.W2.T
        dh_relu = dh * (h > 0)
        dW1 = X.T @ dh_relu / len(X)
        db1 = dh_relu.mean(axis=0)
        m.W1 -= lr * dW1
        m.b1 -= lr * db1
        m.W2 -= lr * dW2
        m.b2 -= lr * db2
    n_trainable = m.W1.size + m.b1.size + m.W2.size + m.b2.size
    return m, n_trainable


def lora_finetune(model, X, y, rank=2, n_iters=500, lr=0.5):
    """W1を凍結し、ΔW1 = B@A (低ランク)だけを学習するLoRA適応。
    Bを0で初期化することで、学習開始時点ではΔW1=0(=適応前のモデルと完全に同じ挙動)から
    スタートできるのがLoRAの重要な性質。"""
    in_dim, hidden = model.W1.shape
    A = rng.randn(in_dim, rank) * 0.01
    B = np.zeros((rank, hidden))  # B=0スタート → 学習開始時 ΔW1 = B@A = 0
    y = y.reshape(-1, 1)
    # LoRA適応中は出力層W2・b2も含め、W1以外は凍結(元のモデルのまま)する
    for _ in range(n_iters):
        delta_W1 = A @ B
        y_hat, h = model.forward(X, delta_W1=delta_W1)
        dh = (y_hat - y) @ model.W2.T
        dh_relu = dh * (h > 0)
        d_deltaW1 = X.T @ dh_relu / len(X)  # ΔW1に対する勾配
        dA = d_deltaW1 @ B.T
        dB = A.T @ d_deltaW1
        A -= lr * dA
        B -= lr * dB
    n_trainable = A.size + B.size
    return A, B, n_trainable


def main() -> None:
    print("=== 1. タスクAで『事前学習済みモデル』を作る ===")
    base_model, acc_A = pretrain_on_task_A()
    print(f"タスクAでの学習後精度={acc_A:.3f}")
    print(f"W1(入力→隠れ層)の形状={base_model.W1.shape}, パラメータ数={base_model.W1.size}")

    print("\n=== 2. タスクB(決定境界の向きが90度変わる新しいタスク)への適応を2通りで比較 ===")
    X_B, y_B = make_task_data(axis="y", seed=2)
    X_B_test, y_B_test = make_task_data(axis="y", seed=3)

    acc_before = ((base_model.forward(X_B_test)[0] > 0.5).astype(float) == y_B_test.reshape(-1, 1)).mean()
    print(f"適応前(タスクAのまま)でタスクBを解いた場合の精度={acc_before:.3f}(x座標しか見ていないため、ほぼランダム性能のはず)")

    full_model, n_full = full_finetune(base_model, X_B, y_B)
    acc_full = ((full_model.forward(X_B_test)[0] > 0.5).astype(float) == y_B_test.reshape(-1, 1)).mean()
    print(f"\nフルファインチューニング: 学習対象パラメータ数={n_full}, タスクB精度={acc_full:.3f}")

    results_lora = {}
    for rank in [1, 2, 4, 8]:
        A, B, n_lora = lora_finetune(base_model, X_B, y_B, rank=rank)
        delta_W1 = A @ B
        acc_lora = ((base_model.forward(X_B_test, delta_W1=delta_W1)[0] > 0.5).astype(float)
                     == y_B_test.reshape(-1, 1)).mean()
        results_lora[rank] = (n_lora, acc_lora)
        print(f"LoRA(rank={rank}): 学習対象パラメータ数={n_lora}, タスクB精度={acc_lora:.3f}")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    ranks = list(results_lora.keys())
    lora_params = [results_lora[r][0] for r in ranks]
    lora_accs = [results_lora[r][1] for r in ranks]

    axes[0].axhline(n_full, color="tab:red", linestyle="--", label=f"フルファインチューニング({n_full}個)")
    axes[0].plot(ranks, lora_params, "o-", color="tab:blue", label="LoRA")
    axes[0].set_xlabel("LoRAのrank r")
    axes[0].set_ylabel("学習対象パラメータ数")
    axes[0].set_title("学習対象パラメータ数の比較")
    axes[0].legend()
    axes[0].set_yscale("log")

    axes[1].axhline(acc_full, color="tab:red", linestyle="--", label=f"フルファインチューニング(acc={acc_full:.3f})")
    axes[1].axhline(acc_before, color="gray", linestyle=":", label=f"適応なし(acc={acc_before:.3f})")
    axes[1].plot(ranks, lora_accs, "o-", color="tab:blue", label="LoRA")
    axes[1].set_xlabel("LoRAのrank r")
    axes[1].set_ylabel("タスクBでの精度")
    axes[1].set_title("適応後の精度比較")
    axes[1].legend()
    axes[1].set_ylim(0.4, 1.05)

    fig.tight_layout()
    out_path = "lora_scratch.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    reduction = n_full / results_lora[1][0]
    print(
        f"\n適応前は精度{acc_before:.3f}(ほぼランダム)だったのに対し、フルファインチューニングは"
        f"{n_full}個のパラメータを再学習して精度{acc_full:.3f}まで回復させた。一方LoRAは"
        f"最小のrank=1(パラメータ数{results_lora[1][0]}個、フルの1/{reduction:.0f})でも"
        f"精度{results_lora[1][1]:.3f}に到達しており、rank=2〜8にしてもほぼ同じ精度に"
        "留まった。今回の『決定境界の向きが90度変わる』というタスクBは、本質的には"
        "隠れ層への入力の線形結合を1方向切り替えるだけで解けるごく単純な変化のため、"
        "rank=1の低ランク差分だけで十分に表現できてしまったと考えられる——逆に言えば、"
        "適応に必要な『情報の複雑さ』が小さいタスクほど、LoRAの低ランク近似という制約が"
        "ボトルネックになりにくいということでもある。実際のLLM(数十億パラメータ)では、"
        "この『パラメータ数の削減比率』が桁違いに大きくなるため、LoRAは限られたGPUメモリでも"
        "大規模モデルを実用的にファインチューニングできる手法として広く使われている。"
    )


if __name__ == "__main__":
    main()
