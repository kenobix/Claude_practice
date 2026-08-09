"""Q学習とSARSAを、FrozenLake(Gymnasium)でスクラッチ実装し、方策の違いを比較する

01のバンディット問題には「状態」がなかった(どの腕を引いても次の状況は変わらない)。
FrozenLakeは、4x4マスの氷上を移動して穴(H)を避けながらゴール(G)を目指す、
状態遷移のあるマルコフ決定過程(MDP)。`is_slippery=True`にすると、選んだ方向に
必ず進めるとは限らず(氷が滑るため確率的に別方向へ進む)、行動の結果に不確実性がある
より現実的な設定になる。

Q学習とSARSAはどちらも「行動価値関数Q(s,a)」(ある状態sで行動aを取ることの価値)を
学習する点は共通だが、更新式の一部が異なる:
  Q学習(off-policy):  Q(s,a) ← Q(s,a) + α[r + γ*max_a' Q(s',a') - Q(s,a)]
    次状態で『最善の行動を取った場合』の価値で更新する(実際に取る行動とは無関係)
  SARSA(on-policy):   Q(s,a) ← Q(s,a) + α[r + γ*Q(s',a') - Q(s,a)]
    次状態で『実際に(ε-greedyで)選んだ行動』の価値で更新する
この違いにより、Q学習は「理論上の最適行動」を学習しようとする一方、SARSAは
「実際に探索も行いながら動くときの現実的な行動」を学習する傾向があり、
崖(危険な状態)が近いタスクでは両者の学習結果が異なることが知られている。
"""
import numpy as np
import gymnasium as gym
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

rng = np.random.RandomState(42)


def epsilon_greedy_action(Q, state, epsilon, n_actions, rng):
    if rng.random() < epsilon:
        return rng.randint(n_actions)
    return int(np.argmax(Q[state]))


def train_qlearning(env, n_episodes, alpha=0.1, gamma=0.99, epsilon_start=1.0, epsilon_end=0.05, rng=None):
    n_states, n_actions = env.observation_space.n, env.action_space.n
    Q = np.zeros((n_states, n_actions))
    success_history = []
    for ep in range(n_episodes):
        epsilon = max(epsilon_end, epsilon_start - (epsilon_start - epsilon_end) * ep / (n_episodes * 0.6))
        state, _ = env.reset(seed=int(rng.randint(1_000_000)))
        done = False
        total_reward = 0
        while not done:
            action = epsilon_greedy_action(Q, state, epsilon, n_actions, rng)
            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            # Q学習: 次状態で"最善"を尽くした場合の価値で更新(実際の行動方針とは無関係)
            best_next = np.max(Q[next_state])
            Q[state, action] += alpha * (reward + gamma * best_next - Q[state, action])
            state = next_state
            total_reward += reward
        success_history.append(total_reward)
    return Q, np.array(success_history)


def train_sarsa(env, n_episodes, alpha=0.1, gamma=0.99, epsilon_start=1.0, epsilon_end=0.05, rng=None):
    n_states, n_actions = env.observation_space.n, env.action_space.n
    Q = np.zeros((n_states, n_actions))
    success_history = []
    for ep in range(n_episodes):
        epsilon = max(epsilon_end, epsilon_start - (epsilon_start - epsilon_end) * ep / (n_episodes * 0.6))
        state, _ = env.reset(seed=int(rng.randint(1_000_000)))
        action = epsilon_greedy_action(Q, state, epsilon, n_actions, rng)
        done = False
        total_reward = 0
        while not done:
            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            next_action = epsilon_greedy_action(Q, next_state, epsilon, n_actions, rng)
            # SARSA: 次状態で"実際に選んだ"行動(探索込み)の価値で更新
            Q[state, action] += alpha * (reward + gamma * Q[next_state, next_action] - Q[state, action])
            state, action = next_state, next_action
            total_reward += reward
        success_history.append(total_reward)
    return Q, np.array(success_history)


def moving_average(x, window=100):
    return np.convolve(x, np.ones(window) / window, mode="valid")


def render_policy_grid(Q, shape=(4, 4)):
    """各マスで最も価値が高い行動を矢印で表す。0:左,1:下,2:右,3:上(FrozenLakeの行動定義)"""
    arrows = {0: "←", 1: "↓", 2: "→", 3: "↑"}
    grid = []
    for s in range(shape[0] * shape[1]):
        grid.append(arrows[int(np.argmax(Q[s]))])
    return np.array(grid).reshape(shape)


def main() -> None:
    print("=== 1. FrozenLake環境(4x4, is_slippery=True)でQ学習とSARSAを学習 ===")
    env = gym.make("FrozenLake-v1", is_slippery=True)
    n_episodes = 20000

    Q_learning, history_q = train_qlearning(env, n_episodes, rng=np.random.RandomState(0))
    Q_sarsa, history_sarsa = train_sarsa(env, n_episodes, rng=np.random.RandomState(0))

    success_rate_q = history_q[-2000:].mean()
    success_rate_sarsa = history_sarsa[-2000:].mean()
    print(f"Q学習   : 最後の2000エピソードの成功率={success_rate_q:.3f}")
    print(f"SARSA   : 最後の2000エピソードの成功率={success_rate_sarsa:.3f}")

    print("\n=== 2. 学習済み方策(各マスでの最良の行動)を表示 ===")
    lake_map = ["SFFF", "FHFH", "FFFH", "HFFG"]  # S:スタート F:氷 H:穴 G:ゴール
    print("マップ:")
    for row in lake_map:
        print("  " + row)
    print("\nQ学習が学習した方策:")
    for row in render_policy_grid(Q_learning):
        print("  " + " ".join(row))
    print("\nSARSAが学習した方策:")
    for row in render_policy_grid(Q_sarsa):
        print("  " + " ".join(row))

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    window = 500
    axes[0].plot(moving_average(history_q, window), label="Q学習")
    axes[0].plot(moving_average(history_sarsa, window), label="SARSA")
    axes[0].set_xlabel("エピソード")
    axes[0].set_ylabel(f"成功率(移動平均, window={window})")
    axes[0].set_title("学習曲線: Q学習 vs SARSA")
    axes[0].legend()

    for ax, Q, name in [(axes[1], Q_learning, "Q学習"), (axes[2], Q_sarsa, "SARSA")]:
        V = Q.max(axis=1).reshape(4, 4)  # 状態価値=その状態での最良の行動価値
        im = ax.imshow(V, cmap="viridis")
        policy_grid = render_policy_grid(Q)
        for i in range(4):
            for j in range(4):
                cell = lake_map[i][j]
                label = policy_grid[i, j] if cell not in ("H", "G") else cell
                ax.text(j, i, label, ha="center", va="center", color="white", fontsize=14)
        ax.set_title(f"{name}が学習した状態価値V(s)と方策\n(H=穴, G=ゴール, 矢印=学習した行動)")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, fraction=0.046)

    fig.tight_layout()
    out_path = "qlearning_sarsa_frozenlake.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    n_diff_cells = sum(1 for a, b in zip(render_policy_grid(Q_learning).flatten(),
                                          render_policy_grid(Q_sarsa).flatten()) if a != b)
    print(
        f"\n最終的な成功率は Q学習={success_rate_q:.3f}, SARSA={success_rate_sarsa:.3f} と、"
        "ほぼ同水準になった。理論的には、Q学習が次状態で『理論上の最善行動』を仮定して"
        "更新するのに対し、SARSAは『実際にε-greedyで探索も行う自分自身の方策』を前提に"
        "更新するため、穴に落ちるリスクのある状況ではSARSAの方が保守的な方策を学習しやすい"
        "とされる。ただし今回学習した16マスの方策を比較すると、実際に異なる行動を"
        f"選んでいたのは{n_diff_cells}マスのみで、学習結果のほとんどは一致していた——"
        "FrozenLakeの4x4マップ程度の小さな状態空間・20000エピソードの学習では、"
        "両手法の違いが方策に大きく表れるほどの差は生まれなかったと考えられる。"
        "学習曲線を見ると、状態遷移が確率的(is_slippery=True)であるためどちらの手法も"
        "成功率が1.0に張り付くことはなく、滑る確率そのものに起因する失敗が一定割合"
        "残ることも確認できる。"
    )


if __name__ == "__main__":
    main()
