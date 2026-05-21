# 260521 — OpenAI API チャットアプリ構築記録

GitHub Pages上で動作するOpenAI APIを使ったチャットアプリの構築記録。

---

## 作成したもの

- [index.html](./index.html) — ブラウザのみで動作するチャットUI

### アプリの特徴

- APIキーをUI上で入力 → ブラウザの `localStorage` にのみ保存（サーバー送信なし）
- モデル選択: gpt-4.1-mini / gpt-4o-mini / gpt-4.1 / gpt-4o
- 会話履歴を保持したマルチターン対話
- 入力中インジケーター（タイピングアニメーション）
- エラーメッセージの日本語表示
- Shift+Enter で改行 / Enter で送信

### GitHub Pages URL

```
https://kenobix.github.io/Claude_practice/260521/
```

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

### A. OpenAIクレジットを追加して使う（$5〜）

Settings → Billing → Add credits で最低$5チャージ。
`gpt-4.1-mini` は低コストで1,000〜数万回の応答が可能。

### B. チャットアプリをGemini API対応に変更する

[260427フォルダ](../260427/)で構築済みのGemini API環境（無料・動作確認済み）を使って、
同じチャットアプリをGemini対応に改修する。

---

## セキュリティ上の注意

- APIキーをコードに直接書かない（GitHubに公開されるため）
- 本アプリはユーザーがUIでAPIキーを入力する設計（`localStorage` のみ保存）
- `Share inputs and outputs` をオンにしている場合、会話内容がOpenAIに送信される
