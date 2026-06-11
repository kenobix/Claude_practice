# RAG（Retrieval-Augmented Generation）学習ガイド

WSL2上でプログラムを書きながらRAGを学ぶための学習ロードマップ。
前回Claudeに提示された内容を精査・修正し、学習順序を整理した。

---

## 前回の内容の精査結果

### 概念説明（Part 1）: 正確

RAGの定義・必要性・処理フローの説明はすべて正確。

### コンポーネント整理（Part 2）: ほぼ正確

- `text-embedding-004`（Google）は実在するEmbeddingモデルで正しい
- ChromaDB / FAISS / Pinecone はいずれも正しい選択肢
- OpenClawをオーケストレーターとして使う説明も正しい

### コード例（Part 3）: **要注意点あり**

以下の点が現在（2026年時点）は変わっている：

| 箇所 | 前回の内容 | 現在の正しい状況 |
|------|----------|----------------|
| `RetrievalQA` | `langchain.chains` からimport | **LangChain 0.2以降で非推奨（deprecated）**。LCEL（LangChain Expression Language）が推奨される |
| Chroma import | `langchain_community.vectorstores` から | 新しいバージョンでは `langchain_chroma` パッケージに移動 |
| Ollama import | `langchain_community.llms` から | 新しいバージョンでは `langchain_ollama` パッケージに移動 |
| `sentence-transformers` | インストールリストに含まれる | サンプルコード内では未使用。ローカルEmbeddingを使う場合に必要 |
| Geminiモデル名 | `gemini-2.0-flash` | 現在は `gemini-2.5-flash` が推奨 |
| ChromaDB永続化 | `persist_directory` 指定のみ | 新しいChromaDB（0.4+）はデフォルトで自動永続化。`vectordb.persist()` の明示的呼び出し不要 |

### GitHub Pages制約（Part 4）: 正確

静的サイトではサーバーサイド処理が動かないという説明は正確。

---

## RAGの概念整理（修正・補足版）

### RAGとは

RAG（Retrieval-Augmented Generation）とは、AIが回答を生成する前に関連情報を外部から検索・取得して、それをコンテキストとして与える仕組み。

### なぜ必要か

LLM（GPT / Gemini / Claude）には根本的な欠点が2つある：

1. **知識カットオフ** — 学習日時以降の情報を知らない
2. **ドメイン知識の欠如** — 社内ドキュメント・個人メモ・特定の専門資料を知らない

RAGはこれを解決する。

### 処理フロー

```
【事前準備（インデックス作成）】
ドキュメント群（PDF / Markdown / テキスト等）
  ↓ テキスト分割（チャンク化）
  ↓ Embeddingモデルでベクトル化
  ↓ ベクトルDBに保存

【質問時（推論）】
ユーザーの質問
  ↓ 同じEmbeddingモデルでベクトル化
  ↓ ベクトルDBでコサイン類似度検索 → 関連チャンク取得（Retrieval）
  ↓ 「以下の文脈を元に答えて：{取得チャンク}\n質問：{ユーザーの質問}」
  ↓ LLMが回答生成（Generation）
  ↓ 回答
```

### コンポーネントと選択肢

| コンポーネント | 役割 | 選択肢 |
|-------------|------|--------|
| **LLM** | 最終的な文章生成 | Gemini API、Claude API、Ollama（ローカル） |
| **Embeddingモデル** | テキストをベクトルに変換 | `text-embedding-004`（Google）、`nomic-embed-text`（Ollama）|
| **ベクトルDB** | ベクトルを保存・検索 | ChromaDB（ローカル・学習向け）、FAISS（高速・メモリ）、Pinecone（クラウド）|
| **オーケストレーター** | 検索→プロンプト構築→LLM呼び出しの制御 | Pythonコード直書き → LangChain → OpenClaw |

> **重要:** EmbeddingモデルとLLMは別物。LLMをGeminiからOllamaに切り替えても、Embeddingは引き続きGemini APIを使うことができる（品質が高いため推奨）。

---

## 学習ロードマップ

### Phase 1: 基礎理解（1〜2日）

フレームワークを使わずにRAGの動作原理を手で理解する。

**学ぶこと:**
- ベクトル（Embedding）とは何か
- コサイン類似度で「似ている文章」を探す仕組み
- プロンプトエンジニアリングの基礎

**注意: google-generativeai（旧SDK）は2025年に非推奨となった。`google-genai`（新SDK）を使う。**

```bash
pip install google-genai numpy
export GOOGLE_API_KEY="your-api-key"
```

**手を動かす内容:**

実装済みサンプル: [phase1/rag_phase1.py](./phase1/rag_phase1.py)

```python
# Phase1: フレームワークなしの最小RAG（新SDK版）
from google import genai
from google.genai import types
import numpy as np

client = genai.Client(api_key="your-gemini-api-key")

# テキストをベクトル化（ドキュメント用）
def embed_docs(texts):
    result = client.models.embed_content(
        model="gemini-embedding-001",  # text-embedding-004 は旧SDK用
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return np.array([e.values for e in result.embeddings])

# テキストをベクトル化（クエリ用）
def embed_query(text):
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return np.array(result.embeddings[0].values)

# コサイン類似度
def cosine_similarity(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

docs = ["東京の人口は約1400万人です。", "大阪は西日本最大の都市です。", "富士山の高さは3776メートルです。"]
doc_vecs = embed_docs(docs)

query = "富士山について教えて"
q_vec = embed_query(query)

scores = [cosine_similarity(q_vec, dv) for dv in doc_vecs]
best = docs[np.argmax(scores)]
print("検索結果:", best)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=f"情報: {best}\n質問: {query}"
)
print(response.text)
```

**確認ポイント:**
- ベクトルの次元数を確認する（`gemini-embedding-001` は3072次元）
- 類似度スコアの値の範囲を観察する
- 質問を変えると検索結果がどう変わるか確認する

---

## Phase 1 実施結果（2026-05-28〜2026-06-11）

### 処理の流れ（図解）

```
┌─────────────────────────┐
│ docs/sample.txt（7段落）  │
└────────────┬─────────────┘
             │ load_and_chunk()  ※空行で分割
             ▼
┌─────────────────────────┐
│ チャンク1〜7（文字列）    │
└────────────┬─────────────┘
             │ embed_documents()  task_type=RETRIEVAL_DOCUMENT
             ▼
┌─────────────────────────────────┐
│ doc_vecs: shape (7, 3072)        │  ← gemini-embedding-001
└────────────┬──────────────────────┘
             │
質問文 ──► embed_query() task_type=RETRIEVAL_QUERY ──► query_vec (3072,)
             │
             ▼
┌─────────────────────────────────────────┐
│ cosine_similarity(query_vec, 各doc_vec)   │
│ → スコア降順でtop_k=3件を retrieve()       │
└────────────┬──────────────────────────────┘
             │ 上位3チャンクをcontextとして結合
             ▼
┌─────────────────────────────────────────┐
│ generate_answer(question, context_chunks) │
│ gemini-2.5-flash に「文脈＋質問」を渡す     │
└────────────┬──────────────────────────────┘
             ▼
          回答テキスト
```

### 実行コマンド

venv（`venv/`）はpip未インストールでactivateスクリプトも存在しなかったため、システムのユーザーローカル環境にパッケージを入れて`python3`で直接実行する。

```bash
cd 260525/phase1
python3 -m pip install -r requirements.txt --user --break-system-packages
export GEMINI_API_KEY="your-api-key"   # GOOGLE_API_KEY でも可
python3 rag_phase1.py "富士山について教えてください。"
```

### テスト結果

| 質問 | top1チャンク | スコア | 回答 |
|------|-------------|--------|------|
| 富士山について教えてください。 | 富士山チャンク | 0.7423 | 正しく富士山の説明を返した |
| 富士山について教えて | 富士山チャンク | 0.7392 | 正しく富士山の説明を返した |
| Pythonとは何ですか？ | Pythonチャンク | 0.7300 | 正しくPythonの説明を返した |
| Pythonとは何？ | Pythonチャンク | 0.7338 | 正しくPythonの説明を返した |

→ いずれも質問と意味的に近いチャンクが最高スコアで検索され、そのチャンクの内容に基づいた回答が生成された。**コサイン類似度による検索とRAGの基本動作は正しく機能していることを確認。**

### 発生した問題と対応

| 問題 | 原因 | 対応 |
|------|------|------|
| `python: command not found` | WSL2に`python`コマンド（`python-is-python3`）が未インストール | `python3`コマンドを使用 |
| `GOOGLE_API_KEY 環境変数が設定されていません` | `GEMINI_API_KEY`を設定していたが、スクリプトは`GOOGLE_API_KEY`のみ参照 | [rag_phase1.py](./phase1/rag_phase1.py)を修正し、`GOOGLE_API_KEY`と`GEMINI_API_KEY`の両方に対応 |
| `503 UNAVAILABLE`（モデル高負荷） | Gemini API側の一時的な高負荷（コード側のバグではない） | 数秒後に再実行したら成功。一時的なエラーのため再試行で解決 |
| `venv/`が空でactivateできない | venvが`pip`なしで作成されていた | システムのユーザーローカル環境（`--user --break-system-packages`）を使用。`venv/`は今後削除予定 |

### 観察事項

- ベクトルの次元数は`gemini-embedding-001`で**3072次元**（学習ガイド作成当初に記載していた`text-embedding-004`の768次元から変更）
- 同じ「富士山」関連の質問でも文末表現（「教えてください」と「教えて」）でスコアがわずかに変動する（0.7423 vs 0.7392）が、上位の順位には影響しない
- 503エラーはGoogle API側の一時的な高負荷によるもので、コードの問題ではない。再試行で解決する

### 追加テスト（質問の傾向を変えた場合）

| 質問 | top1チャンク | スコア | 回答 |
|------|-------------|--------|------|
| 富士山は何県と何県の間にありますか | 富士山チャンク | 0.7328 | 「富士山は静岡県と山梨県にまたがっています。」と正しく回答 |
| 機械学習とLLMの違いを教えてください | 機械学習チャンク(0.6997)、RAGチャンク(0.6797) | 0.6997 | 機械学習の説明は文脈から回答。LLMの定義は「文脈にはない」と正直に回答（部分的に「文脈にはありません」を含む応答） |
| 日本で二番目に高い山は何ですか | 富士山チャンク | 0.6921 | **「文脈にはありません」** |

**この結果から分かること（重要）:**

1. **「富士山は何県と何県の間にありますか」** → 検索でヒットした富士山チャンクの中から、質問が求める「県名」という具体的な情報をLLMが正しく抜き出して回答できている。チャンク全文をそのまま返すのではなく、**質問に応じて必要な部分を要約・抽出する**のがGenerationの役割であることが分かる。

2. **「機械学習とLLMの違い」** → top1とtop2のスコアが0.6997と0.6797で僅差。これは「機械学習」チャンクと「RAG」チャンク（LLMについて触れている）の両方が、ある程度質問に関連するトピックだったため。LLMは検索結果のうち**機械学習について書かれている部分は回答し、LLMの定義については「文脈にはない」と切り分けて回答**した。これはプロンプトの「文脈に含まれない情報については『文脈にはありません』と答えてください」という指示が効いている。

3. **「日本で二番目に高い山は何ですか」** ← **最も重要な観察**。
   - 検索スコアは0.6921と、他の質問と同程度に「それなりに高い」
   - しかし回答は**「文脈にはありません」**
   - つまり、**コサイン類似度が高い ＝ 質問に対する答えがそこに書いてある、とは限らない**。富士山チャンクは「山」というトピックでは質問に近いが、「2番目に高い山」という情報は実際には書かれていない
   - **検索（Retrieval）は「トピックが近い文章」を見つけるだけ**で、「正確な答えが含まれているか」を判断するのは**生成（Generation）側のLLMの役割**。この役割分担を理解することがRAGの設計上とても重要
   - もしプロンプトに「文脈にない場合は『文脈にはありません』と答えてください」という指示がなければ、LLMは富士山チャンクの情報をもとに**もっともらしい誤った回答（ハルシネーション）**を生成していた可能性がある

---

## Google APIと`docs/sample.txt`の関係

**疑問:** テキストファイル(`sample.txt`)が手元にあるなら、Google APIは不要では？

**答え: いいえ、APIは2箇所で必須です。** `sample.txt`は「生の知識データ」を提供しているだけで、それを**検索可能にする処理**と**回答文を作る処理**には、それぞれAIモデルの計算能力が必要だからです。

```
docs/sample.txt（ただのテキストファイル・ローカル）
        │
        │ ① embed_documents() / embed_query()
        │    ─ Google API（gemini-embedding-001）が必要
        │    ─ 文章 → 3072次元のベクトルへの変換は、
        │      巨大な学習済みモデルでないと計算できない
        ▼
   doc_vecs / query_vec（ベクトル）
        │
        │ ② cosine_similarity() / retrieve()
        │    ─ ここはAPI不要。numpyだけで計算できる
        │    ─ 単なる数値計算（内積・ノルム）
        ▼
   関連チャンク（テキスト）
        │
        │ ③ generate_answer()
        │    ─ Google API（gemini-2.5-flash）が必要
        │    ─ 「文脈＋質問」を理解し、自然な日本語の
        │      回答文を作るのはLLMにしかできない
        ▼
      最終回答
```

**それぞれのステップでなぜAPIが必要か:**

| ステップ | API必要？ | 理由 |
|---------|----------|------|
| ①Embedding（ベクトル化） | **必要** | 「意味の近さ」を表す3072次元のベクトルへの変換は、Googleが学習させた巨大なニューラルネットワーク（Embeddingモデル）でしか計算できない。自分のPCで同等のモデルを動かすことも理論上は可能（後述）だが、Phase1では手軽さと精度のためAPIを使っている |
| ②類似度計算・検索 | **不要** | ベクトル同士の内積やノルムの計算は単純な数値演算なので、`numpy`があればローカルで完結する |
| ③回答生成 | **必要** | 「検索結果の文章を読んで、質問に対する自然な日本語の回答を作る」のはLLM（大規模言語モデル）の仕事。`sample.txt`にはこの「読解して答えを作る」機能は当然含まれていない |

**もしAPIを使わなかったら何ができるか:**
- `sample.txt`に対して`grep`などで**キーワードの完全一致検索**はできる（例: 「富士山」という単語を含む行を探す）
- しかし「富士山は何県と何県の間にありますか」のように、**質問文に「県」という単語があってもチャンク内に同じ単語が無いケースで意味的に正しいチャンクを探す**ことはできない（これがEmbeddingの強み）
- また、たとえ正しいチャンクが見つかっても、それを**質問に対する自然な文章として整形・要約する**にはLLMが必要

**補足（ローカルで動かす選択肢）:**
学習ガイドのPhase3以降で触れているように、`Ollama`などを使えばEmbeddingモデルもLLMも自分のPC上で動かすことができ、その場合はGoogle/外部APIが不要になる。ただし精度や速度はクラウドAPIに劣ることが多い。

---

## 「スコア計算」と「回答生成」、それぞれどこで何のAPIが使われているか

**質問:** 現状のスクリプトでは、スコア（コサイン類似度）はAPIなしで求めて、実際の回答だけAPIを使っているのか？

**回答:** その理解で正しい。`rag_phase1.py`の中でAPIを呼んでいるのは**2箇所だけ**で、スコア計算自体は呼んでいない。

```python
# ① API呼び出し（Embeddingモデル） ─ ベクトルを取得するため
doc_vecs  = embed_documents(chunks)   # client.models.embed_content(...)
query_vec = embed_query(question)     # client.models.embed_content(...)

# ② API不要（ローカル計算） ─ 取得済みのベクトルを使うだけ
results = retrieve(query_vec, doc_vecs, chunks, top_k=3)
#   └─ 中身は cosine_similarity() = np.dot() と np.linalg.norm() だけ

# ③ API呼び出し（生成モデル） ─ 検索結果＋質問から回答文を作るため
answer = generate_answer(question, context_texts)  # client.models.generate_content(...)
```

つまり「スコアを求める」ためにAPIが必要なのではなく、**スコアの元になる『ベクトル』を作る①の時点でAPIを使っており、スコア自体（②）はそのベクトルを使ったただの数値計算**、という整理になる。

### これは学習用の仕組みか？実際のRAGでも同じか？

**この役割分担（①Embedding API → ②ローカルでベクトル比較 → ③生成API）は、Phase1特有のものではなく、実際のRAGシステムでも基本的に同じ構造。** 違うのは②の部分の「実装方法」だけ。

| | Phase1（今回） | 実際のRAGシステム |
|---|---|---|
| ①ベクトル化 | Google API（`embed_content`）を都度呼ぶ | 同様にAPIまたはローカルEmbeddingモデルを使う |
| ②類似度計算・検索 | `numpy`で全チャンクと総当たりのコサイン類似度を計算（毎回） | **ベクトルDB**（ChromaDB, FAISS, Pineconeなど）に事前保存し、近似最近傍探索（ANN）で高速検索 |
| ③回答生成 | Google API（`generate_content`）を呼ぶ | 同様にLLM APIまたはローカルLLMを呼ぶ |

つまり実際のシステムとの最大の違いは、**②を「毎回全件総当たりで計算するか」「事前にベクトルDBへ保存しておき高速に検索するか」**という点。Phase1はチャンクが7個しかないので毎回計算しても一瞬だが、ドキュメントが数万件になると総当たりは遅すぎるため、ベクトルDB（Phase2で導入予定）が必要になる。

**まとめ:**
- 「ベクトル化」と「回答生成」にAPI（≒AIモデル）が必要、という構造は学習用・実用問わず共通
- 「類似度計算・検索」をどう効率化するか（毎回計算 vs ベクトルDB）が、学習用と実用の主な違い

---

## Phase 1 用語解説（初学者向け詳細）

「チャンク化」「ベクトル化（Embedding）」「コサイン類似度検索」「スコア」が、それぞれ何をしているのかを実際のデータで追いながら説明する。

### 1. チャンク化（Chunking）— 文章を検索しやすい単位に分割する

**何をしているか:**
`docs/sample.txt`は1つのファイルだが、中には「東京」「富士山」「大阪」「Python」など全く違う話題の段落が混在している。これを丸ごと1つの塊としてLLMに渡すと、質問と無関係な情報までコンテキストに含まれてしまい、回答の精度が落ちる。

そこで`load_and_chunk()`は、**空行（段落の区切り）でテキストを分割**し、7つの「チャンク」（小さな文章のかたまり）にする。

```python
chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
```

```
sample.txt
┌─────────────────────────────┐
│ 東京都は日本の首都...          │ ← チャンク1
│                                │
│ 富士山は日本最高峰の山...       │ ← チャンク2
│                                │
│ 大阪府は西日本最大の...         │ ← チャンク3
│        ︙（以下7まで）          │
└─────────────────────────────┘
```

**なぜ必要か:** 後の検索ステップで「質問に最も関係するチャンクだけ」を選び出せるようにするため。チャンクが大きすぎると無関係な情報が混ざり、小さすぎると文脈が失われる（実用システムでは数百文字程度に分割することが多いが、Phase1では「段落＝チャンク」というシンプルな分割にしている）。

### 2. ベクトル化（Embedding）— 文章を「意味を表す数字の列」に変換する

**何をしているか:**
コンピュータは文字列同士の「意味の近さ」をそのままでは比較できない。そこでEmbeddingモデル（`gemini-embedding-001`）を使い、各チャンクを**3072個の数値が並んだ配列（ベクトル）**に変換する。

```python
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=texts,
    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
)
```

実行結果より、チャンク1（東京の説明）は次のようなベクトルになった（先頭5要素のみ表示）:

```
[-0.034, -0.0158, 0.0124, -0.0646, -0.0097, ... （合計3072個）]
```

**重要なポイント:**
- このベクトルは人間には意味不明な数字の羅列だが、**「意味が近い文章は、似たベクトル（近い場所）になる」**ように学習されたモデルが生成している
- 例えば「富士山は高い山です」と「富士山の標高は3776m」は文字列としては違うが、ベクトルにすると近い位置になる
- イメージとしては、すべての文章を「意味の地図」上の座標（3072次元空間内の点）にプロットしているようなもの

```
意味の地図（イメージ図・実際は3072次元）

        Python ●
                 ● 機械学習
   富士山 ●
       ● 東京
                          ● 大阪
```

→ 「富士山」と「東京」は地理の話なので近く、「Python」と「機械学習」はIT系の話なので近い、というイメージ。

**ドキュメント用とクエリ用でtask_typeが違う理由:**
- `embed_documents()`は`task_type="RETRIEVAL_DOCUMENT"` → 「検索される側」として最適化
- `embed_query()`は`task_type="RETRIEVAL_QUERY"` → 「検索する側（質問文）」として最適化
- 同じモデルでも目的に応じてベクトルの作られ方が微調整されており、検索精度が上がる

### 3. コサイン類似度検索 — ベクトル同士の「向きの近さ」を測る

**何をしているか:**
質問文もEmbeddingで3072次元のベクトル（`query_vec`）に変換し、これと7個のチャンクのベクトル（`doc_vecs`）それぞれとの「近さ」を計算する。

```python
def cosine_similarity(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
```

**仕組みのイメージ:**
2つのベクトルを矢印として描いたとき、**矢印同士の向きがどれくらい近いか**を表すのがコサイン類似度。

```
         query_vec（質問: 富士山について）
              ↗
            ／ θ（角度が小さい＝似ている）
          ／___→ doc_vecs[1]（富士山チャンク）
        ／
      ／＿＿＿＿＿＿＿＿→ doc_vecs[3]（Pythonチャンク）
                          （角度が大きい＝似ていない）
```

- 角度が0°（同じ向き）に近いほど → コサイン類似度は **1に近づく**（=とても似ている）
- 角度が90°（無関係な向き）に近いほど → コサイン類似度は **0に近づく**
- 角度が180°（正反対）に近いほど → コサイン類似度は **-1に近づく**

`np.dot(a, b)`はベクトルの「内積」（向きが揃っているほど大きくなる）、`np.linalg.norm(a)`はベクトルの「長さ」。内積を2つのベクトルの長さで割ることで、**長さの影響を消して「向きの近さ」だけを取り出す**のがコサイン類似度。

`retrieve()`は、質問ベクトルと7個のチャンクベクトルそれぞれの類似度を計算し、スコアが高い順に並び替えて上位3件（`top_k=3`）を返す。

### 4. スコア — 類似度の数値が何を意味するか

実行結果の例（質問:「富士山について教えてください。」）:

```
1. score=0.7423  「富士山は日本最高峰の山で、標高は3776メートルです...」
2. score=0.6041  「東京都は日本の首都であり、日本最大の都市です...」
3. score=0.5675  「Python（パイソン）は1991年にグイド・ヴァン・ロッサムによって...」
```

**読み方:**
- `0.7423`は、質問「富士山について教えてください」のベクトルと、富士山チャンクのベクトルの**コサイン類似度**
- 1位の富士山チャンク(0.7423)が、2位の東京チャンク(0.6041)より明確にスコアが高い → **質問と最も意味的に近い文章が正しく1位に来ている**
- 2位に「東京」が来ているのは、富士山も東京も「日本の地理」というやや近いトピックだから（無関係なPythonチャンクより高スコア）
- 全体的に0.5〜0.6程度のスコアでも「無関係」とは限らない点に注意（同じ言語・似た文体の文章同士は、内容が違っても多少のベース類似度を持つ）。**重要なのは絶対値そのものより「他のチャンクと比べて相対的に高いか」**

この上位3チャンクのテキストだけを`generate_answer()`でLLMに渡すことで、LLMは7チャンク全部ではなく**質問に関係する情報だけ**を見て回答できる。これがRAGの「Retrieval（検索）」が「Generation（生成）」を助ける仕組み。

---

### Phase 2: ChromaDB導入（2〜3日）

ドキュメント数が増えても検索できるようにベクトルDBを使う。

**学ぶこと:**
- ChromaDBの基本操作（追加・検索・削除）
- チャンク分割の考え方（chunk_size / chunk_overlap）
- 永続化（ディスクに保存して再利用）

**環境セットアップ:**

```bash
cd ~/rag-project
python3 -m venv venv
source venv/bin/activate
pip install chromadb google-generativeai pypdf
```

**手を動かす内容:**

```python
# Phase2: ChromaDBを使ったRAG（フレームワークなし）
import chromadb
import google.generativeai as genai

genai.configure(api_key="your-gemini-api-key")

# ChromaDB初期化（永続化あり）
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection("my_docs")

def embed(texts):
    result = genai.embed_content(
        model="models/text-embedding-004",
        content=texts,
        task_type="retrieval_document"
    )
    return result["embedding"]

# ドキュメント追加
docs = ["東京の人口は約1400万人です。", "富士山の高さは3776メートルです。"]
embeddings = embed(docs)
collection.add(
    documents=docs,
    embeddings=embeddings,
    ids=["doc1", "doc2"]
)

# 検索
query = "富士山の高さは？"
q_emb = genai.embed_content(
    model="models/text-embedding-004",
    content=query,
    task_type="retrieval_query"
)["embedding"]

results = collection.query(query_embeddings=[q_emb], n_results=2)
context = "\n".join(results["documents"][0])

# LLMで回答
model = genai.GenerativeModel("gemini-2.5-flash")
prompt = f"情報:\n{context}\n\n質問: {query}"
print(model.generate_content(prompt).text)
```

**チャンク分割の考え方:**

```python
def simple_chunker(text, chunk_size=300, overlap=50):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks
```

| パラメータ | 小さい値 | 大きい値 |
|-----------|---------|---------|
| `chunk_size` | 精度は高いが文脈が途切れやすい | 文脈が豊富だがノイズが増える |
| `chunk_overlap` | 分割境界で情報が途切れる | 重複が増えるがつながりが保たれる |

---

### Phase 3: LangChainで整理（3〜4日）

Phase 1〜2で手書きしたパイプラインをLangChainで書き直し、フレームワークの価値を理解する。

**重要:** LangChain 0.2以降では `RetrievalQA`（旧API）は非推奨。**LCEL（LangChain Expression Language）** を使う。

**インストール:**

```bash
pip install langchain langchain-google-genai langchain-chroma langchain-community pypdf
```

**LCEL版のRAG（現在の推奨スタイル）:**

```python
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
import os

os.environ["GOOGLE_API_KEY"] = "your-gemini-api-key"

# 1. ドキュメント読み込み・チャンク分割
loader = TextLoader("./docs/sample.txt")
docs = loader.load()
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
chunks = splitter.split_documents(docs)

# 2. ベクトルDB構築
embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
vectordb = Chroma.from_documents(chunks, embeddings, persist_directory="./chroma_db")
retriever = vectordb.as_retriever(search_kwargs={"k": 3})

# 3. プロンプトテンプレート
prompt = ChatPromptTemplate.from_template("""
以下の文脈を元に質問に答えてください。文脈にない情報は「わかりません」と答えてください。

文脈:
{context}

質問: {question}
""")

# 4. LCELチェーン（|でつなぐパイプライン）
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

# 5. 実行
answer = chain.invoke("ドキュメントの要点は何ですか？")
print(answer)
```

**旧来の `RetrievalQA`（参考）:**

```python
# ※非推奨。既存コードを読む際の参考として残す
from langchain.chains import RetrievalQA
qa_chain = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)
# 上記のLCEL版に書き換えることを推奨
```

---

### Phase 4: PDFや複数ファイルの対応（2〜3日）

実際のユースケースに近い形でドキュメントを処理する。

**学ぶこと:**
- PDFの読み込みと前処理
- 複数ファイルの一括インデックス化
- メタデータの付与（どのファイルの何ページか）

```python
from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader

# PDFを読み込む
loader = PyPDFLoader("./docs/report.pdf")
pages = loader.load()  # 各ページがDocumentオブジェクトになる

# ディレクトリ内の全PDFを読み込む
loader = DirectoryLoader("./docs/", glob="**/*.pdf", loader_cls=PyPDFLoader)
all_docs = loader.load()

# メタデータを確認（source, page が自動付与される）
for doc in all_docs[:3]:
    print(doc.metadata)  # {'source': 'docs/report.pdf', 'page': 0}
```

**手を動かす内容:**
1. 自分のMarkdownメモや手元のPDFをドキュメントとして使う
2. 「このドキュメントの○○の部分を教えて」と質問して正確に答えられるか確認する

---

### Phase 5: 精度改善と評価（3〜5日）

RAGの弱点を理解し、改善手法を学ぶ。

**学ぶこと:**
- チャンク戦略の比較（文単位 vs 固定長 vs セマンティック）
- ハイブリッド検索（ベクトル検索 + キーワード検索）
- リランキング（検索結果の再スコアリング）
- RAG評価指標（Context Precision / Answer Faithfulness）

**チャンク戦略の違い:**

| 方法 | 説明 | 向いている用途 |
|------|------|-------------|
| `RecursiveCharacterTextSplitter` | 段落→文→文字の順で分割 | 汎用（最初はこれ） |
| `SentenceTransformersTokenTextSplitter` | トークン数基準で分割 | モデルのコンテキスト上限を意識する場合 |
| セマンティック分割 | 意味の変わり目で分割 | 長い技術文書 |

**簡易評価（手動）:**

| 質問 | 正解 | RAGの回答 | 評価 |
|------|------|-----------|------|
| Q1 | 〇〇 | 〇〇 | ✓ |
| Q2 | △△ | 全然違う | ✗ → チャンク・検索数を調整 |

---

### Phase 6: Webアプリ化（3〜5日）

RAGをAPIサーバーとして公開し、フロントエンドから使えるようにする。

**構成:**

```
ブラウザ（HTML/JS）
  ↓ APIリクエスト
FastAPI（WSL2上）
  ├── ChromaDB（ローカル）
  └── Gemini API
```

**インストール:**

```bash
pip install fastapi uvicorn
```

**最小FastAPI + RAGサーバー:**

```python
# server.py
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
# ... （Phase3のRAGセットアップを流用）

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Query(BaseModel):
    question: str

@app.post("/ask")
async def ask(query: Query):
    answer = chain.invoke(query.question)
    return {"answer": answer}
```

```bash
uvicorn server:app --reload --port 8000
```

> GitHub Pagesでフロントエンドを公開し、このFastAPIサーバーにリクエストを送る構成が「Phase 4: GitHub Pagesでの動作可否」で説明されていた正しいアーキテクチャ。

---

## GitHub Pagesとの関係（再整理）

| 処理 | GitHub Pagesで動くか |
|------|-------------------|
| フロントエンドUI（HTML/CSS/JS） | ✅ 動く |
| ベクトルDBの検索 | ❌ サーバーが必要 |
| Embeddingの生成 | ❌ サーバーが必要 |
| LLM API呼び出し（クライアントサイド） | ⚠️ 技術的には可能だがAPIキーが露出するため公開リポジトリには不適 |

**現実的な構成:**
- 学習・検証：WSL2のFastAPIサーバーをlocalhostで使う
- 公開する場合：フロントはGitHub Pages、バックエンドはVPS（Fly.io / Render / Railway等）にデプロイ

---

## 環境構築チートシート

```bash
# WSL2 Ubuntu
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

mkdir ~/rag-project && cd ~/rag-project
python3 -m venv venv
source venv/bin/activate

# 基本セット（Phase 1〜3）
pip install google-generativeai chromadb \
    langchain langchain-google-genai langchain-chroma langchain-community \
    pypdf fastapi uvicorn

# Gemini APIキー設定（.envファイルや ~/.bashrc に書く）
export GOOGLE_API_KEY="your-api-key-here"
```

---

## 学習の全体像

```
Phase 1: ベクトル・類似度検索の原理を手で実装（フレームワークなし）
   ↓
Phase 2: ChromaDBで永続化・チャンク分割を理解
   ↓
Phase 3: LangChain（LCEL）でパイプラインをシンプルに書き直す
   ↓
Phase 4: PDF・複数ファイルを実際のドキュメントとして使う
   ↓
Phase 5: 精度改善（チャンク戦略・ハイブリッド検索・評価）
   ↓
Phase 6: FastAPIでWebアプリ化 → フロント（GitHub Pages）と連携
```

**各Phaseの目安日数:** 1〜3週間で Phase 1〜4 まで到達できる。  
Phase 5・6 はプロジェクトベースで進めると理解が深まる。

---

## 参考資料

- [LangChain 公式ドキュメント（LCEL）](https://python.langchain.com/docs/expression_language/)
- [Google AI Studio（Gemini APIキー取得）](https://aistudio.google.com/)
- [ChromaDB ドキュメント](https://docs.trychroma.com/)
- [Gemini Embedding API](https://ai.google.dev/gemini-api/docs/embeddings)
