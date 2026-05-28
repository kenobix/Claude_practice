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
- ベクトルの次元数を確認する（`text-embedding-004` は768次元）
- 類似度スコアの値の範囲を観察する
- 質問を変えると検索結果がどう変わるか確認する

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
