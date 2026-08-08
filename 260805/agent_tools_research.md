# 外部リポジトリ調査: ponytail / GitHub trending / エージェントランタイム

Claude Codeの作業品質向上、および `260427` のOpenClaw構築に続く別エージェントランタイムの試用候補を探すため、GitHub trending（monthly/weekly）と個別に提示されたリポジトリを調査した記録。

## 調査方針

提示された参考情報（過去の別会話）に含まれる数値（スター数等）が、短期間で急成長したリポジトリとして不自然に見えるものがあったため、**GitHub API（`api.github.com/repos/...`）・WebFetch・WebSearchで直接裏取りした**。ブログ記事等の二次情報はそのままでは根拠として採用せず、実在性・規模・活動状況を確認したもののみを推奨対象とした。

## 裏取り結果

| リポジトリ | スター数 | フォーク | ライセンス | 作成日 | 備考 |
|---|---|---|---|---|---|
| `DietrichGebert/ponytail` | 95,564 | 5,253 | MIT | 2026-06-12 | 作成から2ヶ月足らずで9万超という急成長。参考情報と一致。 |
| `NousResearch/hermes-agent` | 225,263 | 43,719 | MIT | 2025-07-22 | 2026-08時点でも活発にコミット継続。参考情報の数値は誇張ではなかった。 |
| `openclaw`組織本体（`openclaw/openclaw`） | 385,108 | - | - | - | 実在・活発。`260427`の前提と矛盾なし。 |
| `ComposioHQ/trustclaw` | 872 | - | - | - | "OpenClaw代替"記事群で名指しされていた`TrustClaw`の「公式」実体。詳細は後述。 |

### "TrustClaw"問題の裏付け

"OpenClaw代替"を謳う記事（Composio, Vellum, Contabo等のブログ）で名指しされていた`TrustClaw`をGitHub検索した結果、無関係な同名リポジトリが**26件**乱立していることが判明した。記事の発信元であるComposio自身が公開する `ComposioHQ/trustclaw` はわずか872★——自社ブログでの自己宣伝目的の便乗ネーミングだったことが確認できた。参考情報にあった「"-Claw"便乗名の氾濫は要注意」という警告は正しく、実例で裏付けが取れた形になる。同種の"○○Claw"系（NanoClaw・ZeroClaw・PicoClaw・MimiClaw等）は、記事の推薦文だけを根拠に導入しない方がよい。

## A. この環境への導入推奨（トレンドリポジトリ）

### Tier 1（優先度高、実在・規模を確認済み）

| リポジトリ | 実績 | このリポジトリとの関連 |
|---|---|---|
| [`DietrichGebert/ponytail`](https://github.com/DietrichGebert/ponytail) | 95.6k★, MIT | Claude Code含む15+ツール対応の「不要なコードを書かせない」プラグイン（laziness hierarchy）。コード量54%減・実行コスト20%減を謳う。リポジトリ全体に適用可能な汎用プラグインだが、まず1つの日付ディレクトリで試してから本番導入を判断するのが無難。 |
| [`Nutlope/hallmark`](https://github.com/Nutlope/hallmark) | 21.5k★, MIT | Claude Code/Cursor/Codex向け「Anti-AI-slop design skill」。`260502`のデザインキット作業や今後のフロントエンドUI作業（チャットアプリ等）で、AI生成にありがちな没個性なデザインを避ける方向づけになる。 |
| [`usestrix/strix`](https://github.com/usestrix/strix) | 47.8k★, Apache-2.0 | AIによる自動ペネトレーションテストツール。`260510`のセキュリティ学習カリキュラム（THMラボ・OWASP ZAP）と直結。**自分の管理下にある学習用ターゲット（自作ラボや許可されたTryHackMe等の環境）以外には絶対に向けないこと。** |

### Tier 2（任意・リファレンス用途）

| リポジトリ | 実績 | 関連 |
|---|---|---|
| [`Shubhamsaboo/awesome-llm-apps`](https://github.com/Shubhamsaboo/awesome-llm-apps) | 130k★, Apache-2.0 | RAG/エージェントアプリの実装例集。`260525`（RAG学習）・`260701`（AI箱庭）の次のステップを考える際の参考資料。「セットアップ」というよりリファレンスとしてクローンする価値。 |
| [`virgiliojr94/book-to-skill`](https://github.com/virgiliojr94/book-to-skill) | 16.2k★, MIT | 技術書PDFをClaude Skillに変換するツール。`260413`・`260525`・`260804`のような学習ロードマップ作成の効率化に使える可能性。 |

### 見送り（理由付き）

- **`wonderwhy-er/DesktopCommanderMCP`**（9.1k★）: ターミナル制御・ファイル編集のMCPサーバーだが、Claude Code自体がこれらをネイティブに持つため用途が重複する。Cursor等ネイティブなターミナル制御を持たないツール向け。
- **`zhaoxuya520/reverse-skill`**（17.3k★, MIT）: 実在は確認できたが個人開発者によるセキュリティ系ツールで実績が浅い。`260510`用途なら組織的信頼度が高い`strix`を優先。

## B. Hermes Agentと「他に試すべきもの」

- **Hermes Agent**（`NousResearch/hermes-agent`）は実在・大規模で信頼できる（上記裏取り参照）。「経験からスキルを自作・改善する学習ループ」「セッションを跨いだユーザーモデルの蓄積」「Telegram/Discord/Slack/WhatsApp/Signal/CLIを単一ゲートウェイで統合」が特徴。
- **重要な注意点**: 公式インストーラ（`hermes install` → `hermes model`）が明示的に対応するのは Nous Portal / OpenAI / OpenRouter / Anthropic で、READMEに**Gemini APIやOllamaの明記はない**（カスタムエンドポイント機能で接続できる可能性はあるが未検証）。`260427`で組んだ無料Gemini API構成をそのまま流用できるとは限らない点は、試す前に確認が必要。
- システム要件: Python 3.11 / Node.js / uv / ripgrep / ffmpeg をインストーラが自動構築（`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`）。
- OpenClaw（`260427`で試用中、コーディングエージェントのオーケストレーション基盤）とHermes Agent（メッセージング常駐・自己学習型パーソナルアシスタント）はカテゴリが異なるため、両方試す意義はある。
- 調査の結果、Hermesと同カテゴリ（常駐型パーソナルAIアシスタント）で同等の実績・信頼性を持つ**第三の選択肢は見つからなかった**。むしろ"-Claw"便乗ネーミングの実態（TrustClaw = 872★の自社宣伝案件、無関係な同名リポジトリ26件の乱立）が確認できたため、そうした記事ベースの推薦は避けるべきと結論づけた。
- **結論**: 追加で試すなら、パーソナルアシスタント系をもう一つ探すより、Aで挙げた `ponytail` / `hallmark` / `strix` のような「Claude Codeでの作業品質を上げる方向のツール」を優先する方が、このリポジトリの学習目的に合う。

## スコープ

本ドキュメントは調査・推奨の記録であり、実際のインストール（`ponytail`の`~/.config/ponytail/config.json`設定、`hermes-agent`のインストーラ実行など）はまだ行っていない。着手する場合は別途タスクとして実施する。
