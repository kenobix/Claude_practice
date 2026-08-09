"""ミニプロジェクト: CartPoleをDQNで攻略し、方策勾配法(REINFORCE)とも比較する

03ではDQNの学習が(単一の乱数シードでは)非常に高分散になりうることを確認した。
このミニプロジェクトでは、
  (A) DQN(価値ベース手法): Q(s,a)を学習し、各状態で最も価値の高い行動を選ぶ
  (B) REINFORCE(方策勾配法): 状態から直接「行動の確率分布」を出力する方策
      ネットワークπ(a|s;θ)を学習する。行動を選ぶたびに、その後の収益(累積報酬)が
      高ければその行動を取る確率を上げ、低ければ下げる、というシンプルな更新則
      (∇θ J(θ) = E[∇θ log π(a|s;θ) * G]、Gはエピソードの収益)を使う
の2つの流派を、同じCartPole-v1タスクで比較する。DQNは「価値を経由して間接的に
行動を決める」のに対し、REINFORCEは「行動の確率そのものを直接最適化する」という
発想の違いがあり、これが強化学習の2大流派(価値ベース法 / 方策ベース法)の違いになっている。
CartPole-v1の「解けた」基準として、直近100エピソードの平均報酬が475以上を採用する。
"""
import random
import time
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import gymnasium as gym
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

SOLVED_THRESHOLD = 475
SOLVED_WINDOW = 100


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


class ReplayBuffer:
    def __init__(self, capacity=20000):
        self.buffer = deque(maxlen=capacity)

    def push(self, s, a, r, s2, done):
        self.buffer.append((s, a, r, s2, done))

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        s, a, r, s2, done = zip(*batch)
        return (torch.tensor(np.array(s), dtype=torch.float32),
                torch.tensor(a, dtype=torch.long),
                torch.tensor(r, dtype=torch.float32),
                torch.tensor(np.array(s2), dtype=torch.float32),
                torch.tensor(done, dtype=torch.float32))

    def __len__(self):
        return len(self.buffer)


class PolicyNetwork(nn.Module):
    def __init__(self, state_dim, n_actions, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x):
        return torch.softmax(self.net(x), dim=-1)


def train_dqn_until_solved(env, max_episodes=800, gamma=0.99, lr=1e-3, batch_size=64, target_update_freq=10):
    state_dim, n_actions = env.observation_space.shape[0], env.action_space.n
    q_net = QNetwork(state_dim, n_actions)
    target_net = QNetwork(state_dim, n_actions)
    target_net.load_state_dict(q_net.state_dict())
    optimizer = torch.optim.Adam(q_net.parameters(), lr=lr)
    buffer = ReplayBuffer()

    epsilon_start, epsilon_end, epsilon_decay_episodes = 1.0, 0.02, max_episodes * 0.5
    episode_rewards = []

    for ep in range(max_episodes):
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
                    y = r + gamma * target_net(s2).max(1).values * (1 - d)
                q_pred = q_net(s).gather(1, a.unsqueeze(1)).squeeze(1)
                loss = nn.functional.mse_loss(q_pred, y)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        episode_rewards.append(total_reward)
        if ep % target_update_freq == 0:
            target_net.load_state_dict(q_net.state_dict())

        if ep >= SOLVED_WINDOW and np.mean(episode_rewards[-SOLVED_WINDOW:]) >= SOLVED_THRESHOLD:
            print(f"  DQNはエピソード{ep}で解けた基準(直近{SOLVED_WINDOW}エピソード平均"
                  f"{SOLVED_THRESHOLD}以上)に到達")
            break
    return q_net, np.array(episode_rewards)


def train_reinforce_until_solved(env, max_episodes=800, gamma=0.99, lr=1e-3):
    state_dim, n_actions = env.observation_space.shape[0], env.action_space.n
    policy = PolicyNetwork(state_dim, n_actions)
    optimizer = torch.optim.Adam(policy.parameters(), lr=lr)
    episode_rewards = []
    return_baseline = deque(maxlen=100)  # 直近の収益の平均をベースラインとして使い、分散を減らす

    for ep in range(max_episodes):
        state, _ = env.reset(seed=random.randint(0, 1_000_000))
        log_probs, rewards = [], []
        done = False
        while not done:
            state_t = torch.tensor(state, dtype=torch.float32)
            probs = policy(state_t)
            action = torch.multinomial(probs, 1).item()
            log_probs.append(torch.log(probs[action] + 1e-8))
            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            rewards.append(reward)
            state = next_state

        # 割引累積収益G_tを、エピソード末尾から逆順に計算する
        G = 0.0
        returns = []
        for r in reversed(rewards):
            G = r + gamma * G
            returns.insert(0, G)
        returns = torch.tensor(returns, dtype=torch.float32)

        baseline = np.mean(return_baseline) if return_baseline else 0.0
        advantage = returns - baseline
        loss = -torch.sum(torch.stack(log_probs) * advantage)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_reward = sum(rewards)
        episode_rewards.append(total_reward)
        return_baseline.append(returns[0].item())

        if ep >= SOLVED_WINDOW and np.mean(episode_rewards[-SOLVED_WINDOW:]) >= SOLVED_THRESHOLD:
            print(f"  REINFORCEはエピソード{ep}で解けた基準(直近{SOLVED_WINDOW}エピソード平均"
                  f"{SOLVED_THRESHOLD}以上)に到達")
            break
    return policy, np.array(episode_rewards)


def moving_average(x, window=100):
    if len(x) < window:
        return np.array([])
    return np.convolve(x, np.ones(window) / window, mode="valid")


def main() -> None:
    torch.manual_seed(0)
    random.seed(0)
    print("=== 1. DQN(価値ベース手法)でCartPoleを攻略 ===")
    env = gym.make("CartPole-v1")
    t0 = time.perf_counter()
    q_net, rewards_dqn = train_dqn_until_solved(env, max_episodes=800)
    time_dqn = time.perf_counter() - t0
    print(f"学習時間={time_dqn:.1f}秒, 総エピソード数={len(rewards_dqn)}, "
          f"最終{SOLVED_WINDOW}エピソード平均={rewards_dqn[-SOLVED_WINDOW:].mean():.1f}")

    torch.manual_seed(0)
    random.seed(0)
    print("\n=== 2. REINFORCE(方策勾配法)でCartPoleを攻略 ===")
    t0 = time.perf_counter()
    policy, rewards_reinforce = train_reinforce_until_solved(env, max_episodes=800)
    time_reinforce = time.perf_counter() - t0
    print(f"学習時間={time_reinforce:.1f}秒, 総エピソード数={len(rewards_reinforce)}, "
          f"最終{SOLVED_WINDOW}エピソード平均={rewards_reinforce[-SOLVED_WINDOW:].mean():.1f}")

    print("\n=== 3. 可視化 ===")
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    ax.plot(rewards_dqn, alpha=0.25, color="tab:blue")
    ma_dqn = moving_average(rewards_dqn)
    if len(ma_dqn) > 0:
        ax.plot(range(SOLVED_WINDOW - 1, SOLVED_WINDOW - 1 + len(ma_dqn)), ma_dqn,
                color="tab:blue", label=f"DQN(移動平均, {len(rewards_dqn)}エピソードで完了)")
    ax.plot(rewards_reinforce, alpha=0.25, color="tab:green")
    ma_reinforce = moving_average(rewards_reinforce)
    if len(ma_reinforce) > 0:
        ax.plot(range(SOLVED_WINDOW - 1, SOLVED_WINDOW - 1 + len(ma_reinforce)), ma_reinforce,
                color="tab:green", label=f"REINFORCE(移動平均, {len(rewards_reinforce)}エピソードで完了)")
    ax.axhline(SOLVED_THRESHOLD, color="gray", linestyle="--", label=f"解けた基準({SOLVED_THRESHOLD})")
    ax.set_xlabel("エピソード")
    ax.set_ylabel("エピソード報酬(生存ステップ数)")
    ax.set_title("CartPole-v1: DQN(価値ベース) vs REINFORCE(方策勾配法)")
    ax.legend()

    fig.tight_layout()
    out_path = "cartpole_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    dqn_solved = rewards_dqn[-SOLVED_WINDOW:].mean() >= SOLVED_THRESHOLD
    reinforce_solved = rewards_reinforce[-SOLVED_WINDOW:].mean() >= SOLVED_THRESHOLD
    print(
        f"\nDQNは{len(rewards_dqn)}エピソード、REINFORCEは{len(rewards_reinforce)}エピソードで"
        f"学習を終えた(どちらも上限800エピソード、または解けた基準への到達で終了)。"
        f"DQNは{'解けた基準に到達した' if dqn_solved else '上限エピソードまでに解けた基準には届かなかった'}。"
        f"REINFORCEは{'解けた基準に到達した' if reinforce_solved else '上限エピソードまでに解けた基準には届かなかった'}。"
        "DQNはExperience ReplayとTarget Networkにより比較的安定して学習が進む一方、"
        "1ステップごとに学習できるため、方策の更新頻度が高い。REINFORCEは1エピソード"
        "終わるまで学習を待つ必要があり、また『たまたま運が良かっただけの行動』も"
        "そのエピソードの収益が高ければ強化されてしまうため、DQNより学習曲線の分散が"
        "大きくなりやすいことが知られている(今回はベースライン減算で分散低減を試みている"
        "が、Actor-Criticのように状態ごとの価値関数を学習してより精緻にベースラインを"
        "推定する手法と比べると限定的)。価値ベース法(DQN)と方策ベース法(REINFORCE)は"
        "どちらも『試行錯誤から学ぶ』という強化学習の枠組みを共有しつつ、"
        "『何を直接学習するか』という設計思想が異なる代表例であることを、"
        "同じタスクでの実装・実行を通じて確認できた。"
    )


if __name__ == "__main__":
    main()
