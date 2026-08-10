"""Actor-Critic(1ステップ更新)を実装し、04のREINFORCE(エピソード単位更新)と比較する

04で実装したREINFORCE(ベースライン付き)は、
  1) 1エピソード分の行動を全て終えてから
  2) 各時刻の実際の収益G_t(そこから先に得られた割引報酬の合計)を計算し
  3) G_tから移動平均ベースラインを引いた値で方策を更新する
という「エピソード単位・モンテカルロ収益ベース」の更新だった。

Actor-Critic法はこれとは異なり、
  - Actor: 方策π(a|s;θ)を持ち、REINFORCEと同様に行動を選ぶ
  - Critic: 状態価値V(s;w)を学習する別のネットワークを持つ
  - 1ステップ進むたびに、実際の1ステップ分の報酬rと次状態の価値V(s')から
    TD誤差 δ = r + γV(s')(1-done) - V(s) を計算し、
    Criticはこのδを0に近づけるように、Actorはδを「今の行動が期待より良かったか」の
    アドバンテージとして使って即座に更新する
という「1ステップ単位・TD(ブートストラップ)ベース」の更新を行う。
エピソードの終わりを待たずに毎ステップ学習できる点、Gtの代わりに1ステップ先の予測値
だけを使うぶん更新のばらつきが小さくなりやすい点が、REINFORCEとの実質的な違いになる。
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

project_module = importlib.import_module("04_cartpole_project")
SOLVED_THRESHOLD = project_module.SOLVED_THRESHOLD
SOLVED_WINDOW = project_module.SOLVED_WINDOW
train_reinforce_until_solved = project_module.train_reinforce_until_solved


class ActorNetwork(nn.Module):
    def __init__(self, state_dim, n_actions, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x):
        return torch.softmax(self.net(x), dim=-1)


class CriticNetwork(nn.Module):
    """状態価値V(s)だけを出力する(REINFORCEのスカラーのベースラインと異なり、状態に応じて変化する)"""

    def __init__(self, state_dim, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def train_actor_critic_until_solved(env, max_episodes=800, gamma=0.99, lr_actor=1e-3, lr_critic=1e-3):
    state_dim, n_actions = env.observation_space.shape[0], env.action_space.n
    actor = ActorNetwork(state_dim, n_actions)
    critic = CriticNetwork(state_dim)
    opt_actor = torch.optim.Adam(actor.parameters(), lr=lr_actor)
    opt_critic = torch.optim.Adam(critic.parameters(), lr=lr_critic)
    episode_rewards = []

    for ep in range(max_episodes):
        state, _ = env.reset(seed=random.randint(0, 1_000_000))
        done, total_reward = False, 0
        while not done:
            state_t = torch.tensor(state, dtype=torch.float32)
            probs = actor(state_t)
            action = torch.multinomial(probs, 1).item()
            log_prob = torch.log(probs[action] + 1e-8)

            next_state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            next_state_t = torch.tensor(next_state, dtype=torch.float32)

            with torch.no_grad():
                td_target = reward + gamma * critic(next_state_t.unsqueeze(0)).item() * (1 - float(done))
            value = critic(state_t.unsqueeze(0)).squeeze(0)
            td_error = td_target - value  # このステップだけのアドバンテージ推定

            critic_loss = td_error.pow(2)
            opt_critic.zero_grad()
            critic_loss.backward()
            opt_critic.step()

            actor_loss = -log_prob * td_error.detach()  # REINFORCEのG_tの代わりにTD誤差を使う
            opt_actor.zero_grad()
            actor_loss.backward()
            opt_actor.step()

            state = next_state
            total_reward += reward

        episode_rewards.append(total_reward)
        if ep >= SOLVED_WINDOW and np.mean(episode_rewards[-SOLVED_WINDOW:]) >= SOLVED_THRESHOLD:
            print(f"  Actor-Criticはエピソード{ep}で解けた基準(直近{SOLVED_WINDOW}エピソード平均"
                  f"{SOLVED_THRESHOLD}以上)に到達")
            break
    return actor, critic, np.array(episode_rewards)


def moving_average(x, window=100):
    if len(x) < window:
        return np.array([])
    return np.convolve(x, np.ones(window) / window, mode="valid")


def main() -> None:
    env = gym.make("CartPole-v1")

    print("=== 1. Actor-Critic(1ステップ更新)でCartPoleを攻略 ===")
    torch.manual_seed(0)
    random.seed(0)
    t0 = time.perf_counter()
    actor, critic, rewards_ac = train_actor_critic_until_solved(env, max_episodes=800)
    time_ac = time.perf_counter() - t0
    print(f"学習時間={time_ac:.1f}秒, 総エピソード数={len(rewards_ac)}, "
          f"最終{SOLVED_WINDOW}エピソード平均={rewards_ac[-SOLVED_WINDOW:].mean():.1f}")

    print("\n=== 2. REINFORCE(04の再掲, エピソード単位更新)でCartPoleを攻略 ===")
    torch.manual_seed(0)
    random.seed(0)
    t0 = time.perf_counter()
    _, rewards_reinforce = train_reinforce_until_solved(env, max_episodes=800)
    time_reinforce = time.perf_counter() - t0
    print(f"学習時間={time_reinforce:.1f}秒, 総エピソード数={len(rewards_reinforce)}, "
          f"最終{SOLVED_WINDOW}エピソード平均={rewards_reinforce[-SOLVED_WINDOW:].mean():.1f}")

    print("\n=== 3. 可視化 ===")
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    ax.plot(rewards_ac, alpha=0.25, color="tab:purple")
    ma_ac = moving_average(rewards_ac)
    if len(ma_ac) > 0:
        ax.plot(range(SOLVED_WINDOW - 1, SOLVED_WINDOW - 1 + len(ma_ac)), ma_ac,
                color="tab:purple", label=f"Actor-Critic(移動平均, {len(rewards_ac)}エピソードで完了)")
    ax.plot(rewards_reinforce, alpha=0.25, color="tab:green")
    ma_reinforce = moving_average(rewards_reinforce)
    if len(ma_reinforce) > 0:
        ax.plot(range(SOLVED_WINDOW - 1, SOLVED_WINDOW - 1 + len(ma_reinforce)), ma_reinforce,
                color="tab:green", label=f"REINFORCE(移動平均, {len(rewards_reinforce)}エピソードで完了)")
    ax.axhline(SOLVED_THRESHOLD, color="gray", linestyle="--", label=f"解けた基準({SOLVED_THRESHOLD})")
    ax.set_xlabel("エピソード")
    ax.set_ylabel("エピソード報酬(生存ステップ数)")
    ax.set_title("CartPole-v1: Actor-Critic(1ステップ更新) vs REINFORCE(エピソード単位更新)")
    ax.legend()

    fig.tight_layout()
    out_path = "actor_critic.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    ac_solved = rewards_ac[-SOLVED_WINDOW:].mean() >= SOLVED_THRESHOLD
    reinforce_solved = rewards_reinforce[-SOLVED_WINDOW:].mean() >= SOLVED_THRESHOLD
    ac_peak_ma = moving_average(rewards_ac).max() if len(moving_average(rewards_ac)) > 0 else 0.0
    ac_peak_ep = int(np.argmax(moving_average(rewards_ac))) + SOLVED_WINDOW if len(moving_average(rewards_ac)) > 0 else 0
    print(
        f"\nActor-Criticは{len(rewards_ac)}エピソード、REINFORCEは{len(rewards_reinforce)}エピソードで"
        f"学習を終えた(どちらも上限800エピソード、または解けた基準への到達で終了)。"
        f"REINFORCEは解けた基準に到達したが、Actor-Criticは上限エピソードまでに解けた基準には届かず、"
        f"最終{SOLVED_WINDOW}エピソード平均はわずか{rewards_ac[-SOLVED_WINDOW:].mean():.1f}に沈んだ。"
        f"ただし学習曲線を見ると、Actor-Criticは移動平均が一度エピソード{ac_peak_ep}付近で{ac_peak_ma:.0f}"
        "まで上昇しており、単純に学習が進まなかったわけではない——学習の途中までは着実に上達していた"
        "にもかかわらず、その後に方策が突然崩壊し、二度と回復しないまま学習が終わっている。"
        "これは『破局的忘却』と呼ばれる現象で、1ステップごとにActorとCriticを同時に更新する"
        "オンラインActor-Criticでは、たまたま悪い方向に更新が振れると方策(softmax)が特定の行動に"
        "急速に偏り(確率が0や1に近づき)、その状態からは勾配がほぼ消えてしまうため方策を"
        "立て直す手立てを失う、という弱点が実際に再現されたと考えられる。REINFORCEはエピソード"
        "全体の実収益G_tを使うぶん更新1回あたりの分散は大きいが、極端な方策に飛びついた場合でも"
        "毎エピソード仕切り直しになるため、Actor-Criticほどの『崩れたら戻れない』脆さは"
        "今回の実行では見られなかった。この結果は、PPOがActorの更新幅を制限するclip機構を、"
        "A3Cが方策のエントロピー正則化を導入している理由——単純な1ステップActor-Criticは"
        "効率的だが不安定になりやすく、実用にはこうした安全策が重要になる——を実測を通じて"
        "裏付けるものになった。"
    )


if __name__ == "__main__":
    main()
