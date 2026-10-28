# R06 staging実装後のチェックポイントレビュー

確認日: 2026-10-28（JST）。前回の正式レビューを含むcommit `7609af5d175216052485df1060c4148fb4ccbeb5`の後から、`5df03f7852ccbf9107f88d7bae6ee7aa3f5506b0`までを対象にした。13コミット、45ファイル。前回レビューが比較に使った`9131b1f`以降をもう一度全部やり直すのではなく、前回レビューの確定後に増えた実装・記録を確認した。

結論: 実装の方向、評価期間を保護する判断、隔離環境での試験は妥当。ただしR06は未完了で、Capitalを実測・承認・通常配信できる状態ではない。現在のCLI停止と`not_evaluated`の扱いはコードと一致する。直近の細かな部品追加を、次は「候補の証拠→固定DB→HTTP→監査行→運用report」を通す一つの完成条件にまとめる必要がある。

集計証拠は[チェックポイント証拠JSON](20261028-staging-checkpoint-evidence.json)。今回の全テスト出力はGit対象外の`data/reports/review-20261028-staging/unit-tests.log`に保存した。レビューでは実候補のfit、実評価期間の再採点、登録タグ変更、配信切替は行っていない。

## コミットと計画への適合

| コミット | 主な内容 | 確認と判定 |
|---|---|---|
| `13ea918`、`b43cb76` | timezone供給元の固定と記録 | `ZoneInfo.from_file`でlocked tzdataの実bytesを読み、schema 3とAPIへidentityを記録。R04の目的に合致。今回は全原本の再集計を繰り返していない。 |
| `513436b` | incumbentなしbootstrap protocol | UCIと異なるtargetを比較する問題を避け、同target baselineへ移行。未使用期間を保護する順序は妥当。 |
| `35065bd`、`0cdac25` | MAE/RMSE gate、final CLI、配信拒否、測定部品 | 同じartifactのfinal採点とrow/ledger照合を試験。通常Capital配信はgate tagを要求するが、最終の証拠更新・承認経路は未完了。 |
| `d263f8e`、`2a193c0`、`f365dfa` | blocked候補の登録と隔離staging、実施順序 | 実候補前に合成system test、実候補後に実測という依存関係を整理。通常runtimeの未達gateを緩めない方針は妥当。 |
| `a07c581`、`41bf7e9` | 合成Compose実検証、候補ID保存、Dockerのprofile path修正 | 保存済み報告とコードを照合。候補切替後の再送は保存IDを返す。API imageはテスト用overrideを併用しており、最新HEADの本来のDockerfileで全system testした証拠にはならない。 |
| `ea722b4` | candidate/CSV/manifest/registry/DBのseed照合 | 余分な既存行を含め不一致をtransaction内で拒否。SQLite試験はouter CLIと実registryを通す試験ではない。 |
| `85b6e7c` | `--verify-only`と一時PostgreSQL検証 | 全行照合は実装済み。ロックは検査transaction終了で解放され、測定中の固定にはまだ使われていない。 |
| `5df03f7` | 凍結training行と168時間の照合、実測停止 | cutoffまでのラベル到着・row hash/countを検査。CLIは入力確認専用。内部の数値成功も運用gateを`passed`にしない。安全側の途中状態として妥当。 |

上記13コミットには各々contract CIとPages workflowがあり、計26 runはすべてsuccess。最新contract CIは[40693844897](https://github.com/kenobix/Claude_practice/actions/runs/40693844897)、Pagesは[40693843604](https://github.com/kenobix/Claude_practice/actions/runs/40693843604)。CI成功はlock install・editable install・pip check・unit tests・compileallの成功であり、Docker buildやMLflow/PostgreSQL/Caddy統合の成功は含まない。

## 今回再確認した証拠

| 対象 | 結果と確認範囲 |
|---|---|
| テスト | 対象HEADで86 tests、10.996秒、OK。実行ログSHAは証拠JSONに保存。FastAPIの既知の制約を避け通常実行した。 |
| 静的/依存検査 | `compileall`、`pip check`、`sh -n scripts/bikectl`、3-file Compose `config -q`、`git diff --check`が成功。既存のStarlette/httpx非推奨警告とpip cache書込不可警告はあり。 |
| 既存UCI | 5 container稼働、PostgreSQL healthy、入口ready 200、版3、artifact SHA `79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`。Capital/テストcontainerは残っていない。 |
| UCIの差分 | 基準commit時刻以降のprediction/deployment/monitor追加更新は0。UCI MLflowの新run・model version作成更新も0。今回の読み取りの範囲で確認。消去済み一時registryの履歴や外部処理の不在までは証明しない。 |
| Composeログ | 基準時刻以降の取得可能なログは10行。Traceback/ERROR/permission denied/Invalid Host header/No such artifactの各検索0件。ローテーション等で失われた過去ログは復元していない。 |
| Capital成果物 | 原本ZIP 41個。canonical candidate report 0、Capital report JSONは既存の`data-audit-v2.json`のみ。新しい実candidate/final reportはない。今回は原本全件のhash監査はしていない。 |
| 合成測定 | 保存JSONのSHAは記録どおり`cc68b465ae2dc1b819148f367361d1c92677731d6a883964a9383bf9b503c8f5`。1,050新規ID、1,000測定、HTTP 200、error 0、nearest-rank p95 `106.46448500119732ms`を再計算で確認。 |

合成測定の最後2対象時刻`2024-01-14 22:00`、`23:00`は、3時間遅延のためtraining cutoff `2024-01-15 00:00`までにラベルが到着しない。旧JSON内の`operational_gate.status=passed`は当時の数値判定として履歴を保ち、実候補の運用合格証拠から除外する。現行コードは同条件を入力確認で拒否する。

## 指摘事項と優先度

### S01 / P1: 実測の入力・DB・API・監査がまだ一つにつながっていない

[DB照合](../../src/bike_mlops/db.py)は全行を検査するがtransactionの終了とともにロックを解放する。[測定部品](../../src/bike_mlops/operational_benchmark.py)は応答の`data_version_sha256`の形式だけを確認し、候補データから再計算した値との一致を要求しない。合成の任意の正しい64桁hashが通ることを今回pure-function probeで確認した。これは既知の未完了条件で、CLIが停止しているため現段階で運用合格を作る経路にはしない。

次の完成条件は、候補/report/registryの版・artifactを固定→同じ観測DBを全行照合→測定中の観測書換えを防止→候補CSVからlag出典hashを計算して全応答を比較→request IDごとの監査行を照合→reportを確定、までを一つの実行で通すこと。DB接続喪失・ロック取得timeout/喪失・途中中断・不一致は合格にしない。観測表の固定が予測監査表へのINSERTを妨げないことも試験する。`data_version_sha256`はCSV全体のSHAではなく、利用した24/168時間lagの時刻・値・source hashから作るhashなので、比較対象を取り違えない。

### S02 / P1: 配信可否判定はstatus tagの整合検査に留まる

[require_deployment_eligible](../../src/bike_mlops/deployment_gates.py)へ7個の期待status/policy tagだけを渡すと、report hash・candidate ID・承認記録なしでも関数は受理する。pure-function probeで確認した。今回registryを書き換えたり、配信を試したりはしていない。

現時点ではCapital候補がblockedで、eligible更新機能も未実装なので、現在配信されたという発見ではない。今後の終端処理をtag更新だけで完成させてはいけない。正確なmodel/version/candidate/artifact、final/operational reportのSHAと内容、測定plan、policy、監査整合、承認者・理由・時刻・証拠hashを照合し、最後にeligibleを設定する。失敗・不足・値違いを人の承認で上書きしない。通常loader/runtime/promoteで同じ証拠条件を要求し、READY状態と復旧検証の条件も整理する。

### S03 / P1: 測定条件を保存してから実行する仕組みが不足

測定仕様は文書と定数にあるが、対象168時間・順序・body生成規則・暖機件数・資源条件・測定policyを版付きhashへ固定して、最初の要求より前に保存する実装はない。現在の入力確認はfinal reportを要求するため、実験前の計画固定そのものには使えない。

候補fit前の精度protocolと、測定前の運用planを区別する。運用planは少なくとも最初の要求の前に不変ファイルとして保存し、候補/データ/期間/policyを結び付ける。未使用データ取得前に実装・合成試験を終える。測定週や閾値を実測結果に合わせて差し替えたり、速い再実行だけを採用したりしない。失敗・中断後の再試行は別attemptとし理由を残す。

### S04 / P1: 最新HEADで再実行できるsystem testが必要

合成Compose試験の記録は残る一方、固定一時パス・project名を含む歴史的スクリプトで、一般の再実行手順ではない。後続のPostgreSQL lock試験は一時スクリプト削除済みで、最終版のコードを再取得できない。過去実施の記録は尊重するが、独立した再現証拠は弱い。最新seed/preflightを本来のDockerfileでbuildしてMLflow/PostgreSQL/API経由で通したことも、unit CIだけでは確認できない。

一意のproject/port/volume、対象所有確認、秘密値非出力、失敗時cleanupを備えた再利用可能なintegration harnessを保存する。最新DockerfileそのものでUCI/Capital profile解決を確認し、S01/S02の正常・不一致・中断を実stackで試験する。PostgreSQLのlock試験と、APIの同一request ID並行再送の試験は別物なので、後者はR08の未完了として維持する。

### S05 / P2: 現行説明に一部古い状態が残る

runbookには「実候補とseedデータの一致強制が未完成」とあり、最新実装のseed時照合と測定中照合の違いが分かりにくかった。計画書にも「scriptが1,050要求を送る」とある一方で停止中の注記が別行にあった。今回、現行説明を入力確認専用へ揃えた。過去の作業ログと合成JSONの当時の結果は書き換えない。

### S06 / P1: 手動承認と復旧検証の順序を揃える

計画書は配信許可に復旧の証拠も要求する一方、旧11.4の順番7にmanual approval、順番9に復旧試験があり、順序が不明確だった。今回、実候補の運用測定はblockedのまま行い、監視・隔離復旧の証拠を揃えてから実候補の採否を決める順序へ訂正した。復旧能力の試験には先行して合成fixturesを使い、実候補の承認待ちを理由に検証できなくなる依存の循環を避ける。今回、実配信操作はしていない。

## ワークフローとデバッグの評価

- 小さく対象を指定してcommit/pushし、無関係な4未追跡ファイルを含めなかった運用は妥当。基準時刻以降のUCI DB/registry差分も0だった。
- syntheticを実精度と混同せず、Q3/Q4や過去Capital評価期間を再採点してデバッグしなかった点は適切。供給元tzdata・source snapshot・artifact固定・遅延ラベルの条件は計画の目的に沿う。
- Dockerのprofile path、候補切替後のID、pandas datetime64[us]拒否を実行結果から修正したのは妥当。反対に、syntheticの入力期間違反とseedのデータ版不足はsystem test後に見つかった。先に受入条件の負例を列挙し、それを満たしてから計測する工程へ改める。
- FastAPIがsandbox内で停止し通常実行で成功したという観測は再現しているが、記録だけで原因を特定のsocket syscallまで断定できない。環境要因と推定する範囲に留め、必要時はtimeoutとstack traceで確認する。依存の非推奨警告は失敗と混同せず、lock更新時の互換確認に残す。
- 全テスト再実行は変更の影響確認として妥当。ただしmock中心のテスト件数増加をsystem testの代わりにしない。次の節目はhelper追加ごとに「次回へ」とする区切りから、測定CLIが証拠を伴って動くところまでを完成単位にする。commitは引き続きレビュー可能な単位で行う。
- status tagだけを信用しない証拠検証は必要だが、ローカルの利用者がDB・原本・tagを任意に書き換えられる構成を、外部の未信頼者へ耐える認証基盤と説明しない。今回の範囲はローカル学習用の整合性と誤操作検出。

## 更新した残課題

正規の順序・依存関係・完了条件は[計画書11.5](../../../261014/plan_mlops.md#115-2026-10-28-stagingチェックポイント後の残課題)。次の一つの完成節目はS01〜S04を結んだ「再現可能な合成system testで測定CLIと証拠保存を復帰」。その後に初めて未使用原本を取得し、protocol freeze→一度のvalidation→合格時だけ同一artifactの独立final→実候補の運用測定へ進む。監視・rollback/recover・backup/restore・採否デモも未完了として維持する。

## 今回の主なコマンド

```bash
git log --reverse --format='%h %s' 7609af5..5df03f7
git diff --name-only 7609af5..5df03f7
gh run list --limit 50 --json databaseId,workflowName,headSha,status,conclusion,url
gh run view 40693844897 --json headSha,status,conclusion,url,jobs
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
sh -n scripts/bikectl
docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
curl -fsS --max-time 5 http://127.0.0.1:8080/ready
curl -fsS --max-time 5 http://127.0.0.1:8080/version
```

UCI DBは基準時刻を条件とするSELECT、MLflowは検索APIを全page取得、Composeログは同時刻以降を取得した。集計したpure-function probeと測定JSON再計算の結果も証拠JSONへ保存した。rawログ・原本・実`.env`はコミットしない。
