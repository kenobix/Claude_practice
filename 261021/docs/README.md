# 文書一覧と読み方

このページは、目的に合う文書を選ぶための索引です。最初は「構成と用語集」、実際に動かすときは「操作手順」を読んでください。
Capital候補は2026年11月16日に配信不採用と判断しました。過去の節にある「未完了」「次に実施する」は当時の状態です。
現在の結論はCapital文書の最終節、実行の経緯は作業ログで確認できます。

| 文書 | 内容 |
|---|---|
| [構成と用語集](architecture.md) | 何を作ったか、データをどう扱うか、用語の意味 |
| [操作手順](runbook.md) | 環境の準備、起動、配信の切替、復旧 |
| [Capitalの評価と判断](capital.md) | データの固定、精度評価、隔離試験、不採用の根拠 |
| [監視の仕様](monitoring.md) | 分布や誤差の警告、保存結果の照合、中断時の扱い |
| [証拠の読み方](evidence.md) | 試験で確認できたことと限界、過去の監査・引継ぎ |
| [clock-operations.md](clock-operations.md) | 時計資料：手順と選定・保留事項 |
| [clock-implementation.md](clock-implementation.md) | 時計資料：offline部品と合成検証 |
| [clock-history.md](clock-history.md) | 時計資料：原因調査の履歴（保留） |

時計の3冊は補足資料です。通常の利用手順から分けており、記載された調査や設定操作を再開する許可は含みません。

検証記録は[用途別5冊の索引](reviews/README.md)から探せます。
JSON（JavaScript Object Notation：項目名と値を保存する形式）内の`reviews[旧ファイル名]`が本文です。
`originals`には元のUTF-8（Unicode Transformation Format 8-bit）原文と、SHA-256（Secure Hash Algorithm 256-bit）ハッシュも保持しています。
ハッシュは内容の一致を調べる値で、記録の作成者を証明する署名ではありません。

## Pythonの入口

製品コードは18本、テストは12本、補助スクリプトは7本に整理しました。
CLI（Command Line Interface）は、ターミナルに文字のコマンドを入力して操作する窓口です。
以下は`261021`ディレクトリで実行する補助CLIの一覧です。内容を確認する場合は、コマンドの末尾に`--help`を付けます。

| 補助CLI | コマンド |
|---|---|
| `python scripts/data_tools.py` | `prepare`, `protocol`, `evaluate`, `evaluation-ledger` |
| `python scripts/runtime_tools.py` | `register`, `seed`, `plan`, `benchmark`, `recheck`, `record-operational`, `evidence`, `decision`, `inspect`, `pending-audit`, `attempt-ledger`, `eligibility-review`, `product-acceptance` |
| `python scripts/clock_tools.py` | `capture-fake`, `derive`, `review`, `rate-review`, `bundle`, `diagnose` |
| `python scripts/system_harness.py` | `run`, `fixture` |
| `python scripts/storage_checks.py` | `snapshot`, `interruption`, `monitor-inspection`, `monitor-persistence`, `migration`, `sqlite-os`, `storage-probe` |
| `python scripts/runtime_checks.py` | `decision-http`, `deployment-recovery` |

これらのコマンドは、一覧にあるだけで既存環境へ実行してよいわけではありません。
特に`benchmark`や`decision`は測定や判断の記録を伴うため、操作手順の開始条件を先に確認してください。

## 製品コードとテストの配置

DB（Database）はデータベース、CI（Continuous Integration）は変更ごとに実行する自動試験です。
表の「隔離候補」は、通常の利用者向け環境から分けて試す候補モデルを指します。

| 役割 | `src/bike_mlops/` | `tests/` |
|---|---|---|
| 設定・データ・学習 | `config.py`, `data.py`, `evaluation.py`, `training.py`, `model.py` | `test_data.py` |
| DB・API・隔離候補 | `db.py`, `api.py`, `staging.py` | `test_api.py`, `test_data.py` |
| 監視・保存結果の照合 | `monitoring.py`, `monitor_inspection.py` | `test_monitoring.py`, `test_monitor_inspection.py` |
| 配信・復旧・操作状態 | `deployment.py`, `deployment_runtime.py` | `test_deployment.py` |
| 計測・証拠・採否 | `operational.py`, `operational_validation.py`, `operational_runner.py` | `test_operational.py`, `test_measurement.py` |
| 合成システム試験 | `scripts/system_harness.py`, `scripts/runtime_checks.py`, `scripts/storage_checks.py` | `test_system.py` |
| 保留中の時計部品 | `clock.py` | `test_clock.py` |
| 共通入口・試験補助 | `__init__.py`, `cli.py` | `support.py`, `request_interruption_child.py`, `probe_decision_clock.py` |

各ファイルの区切りコメントに統合前の名前を残しています。
例えば旧コマンド`python scripts/register_capital_candidate.py --help`は、現在は`python scripts/runtime_tools.py register --help`です。
旧名の互換ファイルはありません。関数名も含む詳しい対応は[ファイル配置表](../configs/file-layout.json)で確認できます。
通常のAPI（Application Programming Interface：プログラムから予測を要求する窓口）は`bike_mlops.api:app`、Capital専用のAPIは`bike_mlops.api:_staging_api_app`です。

ファイルをまとめた後も、状態管理と試験で使う代替部品の参照先を区別しています。
実行時に旧ソースを読み直す仕組みはありません。
`storage_checks.py`は選択した試験の設定だけを読み、別の移行試験の設定を混ぜません。

時計の過去の合成記録は、旧ソース名が含まれていても読み取れます。現在の形式では`clock.py`と`clock_tools.py`を対応付けます。
保存されたコードは実行しません。公開用の書き出しでは、インストール中のソースと内容が一致することを引き続き要求します。
この互換処理は時計の精度を保証するものではありません。

## 旧Markdownから探す

2026年11月15日に67本の文書を8冊と本索引へまとめました。以下は旧ファイル名から探すための対応表です。
過去の原文・コマンド・ハッシュは、整理前のGitコミット`9479201`、作業ログ、検証記録にも残しています。

<details>
<summary>旧ファイル名と現在の収録先を表示</summary>

| 旧ファイル | 統合先 |
|---|---|
| `architecture.md` | [architecture.md#architecture](architecture.md#architecture) |
| `backend-time-path-review.md` | [clock-history.md#backend-time-path-review](clock-history.md#backend-time-path-review) |
| `capital-bootstrap-gate.md` | [capital.md#capital-bootstrap-gate](capital.md#capital-bootstrap-gate) |
| `capital-decision-http.md` | [capital.md#capital-decision-http](capital.md#capital-decision-http) |
| `capital-deployment-evidence.md` | [capital.md#capital-deployment-evidence](capital.md#capital-deployment-evidence) |
| `capital-first-evaluation.md` | [capital.md#capital-first-evaluation](capital.md#capital-first-evaluation) |
| `capital-offline-monitoring.md` | [capital.md#capital-offline-monitoring](capital.md#capital-offline-monitoring) |
| `capital-operational-hold.md` | [capital.md#capital-operational-hold](capital.md#capital-operational-hold) |
| `capital-runtime-acceptance.md` | [capital.md#capital-runtime-acceptance](capital.md#capital-runtime-acceptance) |
| `capital-runtime-handoff.md` | [capital.md#capital-runtime-handoff](capital.md#capital-runtime-handoff) |
| `chrony-control-analysis.md` | [clock-history.md#chrony-control-analysis](clock-history.md#chrony-control-analysis) |
| `chrony-runtime-control.md` | [clock-implementation.md#chrony-runtime-control](clock-implementation.md#chrony-runtime-control) |
| `ci-artifact-maintenance.md` | [runbook.md#ci-artifact-maintenance](runbook.md#ci-artifact-maintenance) |
| `clock-admin-log-review.md` | [clock-history.md#clock-admin-log-review](clock-history.md#clock-admin-log-review) |
| `clock-campaign-acquisition.md` | [clock-implementation.md#clock-campaign-acquisition](clock-implementation.md#clock-campaign-acquisition) |
| `clock-campaign-review.md` | [clock-implementation.md#clock-campaign-review](clock-implementation.md#clock-campaign-review) |
| `clock-fake-acquisition.md` | [clock-implementation.md#clock-fake-acquisition](clock-implementation.md#clock-fake-acquisition) |
| `clock-fixed-schedule.md` | [clock-implementation.md#clock-fixed-schedule](clock-implementation.md#clock-fixed-schedule) |
| `clock-followup.md` | [clock-history.md#clock-followup](clock-history.md#clock-followup) |
| `clock-host-cause-review.md` | [clock-history.md#clock-host-cause-review](clock-history.md#clock-host-cause-review) |
| `clock-host-maintenance-plan.md` | [clock-operations.md#clock-host-maintenance-plan](clock-operations.md#clock-host-maintenance-plan) |
| `clock-host-runtime-review.md` | [clock-operations.md#clock-host-runtime-review](clock-operations.md#clock-host-runtime-review) |
| `clock-input-request.md` | [clock-operations.md#clock-input-request](clock-operations.md#clock-input-request) |
| `clock-nict-method-review.md` | [clock-operations.md#clock-nict-method-review](clock-operations.md#clock-nict-method-review) |
| `clock-origin-investigation.md` | [clock-history.md#clock-origin-investigation](clock-history.md#clock-origin-investigation) |
| `clock-owner-admin-read.md` | [clock-operations.md#clock-owner-admin-read](clock-operations.md#clock-owner-admin-read) |
| `clock-process-ownership.md` | [clock-history.md#clock-process-ownership](clock-history.md#clock-process-ownership) |
| `clock-qualification-plan.md` | [clock-operations.md#clock-qualification-plan](clock-operations.md#clock-qualification-plan) |
| `clock-rate-review.md` | [clock-implementation.md#clock-rate-review](clock-implementation.md#clock-rate-review) |
| `clock-raw-bundle.md` | [clock-implementation.md#clock-raw-bundle](clock-implementation.md#clock-raw-bundle) |
| `clock-resources-admin-review.md` | [clock-history.md#clock-resources-admin-review](clock-history.md#clock-resources-admin-review) |
| `clock-resync-result.md` | [clock-operations.md#clock-resync-result](clock-operations.md#clock-resync-result) |
| `clock-source-readiness.md` | [clock-operations.md#clock-source-readiness](clock-operations.md#clock-source-readiness) |
| `clock-task-definition-review.md` | [clock-operations.md#clock-task-definition-review](clock-operations.md#clock-task-definition-review) |
| `clock-web-source-review.md` | [clock-operations.md#clock-web-source-review](clock-operations.md#clock-web-source-review) |
| `data-contract.md` | [architecture.md#data-contract](architecture.md#data-contract) |
| `data-profiles.md` | [architecture.md#data-profiles](architecture.md#data-profiles) |
| `deployment-observability.md` | [runbook.md#deployment-observability](runbook.md#deployment-observability) |
| `deployment-recovery.md` | [runbook.md#deployment-recovery](runbook.md#deployment-recovery) |
| `evidence-guide.md` | [evidence.md#evidence-guide](evidence.md#evidence-guide) |
| `glossary.md` | [architecture.md#glossary](architecture.md#glossary) |
| `initd-owner-correlation.md` | [clock-history.md#initd-owner-correlation](clock-history.md#initd-owner-correlation) |
| `interruption-clock-diagnostics.md` | [clock-history.md#interruption-clock-diagnostics](clock-history.md#interruption-clock-diagnostics) |
| `measurement-evidence-archive.md` | [monitoring.md#measurement-evidence-archive](monitoring.md#measurement-evidence-archive) |
| `measurement-interruption.md` | [monitoring.md#measurement-interruption](monitoring.md#measurement-interruption) |
| `mlflow-orm-compatibility.md` | [runbook.md#mlflow-orm-compatibility](runbook.md#mlflow-orm-compatibility) |
| `monitor-investigation-and-next-data.md` | [capital.md#monitor-investigation-and-next-data](capital.md#monitor-investigation-and-next-data) |
| `monitor-reconciliation.md` | [monitoring.md#monitor-reconciliation](monitoring.md#monitor-reconciliation) |
| `monitoring-acceptance.md` | [monitoring.md#monitoring-acceptance](monitoring.md#monitoring-acceptance) |
| `prediction-precision-migration.md` | [runbook.md#prediction-precision-migration](runbook.md#prediction-precision-migration) |
| `product-completion-policy.md` | [capital.md#product-completion-policy](capital.md#product-completion-policy) |
| `reviews/20261028-r06-delta-review.md` | [evidence.md#20261028-r06-delta-review](evidence.md#20261028-r06-delta-review) |
| `reviews/20261028-review.md` | [evidence.md#20261028-review](evidence.md#20261028-review) |
| `reviews/20261028-staging-checkpoint-review.md` | [evidence.md#20261028-staging-checkpoint-review](evidence.md#20261028-staging-checkpoint-review) |
| `reviews/20261028-synthetic-staging-harness.md` | [evidence.md#20261028-synthetic-staging-harness](evidence.md#20261028-synthetic-staging-harness) |
| `reviews/20261030-checkpoint-review.md` | [evidence.md#20261030-checkpoint-review](evidence.md#20261030-checkpoint-review) |
| `runbook.md` | [runbook.md#runbook](runbook.md#runbook) |
| `t06-luna-handoff.md` | [evidence.md#t06-luna-handoff](evidence.md#t06-luna-handoff) |
| `t08-sol-handoff.md` | [evidence.md#t08-sol-handoff](evidence.md#t08-sol-handoff) |
| `t09-october-acquisition.md` | [capital.md#t09-october-acquisition](capital.md#t09-october-acquisition) |
| `t10-input-readiness.md` | [capital.md#t10-input-readiness](capital.md#t10-input-readiness) |
| `t10-snapshot-review.md` | [capital.md#t10-snapshot-review](capital.md#t10-snapshot-review) |
| `t15-luna-handoff.md` | [evidence.md#t15-luna-handoff](evidence.md#t15-luna-handoff) |
| `t16-luna-handoff.md` | [evidence.md#t16-luna-handoff](evidence.md#t16-luna-handoff) |
| `t17b-sol-handoff.md` | [evidence.md#t17b-sol-handoff](evidence.md#t17b-sol-handoff) |
| `time-api-options.md` | [clock-operations.md#time-api-options](clock-operations.md#time-api-options) |
| `training-monitor-reference.md` | [monitoring.md#training-monitor-reference](monitoring.md#training-monitor-reference) |

</details>

## 文書の編集方針

説明は「目的、必要な準備、操作、確認する結果」の順に書きます。
略語はページ内で一度だけ正式名称と意味を説明し、以後は短い表記を使います。識別子やコマンド、画面の状態名は変更しません。
同じ内容へのリンクを何度も置かず、入口となる一覧へまとめます。流れや分岐は図にし、細かな条件や数値は表で確認できるようにします。

過去の実行結果と、現在の説明文は扱いを分けています。
作業ログのコマンド、エラー原文、識別子、測定値は保持し、読み始めの案内と折りたたみで探しやすくしました。
仕様中の否定、必須条件、不確かさは、文章を短くするために取り除きません。

今回の推敲では、指定された記事とスキルを確認し、日本語の表記は[JTFのスタイルガイド](https://www.jtf.jp/index.php/tips/styleguide)、略語の扱いは[Microsoftの執筆ガイド](https://learn.microsoft.com/ja-jp/style-guide/acronyms)も参照しました。
表現の自然さは規則だけでは決められないため、主語と動作、条件、時制が元の資料と同じ意味になるかも読み比べています。

日本語の検査には[yomiyasu](https://github.com/nanaism/yomiyasu)のバージョン3.1.0を一時的に取得して使用しました。
`natural-japanese`と`meiseki`の紹介は方針を比較する資料として読み、複数の推敲スキルを同時適用してはいません。
機械検査の指摘件数は、読みやすさの得点や意味保存の証明ではありません。
