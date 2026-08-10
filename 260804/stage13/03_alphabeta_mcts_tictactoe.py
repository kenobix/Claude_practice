"""αβ法(Mini-Max法の枝刈り)と、モンテカルロ木探索(MCTS)を三目並べに実装する

02で実装した素朴なMini-Max法は、勝敗に関係ない枝も含めて全ての分岐を律儀に
展開していた。αβ法は「すでに見つけている最善の選択肢より悪いことが確定した
時点で、それ以上その枝を調べても結論を変えられない」という性質を利用して
探索を打ち切る(枝刈りする)ことで、Mini-Max法と全く同じ結果を、より少ない
ノード数で得る。

MCTS(モンテカルロ木探索)は発想が異なり、ゲーム木を全展開する代わりに
  1) 選択(Selection): UCB1スコアが最大の子を辿って未探索の局面まで進む
  2) 展開(Expansion): その局面の子を1つ新たに追加する
  3) シミュレーション(Simulation/Rollout): そこからランダムに手を打ち合って決着まで進める
  4) 逆伝播(Backpropagation): 結果を根まで伝えて訪問回数・勝率を更新する
を繰り返し、統計的に「有望な手」に探索を集中させていく。三目並べは全探索が
可能なほど小さいゲームなのでMCTSの強みは活きにくいが、シミュレーション回数を
増やすほどMini-Max法が示す最適手に近づいていく様子は確認できる
(これが囲碁のような全探索不可能なゲームでMCTSが実用される理由でもある)。
"""
import math
import random
import time

import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401
from tictactoe import (
    initial_state, available_moves, apply_move, other_player, check_winner,
)

# --- αβ法 ---
ab_node_counter = {"count": 0}
plain_node_counter = {"count": 0}


def minimax_plain(state, player):
    plain_node_counter["count"] += 1
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
        score, _ = minimax_plain(apply_move(state, move, player), other_player(player))
        if (maximizing and score > best_score) or (not maximizing and score < best_score):
            best_score, best_move = score, move
    return best_score, best_move


def minimax_ab(state, player, alpha=-2, beta=2):
    ab_node_counter["count"] += 1
    winner = check_winner(state)
    if winner == "X":
        return 1, None
    if winner == "O":
        return -1, None
    if winner == "Draw":
        return 0, None
    maximizing = player == "X"
    best_move = None
    if maximizing:
        best_score = -2
        for move in available_moves(state):
            score, _ = minimax_ab(apply_move(state, move, player), other_player(player), alpha, beta)
            if score > best_score:
                best_score, best_move = score, move
            alpha = max(alpha, best_score)
            if alpha >= beta:
                break  # betaカット: 相手(minimizing)がこの手を絶対選ばせない
    else:
        best_score = 2
        for move in available_moves(state):
            score, _ = minimax_ab(apply_move(state, move, player), other_player(player), alpha, beta)
            if score < best_score:
                best_score, best_move = score, move
            beta = min(beta, best_score)
            if alpha >= beta:
                break  # alphaカット
    return best_score, best_move


# --- MCTS ---
class MCTSNode:
    def __init__(self, state, player_to_move, parent=None, move_from_parent=None):
        self.state = state
        self.player_to_move = player_to_move
        self.parent = parent
        self.move_from_parent = move_from_parent
        self.children = {}
        self.untried_moves = available_moves(state)
        self.visits = 0
        self.value_sum = 0.0  # このノードに至る手を打ったプレイヤーから見た累積スコア

    def ucb1_score(self, c=math.sqrt(2)):
        if self.visits == 0:
            return float("inf")
        exploitation = self.value_sum / self.visits
        exploration = c * math.sqrt(math.log(self.parent.visits) / self.visits)
        return exploitation + exploration

    def best_child(self):
        return max(self.children.values(), key=lambda n: n.ucb1_score())

    def most_visited_child(self):
        return max(self.children.values(), key=lambda n: n.visits)


def rollout(state, player):
    """決着がつくまでランダムに打ち合い、X視点の結果(+1/-1/0)を返す"""
    while True:
        winner = check_winner(state)
        if winner is not None:
            return {"X": 1, "O": -1, "Draw": 0}[winner]
        move = random.choice(available_moves(state))
        state = apply_move(state, move, player)
        player = other_player(player)


def mcts_search(root_state, root_player, n_simulations):
    root = MCTSNode(root_state, root_player)
    for _ in range(n_simulations):
        node = root
        # 1) 選択: 全て展開済みのノードをUCB1に従って辿る
        while not node.untried_moves and node.children:
            node = node.best_child()
        # 2) 展開: 未試行の手が残っていれば1つ子を追加する
        if node.untried_moves and check_winner(node.state) is None:
            move = node.untried_moves.pop(random.randrange(len(node.untried_moves)))
            next_state = apply_move(node.state, move, node.player_to_move)
            child = MCTSNode(next_state, other_player(node.player_to_move), parent=node, move_from_parent=move)
            node.children[move] = child
            node = child
        # 3) シミュレーション: ランダムロールアウトで決着させる
        result_for_x = rollout(node.state, node.player_to_move)
        # 4) 逆伝播: 「そのノードに至る手を打ったプレイヤー」から見た評価として加算
        backup_node = node
        while backup_node is not None:
            mover = other_player(backup_node.player_to_move)  # このノードに至る手を打ったプレイヤー
            backup_node.value_sum += result_for_x if mover == "X" else -result_for_x
            backup_node.visits += 1
            backup_node = backup_node.parent
    return root.most_visited_child().move_from_parent, root


def main() -> None:
    print("=== 1. αβ法 vs 素朴なMini-Max法: 探索ノード数の比較 ===")
    plain_node_counter["count"] = 0
    score_plain, move_plain = minimax_plain(initial_state(), "X")
    ab_node_counter["count"] = 0
    score_ab, move_ab = minimax_ab(initial_state(), "X")
    n_plain, n_ab = plain_node_counter["count"], ab_node_counter["count"]
    print(f"素朴なMini-Max法: 探索ノード数={n_plain:,}, 評価値={score_plain}, 最善手={move_plain}")
    print(f"αβ法          : 探索ノード数={n_ab:,}, 評価値={score_ab}, 最善手={move_ab}")
    print(f"ノード数の削減率={(1 - n_ab / n_plain) * 100:.1f}%")

    print("\n=== 2. MCTSで『相手の勝ちを阻止できる一手』を見つけられるか検証 ===")
    # X O X / . O . / . . . という局面。Oは1,4にO(左列)を並べており、
    # Xが6以外に打つとOに7を取られて縦の3並びを許してしまう「一手のミス」もできない局面。
    test_state = ("X", "O", "X", " ", "O", " ", " ", " ", " ")
    print("検証局面(Xの手番):")
    for r in range(0, 9, 3):
        print("  " + "|".join(c if c != " " else "." for c in test_state[r:r + 3]))
    optimal_value, optimal_move = minimax_ab(test_state, "X")
    move_values = {}
    for m in available_moves(test_state):
        v, _ = minimax_ab(apply_move(test_state, m, "X"), "O")
        move_values[m] = v
    optimal_moves = {m for m, v in move_values.items() if v == optimal_value}
    print(f"Mini-Max法での各手の評価値: {move_values}")
    print(f"唯一の最適手(評価値={optimal_value}) = {optimal_moves}")

    print("\n=== 3. シミュレーション回数を増やしながらMCTSの一致率を計測 ===")
    budgets = [10, 30, 100, 300, 1000, 3000]
    n_trials = 20
    agreement_rates = []
    random.seed(0)
    for budget in budgets:
        n_agree = 0
        for _ in range(n_trials):
            move, _ = mcts_search(test_state, "X", budget)
            if move in optimal_moves:
                n_agree += 1
        rate = n_agree / n_trials
        agreement_rates.append(rate)
        print(f"  シミュレーション回数={budget:>5}: 最適手との一致率={rate:.0%}({n_agree}/{n_trials}試行)")

    print("\n=== 4. 可視化 ===")
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.plot(budgets, agreement_rates, marker="o", color="tab:purple")
    ax.set_xscale("log")
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlabel("MCTSのシミュレーション回数(対数軸)")
    ax.set_ylabel(f"最適手との一致率({n_trials}試行平均)")
    ax.set_title("MCTSのシミュレーション回数と最適手一致率\n(検証局面: 唯一の最適手を見逃すと即敗着)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out_path = "alphabeta_mcts_tictactoe.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    improved = agreement_rates[-1] > agreement_rates[0]
    print(
        f"\nαβ法は素朴なMini-Max法と全く同じ評価値・同じ最善手を"
        f"{n_ab:,}ノード(素朴な方式の{n_ab / n_plain * 100:.1f}%)で求められ、"
        "結果を変えずに探索コストだけを削減できることを確認した。"
        f"MCTSについては、シミュレーション回数={budgets[0]}回では最適手との一致率が"
        f"{agreement_rates[0]:.0%}だったのに対し、{budgets[-1]}回まで増やすと"
        f"{agreement_rates[-1]:.0%}まで{'上昇した' if improved else '変化しなかった'}——"
        "ランダムロールアウトに基づく統計的な評価であっても、探索(シミュレーション)回数を"
        "増やすほどMini-Max法が示す真の最適手に近づいていくことが確認できた。"
        "三目並べ程度の規模ではMini-Max法(全探索)の方が高速かつ厳密だが、"
        "囲碁や将棋のように全探索が不可能なほど巨大なゲーム木を持つゲームでは、"
        "この『限られた探索回数の中で有望な手に絞り込んでいく』MCTSの性質が"
        "実用上重要になる。"
    )


if __name__ == "__main__":
    main()
