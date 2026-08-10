"""報酬形成(Reward Shaping)をMountainCarで実装し、設計の良し悪しによる効果の違いを確認する

MountainCar-v0は「谷底の車を、エンジン出力だけで右側の山頂(ゴール)まで登らせる」課題。
1ステップごとに-1の報酬しか得られず、ゴールに到達しない限り「今のやり方が近づいているのか
遠ざかっているのか」という手がかりが一切ない、疎な報酬(sparse reward)の典型例になっている。
しかも真の最適戦略は「一度左(ゴールと逆方向)に大きく後退して勢いをつけてから右に加速する」
というもので、単純に「ゴールに近づいたら報酬を与える」ような設計を誤ると、
本来必要な「後退」行動を妨げてしまう恐れがある。

このスクリプトでは、素の報酬(shapingなし)に対して、
  - 位置ベースの単純な形成(naive): 位置(右に進むほど高評価)だけを使う
  - エネルギーベースの形成(energy): 位置による高さ+速度の2乗(運動エネルギー)を使う
    →左右どちらに動いても「エネルギーを増やす」行動が評価されるため、後退による
      加速も正しく評価できる
の2種類の報酬形成(どちらもポテンシャルベース: F(s,s')=γΦ(s')-Φ(s)の形)を、
tabular Q学習で比較する。ポテンシャルベースの形成は理論上「最適方策を変えない」ことが
保証されているが、どのポテンシャル関数Φを選ぶかによって学習の速さは大きく変わりうる。
"""
import numpy as np
import gymnasium as gym
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

N_BINS = 20
POS_BINS = np.linspace(-1.2, 0.6, N_BINS - 1)
VEL_BINS = np.linspace(-0.07, 0.07, N_BINS - 1)


def discretize(state):
    pos, vel = state
    return int(np.digitize(pos, POS_BINS)), int(np.digitize(vel, VEL_BINS))


def height(position):
    return np.sin(3 * position)  # MountainCarの地形の高さの近似式(gymnasium実装に準拠)


def potential_none(state):
    return 0.0


def potential_naive(state):
    """位置だけを見るポテンシャル: 右(ゴール方向)に進むほど高評価"""
    pos, _vel = state
    return pos


def potential_energy(state):
    """位置エネルギー(高さ)+運動エネルギー(速度の2乗)。方向によらずエネルギーを評価する"""
    pos, vel = state
    return height(pos) + 200.0 * vel ** 2


def train_qlearning(env, potential_fn, n_episodes, alpha=0.1, gamma=0.99, rng=None):
    n_actions = env.action_space.n
    Q = np.zeros((N_BINS, N_BINS, n_actions))
    success_history = np.zeros(n_episodes, dtype=bool)
    steps_history = np.zeros(n_episodes, dtype=int)

    for ep in range(n_episodes):
        epsilon = max(0.05, 1.0 - ep / (n_episodes * 0.5))
        state, _ = env.reset(seed=int(rng.randint(1_000_000)))
        s_disc = discretize(state)
        done, steps = False, 0
        while not done:
            if rng.random() < epsilon:
                action = rng.randint(n_actions)
            else:
                action = int(np.argmax(Q[s_disc]))
            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            steps += 1

            shaped_reward = reward + gamma * potential_fn(next_state) - potential_fn(state)
            next_disc = discretize(next_state)
            best_next = np.max(Q[next_disc])
            Q[s_disc][action] += alpha * (shaped_reward + gamma * best_next - Q[s_disc][action])

            state, s_disc = next_state, next_disc

        success_history[ep] = terminated  # 200ステップの時間切れでなくゴール到達で終わったか
        steps_history[ep] = steps

    return Q, success_history, steps_history


def moving_average(x, window=200):
    return np.convolve(x.astype(float), np.ones(window) / window, mode="valid")


def main() -> None:
    print("=== 1. 報酬形成なし/naive(位置)/energy(エネルギー)でtabular Q学習を比較 ===")
    env = gym.make("MountainCar-v0")
    n_episodes = 3000
    variants = {
        "形成なし(素の報酬)": potential_none,
        "naive(位置ベース)": potential_naive,
        "energy(エネルギーベース)": potential_energy,
    }
    results = {}
    for name, potential_fn in variants.items():
        rng = np.random.RandomState(0)
        Q, success, steps = train_qlearning(env, potential_fn, n_episodes, rng=rng)
        results[name] = {"success": success, "steps": steps}
        first_success = np.argmax(success) if success.any() else None
        success_rate_last500 = success[-500:].mean()
        print(f"[{name}] 初めてゴールに到達したエピソード="
              f"{first_success if first_success is not None else '到達なし'}, "
              f"直近500エピソードの成功率={success_rate_last500:.1%}, "
              f"直近500エピソードの平均ステップ数={steps[-500:].mean():.1f}")

    print("\n=== 2. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    colors = {"形成なし(素の報酬)": "tab:red", "naive(位置ベース)": "tab:orange",
              "energy(エネルギーベース)": "tab:blue"}
    for name, r in results.items():
        ma = moving_average(r["success"])
        axes[0].plot(ma, color=colors[name], label=name)
    axes[0].set_xlabel("エピソード")
    axes[0].set_ylabel("ゴール到達率(移動平均, window=200)")
    axes[0].set_title("報酬形成の有無・設計による学習速度の違い")
    axes[0].legend(fontsize=8)

    for name, r in results.items():
        ma = moving_average(r["steps"].astype(float))
        axes[1].plot(ma, color=colors[name], label=name)
    axes[1].set_xlabel("エピソード")
    axes[1].set_ylabel("1エピソードのステップ数(移動平均, 200=時間切れ)")
    axes[1].set_title("ゴールまでのステップ数の推移(小さいほど良い)")
    axes[1].legend(fontsize=8)

    fig.tight_layout()
    out_path = "reward_shaping.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    rate_none = results["形成なし(素の報酬)"]["success"][-500:].mean()
    rate_naive = results["naive(位置ベース)"]["success"][-500:].mean()
    rate_energy = results["energy(エネルギーベース)"]["success"][-500:].mean()
    print(
        f"\n直近500エピソードの成功率は、形成なし={rate_none:.1%}、naive(位置)={rate_naive:.1%}、"
        f"energy(エネルギー)={rate_energy:.1%}となった。"
        f"{'energyの方がnaiveより高い成功率を達成し、方向に依存しないエネルギーベースの設計が有効に働いた' if rate_energy > rate_naive else 'naiveとenergyの差は明確には出なかった'}——"
        "MountainCarは『ゴールに向かって直接進む』のではなく『逆方向に後退して勢いをつける』"
        "ことが最適方策の一部であるため、位置(ゴールへの近さ)だけを評価するnaiveな報酬形成は、"
        "後退行動を(ポテンシャルベースなので理論上は方策自体を歪めないとはいえ)学習初期に"
        "評価しにくくする可能性がある一方、エネルギー(位置+速度の2乗)を評価するenergy形成は"
        "後退による加速も正しく高く評価できるため、タスクの構造に適した報酬形成になっている。"
        "報酬形成はポテンシャルベースであれば理論上『最適方策を変えない』ことが保証されているが、"
        "どのポテンシャル関数を選ぶかによって学習の『速さ』は大きく変わりうるという、"
        "理論的な安全性と実践上の設計難易度は別問題であることを実測を通じて確認できた。"
    )


if __name__ == "__main__":
    main()
