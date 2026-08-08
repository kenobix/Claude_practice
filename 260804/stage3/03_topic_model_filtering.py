"""トピックモデル(LDA)・協調フィルタリング・コンテンツベースフィルタリング

インターネット接続なしで再現できるよう、外部データセットを取得せず
手作りの小さなコーパス・評価行列で実装している。
"""
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.metrics.pairwise import cosine_similarity

DOCUMENTS = [
    "python is great for machine learning and data science",
    "deep learning models use neural networks and gradient descent",
    "pandas and numpy are useful libraries for data science in python",
    "neural networks learn weights through backpropagation and gradient descent",
    "soccer is a popular sport played with a ball and two teams",
    "the football match ended with a goal in the final minute",
    "basketball players score points by shooting the ball through a hoop",
    "the team practiced free throws and passing before the match",
    "japanese cuisine includes sushi ramen and tempura",
    "ramen noodles are served in a hot broth with pork and egg",
    "sushi is made with vinegared rice and fresh fish",
    "tempura is deep fried seafood and vegetables in light batter",
]


def demo_topic_model() -> None:
    print("=== 1. トピックモデル（LDA: Latent Dirichlet Allocation） ===")
    print(f"文書数: {len(DOCUMENTS)}件（機械学習/スポーツ/日本食の話題が混在した英文コーパス）")

    vectorizer = CountVectorizer(stop_words="english")
    X_counts = vectorizer.fit_transform(DOCUMENTS)
    vocab = vectorizer.get_feature_names_out()

    lda = LatentDirichletAllocation(n_components=3, random_state=42, max_iter=50)
    doc_topic = lda.fit_transform(X_counts)

    print("\n各トピックの上位単語(単語の出現しやすさが高い順):")
    for topic_idx, topic in enumerate(lda.components_):
        top_words = [vocab[i] for i in topic.argsort()[::-1][:6]]
        print(f"  トピック{topic_idx}: {', '.join(top_words)}")

    print("\n各文書がどのトピックに一番強く属するか:")
    assigned = doc_topic.argmax(axis=1)
    for doc, topic_idx, probs in zip(DOCUMENTS, assigned, doc_topic):
        print(f"  トピック{topic_idx} (確率{probs[topic_idx]:.2f}): {doc[:50]}...")

    print(
        "\nLDAは各文書を『複数のトピックの混合』、各トピックを『複数の単語の出現確率分布』"
        "とみなす生成モデル。事前にラベルを与えていないのに、日本食の文書(8〜11番目)は"
        "トピック0にほぼ綺麗にまとまった。一方、機械学習とスポーツの文書はトピック1と2に"
        "割れて混在しており、完全にはジャンル通りに分離できていない。"
        "これは文書数がわずか12件・各文書も短文で共有語彙が少ないため、LDAが"
        "統計的手がかりを十分に得られなかったことが原因。"
        "LDAの精度は文書数・語彙の豊富さに大きく依存するという教師なし学習共通の"
        "注意点(データが少ないと構造を見誤る)が、ここでも表れている。"
    )


def build_synthetic_ratings() -> pd.DataFrame:
    """5人のユーザーが6本の映画を評価した(欠損あり)評価行列を作る"""
    users = ["Alice", "Bob", "Carol", "Dave", "Eve"]
    movies = ["SF大作A", "SF大作B", "恋愛映画A", "恋愛映画B", "アクションA", "アクションB"]
    ratings = np.array(
        [
            [5, 4, 1, np.nan, 4, 5],  # Alice: SF・アクション好き
            [4, 5, 2, 1, 5, np.nan],  # Bob: SF・アクション好き
            [1, 2, 5, 4, np.nan, 2],  # Carol: 恋愛映画好き
            [np.nan, 1, 4, 5, 2, 1],  # Dave: 恋愛映画好き
            [4, np.nan, 2, 2, 5, 4],  # Eve: SF・アクション好き寄り
        ]
    )
    return pd.DataFrame(ratings, index=users, columns=movies)


def demo_collaborative_filtering(ratings: pd.DataFrame) -> None:
    print("\n=== 2. 協調フィルタリング（ユーザーベース） ===")
    print("評価行列(NaN=未評価):")
    print(ratings.to_string())

    # ユーザーごとの平均評価でNaNを仮埋めしてから類似度を計算する
    filled = ratings.apply(lambda row: row.fillna(row.mean()), axis=1)
    sim = pd.DataFrame(
        cosine_similarity(filled), index=ratings.index, columns=ratings.index
    )
    print("\nユーザー間のコサイン類似度:")
    print(sim.round(3).to_string())

    # 例: Eveが未評価の「SF大作B」を、Eveと似ているユーザーの評価から予測する
    target_user, target_item = "Eve", "SF大作B"
    sims = sim[target_user].drop(target_user)
    rated_by_others = ratings[target_item].drop(target_user).dropna()
    common_users = sims.index.intersection(rated_by_others.index)
    weights = sims[common_users]
    pred = float((weights * rated_by_others[common_users]).sum() / weights.abs().sum())
    print(
        f"\n『{target_user}』は『{target_item}』を未評価。"
        f"全ユーザーの評価を類似度で重み付け平均すると予測評価={pred:.2f}"
    )
    print(
        f"（{target_user}と最も似ているAlice(sim={sims['Alice']:.2f})・Bob(sim={sims['Bob']:.2f})は"
        f"それぞれ4・5と高評価している一方、似ていないCarol(sim={sims['Carol']:.2f})・"
        f"Dave(sim={sims['Dave']:.2f})の低評価(2・1)も重みを持って混ざるため、"
        "単純な全員の重み付き平均では中間的な値に引っ張られる。"
        "実務では上位k人の類似ユーザーだけに絞る『k近傍法』を組み合わせることが多い）"
    )
    print(
        "協調フィルタリングは『似た好みのユーザーは、まだ見ていない作品でも同じように"
        "評価するはず』という仮定に基づき、作品の中身(ジャンル等)を一切見ずに"
        "ユーザー×アイテムの評価パターンだけから予測する。"
    )


def demo_content_based_filtering() -> None:
    print("\n=== 3. コンテンツベースフィルタリング ===")
    movies = ["SF大作A", "SF大作B", "恋愛映画A", "恋愛映画B", "アクションA", "アクションB"]
    # 各映画のジャンル特徴量(0-1の含有度)。協調フィルタリングと異なり「作品の中身」を使う
    features = pd.DataFrame(
        {
            "SF": [0.9, 0.8, 0.0, 0.1, 0.2, 0.1],
            "恋愛": [0.0, 0.1, 0.9, 0.9, 0.0, 0.0],
            "アクション": [0.3, 0.2, 0.0, 0.0, 0.9, 0.95],
        },
        index=movies,
    )
    print("映画ごとのジャンル特徴量:")
    print(features.to_string())

    # Eveが高評価した「SF大作A」「アクションA」からEveの好みプロファイルを作る
    liked_movies = ["SF大作A", "アクションA"]
    profile = features.loc[liked_movies].mean(axis=0)
    print(f"\nEveの好みプロファイル(高評価した{liked_movies}のジャンル特徴量の平均):")
    print(profile.round(3).to_string())

    unrated = [m for m in movies if m not in liked_movies]
    sims = cosine_similarity([profile], features.loc[unrated])[0]
    recommendation = pd.Series(sims, index=unrated).sort_values(ascending=False)
    print("\nEveの好みプロファイルと、まだ評価していない映画とのコサイン類似度:")
    print(recommendation.round(3).to_string())
    print(
        f"\n最もおすすめ: 『{recommendation.index[0]}』"
        "(Eveが好きなジャンル傾向に最も近い作品を、他ユーザーの評価を使わず作品の特徴だけで推薦)"
    )
    print(
        "コンテンツベースフィルタリングは『そのユーザー自身が過去に好んだ作品と似た特徴を持つ"
        "作品』を推薦する。協調フィルタリングと違い他ユーザーのデータが不要なため、"
        "新規ユーザー・新規アイテムにも対応しやすい一方、ジャンル等の特徴量設計に推薦の質が左右される。"
    )


def main() -> None:
    demo_topic_model()
    ratings = build_synthetic_ratings()
    demo_collaborative_filtering(ratings)
    demo_content_based_filtering()


if __name__ == "__main__":
    main()
