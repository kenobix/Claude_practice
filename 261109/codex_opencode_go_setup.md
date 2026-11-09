# Codex CLIとOpenCode Goの連携記録

- 作業日：2026-11-09
- 環境：WSL2 Ubuntu 24.04（実機は24.04.5 LTS）
- 目的：従来のChatGPT経由のCodexを維持し、Go用の起動経路を追加する。
- 関連履歴：[Go・無料モデルの検証](opencode_go_evaluation_20261109.md)、[ローカル8B/14B実験](opencode_local_llm_test_20261109.md)
- 情報源：利用者が完了を報告した導入・接続手順、実機のバージョンと設定構造、公式資料。今回の記録作業でセットアップやモデルへの問い合わせは再実行していない。

## 1. 導入結果

| 項目 | 2026-11-09の結果 | 今回の確認方法 |
|---|---|---|
| nvm | 0.41.0 | `nvm --version` |
| Node.js | 24.22.2 | nvm配下の`node --version` |
| npm / npx | いずれも11.19.1 | nvm配下をPATHに指定して各`--version` |
| Codex CLI | 0.167.0 | nvm配下をPATHに指定して`codex --version` |
| OpenCode Console | Personalワークスペースへ接続済み | 利用者の完了報告 |
| 通常起動 | `codex`でGPT-6 Luna | 利用者の動作報告と、既定モデル`gpt-6-luna`の設定確認 |
| Go起動 | `codex-go`でKimi K2.7 Code | 利用者の動作報告と、Go用プロファイル・起動スクリプトの構造確認 |

nvm 0.41.0を導入後、Node.js 24.22.2を用意し、npm経由でCodex CLI 0.167.0を導入した。以下は対応する導入・確認用のコマンド例であり、保存された逐語的な実行ログではない。今回これらのインストール操作は行っていない。

```bash
# nvm導入済みのシェルでの導入例
nvm install 24.22.2
nvm use 24.22.2
npm install -g @openai/codex@0.167.0

# バージョンの確認
nvm --version
node --version
npm --version
npx --version
codex --version
```

今回の非対話シェルではnvmと`~/.local/bin`がPATHへ反映されていなかったため、確認時にはnvmのスクリプトとインストール済みの実行パスを明示した。利用者の通常のUbuntu端末での起動経路とは区別する。

## 2. Console接続と従来プロバイダーの維持

利用者は次のセットアップ方式でConsoleのPersonalワークスペースに接続し、従来のCodexのサインインとモデルを維持した。

```bash
npx @opencode/console setup codex --keep-provider
```

`--keep-provider`は`@opencode/console`のセットアップオプションであり、`codex login`のオプションではない。ローカルにキャッシュされた公式パッケージ`@opencode/console` 0.1.7のREADME・実装で確認した。READMEでは既存のサインイン・モデルを維持しつつ、ワークスペースのMCP・skills・instructions・policiesを適用する仕組みと説明されている。セットアップは設定やhooksを書き換える操作なので、今回再実行していない。

同READMEでは、ワークスペースの方針によってはモデル名・トークン数をhooks経由で報告すると説明されている。従来モデルの維持を、Consoleとの通信が一切なくなる意味に解釈しない。実機のベース設定にはhooksが存在することだけを確認し、認証状態や送信履歴は調べていない。

## 3. ベース設定とGo専用プロファイル

実機では`~/.codex/config.toml`のトップレベルが`model = "gpt-6-luna"`、`model_reasoning_effort = "high"`で、既定の`model_provider`を上書きしていなかった。Goの定義を追加し、起動時のプロファイルで切り替える。

モデル選択に関係する項目だけを抜粋した構造例を示す。実ファイルのコピーや、単独でセットアップを完結させるための設定一式ではない。

```toml
# ~/.codex/config.toml の構造例
model = "gpt-6-luna"
model_reasoning_effort = "high"

[model_providers.opencode-go]
name = "OpenCode Go"
base_url = "https://opencode.ai/inference/go/openai/v1"
wire_api = "responses"
```

Go専用ファイルには切り替えに必要な項目だけを置く。

```toml
# ~/.codex/go.config.toml
model = "kimi-k2.7-code"
model_provider = "opencode-go"
```

Codex CLI 0.167.0では`codex --profile go`がベース設定の上に`go.config.toml`を重ねる。`[profiles.go]`をベースファイルに作る旧方式は使用しない。実機にも旧`profiles`テーブルはなかった。この読み込み方式は[OpenAI公式のAdvanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles)で確認した。

Goのエンドポイントと`wire_api = "responses"`は[OpenCode公式Codexガイド](https://opencode.ai/docs/console/harnesses/codex/)と一致した。設定の各項目は[OpenAI公式Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)も参照。

## 4. Go専用の起動スクリプト

`~/.local/bin/codex-go`にGo専用の起動スクリプトを用意した。モデル選択を行う最終行の抜粋は次のとおり。

```bash
exec codex --profile go "$@"
```

実機のスクリプトは実行可能で、`--profile go`と引数の引き継ぎを確認した。`exec`でCodexに置き換わる。今回スクリプト自体は実行していない。

## 5. .bashrcと起動の使い分け

実機の`.bashrc`ではnvmの読み込みと次のPATH設定を確認した。公開例は関連部分のみ。

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.local/bin:$PATH"
```

通常のUbuntu端末では、新しいシェルで設定を反映して起動する。

| コマンド | 選ぶ設定・モデル | 利用枠・接続先 |
|---|---|---|
| `codex` | ベース設定、GPT-6 Luna | 従来のChatGPT経由のCodexを維持（利用者報告） |
| `codex-go` | `go.config.toml`、Kimi K2.7 Code | PersonalワークスペースのOpenCode Go |

Go用プロファイルはCodex CLIの通常モデルを置き換えずに選択できる。今回の確認は設定とバージョンまでで、両モデルへの新たな推論実行や性能測定はしていない。

## 6. Goの契約枠と追加従量課金の防止

同日の[既存の契約・設定観測](opencode_go_evaluation_20261109.md)は、Goが月額$10＋適用税、次回更新2026-12-09、Extra Usage OFF、Auto-recharge OFF、追加クレジット$0。今回Consoleへアクセスして現在値を再確認したものではない。

[OpenCode公式Go資料](https://opencode.ai/docs/console/go/)を2026-11-09に参照したところ、GoのKimi K2.7 Codeの月間利用枠は利用額換算$60で、5時間枠は月間の20%、週間枠は50%と説明されていた。この金額は契約料金や追加クレジット残高とは別の利用量の計数枠で、無制限の利用を意味しない。将来の枠はConsoleと公式資料で確認する。

同資料ではExtra Usageを有効にすると、Goの上限到達後に従量課金残高へ移行する。従量利用への切り替えを避けるためExtra Usage OFF、残高の自動補充を避けるためAuto-recharge OFFを維持する方針。月額契約の更新まで止まる設定ではない。以前のKimiの約$0.02という表示も、別途の追加請求とは断定していない。

## 7. 今回の確認範囲と残作業

バージョン、モデル・プロバイダー・プロファイルの構造、起動スクリプトのモデル選択、PATH設定を読み取り中心で確認した。既存のユーザー設定は変更せず、セットアップ、課金設定変更、モデルのダウンロード、API呼び出しは実施していない。

残作業は通常の開発中の成功・手戻りの記録。無料モデルの学習利用許可の現在値も、従来どおり未確認の課題として保持する。従来のCodexと独立したGoを維持し、専用ベンチマークの優先度は下げる。
