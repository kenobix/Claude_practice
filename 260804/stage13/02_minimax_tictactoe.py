"""Mini-Max法を三目並べにスクラッチ実装し、「最適な対戦相手には勝てないゲーム」であることを確認する

Mini-Max法は、ゲーム木を末端(勝敗が決まった局面)まで再帰的に展開し、
  - 自分の手番(maximizing player): 子局面のうち評価値が最大のものを選ぶ
  - 相手の手番(minimizing player): 子局面のうち評価値が最小のものを選ぶ
という前提で評価値を下から上に伝播させることで、「相手が最善を尽くしても
どうなるか」という最悪ケースを保証する最適な一手を求めるアルゴリズム。
三目並べは状態数が小さい(高々9!=362880通りの着手順)ため、枝刈りなしの
Mini-Max法でも現実的な時間で全探索できる。評価値は常にXの視点で
+1(Xの勝ち)/-1(Oの勝ち)/0(引き分け)とする。
"""
import random
import time

import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401
from tictactoe import (
    initial_state, available_moves, apply_move, other_player,
    check_winner, print_board,
)

node_counter = {"count": 0}


def minimax(state, player):
    """状態stateでplayerが手番のときの(Xにとっての評価値, 最善手)を返す"""
    node_counter["count"] += 1
    winner = check_winner(state)
    if winner == "X":
        return 1, None
    if winner == "O":
        return -1, None
    if winner == "Draw":
        return 0, None

    maximizing = player == "X"
    best_score = -2 if maximizing else 2
    best_move = None
    for move in available_moves(state):
        next_state = apply_move(state, move, player)
        score, _ = minimax(next_state, other_player(player))
        if (maximizing and score > best_score) or (not maximizing and score < best_score):
            best_score, best_move = score, move
    return best_score, best_move


def random_move(state):
    return random.choice(available_moves(state))


def play_game(agent_x, agent_o, verbose=False):
    """agent_x/agent_oは、盤面を受け取り着手位置を返す関数"""
    state = initial_state()
    player = "X"
    while check_winner(state) is None:
        move = agent_x(state) if player == "X" else agent_o(state)
        state = apply_move(state, move, player)
        if verbose:
            print_board(state)
            print()
        player = other_player(player)
    return check_winner(state)


def main() -> None:
    print("=== 1. 空の盤面からMini-Max法で全探索し、ゲームの必勝手を求める ===")
    t0 = time.perf_counter()
    node_counter["count"] = 0
    best_score, best_move = minimax(initial_state(), "X")
    elapsed = time.perf_counter() - t0
    outcome = {1: "Xの必勝", -1: "Oの必勝", 0: "引き分け(双方最善なら)"}[best_score]
    print(f"探索ノード数={node_counter['count']:,}, 所要時間={elapsed:.1f}秒")
    print(f"空の盤面での最善手の評価値={best_score}({outcome}), 最善手のマス={best_move}")

    print("\n=== 2. 9通りの初手それぞれについて評価値と探索コストを調べる ===")
    values = np.zeros((3, 3))
    n_nodes = np.zeros((3, 3))
    for move in available_moves(initial_state()):
        node_counter["count"] = 0
        state = apply_move(initial_state(), move, "X")
        score, _ = minimax(state, "O")
        values[move // 3, move % 3] = score
        n_nodes[move // 3, move % 3] = node_counter["count"]
        print(f"  マス{move}に初手を打つ: 評価値={score:+.0f}, 探索ノード数={int(n_nodes[move // 3, move % 3]):,}")

    print("\n=== 3. Mini-Max(X) vs Mini-Max(O)で1局対戦(双方最善手) ===")
    minimax_agent_x = lambda s: minimax(s, "X")[1]
    minimax_agent_o = lambda s: minimax(s, "O")[1]
    result = play_game(minimax_agent_x, minimax_agent_o, verbose=True)
    print(f"結果: {result}")

    print("\n=== 4. Mini-Max(X) vs ランダム(O)を100局対戦し、Mini-Max側の負けがないか確認 ===")
    random.seed(0)
    results = [play_game(minimax_agent_x, random_move) for _ in range(100)]
    n_x, n_o, n_draw = results.count("X"), results.count("O"), results.count("Draw")
    print(f"Xの勝ち={n_x}, Oの勝ち={n_o}, 引き分け={n_draw}")

    print("\n=== 5. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    im0 = axes[0].imshow(values, cmap="coolwarm", vmin=-1, vmax=1)
    for i in range(3):
        for j in range(3):
            axes[0].text(j, i, f"{values[i, j]:+.0f}", ha="center", va="center", fontsize=14)
    axes[0].set_title("初手ごとの評価値\n(+1=Xの必勝,-1=Oの必勝,0=最善なら引き分け)")
    axes[0].set_xticks([])
    axes[0].set_yticks([])
    fig.colorbar(im0, ax=axes[0], fraction=0.046)

    im1 = axes[1].imshow(n_nodes, cmap="viridis")
    for i in range(3):
        for j in range(3):
            axes[1].text(j, i, f"{int(n_nodes[i, j]):,}", ha="center", va="center",
                          fontsize=10, color="white")
    axes[1].set_title("初手ごとの探索ノード数")
    axes[1].set_xticks([])
    axes[1].set_yticks([])
    fig.colorbar(im1, ax=axes[1], fraction=0.046)

    fig.tight_layout()
    out_path = "minimax_tictactoe.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    all_draw = np.all(values == 0)
    min_nodes_move = int(np.argmin(n_nodes))
    print(
        f"\n9通りの初手はすべて評価値{'0(引き分け)で一致した' if all_draw else 'が一致しなかった'}——"
        "三目並べは双方が最善を尽くせば必ず引き分けになるゲームであり、"
        "先手のどのマスに打っても(相手も最善を尽くす限り)結果は変わらないことが"
        "全探索によって裏付けられた。一方で探索ノード数はマスによって"
        f"最小{int(n_nodes.min()):,}〜最大{int(n_nodes.max()):,}まで差があり、"
        f"最も探索コストが小さかったのはマス{min_nodes_move}だった——盤面の対称性により、"
        "相手の応手の選択肢が実質的に少なくなる/早期に決着がつく分岐が多い初手ほど、"
        "探索ノード数が少なくなる傾向がある。ランダムな相手との100局では"
        f"Xの勝ち={n_x}, Oの勝ち={n_o}, 引き分け={n_draw}となり、"
        f"{'Mini-Max側の負けは一度もなかった' if n_o == 0 else 'Mini-Max側にも負けが生じてしまった(バグの可能性が高い)'}。"
        "Mini-Max法は『相手が最善を尽くしても保証できる結果』を求めるため、"
        "相手がミスをする(ランダムに打つ)場合はその分だけ有利な結果(勝ち)を引き出せる、"
        "という関係が実験的にも確認できた。"
    )


if __name__ == "__main__":
    main()
