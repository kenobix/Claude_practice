"""k-means法とウォード法（階層的クラスタリング）"""
import numpy as np
from sklearn.datasets import load_iris
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.metrics import silhouette_score, adjusted_rand_score
from sklearn.decomposition import PCA
from scipy.cluster.hierarchy import dendrogram, linkage
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401


def main() -> None:
    iris = load_iris()
    X, y_true = iris.data, iris.target
    X_scaled = StandardScaler().fit_transform(X)
    print(f"データ形状: X={X.shape}（アヤメ3品種、がく片・花弁の長さ/幅の4特徴量）")

    # --- 1. エルボー法とシルエットスコアでkを探る（正解ラベルを使わない前提） ---
    print("\n=== 1. k-meansのkをどう決めるか（エルボー法・シルエットスコア） ===")
    ks = range(1, 9)
    inertias = []
    silhouettes = [None]  # k=1はシルエット未定義
    for k in ks:
        km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X_scaled)
        inertias.append(km.inertia_)
        if k >= 2:
            silhouettes.append(silhouette_score(X_scaled, km.labels_))
    for k, inertia, sil in zip(ks, inertias, silhouettes):
        sil_str = f"{sil:.3f}" if sil is not None else "  -  "
        print(f"k={k}: inertia(クラスタ内二乗和)={inertia:8.2f}  silhouette={sil_str}")

    # --- 2. k=3(正解の品種数)でk-meansを実行し、正解ラベルと比較 ---
    print("\n=== 2. k=3でのk-means結果と正解ラベルの一致度 ===")
    kmeans = KMeans(n_clusters=3, n_init=10, random_state=42).fit(X_scaled)
    ari_kmeans = adjusted_rand_score(y_true, kmeans.labels_)
    print(f"Adjusted Rand Index(ARI): {ari_kmeans:.3f}（1.0で完全一致、0.0でランダムと同等）")

    # --- 3. ウォード法による階層的クラスタリング ---
    print("\n=== 3. ウォード法（階層的クラスタリング） ===")
    ward_model = AgglomerativeClustering(n_clusters=3, linkage="ward").fit(X_scaled)
    ari_ward = adjusted_rand_score(y_true, ward_model.labels_)
    print(f"Adjusted Rand Index(ARI): {ari_ward:.3f}")
    print(
        "ウォード法は「クラスタを1つ併合するたびに、クラスタ内分散の増加が最小になる"
        "組み合わせを選ぶ」という基準で、似た者同士から順にボトムアップで併合していく。"
    )

    # --- 可視化 ---
    Z = linkage(X_scaled, method="ward")
    X_pca = PCA(n_components=2, random_state=42).fit_transform(X_scaled)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    axes[0].plot(list(ks), inertias, marker="o")
    axes[0].set_xlabel("クラスタ数 k")
    axes[0].set_ylabel("inertia（クラスタ内二乗和、小さいほど密集）")
    axes[0].set_title("エルボー法: kを増やした時のinertiaの変化")
    axes[0].axvline(3, color="gray", linestyle="--", alpha=0.6)
    axes[0].annotate("k=3付近で\n傾きが緩やかに", xy=(3, inertias[2]), xytext=(4.5, inertias[0] * 0.6),
                      arrowprops=dict(arrowstyle="->"))

    dendrogram(Z, ax=axes[1], truncate_mode="level", p=5, no_labels=True)
    axes[1].set_title("ウォード法のデンドログラム（樹形図）")
    axes[1].set_xlabel("サンプル(まとめて表示)")
    axes[1].set_ylabel("併合時のクラスタ内分散の増加量")

    scatter = axes[2].scatter(X_pca[:, 0], X_pca[:, 1], c=kmeans.labels_, cmap="viridis", edgecolor="k", s=40)
    axes[2].set_title(f"k-means(k=3)のクラスタ割当\n(PCAで2次元に投影, ARI={ari_kmeans:.3f})")
    axes[2].set_xlabel("PC1")
    axes[2].set_ylabel("PC2")
    plt.colorbar(scatter, ax=axes[2], label="k-meansのクラスタ番号")

    fig.tight_layout()
    out_path = "kmeans_ward.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nk-meansは各クラスタの重心を更新しながら「重心に一番近いクラスタに割り当てる」を"
        "繰り返す分割最適化型、ウォード法は似たサンプル同士を順に併合していく階層型。"
        f"アプローチは異なるが、今回のirisデータではk-means(ARI={ari_kmeans:.3f})と"
        f"ウォード法(ARI={ari_ward:.3f})はほぼ同水準で、どちらも正解の品種にある程度近い"
        "クラスタを見つけられた（完全一致ではない＝教師なしでの品種推定には限界もある）。"
    )
    print(
        "なお、シルエットスコアはk=2で最大(0.582)になりk=3(0.460)より高い。"
        "これは3品種のうちsetosa種は他と明確に離れている一方、versicolor種とvirginica種は"
        "特徴量空間でかなり重なっており、『クラスタの分離しやすさ』だけを見る指標では"
        "2群構成(setosa vs 残り2種)の方が『きれいに分かれて見える』ため。"
        "正解のクラス数(3)と、教師なし指標が示す最適なクラスタ数は必ずしも一致しない、"
        "という教師なし学習特有の注意点を示している。"
    )


if __name__ == "__main__":
    main()
