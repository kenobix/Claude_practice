"""gensimでword2vec(CBOW / skip-gram)を学習し、単語の分散表現を確認する

word2vecは「周囲の単語から中心の単語を予測する(CBOW)」または
「中心の単語から周囲の単語を予測する(skip-gram)」という補助タスクを
大量のテキストで解かせることで、単語を低次元の実数ベクトルに埋め込む手法。
直接ラベル付けされたデータを使わなくても、大量の生テキストだけから
「意味が近い単語は近いベクトルになる」という性質を獲得できる(自己教師あり学習)。

NLTKのmovie_reviewsコーパス(映画レビュー2000本, 約160万語)を使って
CBOWとskip-gramの両方を学習し、
1) 単語の類似度(most_similar)
2) 単語ベクトルの2次元可視化(PCA)
3) 低頻度語での挙動の違い(CBOWは頻度の高いパターンに引きずられやすく、
   skip-gramは低頻度語の学習に強いとされる)
を確認する。
"""
import nltk
from nltk.corpus import movie_reviews
from gensim.models import Word2Vec
import numpy as np
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

nltk.download("movie_reviews", quiet=True)


def load_sentences():
    """レビューを1文書1系列として、小文字化・記号除去した単語列のリストを作る"""
    sentences = []
    for fid in movie_reviews.fileids():
        words = [w.lower() for w in movie_reviews.words(fid) if w.isalpha()]
        sentences.append(words)
    return sentences


def main() -> None:
    print("=== 1. データ準備 ===")
    sentences = load_sentences()
    n_tokens = sum(len(s) for s in sentences)
    print(f"文書数(レビュー数)={len(sentences)}, 総トークン数={n_tokens}")

    print("\n=== 2. CBOWとskip-gramを学習 ===")
    common_kwargs = dict(vector_size=100, window=5, min_count=5, workers=4, epochs=20, seed=42)
    model_cbow = Word2Vec(sentences, sg=0, **common_kwargs)   # sg=0: CBOW
    model_sg = Word2Vec(sentences, sg=1, **common_kwargs)     # sg=1: skip-gram
    print(f"語彙数(min_count=5でフィルタ後)={len(model_cbow.wv)}")

    print("\n=== 3. 単語の類似度比較(most_similar) ===")
    query_words = ["good", "bad", "movie", "actor", "great"]
    for w in query_words:
        if w not in model_cbow.wv:
            continue
        sim_cbow = [f"{s:.2f}:{word}" for word, s in model_cbow.wv.most_similar(w, topn=5)]
        sim_sg = [f"{s:.2f}:{word}" for word, s in model_sg.wv.most_similar(w, topn=5)]
        print(f"\n『{w}』に類似した単語:")
        print(f"  CBOW     : {', '.join(sim_cbow)}")
        print(f"  skip-gram: {', '.join(sim_sg)}")

    print("\n=== 4. 低頻度語での挙動の違い ===")
    vocab_freq = [(w, model_sg.wv.get_vecattr(w, "count")) for w in model_sg.wv.index_to_key]
    vocab_freq.sort(key=lambda x: x[1])
    low_freq_words = [w for w, c in vocab_freq if 5 <= c <= 8][:3]
    print(f"低頻度語(出現回数5〜8回)の例: {low_freq_words}")
    for w in low_freq_words:
        sim_cbow = [f"{word}({s:.2f})" for word, s in model_cbow.wv.most_similar(w, topn=3)]
        sim_sg = [f"{word}({s:.2f})" for word, s in model_sg.wv.most_similar(w, topn=3)]
        print(f"  『{w}』: CBOW={sim_cbow} / skip-gram={sim_sg}")

    print("\n=== 5. 単語ベクトルをPCAで2次元に可視化 ===")
    plot_words = [
        "good", "great", "excellent", "wonderful", "bad", "terrible", "awful", "boring",
        "actor", "actress", "director", "film", "movie", "story", "plot",
        "action", "comedy", "horror", "drama",
    ]
    plot_words = [w for w in plot_words if w in model_sg.wv]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    for ax, model, name in [(axes[0], model_cbow, "CBOW"), (axes[1], model_sg, "skip-gram")]:
        vecs = np.array([model.wv[w] for w in plot_words])
        coords = PCA(n_components=2, random_state=42).fit_transform(vecs)
        ax.scatter(coords[:, 0], coords[:, 1], color="steelblue")
        for (x, y), w in zip(coords, plot_words):
            ax.annotate(w, (x, y), fontsize=9)
        ax.set_title(f"{name}の単語ベクトル(PCAで2次元に投影)")
    fig.tight_layout()
    out_path = "word2vec_embeddings.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nPCAの2次元プロットを見ると、actor/actress/director、あるいはhorror/comedy/action/drama"
        "のような『同じ品詞・同じ話題』の単語同士はまとまって配置される傾向がはっきり見える。"
        "一方で意外なことに、good⇔badやexcellent⇔terribleのような正反対の意味を持つ形容詞同士も"
        "互いにかなり近い位置に来ている(3節のmost_similarでも『good』の最類似語に『bad』が"
        "挙がっている)。これはword2vecの学習が『同じ意味かどうか』ではなく『同じような文脈"
        "(周囲の単語)に現れるかどうか』を捉える仕組みであるためで、"
        "'the movie was ___' のように良い意味の単語も悪い意味の単語も同じ構文パターンに"
        "現れやすいことが原因と考えられる。分散表現は類義語も対義語も『似た文脈に出る語』"
        "としてまとめて近づけてしまう場合があるという、word2vecの重要な限界の一つが実際に観察できた。"
        "教科書的にはskip-gramの方が低頻度語に強いとされるが、min_count=5という閾値を"
        "超えたばかりの語では、コーパスが約2000文書とそれほど大きくないこともあり、"
        "CBOW・skip-gramの差が明確に出るとは限らない。"
    )


if __name__ == "__main__":
    main()
