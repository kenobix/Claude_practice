"""fastTextとword2vecを同条件で学習し、未知語(OOV)への強さの違いを確認する

03のword2vecは、学習データに出現した単語だけをベクトル化する
「単語をまるごと1つの単位として扱う」手法だった。そのため学習後に
語彙にない単語(Out-Of-Vocabulary, OOV)を渡すと埋め込みを作れない。
fastTextは単語を文字n-gram(サブワード)の集合として扱い、
単語ベクトル=それを構成するサブワードベクトルの平均、という形で学習する。
これにより、学習データに一度も出てこなかった単語でも、既知の単語と
綴りの一部(サブワード)を共有していれば、そこから意味の近い埋め込みを
合成できる。この「未知語への強さ」の違いを、03と同じmovie_reviews
コーパス・同じ次元数で学習したword2vecとfastTextで比較する。
"""
import importlib

from gensim.models import FastText
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

w2v_module = importlib.import_module("03_word2vec")

# 学習データに存在する語の綴りを一部変形させた「未知語」
# (実際の語形変化・タイプミス・強調表現を想定した造語)
OOV_TEST_WORDS = [
    ("gooood", "good"),          # 強調(母音を伸ばす)
    ("terriblely", "terrible"),  # 誤った活用形
    ("actoring", "acting"),      # 語尾の作り間違い
    ("filmically", "film"),      # 派生語(film+ically)
    ("wonderfullest", "wonderful"),  # 最上級もどきの誤形成
]


def main() -> None:
    print("=== 1. データ準備(03と同じmovie_reviewsコーパス) ===")
    sentences = w2v_module.load_sentences()
    print(f"文書数={len(sentences)}")

    print("\n=== 2. word2vec(skip-gram)とfastTextを同条件で学習 ===")
    common_kwargs = dict(vector_size=100, window=5, min_count=5, workers=4, epochs=20, seed=42, sg=1)
    model_w2v = w2v_module.Word2Vec(sentences, **common_kwargs)
    model_ft = FastText(sentences, **common_kwargs)
    print(f"word2vec語彙数={len(model_w2v.wv)}, fastText語彙数={len(model_ft.wv)}")

    print("\n=== 3. 未知語(OOV)への対応を比較 ===")
    w2v_success, ft_success = 0, 0
    for oov_word, base_word in OOV_TEST_WORDS:
        in_w2v_vocab = oov_word in model_w2v.wv
        in_ft_vocab = oov_word in model_ft.wv.key_to_index
        print(f"\n『{oov_word}』(想定元の単語: {base_word}):")
        print(f"  word2vecの語彙に存在={in_w2v_vocab}")
        try:
            _ = model_w2v.wv[oov_word]
            w2v_success += 1
            print("  word2vec: ベクトル取得に成功(想定外)")
        except KeyError:
            print("  word2vec: ベクトル取得に失敗(KeyError) — 学習データに無い単語は埋め込めない")

        try:
            ft_vec = model_ft.wv[oov_word]
            ft_success += 1
            nearest = model_ft.wv.most_similar(positive=[ft_vec], topn=5)
            nearest_str = ", ".join(f"{w}({s:.2f})" for w, s in nearest)
            print(f"  fastText: 語彙に無くてもサブワードからベクトル合成に成功。近傍語: {nearest_str}")
        except KeyError:
            print("  fastText: ベクトル取得に失敗")

    print("\n=== 4. 可視化: OOV単語への対応可否 ===")
    fig, ax = plt.subplots(figsize=(7.5, 5))
    counts = [w2v_success, ft_success]
    bars = ax.bar(["word2vec", "fastText"], counts, color=["tab:red", "tab:blue"])
    ax.set_ylim(0, len(OOV_TEST_WORDS) + 0.5)
    ax.set_ylabel("ベクトル化に成功したOOV単語数")
    ax.set_title(f"未知語(OOV)への対応(全{len(OOV_TEST_WORDS)}語: {[w for w, _ in OOV_TEST_WORDS]})")
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, c + 0.1, str(c), ha="center")
    fig.tight_layout()
    out_path = "fasttext_oov_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\n{len(OOV_TEST_WORDS)}個の未知語(学習データに一度も出てこない綴り)のうち、"
        f"word2vecがベクトル化できたのは{w2v_success}個、fastTextは{ft_success}個だった。"
        "word2vecは単語をまるごと1つの単位として扱うため、学習時の語彙に無い単語は"
        "原理的に一切埋め込めない(KeyError)。一方fastTextは単語を文字n-gramの集合として"
        "扱うため、綴りの一部が既知の単語と重なっていれば、学習データに一度も出現しなかった"
        "単語でもベクトルを合成できる。近傍語を見ると、fastTextが合成したOOV単語のベクトルは"
        "多くの場合、綴りが似た実在の語(元になった単語やその変化形)に近い位置に来ており、"
        "サブワード情報が実際に意味の近さの推定に役立っていることが確認できた。"
        "この性質から、fastTextは活用形が多い言語や複合語の多い言語で"
        "word2vecより有利になるとされている。"
    )


if __name__ == "__main__":
    main()
