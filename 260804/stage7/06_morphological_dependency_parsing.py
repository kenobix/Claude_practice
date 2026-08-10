"""形態素解析(Janome)と構文解析・係り受け解析(GiNZA/spaCy)

ここまでのStage 7はword2vec/fastTextのような「ニューラルネットで単語を
ベクトルに埋め込む」統計的な手法だった。このスクリプトでは対照的に、
文法規則に基づいて文の構造を明らかにする古典的な自然言語処理の2段階を扱う。
  1) 形態素解析: 英語と違って単語の区切りに空白を使わない日本語の文を、
     意味を持つ最小単位(形態素)に分割し、品詞を付与する処理。
     「ここではきものをぬいでください」のように、区切り方によって
     意味が変わる文があることが、日本語処理特有の難しさとしてよく知られる。
  2) 係り受け解析: 形態素解析で分割された単語同士が、どの単語がどの単語を
     修飾する(係る)かという文法的な結びつきを明らかにし、文の構造を木構造で表す。
形態素解析には辞書込みで軽量なJanome(純Python実装)を、係り受け解析には
GiNZA(SudachiPy+spaCyベースの日本語解析パイプライン)を使う。
"""
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.path import Path
from janome.tokenizer import Tokenizer
import spacy

import _mpl_ja  # noqa: F401

SENTENCES_MORPH = [
    "すもももももももものうち",
    "ここではきものをぬいでください",
]
SENTENCE_DEP = "深層学習モデルは大量のデータを使って特徴量を学習する"


def demo_morphological_analysis():
    print("=== 1. 形態素解析(Janome) ===")
    tokenizer = Tokenizer()
    results = {}
    for sentence in SENTENCES_MORPH:
        print(f"\n入力文: 『{sentence}』")
        tokens = list(tokenizer.tokenize(sentence))
        surfaces = [t.surface for t in tokens]
        results[sentence] = surfaces
        print(f"分割結果: {' / '.join(surfaces)}")
        for t in tokens:
            pos = t.part_of_speech.split(",")[0]
            print(f"  {t.surface:6s} 品詞={pos}")

    chosen_reading = (
        "『ここでは/きもの(着物)を/脱いで』(「は」を独立した助詞として切り出す解釈)"
        if "は" in results[SENTENCES_MORPH[1]]
        else "『ここで/はきもの(履物)を/脱いで』(「は」を名詞の一部として含める解釈)"
    )
    print(
        f"\n『ここではきものをぬいでください』は、"
        "『ここで/履物(はきもの)を/脱いで』とも『ここでは/着物(きもの)を/脱いで』とも"
        f"区切れてしまう有名な曖昧文で、今回のJanome(IPA辞書)は{chosen_reading}を選んだ。"
        "人間も文脈なしにこの文だけを見ると一意に決められないことがあり、単語の境界を"
        "決めること自体が自明ではない(＝空白で単語が区切られる英語のトークン化とは"
        "根本的に難易度が異なる)日本語処理特有の課題を示す典型例になっている。"
    )


def demo_dependency_parsing():
    print("\n\n=== 2. 係り受け解析(GiNZA) ===")
    # spaCy 3.8とginza 5.2の組み合わせでcompound_splitterのconfigエラーが出るため除外する
    nlp = spacy.load("ja_ginza", exclude=["compound_splitter"])
    doc = nlp(SENTENCE_DEP)
    print(f"入力文: 『{SENTENCE_DEP}』")
    for token in doc:
        print(f"  {token.text:6s} 品詞={token.pos_:6s} 係り受けラベル={token.dep_:8s} 係り先={token.head.text}")
    return doc


def visualize_dependency_tree(doc):
    tokens = list(doc)
    n = len(tokens)
    fig, ax = plt.subplots(figsize=(max(10, n * 1.1), 5))

    for i, token in enumerate(tokens):
        ax.text(i, 0, token.text, ha="center", va="top", fontsize=13)
        ax.text(i, -0.35, token.pos_, ha="center", va="top", fontsize=8, color="gray")

    # 二次ベジエ曲線(始点・制御点・終点)で弧を描く。制御点の高さを直接指定できるため、
    # 見た目の弧の高さとラベル位置・ylimを正確に一致させられる
    arcs = []
    for i, token in enumerate(tokens):
        if token.head.i == i:
            continue  # ROOT自身は矢印なし
        j = token.head.i
        height = 0.5 + 0.3 * abs(j - i)
        arcs.append((i, j, height, token.dep_))
    max_height = max((h for _, _, h, _ in arcs), default=1)

    for i, j, height, label in arcs:
        control = ((i + j) / 2, height)
        path = Path([(i, 0.05), control, (j, 0.05)], [Path.MOVETO, Path.CURVE3, Path.CURVE3])
        arrow = patches.FancyArrowPatch(path=path, arrowstyle="-|>", mutation_scale=12,
                                         color="tab:blue", lw=1.3)
        ax.add_patch(arrow)
        ax.text(control[0], height * 0.6, label, ha="center", fontsize=8, color="tab:blue")

    root = next(t for t in tokens if t.head.i == t.i)
    ax.text(root.i, 0.15, "ROOT", ha="center", fontsize=9, color="tab:red", fontweight="bold")

    ax.set_xlim(-1, n)
    ax.set_ylim(-0.6, max_height + 0.4)
    ax.axis("off")
    ax.set_title(f"係り受け解析(GiNZA): 『{SENTENCE_DEP}』")
    fig.tight_layout()
    out_path = "dependency_parsing.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")


def main() -> None:
    demo_morphological_analysis()
    doc = demo_dependency_parsing()
    visualize_dependency_tree(doc)

    print(
        "\n形態素解析が『文を単語に割る』処理であるのに対し、係り受け解析は"
        "『割られた単語同士がどう結びついているか』を明らかにする一段階上の処理にあたる。"
        "図の矢印は『係る側』から『係り先(head)』に向けて引いており、"
        "例えば『データを』は動詞『使って』に係る(obj: 目的語)、"
        "『深層学習モデルは』は文全体の主語(nsubj)として文末の動詞に係る、"
        "といった関係が可視化できている。word2vec/fastText(意味の近さを連続値の"
        "ベクトルで表す統計的手法)とは対照的に、形態素解析・係り受け解析は"
        "文法規則に基づいて『どの単語がどの役割を持つか』という離散的な構造を"
        "明示的に取り出す手法であり、機械翻訳や文書要約などのより高度なタスクの"
        "前処理として使われることが多い。"
    )


if __name__ == "__main__":
    main()
