# 文書一覧とファイル統合の対応表

2026-11-15：Markdown 67本を用途別8冊＋この索引へ統合。元の内容は各冊の旧ファイル名の節へ収録した。
過去reviewのJSON、保存済み候補・原本・消費台帳は変更していない。過去ログの旧path/hashは当時の記録で、
必要ならGitの整理前commit `9479201`で原本を確認できる。時計研究・運用受入保留は解除していない。

| 文書 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 構成・データ契約・用語 |
| [runbook.md](runbook.md) | 使用操作・配信・復旧・CI運用 |
| [capital.md](capital.md) | Capital計画・評価・runtime受入 |
| [monitoring.md](monitoring.md) | 監視・照合・運用測定の契約 |
| [evidence.md](evidence.md) | 証拠の読み方・過去review・引継ぎ |
| [clock-operations.md](clock-operations.md) | 時計資料：手順と選定・保留事項 |
| [clock-implementation.md](clock-implementation.md) | 時計資料：offline部品と合成検証 |
| [clock-history.md](clock-history.md) | 時計資料：原因調査の履歴（保留） |

## Pythonの入口

製品18本、tests12本、補助Python7本へ統合した。元モジュールのstate/mock境界を維持するため、
同名の補助bindingには役割prefixを付けた。runtimeで旧ソースをexecする仕組みや旧名stubは置いていない。
Python import・テストの参照・CLI/Compose/CIを新配置へ更新した。
完全な旧→新module/export/command対応は[configs/file-layout.json](../configs/file-layout.json)を参照。

| 補助CLI | コマンド |
|---|---|
| `python scripts/data_tools.py` | `prepare`, `protocol`, `evaluate`, `evaluation-ledger` |
| `python scripts/runtime_tools.py` | `register`, `seed`, `plan`, `benchmark`, `recheck`, `record-operational`, `evidence`, `decision`, `inspect`, `pending-audit`, `attempt-ledger` |
| `python scripts/clock_tools.py` | `capture-fake`, `derive`, `review`, `rate-review`, `bundle`, `diagnose` |
| `python scripts/system_harness.py` | `run`, `fixture` |
| `python scripts/storage_checks.py` | `snapshot`, `interruption`, `monitor-inspection`, `monitor-persistence`, `migration`, `sqlite-os`, `storage-probe` |
| `python scripts/runtime_checks.py` | `decision-http`, `deployment-recovery` |

## 製品コードとテストの配置

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

各ファイルの区切りコメントに統合元の名前を残した。CLIはスクリプト名の後にサブコマンドを指定する。
例えば旧 `python scripts/register_capital_candidate.py --help` は、
`python scripts/runtime_tools.py register --help` となる。旧CLI名の互換ファイルは残していない。
通常APIは `bike_mlops.api:app`、専用Capital APIは `bike_mlops.api:_staging_api_app` が入口。
文書・過去ログ・証拠JSONの旧path/hashは当時の記録として読み、再実行時は対応表で現行入口を確認する。

統合前の合成時計raw（6/7個の旧source名）はoffline読取を維持する。新規の合成rawは
実在する `clock.py` と `clock_tools.py` の2本を結合する。保存コードは実行しない。
公開用exportは現在インストールしたsource bytesとの一致を引き続き要求し、時計資格を与えない。

## 旧Markdownから探す

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
