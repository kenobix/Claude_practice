# 260427 — Ollama + OpenClaw ローカルLLMエージェント環境構築

## 概要

WSL2上にOpenClaw（AIエージェントフレームワーク）とOllama（ローカルLLMサーバー）を使って、
ローカルで動作するAIエージェント環境を構築した記録。

## 構成

```
Windows (ホスト)
└─ Ollama サーバー (gemma4:e4b モデル)
   └─ OLLAMA_HOST=0.0.0.0 でWSL2からアクセス可

WSL2 (Ubuntu)
└─ OpenClaw gateway (systemdサービス, port 18789)
   └─ openclaw chat (TUI)
      └─ → http://172.20.112.1:11434 (WindowsのOllama)
```

## 使用モデル

- ベースモデル: `gemma4:e4b`（Google Gemma 4、マルチモーダル）
- カスタムモデル: `gemma4-agent`（Modelfileでパラメータ調整済み）

## Modelfile

```
FROM gemma4:e4b

PARAMETER num_ctx 128000
PARAMETER num_predict 2048
PARAMETER temperature 0.7
```

`num_ctx 128000`はOpenClawが送るAPIリクエストと一致させる（不一致だとrunnerが2つ起動しOOM）。

## Ollama起動コマンド（Windows PowerShell）

```powershell
# Vulkanを無効化してCPUモードで安定動作
$env:OLLAMA_VULKAN = "false"
$env:OLLAMA_KEEP_ALIVE = "24h"
$env:OLLAMA_HOST = "0.0.0.0"
ollama serve
```

**Vulkanを有効にしてはいけない理由**: AMD Radeon iGPUのVulkanドライバが
`ggml_backend_sched_graph_compute_async`で`Exception 0xe06d7363`を発生させ、
推論中にクラッシュする（OS/ドライバレベルの問題）。

## OpenClaw設定 (`~/.openclaw/openclaw.json`)

重要な設定箇所：

```json
"agents": {
  "defaults": {
    "model": { "primary": "ollama/gemma4-agent" },
    "timeoutSeconds": 600
  }
},
"models": {
  "providers": {
    "ollama": {
      "baseUrl": "http://172.20.112.1:11434"
    }
  }
}
```

## ワークスペース設定 (`~/.openclaw/workspace/`)

| ファイル | 内容 |
|---------|------|
| `IDENTITY.md` | エージェント名・キャラクター設定 |
| `USER.md` | ユーザー情報（名前・タイムゾーン） |
| `AGENTS.md` | エージェント動作ルール（日本語返答指示含む） |
| `SOUL.md` | エージェントの性格・行動原則 |

## デバッグ記録

### 問題①: KVキャッシュミスマッチ（解決済み）
- **原因**: Modelfileの`num_ctx=32768`とOpenClawのAPIリクエスト`128000`が不一致
- **解決**: Modelfileを`num_ctx 128000`に統一

### 問題②: Vulkanクラッシュ（解決済み）
- **症状**: `Exception 0xe06d7363 @ ggml_backend_sched_graph_compute_async`
- **解決**: `OLLAMA_VULKAN=false`でCPUモード強制

### 問題③: ストリーミングwatchdog 30秒（調査中）
- **症状**: `streaming watchdog: no stream updates for 30s; resetting status`
- **原因**: `DEFAULT_STREAMING_WATCHDOG_MS = 3e4`（30秒）ハードコード
- **場所**: `~/.nvm/.../openclaw/dist/tui-cli-DXIo9qbx.js` line 3004
- **対処**: `3e4` → `3e5`（300秒）にパッチ済み。TUI再起動で有効化
- **根本原因**: CPU推論でプリフィル（5kトークン）が30秒超かかる

### 問題④: HTTPタイムアウト約2分（調査中）
- **症状**: `[GIN] 500 | 1m57s | POST "/api/chat"`
- **原因**: OpenClawゲートウェイがOllama HTTPコネクションを~2分で切断
- **調査結果**:
  - `timeoutSeconds: 600`はエージェント全体タイムアウト（HTTP接続タイムアウトとは別）
  - undici（HTTPクライアント）の`bodyTimeout`は30分（問題なし）
  - 正確な切断トリガーは特定中
- **未解決**: Flash Attention有効化で推論速度改善を検討中

### 問題⑤: コンテキストサイズ肥大（解決済み）
- **症状**: 14kトークン（ワークスペースファイルが大きすぎる）
- **解決**: `AGENTS.md`を7850バイト→1116バイトに削減（86%削減）
- **結果**: ワークスペース合計4263バイト（旧10997バイト）

## CPU推論ベンチマーク

環境: AMD Ryzen（CPU only、Vulkan無効）、gemma4:e4b

| 条件 | 時間 |
|------|------|
| モデルロード（初回） | 約10.7秒 |
| `num_ctx 4096`、短いプロンプト、応答100トークン程度 | 約2分36秒 |

**結論**: CPU単体では1回の応答に2〜3分かかるため、30秒watchdogと2分HTTPタイムアウトが
いずれも問題になる。Flash Attention有効化で改善を検討中。

## 現在の状態

- Ollama: Vulkan無効・CPUモードで安定動作中
- OpenClaw: ゲートウェイ起動中、TUIで接続可能
- チャット: watchdogパッチ適用済み（TUI再起動で有効化）、2分HTTPタイムアウトは未解決
- 次のステップ: Flash Attention有効化テスト、HTTPタイムアウト原因特定

## 関連ファイル

- [Modelfile](./Modelfile) — gemma4-agentの定義
- `~/.openclaw/openclaw.json` — OpenClaw設定
- `~/.openclaw/workspace/` — エージェントワークスペース
