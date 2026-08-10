"""numpyでScaled Dot-Product Attention / Self-Attention / Multi-Head Attentionをスクラッチ実装する

Stage7で見たRNN/LSTMは、系列の情報を「隠れ状態」という1つのベクトルに圧縮しながら
時刻方向に1ステップずつ伝播させるため、系列が長くなるほど古い情報が薄まっていく
(勾配消失)という構造的な弱点があった。

Attentionは発想が異なる: 各時刻の出力を計算する際に、系列内の「すべての」時刻を
直接参照し、関連度(注意重み)に応じて重み付き平均を取る。系列長に関わらず、
どの2点の間も1ステップで直接つながるため、原理的に長期依存に強い
——これがTransformer("Attention is All You Need")の核心的なアイデア。

Q(Query)・K(Key)・V(Value)という3種類のベクトルを使い、
Attention(Q,K,V) = softmax(QK^T / √d_k) V
という式でAttentionを計算する。
"""
import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

rng = np.random.RandomState(42)


def softmax(x, axis=-1):
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)


def scaled_dot_product_attention(Q, K, V, mask=None):
    """Q: (..., n_q, d_k), K: (..., n_k, d_k), V: (..., n_k, d_v)
    戻り値: (output, attn_weights)
    d_kが大きいほどQK^Tの内積の分散が大きくなり、softmaxが極端に尖ってしまう
    (勾配が消えやすくなる)ため、√d_kで割ってスケールを揃える。"""
    d_k = Q.shape[-1]
    scores = Q @ np.swapaxes(K, -1, -2) / np.sqrt(d_k)
    if mask is not None:
        scores = np.where(mask, scores, -1e9)
    attn_weights = softmax(scores, axis=-1)
    output = attn_weights @ V
    return output, attn_weights


class SelfAttention:
    """1つの入力系列Xから、線形変換でQ・K・Vを作り、自分自身に対してAttentionをかける。
    「文中の各単語が、文中の他のどの単語に注目すべきか」を学習できる仕組みの土台。"""

    def __init__(self, d_model, d_k, seed=0):
        r = np.random.RandomState(seed)
        scale = np.sqrt(1.0 / d_model)
        self.Wq = r.randn(d_model, d_k) * scale
        self.Wk = r.randn(d_model, d_k) * scale
        self.Wv = r.randn(d_model, d_k) * scale

    def forward(self, X, mask=None):
        Q, K, V = X @ self.Wq, X @ self.Wk, X @ self.Wv
        return scaled_dot_product_attention(Q, K, V, mask)


class MultiHeadAttention:
    """複数(h個)の異なるQ/K/V変換(head)を並行して持ち、それぞれ違う観点で
    Attentionを計算してから結合する。1つのAttentionだけでは「1種類の関連性」しか
    捉えられないが、head を増やすことで「文法的な関連」「意味的な関連」など
    複数種類の依存関係を同時に学習できるようになる。"""

    def __init__(self, d_model, n_heads, seed=0):
        assert d_model % n_heads == 0
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        r = np.random.RandomState(seed)
        scale = np.sqrt(1.0 / d_model)
        self.Wq = r.randn(d_model, d_model) * scale
        self.Wk = r.randn(d_model, d_model) * scale
        self.Wv = r.randn(d_model, d_model) * scale
        self.Wo = r.randn(d_model, d_model) * scale

    def _split_heads(self, X):
        # (batch, seq, d_model) → (batch, n_heads, seq, d_k)
        batch, seq, d_model = X.shape
        X = X.reshape(batch, seq, self.n_heads, self.d_k)
        return X.transpose(0, 2, 1, 3)

    def forward(self, X, mask=None):
        batch, seq, d_model = X.shape
        Q = self._split_heads(X @ self.Wq)
        K = self._split_heads(X @ self.Wk)
        V = self._split_heads(X @ self.Wv)
        out, attn_weights = scaled_dot_product_attention(Q, K, V, mask)  # (batch, n_heads, seq, d_k)
        out = out.transpose(0, 2, 1, 3).reshape(batch, seq, d_model)
        return out @ self.Wo, attn_weights


def main() -> None:
    print("=== 1. Scaled Dot-Product Attentionの基本動作を確認 ===")
    d_k = 8
    Q = rng.randn(1, 3, d_k)
    K = rng.randn(1, 5, d_k)
    V = rng.randn(1, 5, d_k)
    out, attn = scaled_dot_product_attention(Q, K, V)
    print(f"Q: {Q.shape} (問い合わせ3個), K/V: {K.shape} (参照先5個)")
    print(f"出力: {out.shape}, 注意重み: {attn.shape} (各行の合計={attn.sum(axis=-1)})")

    print("\n=== 2. スケーリング(√d_k)の効果を確認 ===")
    print("d_kが大きいほどスケーリングなしのsoftmaxが極端に尖る(勾配消失につながる)ことを示す:")
    for d_k_test in [8, 64, 512]:
        Qt = rng.randn(1, 1, d_k_test)
        Kt = rng.randn(1, 20, d_k_test)
        scores_raw = Qt @ np.swapaxes(Kt, -1, -2)
        scores_scaled = scores_raw / np.sqrt(d_k_test)
        attn_raw = softmax(scores_raw)
        attn_scaled = softmax(scores_scaled)
        print(f"  d_k={d_k_test:3d}: スケーリングなしの最大重み={attn_raw.max():.4f}, "
              f"スケーリングありの最大重み={attn_scaled.max():.4f}")

    print("\n=== 3. Self-Attentionで系列内の関連度を可視化 ===")
    # トイ例: 「同じ値を持つ位置同士が強く関連する」ような人工的な系列を作る
    seq_len, d_model = 8, 16
    token_types = np.array([0, 1, 0, 2, 1, 0, 2, 1])  # 同じ数字=同じ種類のトークン
    X = np.zeros((1, seq_len, d_model))
    base_vectors = rng.randn(3, d_model)
    for i, t in enumerate(token_types):
        X[0, i] = base_vectors[t] + rng.randn(d_model) * 0.1  # 同種は近いベクトル+少しノイズ

    self_attn = SelfAttention(d_model, d_k=16, seed=1)
    _, attn_weights = self_attn.forward(X)

    print(f"トークン種類: {token_types} (0/1/2の3種類、同じ数字は意味的に近いベクトル)")
    attn_received = attn_weights[0].mean(axis=0)  # 各Key位置が全クエリから受け取る平均注意
    top_key = int(np.argmax(attn_received))
    print(f"各Key位置が全クエリから受け取る平均注意重み: {np.round(attn_received, 3)}")
    print(f"→ 最も注目を集めている位置は{top_key}(トークン種類={token_types[top_key]})")

    print("\n=== 4. Multi-Head Attentionの動作確認 ===")
    mha = MultiHeadAttention(d_model=16, n_heads=4, seed=2)
    out_mha, attn_mha = mha.forward(X)
    print(f"入力: {X.shape} → 出力: {out_mha.shape}")
    print(f"head別の注意重み: {attn_mha.shape} (batch, n_heads=4, seq, seq)")
    print("4つのheadそれぞれで異なる注意パターンになっているか、行ごとの分散で確認:")
    for h in range(4):
        var = attn_mha[0, h].var()
        print(f"  head{h}: 注意重みの分散={var:.5f}")

    fig, axes = plt.subplots(1, 5, figsize=(20, 4.2))
    im0 = axes[0].imshow(attn_weights[0], cmap="viridis")
    axes[0].set_title("Self-Attention\n(1個のQKV)")
    axes[0].set_xlabel("Key位置")
    axes[0].set_ylabel("Query位置")
    fig.colorbar(im0, ax=axes[0], fraction=0.046)

    for h in range(4):
        im = axes[h + 1].imshow(attn_mha[0, h], cmap="viridis")
        axes[h + 1].set_title(f"Multi-Head Attention\nhead{h}")
        axes[h + 1].set_xlabel("Key位置")
        fig.colorbar(im, ax=axes[h + 1], fraction=0.046)

    fig.suptitle(f"注意重みのヒートマップ(トークン種類: {list(token_types)})")
    fig.tight_layout()
    out_path = "scratch_attention.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nスケーリングの実験(2節)から、内積の次元d_kが大きいほどsoftmax前のスコアの"
        "分散が大きくなり、スケーリングなしではsoftmaxの出力がほぼone-hotに近い形まで"
        "尖ってしまう(=ほとんど1箇所にしか注意を向けられず、勾配がほぼ流れなくなる)ことを"
        "確認した。√d_kで割ることで、d_kによらず適度になだらかなsoftmax分布を保てる。"
        "また、3節のヒートマップを見ると、Self-Attentionは『同じトークン種類同士が"
        "強く注目し合う』というような分かりやすい構造にはなっておらず、むしろ"
        "特定の1〜2箇所(ランダムな重み行列Wkによってたまたま多くのクエリと内積が"
        "大きくなる方向を向いた位置)に、トークン種類に関係なくほぼ全てのクエリの"
        "注意が集中する『注意の吸着』が起きていた。これは学習していない段階の"
        "Attentionが、内積という仕組みだけでは『意味的な関連度』を表せておらず、"
        "むしろ重み行列の初期値に起因する偶然の相関に支配されやすいことを示している。"
        "『関連する単語同士が注目し合う』という直感的に分かりやすいAttentionのパターンは、"
        "アーキテクチャから自動的に生まれるものではなく、実際のタスクで学習して"
        "初めて獲得されるものだという点は重要な教訓である。"
        "Multi-Head Attentionではheadごとに注意重みの分散が異なっており、"
        "同じ入力に対しても複数の異なる『見方』を並行して持てる余地があることは確認できた。"
    )


if __name__ == "__main__":
    main()
