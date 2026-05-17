# Gemini API統合記録（260517）

Ollamaローカル推論からGoogle Gemini API（無料枠）へ移行した作業記録。

---

## 背景

CPU推論のOllamaは短い質問でも~20秒かかり、複雑な質問は失敗することがあった。
Google AI Studioの無料枠でGemini APIが利用できることが判明したため、移行を実施。

---

## Google AI Studio 無料枠の仕様（2025年12月以降）

| モデル | RPM | RPD | 備考 |
|--------|-----|-----|------|
| Gemini 2.5 Pro | 5 | 100 | 制限が厳しく実用困難 |
| **Gemini 2.5 Flash** | **10** | **1,000** | **バランスが良く推奨** |
| Gemini 2.5 Flash-Lite | 15 | 1,500 | 最も制限が緩い |

- APIキーはクレジットカード不要で取得可能
- [Google AI Studio](https://aistudio.google.com/)から発行
- WSL2での動作制限なし（サーバーサイドでクォータ管理）

---

## 実施手順

### 1. Gemini APIキーの取得

Google AI Studio（https://aistudio.google.com/）でAPIキーを発行。

### 2. OpenClawへの登録

```bash
openclaw onboard --auth-choice gemini-api-key
```

ウィザードで以下を選択:
- QuickStart（既存設定を保持）
- 既存の `GEMINI_API_KEY` 環境変数を使用
- モデル: `google/gemini-3.1-pro-preview`（後に `gemini-2.5-flash` に変更）
- Web検索プロバイダ: Gemini
- Gateway: Restart

ウィザード完了後、`~/.openclaw/openclaw.json` に以下が追記される:
```json
{
  "agents": {
    "defaults": {
      "model": { "primary": "google/gemini-2.5-flash" }
    }
  },
  "plugins": {
    "entries": {
      "google": { "enabled": true }
    }
  },
  "auth": {
    "profiles": {
      "google:default": { "provider": "google", "mode": "api_key" }
    }
  }
}
```

### 3. systemdサービスへの環境変数設定

systemdユーザーサービスは `.bashrc` の環境変数を継承しない。
`~/.config/systemd/user/openclaw-gateway.service.d/override.conf` を作成:

```ini
[Service]
Environment="GEMINI_API_KEY=<your-api-key>"
Environment="OPENCLAW_DISABLE_BONJOUR=true"
```

```bash
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
```

---

## 発生したエラーと解決方法

### エラー①: Gatewayが約40秒ごとにクラッシュ（WSL2 mDNS問題）

**症状:**
```
CIAO PROBING CANCELLED
Main process exited, code=exited, status=1/FAILURE
```

**原因:** WSL2はmDNS（Bonjour/CIAO）をサポートしない。
OpenClawのBonjour拡張が `@homebridge/ciao` ライブラリを使用しており、
WSL2環境で `CIAO PROBING CANCELLED` という未処理拒否（unhandled rejection）が発生。

OpenClawは独自のunhandled rejectionハンドラ
（`unhandled-rejections-Bzjq4Io_.js` の `installUnhandledRejectionHandler()`）を持ち、
これが `process.exit(1)` を呼ぶため、Node.jsの `--unhandled-rejections=warn` は効かない。

**試みた失敗策:**
- `openclaw config set plugins.bonjour.enabled false` → `Unrecognized key: "bonjour"` エラー
- `openclaw config set gateway.bonjour.mode off` → 同上エラー
- `NODE_OPTIONS=--unhandled-rejections=warn` をsystemd override.confに追加 → 効果なし

**解決:** OpenClawソースコード（`extensions/bonjour/index.js`）に公式の無効化環境変数が存在:
```ini
Environment="OPENCLAW_DISABLE_BONJOUR=true"
```

### エラー②: `models.providers.google` の追加がリバートされる

**症状:** `openclaw.json` に手動で `models.providers.google` ブロックを追加しても、
保存後すぐに消えてしまう。

**原因:** Gatewayが `openclaw.json` をinotifyで監視しており、ファイルが変更されると
バリデーション・フォーマットして書き戻す。

**解決:** Gatewayを停止してから編集:
```bash
systemctl --user stop openclaw-gateway.service
# ファイルを編集（Python経由）
systemctl --user start openclaw-gateway.service
```

### エラー③: `models.providers.google.models: expected array`

**症状:**
```
Config invalid
models.providers.google.models: Invalid input: expected array, received undefined
```

**原因:** `models.providers` セクションはカスタム・セルフホスト型プロバイダー用。
Google公式プラグイン（`plugins.entries.google.enabled: true`）はモデルを内部で自動登録するため、
`models.providers.google` は不要かつ記述するとスキーマエラーになる。

**解決:** `models.providers.google` ブロックを削除。Googleプラグインが自動でモデルを管理する。

### エラー④: TUIが `local embedded` モードで起動し、Gatewayを使わない

**症状:** `openclaw chat` を実行すると、タイトルバーに `local embedded` と表示される。
GatewayのログにTUIからのリクエストが届いていない。

**原因:** `openclaw chat`（= `openclaw tui`）はデフォルトでローカル埋め込みランタイムを使用する。
Gatewayに接続するには明示的にURLを指定する必要がある。

**現在の状況:** TUIがGatewayに自動接続するURL設定が未確立。（下記「未解決の問題」参照）

### エラー⑤: セッションファイルのロック競合

**症状:**
```
run error: session file locked (timeout 10000ms): pid=XXXXX
~/.openclaw/agents/main/sessions/<uuid>.jsonl.lock
```

**原因:** TUIが埋め込みランタイムで起動すると、Gatewayプロセスと同じセッションファイルを
同時書き込みしようとしてデッドロックが発生する。

**解決（暫定）:** TUIをGatewayに接続するモードで起動する（下記参照）。

---

## モデル名について

OpenClawの内部モデルIDとGoogle APIのモデルIDは異なる:

| OpenClaw ID | Google API モデル | 状態 |
|-------------|------------------|------|
| `google/gemini-3.1-pro-preview` | 不明（存在しない可能性あり） | 要確認 |
| `google/gemini-2.5-flash` | `gemini-2.5-flash` | 動作確認済み |
| `google/gemini-2.5-pro` | `gemini-2.5-pro` | 動作確認済み |

`gemini-3.1-pro-preview` はOpenClawのUI上の名前であり、Google APIの実際のモデルIDと
一致しない可能性がある。確実に動作するのは `gemini-2.5-flash`。

---

## 現在の設定（260517時点）

### `~/.openclaw/openclaw.json`（主要部分）

```json
{
  "agents": {
    "defaults": {
      "model": { "primary": "google/gemini-2.5-flash" },
      "timeoutSeconds": 600,
      "llm": { "idleTimeoutSeconds": 300 }
    }
  },
  "tools": {
    "profile": "minimal",
    "web": { "search": { "provider": "gemini", "enabled": true } }
  },
  "plugins": {
    "entries": {
      "ollama": { "enabled": true },
      "google": { "enabled": true }
    }
  },
  "auth": {
    "profiles": {
      "google:default": { "provider": "google", "mode": "api_key" }
    }
  }
}
```

### `~/.config/systemd/user/openclaw-gateway.service.d/override.conf`

```ini
[Service]
Environment="GEMINI_API_KEY=<your-api-key-here>"
Environment="OPENCLAW_DISABLE_BONJOUR=true"
```

### `~/.openclaw/.env`

```
GEMINI_API_KEY=<your-api-key-here>
```

---

## TUIとGatewayの接続問題

### 問題

`openclaw chat` はデフォルトで `local embedded` モードで起動する。
GatewayとTUIが同じセッションファイル（`~/.openclaw/agents/main/sessions/<uuid>.jsonl.lock`）を
同時にロックしようとし、デッドロックが発生することがある。

### 根本原因

`openclaw.json` の `gateway.remote.url` が未設定の場合、TUIはGatewayに自動接続しない。
`openclaw chat` はデフォルトで自分自身の組み込みランタイムを起動するため、
Gatewayと競合する。

### 対処法（未検証）

同じ問題が再発した場合、TUIをGatewayのクライアントとして明示的に接続する:

**ステップ1: Gatewayのトークンを取得**

`openclaw config get` はリダクトされるため、jsonから直接取得する:

```bash
jq -r '.gateway.auth.token' ~/.openclaw/openclaw.json
```

**ステップ2: トークンを指定してTUIを起動**

```bash
openclaw chat --url ws://127.0.0.1:18789 --token <ステップ1で表示された値>
```

または1行でまとめて:

```bash
openclaw chat --url ws://127.0.0.1:18789 --token "$(jq -r '.gateway.auth.token' ~/.openclaw/openclaw.json)"
```

**確認ポイント:**

TUIが起動したらタイトルバーを確認する:
- 成功: `local ready | idle | google/gemini-2.5-flash | ...`（`local embedded` 表示がなくなる）
- 失敗: `local embedded` と表示される → URLまたはトークンが間違っている

ロックエラーが発生しなくなれば接続成功。

---

## ローカルモデルとの性能比較

移行前は `qwen2.5:1.5b`（OpenClaw上のカスタム名: `gemma4-agent`）をCPU推論で使用していた。

| 項目 | qwen2.5:1.5b（ローカル） | Gemini 2.5 Flash（API） |
|------|--------------------------|-------------------------|
| 応答速度 | 短問で~20秒、複雑な質問は失敗することがある | ほぼ即時（数秒） |
| コンテキストウィンドウ | 16,384トークン | 1,000,000トークン |
| 日本語品質 | 限定的（1.5bモデルの限界） | 自然な日本語で回答可能 |
| 安定性 | watchdogタイムアウト・OOMリスクあり | APIサーバー側で管理、クライアント負荷なし |
| コスト | 電力・RAM消費（28GB割当） | 無料枠（RPD 1,000回/日） |

Gemini 2.5 Flash APIへの移行により、応答品質・速度・安定性が大幅に向上した。

---

## 今後の展望：他のAPIプロバイダーとの比較

### OpenAI GPT APIに無料枠はあるか？

**結論: 条件付きで存在する（ただしデータ共有が必要）。**

OpenAIはかつて新規アカウントへの無料クレジット（$5〜$18相当）を付与していたが、
**2024年以降このプログラムは廃止された。**

ただし、**「データ共有プログラム」による別の無料枠が存在する**（2025年確認）:

OpenAI APIの設定で **「Share Logs with OpenAI」をオンにする**と、
送信データがOpenAIの学習に使用される代わりに、無料トークン枠が付与される。

| モデル種別 | 無料トークン上限（1日あたり） |
|-----------|--------------------------|
| 標準〜大型モデル（o3等） | 100万〜250万トークン |
| 軽量モデル | 1,000万〜2,500万トークン |

**注意点:**
- 個人情報・機密情報の送信は禁止（データがOpenAIのトレーニングに使われるため）
- 「無料クレジット（$）」ではなく「データ提供と引き換えの利用枠」
- クレジットカード登録は別途必要な場合がある

### Google AI Studio との比較

| 項目 | OpenAI API（データ共有枠） | Google AI Studio |
|------|--------------------------|-----------------|
| 無料の条件 | データ共有をオンにする | クレジットカード不要 |
| プライバシー | 送信データがOpenAIの学習に使用される | Googleのポリシーに準拠 |
| 無料トークン量 | 大量（モデルにより異なる） | RPD 1,000回（Gemini 2.5 Flash） |
| 手軽さ | アカウント設定変更が必要 | APIキー発行だけで即利用可 |

### 現時点の推奨

個人情報や業務データを扱わない学習・実験用途であれば、
OpenAI APIのデータ共有枠も有力な選択肢となる。
ただし、プライバシー上の懸念がある場合は **Google AI Studio（Gemini 2.5 Flash）の方が安心**。

---

## 参考資料

- [Google AI Studio](https://aistudio.google.com/) — APIキー発行
- [Google AI Studio Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits) — 公式レートリミット
- [OpenClaw Google Provider ドキュメント](https://docs.openclaw.ai/providers/google)
- [OpenClaw Gemini API Key セットアップ（haimaker.ai）](https://haimaker.ai/blog/gemini-api-key-openclaw/)
- [Gemini API Free Tier 2026 (YingTu)](https://yingtu.ai/en/blog/gemini-api-free-tier)
