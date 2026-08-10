"""主成分分析(PCA)・特異値分解(SVD)・t-SNE・多次元尺度構成法(MDS)による次元削減"""
import numpy as np
from sklearn.datasets import load_digits
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.manifold import TSNE, MDS
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def main() -> None:
    digits = load_digits()
    X, y = digits.data, digits.target
    print(f"データ形状: X={X.shape}（8x8=64画素の手書き数字画像、0〜9の10クラス、{len(X)}枚）")

    # --- 1. PCAとSVDの関係を数値で確認 ---
    print("\n=== 1. PCAとSVDの関係 ===")
    X_centered = X - X.mean(axis=0)
    # 中心化したデータをSVDで直接分解: X_centered = U @ S @ Vt
    U, S, Vt = np.linalg.svd(X_centered, full_matrices=False)
    pca = PCA(n_components=10, random_state=42).fit(X_centered)
    # PCAの主成分方向(components_)は、中心化データのSVDにおけるVtと(符号の違いを除き)一致する
    cos_sim = np.abs(np.sum(pca.components_[0] * Vt[0])) / (
        np.linalg.norm(pca.components_[0]) * np.linalg.norm(Vt[0])
    )
    print(f"PCAの第1主成分ベクトルと、SVDのV(第1行)のなす角のcos類似度: {cos_sim:.6f}（1.0なら向きが一致）")
    print(
        "→ PCAは『中心化したデータ行列をSVD分解し、特異値の大きい順にV(特徴量の組み合わせ方向)を"
        "取り出す』処理と数学的に同一。PCAの分散説明量は特異値Sの2乗に比例する。"
    )
    print(f"第1〜5主成分の累積寄与率: {np.cumsum(pca.explained_variance_ratio_)[:5].round(3)}")

    # --- 2. t-SNEとMDSで2次元に埋め込み、PCAと比較 ---
    print("\n=== 2. PCA・t-SNE・MDSでの2次元埋め込みを比較（計算に少し時間がかかります） ===")
    X_scaled = StandardScaler().fit_transform(X)

    pca_2d = PCA(n_components=2, random_state=42).fit_transform(X_scaled)

    tsne_2d = TSNE(n_components=2, perplexity=30, random_state=42, init="pca").fit_transform(X_scaled)

    # MDSは全サンプル間の距離計算がO(n^2)で重いため、計算時間短縮のため300枚に間引く
    rng = np.random.RandomState(42)
    subset_idx = rng.choice(len(X_scaled), size=300, replace=False)
    mds_2d = MDS(
        n_components=2, random_state=42, normalized_stress="auto", n_init=4, init="random"
    ).fit_transform(X_scaled[subset_idx])

    svd_2d = TruncatedSVD(n_components=2, random_state=42).fit_transform(X)  # 非中心化データに対するSVD

    # --- 可視化 ---
    fig, axes = plt.subplots(2, 2, figsize=(12, 11))

    for ax, (title, coords, labels) in zip(
        axes.flat,
        [
            ("PCA（中心化データのSVD、全1797枚）", pca_2d, y),
            ("TruncatedSVD（非中心化データ、全1797枚）", svd_2d, y),
            ("t-SNE（全1797枚）", tsne_2d, y),
            ("MDS（計算コストの都合で300枚に間引き）", mds_2d, y[subset_idx]),
        ],
    ):
        scatter = ax.scatter(coords[:, 0], coords[:, 1], c=labels, cmap="tab10", s=12, alpha=0.8)
        ax.set_title(title)
        ax.set_xlabel("次元1")
        ax.set_ylabel("次元2")
    fig.colorbar(scatter, ax=axes, label="正解の数字ラベル(0-9)", fraction=0.02)

    out_path = "dimensionality_reduction.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nPCAとTruncatedSVDは似た形の散布図になる(中心化の有無だけの違い)。"
        "PCA/SVDは『分散を最大化する線形方向』を探すため大域的な構造は保つが、"
        "数字同士のクラスタが重なりやすい。t-SNEは『近傍の点同士の距離関係』を優先的に保つ"
        "非線形な手法で、同じ数字同士がより明確に固まって見える一方、クラスタ間の距離の"
        "大きさ自体には意味がない（見た目の距離を絶対視しないこと）。"
        "MDSは『全サンプル間のペア距離をできるだけ保つ』という大域的な基準で2次元に配置するが、"
        "今回の結果ではPCAよりもさらに分離が弱く見える。64次元のユークリッド距離という"
        "『全体の距離』を律儀に保とうとする分、局所的な近傍構造(=同じ数字同士の近さ)を"
        "優先するt-SNEほどクラスタを際立たせることは目的にしていない、という設計思想の違いが"
        "そのまま結果に表れている。"
    )


if __name__ == "__main__":
    main()
