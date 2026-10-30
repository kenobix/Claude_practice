# Capital配信証拠の束ね方と検証

2026-10-30、計画T01とT02の必須証拠schema・拒否処理を実装した。監査A01/A02/A03の修正であり、実Capital候補の採用完了ではない。現在は実candidateがなく、監視・復旧・復元・metadataの実証も残る。正規の残課題は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)、測定からの手順は[bootstrap gate](capital-bootstrap-gate.md)を参照する。

## 証拠bundleとは

承認対象の報告書一式を入れたJSONファイルである。報告書の場所や「passed」というタグだけを残すと、別の報告書に変わったことを見逃しうる。そこで報告書の本文、本文のSHA-256、対象モデル・候補・データの識別情報を同じファイルに保存する。

このbundleはモデルファイルとは別のMLflow run artifactとして保存する。既存モデルartifactを変更しない。登録versionの元runに、次の場所で保存する。

```text
capital-deployment-evidence/<bundleファイルのSHA-256>/bundle.json
```

bundle schemaは`1`、policy IDは`capital-deployment-evidence-v1`。policy SHA-256は`b91bb7f3846e78ff38fa5311847328ade64f92aafe022fc5725aa8fba2161685`。

policyはソート済みキー・空白なし・UTF-8・末尾改行1個のJSONからSHA-256を計算する。実装は`capital_deployment_evidence.canonical_bytes`。各報告書は再整形せず、元ファイルのUTF-8文字列と元バイト列のSHAを保存する。NaN/Infinity、重複JSONキー、未知policy、32MiBを超えるbundleは拒否する。

## 必須の内容

最上位は`schema_version`、`kind=capital-deployment-evidence`、`policy`、`identity`、`reports`を持つ。identityはexact model name/version/runと、profile/target/feature契約、candidate、モデルartifact、データ、protocol、報告書、plan、policy、運用attempt・要求集合・検証status・p95のタグを結ぶ。

reportsには次の11項目すべてを入れる。各項目は`{"sha256":"<元ファイルSHA>","content":"<元ファイルの内容>"}`とする。

| 項目 | 保存するものと検査 |
|---|---|
| candidate | 候補選定報告。candidate identityを再計算し、データ・artifact・policyを照合する。 |
| final | 同じ候補を再学習せず評価した報告。選定に使っていない窓・同じartifact・blocked状態を検査する。 |
| protocol | 凍結したschema 2のbaseline-only評価手順。窓・件数・ラベル遅延・元データhashを照合する。 |
| validation_rows | 保存済み行別予測CSV。MAE/RMSEとvalidation gateを再計算する。 |
| final_rows | 保存済み最終評価の行別予測CSV。MAE/RMSEとfinal gateを再計算する。 |
| plan | 凍結運用計画。candidate/final/protocol/学習行集合/登録artifactと照合する。 |
| operational | 完了attemptの測定報告。50暖機+1,000逐次要求の順序・ID・p95・監査・出典・資源記録を再確認する。 |
| monitoring | 下表の監視6ケースの完了証拠。 |
| recovery | 障害・復旧8ケースの完了証拠。 |
| restore | snapshot・復元・移行4ケースの完了証拠。 |
| metadata | 配信metadata4ケースの完了証拠。 |

CSVの再計算は保存済み予測の計算確認である。新しいfit、モデルを使った予測のやり直し、未使用評価窓の消費はしない。元CSV/manifestとcandidate ledgerの照合は、公開CLIの準備段階で既存`verify_candidate_for_staging`も使って行う。runtimeは束ねた証拠を照合するため、開発PCの元ファイルpathへ依存しない。

## 必須受入ケース

各case IDと結果はpolicyで先に固定する。必要な実装・実証を完了する前に、空のpassed報告書を作って埋めてはいけない。

| 分類 | case ID | 必須observed_outcome |
|---|---|---|
| monitoring | training_reference_bound | matched |
| monitoring | psi_v2_constant_shift | detected |
| monitoring | distribution_error_separation | distinguished |
| monitoring | delayed_missing_duplicate_labels | handled |
| monitoring | insufficient_labels_and_unset_reference | explicit |
| monitoring | monitor_persistence_reconciliation | matched |
| recovery | concurrent_request_retry | committed_once |
| recovery | changed_input_conflict | 409 |
| recovery | version_switch_preserves_saved_prediction | preserved |
| recovery | audit_write_failure | 503 |
| recovery | bad_artifact_and_ready | rejected |
| recovery | api_stop | detected |
| recovery | caddy_switch_and_rollback | recovered |
| recovery | interrupted_deployment | recovered |
| restore | database_artifact_snapshot_binding | matched |
| restore | separate_volume_restore | restored |
| restore | restored_version_prediction_audit | matched |
| restore | legacy_real_double_migration | matched |
| metadata | entry_runtime_database_identity | matched |
| metadata | approval_operation_metadata | matched |
| metadata | unloadable_version | excluded |
| metadata | timeout_stage_outcome | explicit |

4分類の報告書は次の形式にする。`identity`には`model_name`、`model_version`、`candidate_id`、`artifact_sha256`、`data_profile_id`、`target_contract_id`、`data_sha256`、`feature_contract_version`の8項目をすべて含める。versionは文字列とする。

```text
schema_version: 1
kind: capital-deployment-prerequisite
category: monitoring / recovery / restore / metadata のいずれか
identity: 上記8項目
policy_sha256: 上記固定policyのSHA
completed: true
status: passed
evidence_state: confirmed
started_at_utc: UTCの開始時刻
completed_at_utc: UTCの完了時刻
checks:
  各case ID:
    sha256: ケース本文のSHA
    content: ケース結果JSONの元UTF-8文字列
```

各ケース本文は`case_id`、同じ`identity`、`completed=true`、上表の`observed_outcome`、空でないJSON objectの`observations`を持つ。observationsにはproducerが実際に観測した値・件数・参照log等を保存する。必須caseの欠落・余分なcase、failed、未確定、別候補、別artifact、不正hash、未来時刻、開始後より前の完了、空observationsを拒否する。

**検証の限界:** 現schemaはローカルで信頼するproducerの観測申告である。observationsの内容から独立して障害試験を再実行したり、実際に観測した本人を認証したりはしない。内容と全hashを同時に書き換えられる管理者への改変防止、電子署名、IAMを提供しない。今回の正例は明示した合成fixtureだけで、実証producerはT04/T05/T06/T07/T08/T12/T17で実装・検証していく。

## 保存と承認の順序

以下は必要な実証が揃った後の手順。今すぐ実candidateに実行するコマンドではない。261021から実行し、入力報告書はproject内data配下の通常ファイルに置く。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/prepare_capital_deployment_evidence.py \
  --candidate-id '<candidate ID>' --attempt-id '<completed attempt UUID>' \
  --final-report 'data/reports/<final-report>.json' \
  --monitoring-report 'data/reports/<monitoring-evidence>.json' \
  --recovery-report 'data/reports/<recovery-evidence>.json' \
  --restore-report 'data/reports/<restore-evidence>.json' \
  --metadata-report 'data/reports/<metadata-evidence>.json'
```

既存candidate/ledger/version lockを保持し、completed attemptが指すplanとreportを検査する。元データ・候補・全証拠を照合し、bundleを保存してから取得し直して検査する。SHA/run/path/policyを登録タグへ記録する。同じbundleの再試行は許し、別bundleへの上書きは拒否する。保存だけでは`deployment_permission=blocked`、運用gateも従前のままとする。

その後、[review/approve手順](capital-bootstrap-gate.md)で、人が意図した担当者と理由を記録する。review passedもblockedを維持し、別の承認者によるapproveが全条件を満たした場合だけeligibleを最後に記録する。決定schemaは`2`で、証拠全体のsnapshotとUTC時刻をreview/approvalのhashへ含める。部分書込後の同一要求retryでは保存済み時刻を使う。

旧schemaのCapital決定は通常配信で拒否する。既存決定をその場で新schemaへ格上げしない。実Capital承認済み候補は現在ない。UCIのgateや配信対象を変更する移行ではない。

## 配信前の再確認と今回の試験

review、approve、通常loader、runtime init、deploy promoteでbundle本文を取得してSHA/content照合する。取得前後で登録版、run、証拠/決定タグの変更がないことも確認する。Capitalのtracking/registry URIは同じ先へ固定し、環境の別registryへ流れるのを防ぐ。承認後のreport/plan SHA変更、負/NaN/Infinity p95、本文破損、証拠不足、旧schemaを拒否する。読み込んだAPIプロセス内で毎要求bundleを再取得する仕組みではない。

今回、全241 testsが成功した。mock registryによる負例と、一時SQLite registry・file artifact storeを使った実MLflow clientの保存/取得/review/approve/保存元artifact破損拒否を含む。一時SQLite試験はHTTP serverの障害やネットワーク切断を再現しない。T13のHTTP経由部分書込・応答喪失、T07のCaddy復旧、実candidateの運用測定・承認は未完了。コマンド、途中の試験失敗と修正、証拠logのSHAは[作業ログ](../work_log.md)へ記録する。
