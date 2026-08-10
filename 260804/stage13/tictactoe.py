"""三目並べ(〇×ゲーム)の盤面表現とルールをまとめた共通モジュール

Stage13の02〜04で共通して使う。盤面は長さ9のタプル(' '/'X'/'O')で表現し、
インデックスは
  0 1 2
  3 4 5
  6 7 8
に対応する。タプルはハッシュ可能なので、探索アルゴリズムの置換表(transposition
table)のキーにもそのまま使える。
"""
EMPTY = " "
LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # 横
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # 縦
    (0, 4, 8), (2, 4, 6),             # 斜め
]


def initial_state():
    return (EMPTY,) * 9


def available_moves(state):
    return [i for i, cell in enumerate(state) if cell == EMPTY]


def apply_move(state, move, player):
    new_state = list(state)
    new_state[move] = player
    return tuple(new_state)


def other_player(player):
    return "O" if player == "X" else "X"


def check_winner(state):
    """'X'/'O'(勝者), 'Draw'(引き分け), None(まだ決着していない)を返す"""
    for a, b, c in LINES:
        if state[a] != EMPTY and state[a] == state[b] == state[c]:
            return state[a]
    if EMPTY not in state:
        return "Draw"
    return None


def print_board(state):
    rows = [state[0:3], state[3:6], state[6:9]]
    for r in rows:
        print("  " + "|".join(c if c != EMPTY else "." for c in r))
