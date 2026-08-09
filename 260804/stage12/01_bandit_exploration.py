"""多腕バンディット問題で、探索と活用のトレードオフ・ε-greedy/UCB方策をスクラッチ実装する

強化学習は「正解データ」がなく、エージェントが自分の行動の結果(報酬)から
試行錯誤で学ぶ枠組み。その最も単純な形が多腕バンディット(multi-armed bandit)問題:
複数のスロットマシン(腕)があり、それぞれ異なる(未知の)確率で報酬が出る。
限られた試行回数の中で、どの腕を引けば総報酬を最大化できるかを学習する。

ここで本質的に重要になるのが「探索(exploration) vs 活用(exploitation)」のトレードオフ:
  - 活用: これまでの経験上、最も報酬が高そうな腕を引き続ける
  - 探索: まだよく分かっていない腕を試し、より良い腕を発見しようとする
活用ばかりだと真に最良の腕を見逃す可能性があり、探索ばかりだと得られたはずの報酬を
取りこぼす。この问题を解く代表的な方策として、ε-greedy(確率εでランダムに探索)と
UCB(Upper Confidence Bound: 「不確実性の高い腕」を優先的に試す)を実装し比較する。

多腕バンディットは、状態遷移のない(1状態しかない)マルコフ決定過程(MDP)の特殊ケースとも
見なせ、次のスクリプトで状態遷移のあるMDP(FrozenLake)へと発展させる橋渡しになっている。
"""
import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

rng = np.random.RandomState(42)


class BanditEnv:
    """k本の腕、それぞれ異なる(未知の)確率で報酬1を出すベルヌーイバンディット"""

    def __init__(self, true_probs):
        self.true_probs = np.array(true_probs)
        self.k = len(true_probs)

    def pull(self, arm, rng):
        return float(rng.random() < self.true_probs[arm])


def run_random(env, n_steps, rng):
    rewards, regrets = [], []
    optimal = env.true_probs.max()
    for t in range(n_steps):
        arm = rng.randint(env.k)
        r = env.pull(arm, rng)
        rewards.append(r)
        regrets.append(optimal - env.true_probs[arm])
    return np.array(rewards), np.array(regrets)


def run_epsilon_greedy(env, n_steps, epsilon, rng):
    Q = np.zeros(env.k)  # 各腕の推定報酬期待値
    N = np.zeros(env.k)  # 各腕を引いた回数
    rewards, regrets = [], []
    optimal = env.true_probs.max()
    for t in range(n_steps):
        if rng.random() < epsilon:
            arm = rng.randint(env.k)  # 探索: ランダムに選ぶ
        else:
            arm = int(np.argmax(Q))   # 活用: 現時点で最良と思う腕を選ぶ
        r = env.pull(arm, rng)
        N[arm] += 1
        Q[arm] += (r - Q[arm]) / N[arm]  # 逐次平均の更新
        rewards.append(r)
        regrets.append(optimal - env.true_probs[arm])
    return np.array(rewards), np.array(regrets)


def run_ucb(env, n_steps, c, rng):
    Q = np.zeros(env.k)
    N = np.zeros(env.k)
    rewards, regrets = [], []
    optimal = env.true_probs.max()
    for t in range(n_steps):
        if t < env.k:
            arm = t  # 最初は全ての腕を1回ずつ試す
        else:
            # UCBスコア = 推定値 + c*sqrt(log(t)/N[arm]) : 引いた回数が少ない腕ほど
            # 「不確実性ボーナス」が大きくなり、優先的に試されるようになる
            ucb_scores = Q + c * np.sqrt(np.log(t) / N)
            arm = int(np.argmax(ucb_scores))
        r = env.pull(arm, rng)
        N[arm] += 1
        Q[arm] += (r - Q[arm]) / N[arm]
        rewards.append(r)
        regrets.append(optimal - env.true_probs[arm])
    return np.array(rewards), np.array(regrets)


def main() -> None:
    true_probs = [0.2, 0.5, 0.35, 0.7, 0.45]
    env = BanditEnv(true_probs)
    n_steps = 2000
    n_trials = 200  # 乱数の影響を減らすため、複数回試行して平均を取る

    print("=== 1. 多腕バンディット問題の設定 ===")
    print(f"腕の数={len(true_probs)}, 各腕の真の報酬確率={true_probs}")
    print(f"最適な腕はindex={np.argmax(true_probs)}(確率{max(true_probs)})")

    print(f"\n=== 2. ランダム方策・ε-greedy・UCBを{n_trials}試行ずつ平均して比較 ===")
    strategies = {
        "ランダム": lambda seed: run_random(env, n_steps, np.random.RandomState(seed)),
        "ε-greedy(ε=0.1)": lambda seed: run_epsilon_greedy(env, n_steps, 0.1, np.random.RandomState(seed)),
        "ε-greedy(ε=0.3)": lambda seed: run_epsilon_greedy(env, n_steps, 0.3, np.random.RandomState(seed)),
        "UCB(c=1.0)": lambda seed: run_ucb(env, n_steps, 1.0, np.random.RandomState(seed)),
    }

    all_rewards, all_regrets = {}, {}
    for name, fn in strategies.items():
        rewards_list, regrets_list = [], []
        for trial in range(n_trials):
            r, g = fn(trial)
            rewards_list.append(r)
            regrets_list.append(g)
        all_rewards[name] = np.mean(rewards_list, axis=0)
        all_regrets[name] = np.mean(regrets_list, axis=0)
        total_reward = np.sum(all_rewards[name])
        total_regret = np.sum(all_regrets[name])
        print(f"  {name:20s}: 総報酬(平均)={total_reward:.1f}/{n_steps}, "
              f"総後悔(regret)={total_regret:.1f}")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for name in strategies:
        cum_reward_rate = np.cumsum(all_rewards[name]) / (np.arange(n_steps) + 1)
        axes[0].plot(cum_reward_rate, label=name)
    axes[0].axhline(max(true_probs), color="gray", linestyle="--", label="理論上の最大(常に最良の腕)")
    axes[0].set_xlabel("ステップ")
    axes[0].set_ylabel("累積報酬率(平均獲得報酬)")
    axes[0].set_title("方策ごとの累積報酬率の推移")
    axes[0].legend(fontsize=8)

    for name in strategies:
        cum_regret = np.cumsum(all_regrets[name])
        axes[1].plot(cum_regret, label=name)
    axes[1].set_xlabel("ステップ")
    axes[1].set_ylabel("累積後悔(cumulative regret)")
    axes[1].set_title("方策ごとの累積後悔の推移\n(理論上の最良の腕を引き続けた場合との差)")
    axes[1].legend(fontsize=8)

    fig.tight_layout()
    out_path = "bandit_exploration.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    final_regrets = {name: np.sum(all_regrets[name]) for name in strategies}
    best_strategy = min(final_regrets, key=final_regrets.get)
    print(
        f"\n{n_steps}ステップ後の累積後悔が最も小さかったのは『{best_strategy}』"
        f"(後悔={final_regrets[best_strategy]:.1f})だった。ランダム方策は探索しかしないため"
        "後悔が線形に増え続けるのに対し、ε-greedyやUCBは学習が進むにつれて最良の腕を"
        "引く頻度が増え、後悔の増加が徐々に緩やかになる(累積後悔のグラフの傾きが"
        "小さくなっていく)様子が見て取れる。ε-greedyはεの値を大きくすると探索が増えて"
        "序盤の学習が速くなる代わりに、学習後も一定確率でランダムな(悪い)腕を"
        "引き続けてしまうため、長期的な後悔は蓄積し続ける。UCBは『不確実性が高い腕を"
        "優先的に試す』という原理により、確信を持てる腕は次第に探索しなくなる"
        "(理論的に後悔の増加が対数オーダーに収束する)という性質があり、"
        "今回の実験でもその効率の良さが確認できた。"
    )


if __name__ == "__main__":
    main()
