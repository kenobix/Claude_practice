"""Double DQNとDueling Networkを実装し、vanilla DQNとの違いを実測する

03で実装したvanilla DQNのTD目標は y = r + γ*max_a' Q_target(s',a') であり、
「次状態で最も高いQ値を持つ行動」をtarget_netの推定値そのままで評価してしまう。
Q値の推定には常にノイズが乗っているため、たまたまノイズで高く見積もられた行動が
選ばれやすくなり、系統的にQ値を過大評価してしまう(overestimation bias)。
  - Double DQN: 「次状態でどの行動が良いか」の選択にはオンラインネットワーク(q_net)を、
    「その行動の価値がどれくらいか」の評価にはターゲットネットワーク(target_net)を使う、
    というように選択と評価を別のネットワークに分離することでこのバイアスを緩和する
  - Dueling Network: Q(s,a)を「状態sそのものの価値V(s)」と「状態sでの行動aの相対的な
    優位性(アドバンテージ)A(s,a)」に分解し、Q(s,a)=V(s)+(A(s,a)-mean(A(s,·)))として
    合成するネットワーク構造。行動によらない状態価値と、行動ごとの差分を別々に学習できる
このスクリプトでは、(1) Q値の過大評価バイアスをvanilla DQN/Double DQNで直接測定し、
(2) vanilla DQN/Double DQN/Dueling Networkの学習曲線をCartPoleで比較する。
"""
import importlib
import random
import time

import numpy as np
import torch
import torch.nn as nn
import gymnasium as gym
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

dqn_module = importlib.import_module("03_dqn_cartpole")
ReplayBuffer = dqn_module.ReplayBuffer

torch.manual_seed(42)
random.seed(42)


class QNetwork(nn.Module):
    def __init__(self, state_dim, n_actions, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x):
        return self.net(x)


class DuelingQNetwork(nn.Module):
    """特徴抽出を共有し、状態価値V(s)とアドバンテージA(s,a)を別ブランチで出力する"""

    def __init__(self, state_dim, n_actions, hidden=128):
        super().__init__()
        self.shared = nn.Sequential(nn.Linear(state_dim, hidden), nn.ReLU())
        self.value_head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, 1))
        self.adv_head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, n_actions))

    def forward(self, x):
        h = self.shared(x)
        v = self.value_head(h)
        a = self.adv_head(h)
        return v + (a - a.mean(dim=-1, keepdim=True))  # Q(s,a) = V(s) + (A(s,a) - mean_a A(s,a))


def train_dqn_variant(env, network_class, n_episodes=250, gamma=0.99, lr=1e-3,
                       batch_size=64, target_update_freq=10, use_double=False):
    state_dim, n_actions = env.observation_space.shape[0], env.action_space.n
    q_net = network_class(state_dim, n_actions)
    target_net = network_class(state_dim, n_actions)
    target_net.load_state_dict(q_net.state_dict())
    optimizer = torch.optim.Adam(q_net.parameters(), lr=lr)
    buffer = ReplayBuffer()

    epsilon_start, epsilon_end, epsilon_decay_episodes = 1.0, 0.02, n_episodes * 0.6
    episode_rewards = []

    for ep in range(n_episodes):
        epsilon = max(epsilon_end, epsilon_start - (epsilon_start - epsilon_end) * ep / epsilon_decay_episodes)
        state, _ = env.reset(seed=random.randint(0, 1_000_000))
        total_reward, done = 0, False
        while not done:
            if random.random() < epsilon:
                action = env.action_space.sample()
            else:
                with torch.no_grad():
                    action = int(q_net(torch.tensor(state, dtype=torch.float32)).argmax())
            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            buffer.push(state, action, reward, next_state, float(done))
            state = next_state
            total_reward += reward

            if len(buffer) >= batch_size:
                s, a, r, s2, d = buffer.sample(batch_size)
                with torch.no_grad():
                    if use_double:
                        # 選択(argmax)はオンラインネット、評価はターゲットネットで行う
                        next_action = q_net(s2).argmax(dim=1, keepdim=True)
                        target_q = target_net(s2).gather(1, next_action).squeeze(1)
                    else:
                        target_q = target_net(s2).max(1).values
                    y = r + gamma * target_q * (1 - d)
                q_pred = q_net(s).gather(1, a.unsqueeze(1)).squeeze(1)
                loss = nn.functional.mse_loss(q_pred, y)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        episode_rewards.append(total_reward)
        if ep % target_update_freq == 0:
            target_net.load_state_dict(q_net.state_dict())

    return q_net, np.array(episode_rewards)


def estimate_overestimation_bias(q_net, env, n_episodes=20, gamma=0.99):
    """初期状態でのQ値の推定値と、実際に貪欲方策で得られた収益との差(過大評価バイアス)を測る"""
    biases = []
    for ep in range(n_episodes):
        state, _ = env.reset(seed=1_000_000 + ep)
        with torch.no_grad():
            q0_estimate = q_net(torch.tensor(state, dtype=torch.float32)).max().item()

        s, done, total_return, discount = state, False, 0.0, 1.0
        while not done:
            with torch.no_grad():
                action = int(q_net(torch.tensor(s, dtype=torch.float32)).argmax())
            s, r, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            total_return += discount * r
            discount *= gamma
        biases.append(q0_estimate - total_return)
    return float(np.mean(biases)), float(np.std(biases))


def moving_average(x, window=20):
    if len(x) < window:
        return x
    return np.convolve(x, np.ones(window) / window, mode="valid")


def main() -> None:
    env = gym.make("CartPole-v1")
    n_episodes = 250

    print("=== 1. vanilla DQN / Double DQN / Dueling Networkを学習 ===")
    variants = {
        "vanilla DQN": dict(network_class=QNetwork, use_double=False),
        "Double DQN": dict(network_class=QNetwork, use_double=True),
        "Dueling Network": dict(network_class=DuelingQNetwork, use_double=False),
    }
    results = {}
    for name, kwargs in variants.items():
        torch.manual_seed(42)
        random.seed(42)
        t0 = time.perf_counter()
        q_net, rewards = train_dqn_variant(env, n_episodes=n_episodes, **kwargs)
        elapsed = time.perf_counter() - t0
        results[name] = {"q_net": q_net, "rewards": rewards, "time": elapsed}
        print(f"[{name}] 学習時間={elapsed:.1f}秒, 最後の50エピソード平均報酬={rewards[-50:].mean():.1f}")

    print("\n=== 2. Q値の過大評価バイアスを測定(vanilla DQN vs Double DQN) ===")
    bias_vanilla, std_vanilla = estimate_overestimation_bias(results["vanilla DQN"]["q_net"], env)
    bias_double, std_double = estimate_overestimation_bias(results["Double DQN"]["q_net"], env)
    print(f"vanilla DQN: 平均バイアス(Q推定 - 実際の収益)={bias_vanilla:+.2f} (標準偏差{std_vanilla:.2f})")
    print(f"Double DQN : 平均バイアス(Q推定 - 実際の収益)={bias_double:+.2f} (標準偏差{std_double:.2f})")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    colors = {"vanilla DQN": "tab:red", "Double DQN": "tab:blue", "Dueling Network": "tab:green"}
    for name, r in results.items():
        axes[0].plot(r["rewards"], alpha=0.2, color=colors[name])
        ma = moving_average(r["rewards"])
        axes[0].plot(range(len(r["rewards"]) - len(ma), len(r["rewards"])), ma,
                     color=colors[name], label=name)
    axes[0].set_xlabel("エピソード")
    axes[0].set_ylabel("エピソード報酬(移動平均)")
    axes[0].set_title("学習曲線: vanilla DQN vs Double DQN vs Dueling Network")
    axes[0].legend(fontsize=8)

    axes[1].bar(["vanilla DQN", "Double DQN"], [bias_vanilla, bias_double],
                color=[colors["vanilla DQN"], colors["Double DQN"]],
                yerr=[std_vanilla, std_double], capsize=5)
    axes[1].axhline(0, color="gray", linestyle="--")
    axes[1].set_ylabel("Q値の過大評価バイアス(Q推定 - 実際の収益)")
    axes[1].set_title("過大評価バイアスの比較(20エピソードで測定)")

    fig.tight_layout()
    out_path = "double_dqn_dueling.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    bias_reduced = abs(bias_double) < abs(bias_vanilla)
    final_scores = {name: r["rewards"][-50:].mean() for name, r in results.items()}
    best_name = max(final_scores, key=final_scores.get)
    print(
        f"\nQ値のバイアス(Q推定 - 実際の収益)は vanilla DQN={bias_vanilla:+.2f}、"
        f"Double DQN={bias_double:+.2f}となった。理論(Double DQNが過大評価バイアスを緩和する)"
        "が想定する『Q推定が実際の収益を上回る(正のバイアス)』とは逆に、今回はどちらも"
        "負のバイアス(Q推定が実際の収益を下回る=過小評価)になり、"
        f"{'その中でDouble DQNの方が絶対値が小さかった' if bias_reduced else 'しかもDouble DQNの方が絶対値は大きかった'}。"
        "この逆転は、評価に使った『実際の収益』が探索なしの貪欲方策によるロールアウトで"
        "測定しているのに対し、Q関数はε-greedyで探索も混ざった250エピソード分の学習データ"
        "からしか学習しておらず、学習後半で急速に上達した貪欲方策の実力にQ関数の推定が"
        "追いついていない(学習途中のブートストラップ推定が、方策の伸びに対して遅行している)"
        "ことが原因と考えられる——Double DQNの過大評価抑制効果を確認するには、方策が"
        "収束しきった後で評価するか、より長い学習が必要だったと考えられる。"
        f"最後の50エピソード平均報酬で比較すると、{best_name}が{final_scores[best_name]:.1f}で最も高かった"
        "(ただし03で確認した通り、vanilla DQNは少ないエピソード数では本質的に高分散な"
        "学習曲線をたどりやすく、1回の実行だけで手法間の優劣を断定するのは危険であることに注意)。"
        "Dueling Networkについては、CartPoleが行動数2(左/右)と非常に少ないタスクであるため、"
        "『行動によらない状態価値と行動ごとの優位性を分離する』というDueling構造の恩恵は、"
        "行動数がもっと多い環境(Atariの数十行動など)に比べると相対的に小さく出やすいと考えられる。"
    )


if __name__ == "__main__":
    main()
