"""幅優先探索(BFS)と深さ優先探索(DFS)を迷路探索でスクラッチ実装し、性質の違いを比較する

BFS/DFSはどちらも「グラフ(状態空間)を探索して目的の状態にたどり着く」という
古典的な探索アルゴリズムで、後続のMini-Max法・MCTSなどゲーム木探索の基礎になる。
  - BFS: キュー(FIFO)を使い、スタートから近い状態から順に広く探索する。
    辺の重みが均一(1マス移動=コスト1)なら、見つかる経路は必ず最短経路になる。
  - DFS: スタック(LIFO)を使い、行けるところまで一本道を掘り進めてから戻る。
    メモリ効率は良いが、見つかる経路が最短とは限らない。
この違いを、壁のある2次元迷路の最短路探索で確認する。
"""
from collections import deque

import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

# 迷路: 0=通路, 1=壁。S(スタート)は左上、G(ゴール)は右下。
# 疎な壁配置(乱数シード3)にすることでS-G間に長さの異なる複数の経路が存在し、
# DFSが行き止まりに近い遠回りルートを先に掘り進めてしまう状況を作っている。
_rng = np.random.RandomState(3)
MAZE = (_rng.random((10, 10)) < 0.25).astype(int)
MAZE[0, 0] = 0
MAZE[9, 9] = 0
START = (0, 0)
GOAL = (9, 9)


def neighbors(pos, maze):
    r, c = pos
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = r + dr, c + dc
        if 0 <= nr < maze.shape[0] and 0 <= nc < maze.shape[1] and maze[nr, nc] == 0:
            yield (nr, nc)


def bfs(maze, start, goal):
    frontier = deque([start])
    came_from = {start: None}
    n_expanded = 0
    while frontier:
        current = frontier.popleft()
        n_expanded += 1
        if current == goal:
            break
        for nxt in neighbors(current, maze):
            if nxt not in came_from:
                came_from[nxt] = current
                frontier.append(nxt)
    return reconstruct_path(came_from, start, goal), n_expanded


def dfs(maze, start, goal):
    frontier = [start]
    came_from = {start: None}
    n_expanded = 0
    while frontier:
        current = frontier.pop()  # スタック(末尾)から取り出す点だけがBFSと異なる
        n_expanded += 1
        if current == goal:
            break
        for nxt in neighbors(current, maze):
            if nxt not in came_from:
                came_from[nxt] = current
                frontier.append(nxt)
    return reconstruct_path(came_from, start, goal), n_expanded


def reconstruct_path(came_from, start, goal):
    if goal not in came_from:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def draw_path(ax, maze, path, n_expanded, title):
    ax.imshow(maze, cmap="gray_r")
    if path:
        rows = [p[0] for p in path]
        cols = [p[1] for p in path]
        ax.plot(cols, rows, color="tab:red", linewidth=2, marker="o", markersize=3)
    ax.scatter(*START[::-1], color="tab:green", s=100, marker="s", label="S(スタート)", zorder=5)
    ax.scatter(*GOAL[::-1], color="tab:blue", s=100, marker="*", label="G(ゴール)", zorder=5)
    path_len = len(path) - 1 if path else None
    ax.set_title(f"{title}\n経路長={path_len}, 展開ノード数={n_expanded}")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.legend(loc="lower right", fontsize=8)


def main() -> None:
    print("=== 1. BFS(幅優先探索)で迷路を解く ===")
    path_bfs, n_bfs = bfs(MAZE, START, GOAL)
    print(f"経路長={len(path_bfs) - 1}マス, 展開ノード数={n_bfs}")

    print("\n=== 2. DFS(深さ優先探索)で迷路を解く ===")
    path_dfs, n_dfs = dfs(MAZE, START, GOAL)
    print(f"経路長={len(path_dfs) - 1}マス, 展開ノード数={n_dfs}")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))
    draw_path(axes[0], MAZE, path_bfs, n_bfs, "BFS(幅優先探索)")
    draw_path(axes[1], MAZE, path_dfs, n_dfs, "DFS(深さ優先探索)")
    fig.tight_layout()
    out_path = "bfs_dfs_maze.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    len_bfs, len_dfs = len(path_bfs) - 1, len(path_dfs) - 1
    same_length = len_bfs == len_dfs
    print(
        f"\nBFSの経路長={len_bfs}, DFSの経路長={len_dfs}。"
        f"{'両者は同じ長さの経路を見つけた' if same_length else 'DFSはBFSより長い経路を見つけた(最短性が保証されないため)'}。"
        f"展開ノード数はBFS={n_bfs}, DFS={n_dfs}で、"
        f"{'BFS' if n_bfs < n_dfs else 'DFS'}の方が少ないノード数でゴールに到達した。"
        "BFSは『近い状態から順に』広く探索するため見つけた経路が必ず最短になる一方、"
        "スタートに近い階層のノードを大量に保持するためメモリ消費が大きくなりやすい。"
        "DFSは一本道を掘り進めるため経路の最短性は保証されないが、"
        "保持するノード数(スタックの深さ)は経路長程度で済む。この基本的なトレードオフは、"
        "後続のMini-Max法やMCTSなどゲーム木探索でも探索順序・打ち切り戦略を考える際の土台になる。"
    )


if __name__ == "__main__":
    main()
