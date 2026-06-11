# 260521 — チャットアプリ構築記録（OpenAI / Gemini）

GitHub Pages上で動作するチャットアプリの構築記録。OpenAI APIとGemini APIの2種類を実装。

---

## 作成したもの

| ファイル | API | 無料枠 | 動作確認 |
|---------|-----|--------|---------|
| [index.html](./index.html) | OpenAI | なし（$5必要） | クォータエラーで未動作 |
| [gemini.html](./gemini.html) | Gemini | あり（クレジット不要） | **動作確認済み** ✓ |

### 共通の特徴

- APIキーをUI上で入力 → ブラウザの `localStorage` にのみ保存（サーバー送信なし）
- 会話履歴を保持したマルチターン対話
- 入力中インジケーター（タイピングアニメーション）
- エラーメッセージの日本語表示
- Shift+Enter で改行 / Enter で送信

### OpenAI版（index.html）

- モデル選択: gpt-4.1-mini / gpt-4o-mini / gpt-4.1 / gpt-4o
- GitHub Pages URL: `https://kenobix.github.io/Claude_practice/260521/`

### Gemini版（gemini.html）

- モデル選択: gemini-2.5-flash / gemini-2.5-flash-lite / gemini-2.5-pro
- GitHub Pages URL: `https://kenobix.github.io/Claude_practice/260521/gemini.html`
- Gemini APIキーは [Google AI Studio](https://aistudio.google.com/) から無料取得可能

### 動作確認結果（Gemini版）

「こんにちは」→「こんにちは！何かお手伝いできることはありますか？」  
「あなたの名前を教えてください。」→「私はGoogleによってトレーニングされた、大規模言語モデルです。」  
マルチターン対話・日本語応答ともに正常動作を確認。

---

## APIキー取得手順

1. [OpenAI Platform](https://platform.openai.com/) にアクセス
2. Settings → Organization → API keys → **「Create new secret key」**
3. Project に「Default project」を選択して作成
4. 表示された `sk-...` のキーを必ずコピーして保存（再表示不可）

---

## 無料枠の調査結果

### データ共有による無料トークンプログラム（現在は終了）

OpenAIは「Share inputs and outputs with OpenAI」を有効にすることで
無料トークンを付与するプログラムを2024年12月〜2025年4月30日まで実施していた。

| モデル種別 | 無料トークン上限（1日あたり） |
|-----------|--------------------------|
| 大型モデル（GPT-4o等） | Tier1-2: 250,000トークン |
| 小型モデル（GPT-4o-mini等） | Tier1-2: 2,500,000トークン |

**2025年4月30日でプログラム終了。2026年5月現在は利用不可。**

参考: [OpenAI Community - Free tokens program](https://community.openai.com/t/free-tokens-on-traffic-shared-with-openai-extended-through-april-30-2025/1129643)

### 現在（2026年5月）のOpenAI APIの無料枠

| 項目 | 内容 |
|------|------|
| 新規アカウント無料クレジット | **廃止済み** |
| データ共有無料トークン | **2025年4月30日終了** |
| 現在の無料枠 | **なし**（Tier 1になるには$5以上のチャージが必要） |

### 発生したエラー

```
You exceeded your current quota, please check your plan and billing details.
```

**原因:** アカウントのクレジットが$0.00で、かつデータ共有無料プログラムも終了しているため。

### 対処法

| 方法 | 内容 |
|------|------|
| **クレジット追加** | Settings → Billing → Add credits で最低$5チャージ |
| **Gemini APIを使う** | Google AI Studioの無料枠（クレジット不要）を利用 |

---

## Data Controls の設定状況

設定済みの内容（Settings → Data Controls → Sharing）:

| 項目 | 設定 |
|------|------|
| Enable sharing of model feedback | Disabled |
| Share evaluation and fine-tuning data | Disabled（週7回無料evalの特典あり） |
| **Share inputs and outputs with OpenAI** | **Enabled for all projects** |

「Share inputs and outputs」はオンにしたが、無料トークンプログラムは終了済みのため効果なし。
この設定をオンにすると、API送受信データがOpenAIの学習に使用される。

---

## 今後の選択肢

### OpenAI版を使いたい場合

Settings → Billing → Add credits で最低$5チャージ。
`gpt-4.1-mini` は低コストで1,000〜数万回の応答が可能。

### 無料で使い続けたい場合

Gemini版（gemini.html）を使う。[260427フォルダ](../260427/)で構築済みの
Gemini API環境と同じAPIキーがそのまま利用できる。

---

## セキュリティ上の注意

- APIキーをコードに直接書かない（GitHubに公開されるため）
- 本アプリはユーザーがUIでAPIキーを入力する設計（`localStorage` のみ保存）
- `Share inputs and outputs` をオンにしている場合、会話内容がOpenAIに送信される

---

## Gemini APIで取得できるデータまとめ（`google-genai` 新SDK）

`from google import genai` の `client.models.*` 経由で呼べる主なAPIと、それぞれのレスポンスで取得できる情報のまとめ（[260525/phase1](../260525/phase1/rag_phase1.py)での実装経験をもとに整理）。

### 1. `generate_content`（テキスト生成 / チャット応答）

**リクエスト時に指定できる主なパラメータ（`GenerateContentConfig`）:**

| パラメータ | 内容 |
|-----------|------|
| `temperature` | 出力のランダム性（0に近いほど決定的、高いほど多様） |
| `top_p` / `top_k` | 候補トークンの絞り込み方法 |
| `max_output_tokens` | 出力トークン数の上限 |
| `system_instruction` | システムプロンプト（役割・口調などの指示） |
| `safety_settings` | 有害コンテンツのフィルタ強度 |
| `response_mime_type` | `application/json`等を指定して構造化出力させる |
| `tools` | 関数呼び出し（Function Calling）やGoogle検索連携の定義 |

**レスポンスから取得できる主な情報:**

| 項目 | 内容 |
|------|------|
| `response.text` | 生成されたテキスト本文（最も基本） |
| `response.candidates` | 生成候補のリスト（複数候補を要求した場合） |
| `response.candidates[].finish_reason` | 終了理由（`STOP`, `MAX_TOKENS`, `SAFETY`など） |
| `response.candidates[].safety_ratings` | カテゴリ別の安全性評価 |
| `response.usage_metadata.prompt_token_count` | 入力プロンプトのトークン数 |
| `response.usage_metadata.candidates_token_count` | 出力のトークン数 |
| `response.usage_metadata.total_token_count` | 合計トークン数（課金・無料枠管理に使う） |

### 2. `embed_content`（テキストのベクトル化）

[260525/phase1/rag_phase1.py](../260525/phase1/rag_phase1.py)で実際に使用。

**リクエスト時に指定できる主なパラメータ（`EmbedContentConfig`）:**

| パラメータ | 内容 |
|-----------|------|
| `task_type` | 用途指定。`RETRIEVAL_DOCUMENT`（検索される側）/ `RETRIEVAL_QUERY`（検索する側）/ `SEMANTIC_SIMILARITY` / `CLASSIFICATION` など |
| `output_dimensionality` | ベクトルの次元数を指定して圧縮可能（デフォルトは`gemini-embedding-001`で3072次元） |

**レスポンスから取得できる主な情報:**

| 項目 | 内容 |
|------|------|
| `result.embeddings[].values` | 埋め込みベクトル本体（floatのリスト。例: 3072個） |
| `result.embeddings[].statistics` | トークン数などの統計情報 |

### 3. `models.list()`（利用可能なモデル一覧）

| 項目 | 内容 |
|------|------|
| `model.name` | モデルID（例: `gemini-2.5-flash`, `gemini-embedding-001`） |
| `model.supported_actions` | そのモデルで使える操作（`generateContent`, `embedContent`など） |
| `model.input_token_limit` / `output_token_limit` | 入出力の最大トークン数 |

### 4. ストリーミング（`generate_content_stream`）

`response.text`を一括取得する代わりに、生成中のテキストをチャンク単位で逐次受け取れる。チャットUIのタイピング表示などに利用（[gemini.html](./gemini.html)で実装）。

### まとめ

- **「質問への応答（テキスト）」だけでなく**、トークン使用量・安全性評価・終了理由など**メタ情報も同時に取得できる**
- Embedding APIは「ベクトル」そのものに加えて、ベクトルの次元数を指定で変えられる
- 用途（検索用 vs 生成用、テキスト用 vs ベクトル用）によって呼ぶAPIエンドポイントとパラメータが異なる点に注意
