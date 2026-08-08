"""ミニプロジェクト: 手書き数字データをPCA/t-SNEで2次元可視化し、k-meansでクラスタリング"""
import numpy as np
from sklearn.datasets import load_digits
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, confusion_matrix
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def main() -> None:
    digits = load_digits()
    X, y = digits.data, digits.target
    print(f"データ: 手書き数字(load_digits) X={X.shape}, クラス数={len(np.unique(y))}(0〜9)")

    X_scaled = StandardScaler().fit_transform(X)

    # --- 1. 教師なしでk-meansクラスタリング(正解ラベルは評価にのみ使う) ---
    print("\n=== 1. k-means(k=10)でクラスタリング ===")
    kmeans = KMeans(n_clusters=10, n_init=10, random_state=42).fit(X_scaled)
    ari = adjusted_rand_score(y, kmeans.labels_)
    print(f"Adjusted Rand Index(ARI): {ari:.3f}（正解の10クラスとどれだけ一致するか）")

    # 各k-meansクラスタが「実際にはどの数字が最も多いか」を集計
    cm = confusion_matrix(y, kmeans.labels_)
    cluster_to_digit = cm.argmax(axis=0)  # 各クラスタ列で最頻の正解ラベル
    print("\n各k-meansクラスタ番号 → 最も多く含まれる正解の数字:")
    for cluster_id, digit in enumerate(cluster_to_digit):
        count = cm[digit, cluster_id]
        total = cm[:, cluster_id].sum()
        print(f"  クラスタ{cluster_id}: 数字{digit}が最多 ({count}/{total}枚, {count/total:.0%})")

    # 上記の対応表を使い、クラスタ番号を「予測数字ラベル」に変換して正解率を計算
    predicted_digit = np.array([cluster_to_digit[c] for c in kmeans.labels_])
    accuracy_like = (predicted_digit == y).mean()
    print(
        f"\n『各クラスタの最頻ラベルをそのクラスタの予測ラベルとみなす』場合の一致率: "
        f"{accuracy_like:.3f}（教師ラベルなしでここまで数字を当てられた）"
    )

    # --- 2. PCA/t-SNEの2次元埋め込みに、k-meansのクラスタ割当と正解ラベルをそれぞれ重ねて可視化 ---
    print("\n=== 2. PCA/t-SNEの2次元埋め込みで、正解ラベルとk-means結果を見比べる ===")
    pca_2d = PCA(n_components=2, random_state=42).fit_transform(X_scaled)
    tsne_2d = TSNE(n_components=2, perplexity=30, random_state=42, init="pca").fit_transform(X_scaled)

    fig, axes = plt.subplots(2, 2, figsize=(12, 11))
    configs = [
        (axes[0, 0], pca_2d, y, "PCA + 正解ラベル"),
        (axes[0, 1], pca_2d, kmeans.labels_, "PCA + k-meansクラスタ"),
        (axes[1, 0], tsne_2d, y, "t-SNE + 正解ラベル"),
        (axes[1, 1], tsne_2d, kmeans.labels_, "t-SNE + k-meansクラスタ"),
    ]
    for ax, coords, labels, title in configs:
        scatter = ax.scatter(coords[:, 0], coords[:, 1], c=labels, cmap="tab10", s=12, alpha=0.8)
        ax.set_title(title)
        ax.set_xlabel("次元1")
        ax.set_ylabel("次元2")
    fig.suptitle(f"手書き数字のPCA/t-SNE可視化 × k-meansクラスタリング（ARI={ari:.3f}）", fontsize=13)
    fig.colorbar(scatter, ax=axes, label="ラベル/クラスタ番号(0-9)", fraction=0.02)

    out_path = "mnist_clustering_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\n注意: k-meansのクラスタ番号(0〜9)は正解の数字ラベル(0〜9)とは無関係な"
        "『たまたま振られた番号』なので、右列と左列で同じ色でも同じ数字を指すとは限らない。"
        "比較する時は『色の一致』ではなく『塊(かたまり)の形が同じ場所にできているか』を見る。"
        "\n上段(PCA)は正解ラベルの塊とk-meansクラスタの塊の境目が完全には一致せず、"
        "特に形の似た数字(4と9、3と8等)が混ざりやすい。下段(t-SNE)は数字ごとの塊が"
        "PCAよりはっきり分かれており、k-meansのクラスタ境界も正解ラベルの塊とほぼ同じ場所に"
        "できている（左下の紺色の塊≒右下の緑色の塊、等）。ただしt-SNEの座標軸自体は"
        "k-meansの計算には使っていない（k-meansは64次元の元データ全体に対して実行し、"
        "可視化のためだけにPCA/t-SNEで2次元に落としている点に注意）。"
    )


if __name__ == "__main__":
    main()
