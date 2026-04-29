# 260427 — Ollama + OpenClaw ローカルLLMエージェント環境構築

## 概要

WSL2上にOpenClaw（AIエージェントフレームワーク）とOllama（ローカルLLMサーバー）を使って、
ローカルで動作するAIエージェント環境を構築した記録。

## 最終構成（260430時点）

```
WSL2 (Ubuntu)
├─ Ollama サーバー (systemd service, port 127.0.0.1:11434)
│   └─ gemma4-agent (qwen2.5:1.5b ベース, num_ctx 16384)
└─ OpenClaw gateway (systemd user service, port 18789)
    └─ openclaw chat (TUI)
        └─ → http://127.0.0.1:11434
```

## 使用モデル

- ベースモデル: `qwen2.5:1.5b`（超軽量・日本語対応）
- カスタムモデル: `gemma4-agent`（Modelfileでパラメータ調整済み）

### モデル変遷

| 日付 | モデル | 理由 |
|------|--------|------|
| 260427 | gemma4:e4b | 初期構成 |
| 260428 | qwen2.5:3b | CPU推論速度改善のため軽量化 |
| 260430 | qwen2.5:1.5b | さらなる軽量化（3bの約半分） |

## Modelfile

```
FROM qwen2.5:1.5b

PARAMETER num_ctx 16384
PARAMETER num_predict 1024
PARAMETER temperature 0.7
```

`num_ctx`はopenclaw.jsonの`contextWindow`と一致させる（不一致だとrunnerが2つ起動しOOM）。
OpenClawの最小値は16000トークンのため、16384が現実的な下限。

## OpenClaw設定 (`~/.openclaw/openclaw.json`)

重要な設定箇所：

```json
"agents": {
  "defaults": {
    "model": { "primary": "ollama/gemma4-agent" },
    "timeoutSeconds": 600,
    "llm": {
      "idleTimeoutSeconds": 300
    }
  }
},
"tools": {
  "profile": "minimal",
  "allow": []
},
"models": {
  "providers": {
    "ollama": {
      "baseUrl": "http://127.0.0.1:11434",
      "models": [
        {
          "id": "gemma4-agent",
          "contextWindow": 16384,
          "maxTokens": 1024
        }
      ]
    }
  }
}
```

## ワークスペース設定 (`~/.openclaw/workspace/`)

| ファイル | 内容 |
|---------|------|
| `IDENTITY.md` | エージェント名・キャラクター設定 |
| `USER.md` | ユーザー情報（名前・タイムゾーン） |
| `AGENTS.md` | エージェント動作ルール（日本語返答指示含む）|
| `SOUL.md` | エージェントの性格・行動原則 |

合計 ~4263バイト（トークン換算 ~1-2k）。

## TUIパッチ

`~/.nvm/versions/node/v22.22.2/lib/node_modules/openclaw/dist/tui-cli-DXIo9qbx.js` line 3004:

```js
// 変更前: const DEFAULT_STREAMING_WATCHDOG_MS = 3e4;  // 30秒
const DEFAULT_STREAMING_WATCHDOG_MS = 3e5;  // 300秒
```

CPU推論の遅さに対応するため300秒に延長。

## デバッグ記録

### 問題①: KVキャッシュミスマッチ（解決済み）
- **原因**: Modelfileの`num_ctx`とOpenClawのAPIリクエストが不一致
- **解決**: Modelfileとopenclaw.jsonの`contextWindow`を`32768`に統一

### 問題②: Vulkanクラッシュ（解決済み → WSL2移行で不要）
- **症状**: `Exception 0xe06d7363 @ ggml_backend_sched_graph_compute_async`
- **解決当時**: `OLLAMA_VULKAN=false`でCPUモード強制
- **最終**: WSL2にOllamaを移行したため、このオプション不要になった

### 問題③: ストリーミングwatchdog 30秒（解決済み）
- **症状**: `streaming watchdog: no stream updates for 30s; resetting status`
- **解決**: TUIファイルの`3e4`→`3e5`（300秒）にパッチ

### 問題④: HTTPタイムアウト約2分（解決済み）
- **症状**: `[GIN] 500 | 1m57s | POST "/api/chat"`
- **原因**: Windows HostedのOllamaへのHTTP接続が~2分でタイムアウト
- **解決**: WSL2にOllamaを移行（127.0.0.1でloopback接続）

### 問題⑤: コンテキストサイズ肥大（解決済み）
- **症状**: `14k/128k (11%)`（tools.profile: "coding"が~12kトークンのツール定義を追加）
- **解決**: `tools.profile: "minimal"` + `tools.allow: []` + contextWindow修正
- **結果**: `4.6k/33k (14%)`

### 問題⑥: llm-idle-timeout（解決済み）
- **症状**: `run aborted` after ~4m15s
- **原因**: gateway側のidle watchdogが2分でタイムアウト → 2回リトライ → abort
- **解決**: `agents.defaults.llm.idleTimeoutSeconds: 300`に延長

### 問題⑦: streaming watchdog 300秒（大幅改善）
- **症状**: `streaming watchdog: no stream updates for 300s; resetting status`
- **原因**: CPU推論でプリフィルが300秒を超える
- **対策①**: Flash Attention有効化 → CPU推論への効果なし
- **対策②**: qwen2.5:3b→1.5bに変更 → 短い質問は300s以内に改善
- **対策③**: num_ctx 32768→16384に削減 → **~20秒に大幅短縮**
- **注意**: TUIのトークン表示は`X/33k`のままだが実際は16384で推論している（OpenClawがOllamaのモデルメタデータから表示値を取得するため）
- **現状**: 短問は20s程度、複雑な質問でも大幅改善

## CPU推論ベンチマーク

環境: AMD Ryzen（CPU only）、WSL2 Ubuntu

| モデル | 条件 | 応答時間 |
|--------|------|---------|
| gemma4:e4b | openclaw (14kトークン) | 300s超でabort |
| qwen2.5:3b | curl 短文（システムプロンプトなし） | 数秒 |
| qwen2.5:3b | openclaw (4.6kトークン) | ~300秒（watchdog後に応答） |
| qwen2.5:1.5b | openclaw 短問（5.2kトークン, num_ctx 32768） | 300s以内 |
| qwen2.5:1.5b | openclaw 複雑な質問（5.4kトークン, num_ctx 32768） | ~300s（応答破棄される場合あり） |
| qwen2.5:1.5b | openclaw 短問（~5k トークン, num_ctx 16384） | **~20秒** |

## Flash Attention設定（適用済み・CPU効果なし）

`/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
```

Flash AttentionはGPU向けの最適化のため、CPU推論への効果は限定的。

## WSL2 RAM設定 (`/mnt/c/Users/kensh/.wslconfig`)

```ini
[wsl2]
memory=28GB
processors=8
```

デフォルト（~15GB）から28GBに拡張。WSL2再起動（PowerShellで`wsl --shutdown`）後に有効化。

## 関連ファイル

- [Modelfile](./Modelfile) — gemma4-agentの定義
- `~/.openclaw/openclaw.json` — OpenClaw設定
- `~/.openclaw/workspace/` — エージェントワークスペース
