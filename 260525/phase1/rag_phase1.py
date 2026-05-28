"""
Phase 1: フレームワークなしの最小RAG実装

学習目標:
  - Embeddingがどういうものかを確認する（次元数、値の範囲）
  - コサイン類似度で「意味が近い文章」を検索する原理を理解する
  - LLMに「文脈＋質問」を渡すと回答が変わることを体験する

実行前の準備:
  pip install -r requirements.txt
  export GOOGLE_API_KEY="your-api-key"   # Google AI Studio で取得

実行:
  python rag_phase1.py
  python rag_phase1.py "質問をここに書く"

注意: google-generativeai（旧SDK）は非推奨。google-genai（新SDK）を使う。
"""

import os
import sys
import numpy as np
from google import genai
from google.genai import types

# ── 設定 ──────────────────────────────────────────────────────────────────────

EMBEDDING_MODEL = "gemini-embedding-001"  # 3072次元のベクトルを生成（新SDK）
GENERATION_MODEL = "gemini-2.5-flash"
DOCS_PATH = "./docs/sample.txt"

# ── 初期化 ────────────────────────────────────────────────────────────────────

api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key:
    print("エラー: GOOGLE_API_KEY 環境変数が設定されていません。")
    print("  export GOOGLE_API_KEY='your-api-key'")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# ── ステップ 1: ドキュメントの読み込みとチャンク分割 ──────────────────────────

def load_and_chunk(filepath: str) -> list[str]:
    """テキストファイルを読み込み、段落単位でチャンク化する。"""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    # 空行で段落分割し、空のチャンクを除去
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return chunks

# ── ステップ 2: Embeddingの生成 ───────────────────────────────────────────────

def embed_documents(texts: list[str]) -> np.ndarray:
    """ドキュメント群をベクトル化する。task_type=RETRIEVAL_DOCUMENT を使う。"""
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    # result.embeddings はリスト。各要素の .values が float のリスト
    return np.array([e.values for e in result.embeddings])  # shape: (len(texts), 768)

def embed_query(text: str) -> np.ndarray:
    """クエリをベクトル化する。task_type=RETRIEVAL_QUERY を使う（ドキュメントと別）。"""
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return np.array(result.embeddings[0].values)  # shape: (768,)

# ── ステップ 3: コサイン類似度による検索 ─────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """2つのベクトルのコサイン類似度を計算する（-1〜1、1に近いほど類似）。"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def retrieve(query_vec: np.ndarray, doc_vecs: np.ndarray, chunks: list[str], top_k: int = 3):
    """クエリベクトルに最も近いチャンクを top_k 件返す。"""
    scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]
    ranked = sorted(zip(scores, chunks), reverse=True)
    return ranked[:top_k]

# ── ステップ 4: LLMで回答生成 ─────────────────────────────────────────────────

def generate_answer(question: str, context_chunks: list[str]) -> str:
    """検索結果をコンテキストとして渡し、LLMに回答させる。"""
    context = "\n\n".join(context_chunks)
    prompt = f"""以下の文脈を元に質問に答えてください。
文脈に含まれない情報については「文脈にはありません」と答えてください。

文脈:
{context}

質問: {question}
"""
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
    )
    return response.text

# ── メイン ────────────────────────────────────────────────────────────────────

def main():
    # コマンドライン引数で質問を受け取る（なければデフォルト質問を使う）
    question = sys.argv[1] if len(sys.argv) > 1 else "富士山について教えてください。"

    print("=" * 60)
    print(f"質問: {question}")
    print("=" * 60)

    # 1. ドキュメント読み込み
    chunks = load_and_chunk(DOCS_PATH)
    print(f"\n[Step 1] ドキュメントを {len(chunks)} チャンクに分割しました。")
    for i, c in enumerate(chunks, 1):
        print(f"  チャンク{i}: {c[:40]}...")

    # 2. Embedding
    print(f"\n[Step 2] {len(chunks)} チャンクをベクトル化中...")
    doc_vecs = embed_documents(chunks)
    print(f"  → 各チャンクが {doc_vecs.shape[1]} 次元のベクトルになりました。")
    print(f"  → ベクトル配列の shape: {doc_vecs.shape}")
    print(f"  → チャンク1のベクトル先頭5要素: {doc_vecs[0][:5].round(4)}")

    # 3. クエリのEmbedding + 検索
    print(f"\n[Step 3] クエリをベクトル化して類似チャンクを検索...")
    query_vec = embed_query(question)
    results = retrieve(query_vec, doc_vecs, chunks, top_k=3)

    print("\n  類似度スコア上位3件:")
    for i, (score, chunk) in enumerate(results, 1):
        preview = chunk[:50].replace("\n", " ")
        print(f"  {i}. score={score:.4f}  「{preview}...」")

    # 4. LLMで回答生成
    print("\n[Step 4] LLMに検索結果＋質問を渡して回答生成...")
    context_texts = [chunk for _, chunk in results]
    answer = generate_answer(question, context_texts)

    print("\n" + "=" * 60)
    print("回答:")
    print("=" * 60)
    print(answer)

    # ── 観察ポイント ──────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("【観察ポイント】")
    print(f"  - ベクトルの次元数: {doc_vecs.shape[1]} (gemini-embedding-001 の固定値)")
    print(f"  - 最高類似度スコア: {results[0][0]:.4f}")
    print(f"  - 最低類似度スコア: {results[-1][0]:.4f}")
    print("  - 質問を変えて検索結果がどう変わるか確認しましょう。")
    print("    例: python rag_phase1.py 'Pythonとは何ですか？'")
    print("    例: python rag_phase1.py '機械学習の種類を教えて'")
    print("-" * 60)


if __name__ == "__main__":
    main()
