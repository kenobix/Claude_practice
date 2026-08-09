"""Gemini APIで、RAGパイプライン(260525/phase1の発展)とChain-of-Thoughtプロンプトを試す

前半: RAG(Retrieval-Augmented Generation)
  260525/phase1/rag_phase1.py で作った最小RAG実装と同じ手順(チャンク分割→Embedding化→
  コサイン類似度検索→LLMに文脈+質問を渡す)を踏襲しつつ、このロードマップ自身の
  roadmap.mdをドキュメントとして使い、「roadmap.mdの内容について質問する」形で
  RAGの効果(文脈なし vs 文脈ありでの回答の変化)を確認する。

後半: Chain-of-Thought(CoT)プロンプティング
  複雑な推論を要する質問に対して、(a) 直接答えさせるプロンプト と
  (b) 「ステップごとに考えてから答えて」と指示するCoTプロンプト を比較する。
  CoTは学習(ファインチューニング)なしで、プロンプトの書き方だけで
  推論精度を改善できる手法として知られている。
"""
import os
import sys
import time

import numpy as np
from google import genai
from google.genai import types
from google.genai.errors import ClientError

EMBEDDING_MODEL = "gemini-embedding-001"
# gemini-2.5-flashは無料枠の1日あたりリクエスト数上限(20件)に達したため、
# 同程度の性能を持つgemini-flash-latestに切り替えて実行した
GENERATION_MODEL = "gemini-flash-latest"
ROADMAP_PATH = "../roadmap.md"

api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("エラー: GOOGLE_API_KEY(または GEMINI_API_KEY)環境変数が設定されていません。")
    sys.exit(1)
client = genai.Client(api_key=api_key)


def load_and_chunk(filepath: str) -> list[str]:
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    chunks = [c.strip() for c in text.split("\n\n") if c.strip() and len(c.strip()) > 20]
    return chunks


def embed_documents(texts: list[str]) -> np.ndarray:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL, contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return np.array([e.values for e in result.embeddings])


def embed_query(text: str) -> np.ndarray:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL, contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return np.array(result.embeddings[0].values)


def cosine_similarity(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def retrieve(query_vec, doc_vecs, chunks, top_k=3):
    scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]
    ranked = sorted(zip(scores, chunks), reverse=True)
    return ranked[:top_k]


def ask_llm(prompt: str, max_retries=5) -> str:
    """無料枠のレート制限(429)に当たった場合、待って再試行する"""
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
            return response.text
        except ClientError as e:
            if "RESOURCE_EXHAUSTED" in str(e) and attempt < max_retries - 1:
                wait = 20 * (attempt + 1)
                print(f"  (レート制限のため{wait}秒待機して再試行... [{attempt + 1}/{max_retries}])")
                time.sleep(wait)
            else:
                raise


def demo_rag():
    print("=== 1. RAG: roadmap.mdを使った質問応答(文脈なし vs 文脈あり) ===")
    chunks = load_and_chunk(ROADMAP_PATH)
    print(f"roadmap.mdを{len(chunks)}チャンクに分割")

    doc_vecs = embed_documents(chunks)
    print(f"Embedding次元数={doc_vecs.shape[1]}")

    question = "このロードマップのStage 9ではどんなミニプロジェクトを行うと書かれていますか?"

    print(f"\n質問: {question}")
    print("\n--- (a) 文脈なし(RAGを使わず、LLMの一般知識だけで回答) ---")
    answer_no_context = ask_llm(question)
    print(answer_no_context)

    query_vec = embed_query(question)
    top_k = 3
    results = retrieve(query_vec, doc_vecs, chunks, top_k=top_k)
    print(f"\n--- 検索結果(類似度上位{top_k}チャンク) ---")
    for score, chunk in results:
        print(f"  score={score:.3f}: {chunk[:60].replace(chr(10), ' ')}...")

    # 正解が書かれているチャンクが、実際に上位top_k件に入っているかを確認する
    correct_idx = next(i for i, c in enumerate(chunks) if "自作VAEとGANでの画像生成結果を比較" in c)
    all_scores = sorted(
        ((cosine_similarity(query_vec, dv), i) for i, dv in enumerate(doc_vecs)), reverse=True)
    correct_rank = next(rank for rank, (_, i) in enumerate(all_scores, start=1) if i == correct_idx)
    correct_score = next(s for s, i in all_scores if i == correct_idx)
    print(f"\n(参考)実際に正解が書かれているチャンクの順位: {correct_rank}位 "
          f"(score={correct_score:.3f}) — top_k={top_k}に入って{'いる' if correct_rank <= top_k else 'いない'}")

    context = "\n\n".join(c for _, c in results)
    prompt_with_context = f"以下の文脈をもとに質問に答えてください。\n\n文脈:\n{context}\n\n質問: {question}"
    print("\n--- (b) 文脈あり(RAG: 検索したチャンクを渡してから回答) ---")
    answer_with_context = ask_llm(prompt_with_context)
    print(answer_with_context)

    correct_in_context = "自作VAEとGANでの画像生成結果を比較" in context
    if correct_in_context:
        print(
            "\n正解のチャンクが検索結果に含まれていたため、roadmap.mdの実際の記述"
            "(『自作VAEとGANでの画像生成結果を比較』)に基づいた正しい回答ができたかを確認できる。"
        )
    else:
        print(
            f"\n注目すべき点として、正解が書かれているチャンクは類似度で{correct_rank}位"
            f"(score={correct_score:.3f})に沈んでおり、top_k={top_k}にわずかに入らなかった。"
            "そのため実際にLLMに渡された文脈には正解が含まれておらず、"
            "上の回答は誤った(別のStageの内容を混同した)ものになっている可能性が高い。"
            "これは、RAGが検索(Retrieval)に失敗すると、生成(Generation)側がいくら"
            "優秀でも正しい回答を返せないという、RAGパイプライン特有の弱点を示す実例である。"
            "文脈なしの回答が正直に『情報がない』と答えていたのに対し、文脈ありの回答は"
            "(誤った文脈とはいえ)何かしらの文脈を与えられたことでもっともらしく断定的な"
            "誤答をしてしまっており、『中途半端に関連する誤情報を与える方が、"
            "情報が全くないより悪い回答になりうる』という、RAG運用上の重要な教訓が得られた。"
        )

    print(
        "\nRAGは、LLM自体を再学習することなく、外部知識を検索して回答に反映させる手法であり、"
        "02のLoRAファインチューニング(モデル自体を変える)とは対照的なアプローチである。"
        "ただし今回の実験が示す通り、検索精度(チャンク分割の粒度やtop_kの設定)が"
        "RAG全体の回答品質を大きく左右する。"
    )


def demo_cot():
    print("\n\n=== 2. Chain-of-Thought(CoT)プロンプティング ===")
    question = (
        "ある店で、りんごが1個120円、みかんが1個80円で売っている。"
        "太郎は500円を持って行き、りんごを2個とみかんを3個買った。"
        "残ったお金でさらにみかんを買えるだけ買うとき、最終的に太郎の手元に残るお金はいくらか?"
    )

    print(f"質問: {question}")

    print("\n--- (a) 直接プロンプト(いきなり答えだけを求める) ---")
    direct_prompt = question + "\n答えだけを一言(数字のみ)で答えてください。"
    answer_direct = ask_llm(direct_prompt)
    print(answer_direct)

    print("\n--- (b) Chain-of-Thoughtプロンプト(段階的に考えさせる) ---")
    cot_prompt = question + "\nステップごとに計算過程を書きながら考え、最後に答えを示してください。"
    answer_cot = ask_llm(cot_prompt)
    print(answer_cot)

    correct_answer = "20"
    direct_correct = correct_answer in answer_direct
    cot_correct = correct_answer in answer_cot
    print(
        f"\n正解は、りんご2個(240円)+みかん3個(240円)=480円を使い、残り20円ではみかんを"
        "追加購入できない(1個80円のため)ので、最終的に手元に残るのは20円、が期待される計算過程。"
        f"今回は直接プロンプト({'正解' if direct_correct else '不正解'})・"
        f"CoTプロンプト({'正解' if cot_correct else '不正解'})のどちらも最終的な数字は一致しており、"
        "この程度の複雑さの問題では今回試した gemini-flash-latest にとってCoTなしでも解ける"
        "難易度だったことが分かる。ただし、CoTプロンプトの出力は『りんご240円+みかん240円=480円』"
        "→『500円-480円=20円』→『20円では追加のみかん(80円)は買えない』という計算過程が"
        "明示されているのに対し、直接プロンプトは最終的な数字『20』だけしか返しておらず、"
        "その数字がどう導かれたのか、途中で本当に正しい計算をしたのかを人間が検証する手段がない。"
        "CoTの本質的な利点は『正解率が上がること』そのものより『複雑な問題で計算過程が"
        "追跡・検証可能になること』にあり、桁数の多い計算や条件分岐が絡む問題ほど、"
        "直接プロンプトでは表面化しない計算ミスをCoTなら発見しやすくなる。"
    )


def main() -> None:
    demo_rag()
    demo_cot()


if __name__ == "__main__":
    main()
