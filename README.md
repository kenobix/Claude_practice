# claude_practice

Claude Code の学習・実践リポジトリ。日付ごとにディレクトリを作成し、作業ログと成果物を管理している。

**GitHub Pages:** https://kenobix.github.io/Claude_practice/

---

## ディレクトリ一覧

| ディレクトリ | 内容 |
|------------|------|
| [260330](260330/) | Pythonでテキストアドベンチャーゲーム（Claude Quest）を作成。Claude Codeによるコード生成の初回実践。 |
| [260331](260331/) | ローカルリポジトリをGitHubにpushするまでの作業記録。PAT認証・メールプライバシー設定・履歴書き換えのエラー対処を含む。 |
| [260401](260401/) | Claude.aiのコネクタ機能（Google Drive・Gmail・Calendar・GitHub・Notion・TickTick・Figma）を設定し動作確認。 |
| [260402](260402/) | RemotionとClaude Codeを使いClaude Code紹介動画（MP4・28秒）を作成。WSL環境でのChrome依存ライブラリ・日本語フォント問題の解決含む。 |
| [260405](260405/) | スマホからClaude Codeを操作する2つの方法（Remote Control・Dispatch）を調査・設定。WSLとWindowsの制約と使い分けを整理。 |
| [260406](260406/) | Claude Codeのサブエージェント・エージェントチームを活用して個人開発管理ダッシュボード（React + Hono + SQLite）をフルスタックで構築。 |
| [260413](260413/) | Anthropic Academyの学習スケジュール再構成。作業ログをもとに済み分を除外し、残り課題（agent skills・MCPサーバー自作）を整理。 |
| [260427](260427/) | WSL2上にOpenClaw（AIエージェントフレームワーク）とOllama（ローカルLLM）を構築。qwen2.5モデルの動作確認・パラメータ調整を記録。Gemini 2.5 Flash APIへの移行も実施。 |
| [260502](260502/) | Claude Designを調査・実践。Kenshinデザインシステム（ポートフォリオ・提案デッキ・ワンページャー・紹介アニメーション）をClaude Designハンドオフから実装。 |
| [260510](260510/) | WSL2セキュリティ学習カリキュラム第1回。Linux権限・プロセス分離をブラウザ上のフェイクターミナルゲーム形式で体験学習（UID/GID・Capability・SUID・sudo・/proc・namespace）。 |
| [260521](260521/) | GitHub Pages上で動作するチャットアプリを構築。OpenAI API版とGemini API版の2種類を実装。APIキーはlocalStorageのみ保存。Gemini版（gemini-2.5-flash）は無料枠で動作確認済み。 |
| [260525](260525/) | RAG（Retrieval-Augmented Generation）の学習ロードマップ。前回提示された内容を精査・修正し、WSL2でPhase 1〜6の順序で実装しながら学ぶ手順をまとめた。 |
| [260630](260630/) | システムプロンプト（プリプロンプト）をユーザーに意識させず自動注入するチャットアプリを構築。Gemini APIの`systemInstruction`を使い「生成AIシステムアーキテクト」専門家AIにチューニング。動作確認済み。 |
| [260701](260701/) | AI箱庭シミュレーション。複数のAIペルソナを実在の地図（渋谷駅周辺）上で1日分行動させ、架空のコーヒーショップへの購買行動をA/Bテスト・思考ログ(CoT)のJSON化で分析。Python製ターン制シミュレータ(v1)。 |
| [260702](260702/) | 生成AI（Gemini API）連携ブロックチェーンの学習ロードマップとフェーズ1実装。Pythonでハッシュ連結・PoWマイニング・ECDSA署名検証・複数ノードの最長チェーン優先ルールをスクラッチ実装し動作確認。 |
| [260804](260804/) | 機械学習・ディープラーニング学習ロードマップ（約100手法をStage 0〜13に整理）。WSL環境でStage 0(環境構築)〜Stage 2(決定木・アンサンブル・SVM)まで実装・動作確認済み。 |
| [260805](260805/) | 静的サイトのホスティング方針（GitHub Pages/Netlify/Vercel使い分け）の調査・整理、および外部エージェントランタイム（ponytail/Hermes Agent等）のGitHub API裏取り調査。 |

---

## GitHub Pages — 公開ページ一覧

| ページ | URL |
|--------|-----|
| トップ | [kenobix.github.io/Claude_practice/](https://kenobix.github.io/Claude_practice/) |
| ポートフォリオ | [260502/kenshin/ui_kits/portfolio/](https://kenobix.github.io/Claude_practice/260502/kenshin/ui_kits/portfolio/) |
| 紹介アニメーション | [260502/kenshin/intro/](https://kenobix.github.io/Claude_practice/260502/kenshin/intro/) |
| 提案デッキ | [260502/kenshin/ui_kits/deck/](https://kenobix.github.io/Claude_practice/260502/kenshin/ui_kits/deck/) |
| ワンページャー | [260502/kenshin/ui_kits/onepager/](https://kenobix.github.io/Claude_practice/260502/kenshin/ui_kits/onepager/) |
| Linux権限ハッキングラボ（Topic 1） | [260510/01_linux_permissions/](https://kenobix.github.io/Claude_practice/260510/01_linux_permissions/) |
| OpenAI チャットアプリ | [260521/](https://kenobix.github.io/Claude_practice/260521/) |
| Gemini チャットアプリ | [260521/gemini.html](https://kenobix.github.io/Claude_practice/260521/gemini.html) |
| 生成AIシステムアーキテクト相談室 | [260630/](https://kenobix.github.io/Claude_practice/260630/) |
