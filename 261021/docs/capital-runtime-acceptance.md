# Capital実候補の隔離runtime受入（2026-11-14）

追補：[運用試行の保留と部分復元](capital-operational-hold.md)まで進んだ。運用数値は条件内だったが、
全journalのUTC逆行を検出したため採用せず、再測定も行っていない。以下は先行API/監視の節目の記録。

同じ評価済み候補を専用MLflowへblocked登録し、実HTTP予測・PostgreSQL監査・monitor CLIを通した。
**API/監視の今回の受入は成功。T02/T12全体とT13b採否は未完了**。
[今回の証拠索引](reviews/20261114-capital-runtime-checks.json)、[実精度評価](capital-first-evaluation.md)、
[作業別の開始条件](product-completion-policy.md)を参照する。

## 実行環境とidentity

- 実行hostはDESKTOP-J589D75。画像のAI-DESKTOPへは接続せず、ユーザー指定の現在PCで実行した。
- 専用Compose projectは`capital-acceptance-20261114`。MLflow 5001、DB 55434、API 8110をlocalhostだけへ公開。
- 独立したPostgreSQL/artifactの2 volumeを新設。既存UCIの5コンテナはID・image・起動時刻が不変。
- sourceは先行専用projectの`767760d`。現在のDockerfileから別名imageをbuildし、全105固定依存版を照合。
  container Pythonは3.12.11、host専用venvは3.12.3。差を記録し、同一Python patchとは主張しない。
- candidate ID：`e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b`。
- 元skops SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
- 専用registry内の`bike-demand-capital` version **1**。
  登録artifact directory SHA：`13dbe8447e816b29ecf76c1cf4e173f679a50204ab8d9d682c113ed405810f8c`。
  MLflowが読み込んだ候補を包装・保存したdirectoryのhashで、元skopsファイルのhashとは対象が違う。再fitはしない。
- operational gateは`not_evaluated`、deployment permissionは`blocked`。
  APIの`deployment_operation=legacy_unverifiable / approval_evidence=false`を承認済みに読み替えない。

## 確認したこと

| 経路 | 実行結果 |
|---|---|
| 固定入力→専用DB | 同一CSV/manifestから32,849観測行を投入。元default pairと正式消費台帳は不変 |
| readiness/version | 実APIが200。同candidate・version・登録artifact hash・profile/TZif identityを照合 |
| offline/API一致 | 04-01 10時の予測700.7861957728056が一致。同時2要求と同一再送も同じ保存結果 |
| audit/競合 | 対象request IDの監査は1行。同IDで時刻を変えると409。通常deploy eligibility検査は拒否 |
| 実HTTP replay | finalと同じ1,461 targetを予測し、HTTPエラー0。保存済みfinalの全予測・時刻とDB監査を照合 |
| monitor CLI | 登録candidateの訓練専用referenceを取得し1,464窓を保存。CSV/JSON/complete marker/DBの照合は`matched` |
| offlineとの比較 | 全1,464窓の母数・MAE/RMSE・PSI詳細・状態が一致。新しいfit/選定や閾値調整なし |

replay/monitorの事前planを保存した。予測対象は08-01 03時〜10-01未満、
監視clockは末尾ラベル+3hを含む10-01 03時未満とした。
CLIのendを後者にし`--limit 1461`を固定して、10月の追加targetが混入していないことを全監査行で確認した。
finalを再選定へ消費したものではない。

分布警告1,336、誤差警告234、初期のラベル不足26窓は[offline診断](capital-offline-monitoring.md)と同じ。
最新は予測/正解165件、MAE224.346232で誤差警告、分布警告なし。警告を正常に保存できたことと、
業務上許容できることを分ける。天候等の因果は未特定。bins・閾値・recipeを後付けで変えない。

monitor DBのMAE/RMSE/coverageは既存schemaのREAL（binary32）。照合CLIは元binary64を型どおり丸めて比較した。
初期の欠測指標はSQL NaN計52個として明示し、0扱いしない。予測監査のpredictedはDOUBLE PRECISION。
`monitor-inspect`自体のmodel bindingはreport_onlyなので、別途登録reference・API/監査のidentity照合を結合した。

## ログ・画面・再開時のpath

MLflow UIはこのPCの[専用実験run](http://127.0.0.1:5001/#/experiments/1/runs/7827b63cd9034ce7809367cf162e76e5)。
既存UCIの5000と混同しない。APIは[ready](http://127.0.0.1:8110/ready)、[version](http://127.0.0.1:8110/version)、
[操作schema](http://127.0.0.1:8110/docs)。監視結果はMLflowへ自動uploadしていない。

原本はGit対象外の`data/reports/20261114-capital-runtime/`。
`monitor.csv`、`monitor.json`、`monitor.complete.json`、`monitor-inspect-v1.log`、
`monitor-comparison-result.json`、全command/exit/UTC未資格値/monotonic/log hashを保存した。
秘密値は専用`private.env`（0600）だけへ保存し、既存`.env`を読まない。原本とvolumeは続きを行うため保持する。

専用projectの`data/derived/capitalbikeshare-next`は正式snapshotへのaliasのまま。
`data/evaluations`全体を元directoryへ向ける旧aliasは、運用証拠のproject内path契約と両立しないため、
専用directoryへ変更し、**consumption-ledger.jsonファイルだけを元の単一正式台帳へsymlink**した。
そのSHAは`c42de5e869d4765ac5f776ed098e98cbbed347c0dbe2c967016b14d64986b652`のまま。
運用plan/attempt台帳は専用project内。既存の同candidate運用attemptは開始前に存在しないことを確認した。

## 失敗・検証・残件

- 最初のMLflow接続はworker起動前でreset。起動完了ログを確認後の読取でhealth200/登録モデル0を確認してから登録した。
- 中断回帰の初回はsandboxによるsocket作成拒否3件。権限を限定して実行した同9テストは成功。
  T03の自然再発・解決とは扱わない。
- helperの運用plan出力先誤りを製品CLIが拒否。指定directoryへ修正し、保存後の全bytesを保って上記専用pathへ移した。
  この時点では測定attemptを開始していない。
- runner9、journal9、publisher6、future/証拠15の計39回帰が成功。全suite/新CI成功は主張しない。
  製品source/schema/writer/lock/workflowは変更していない。
- 通常の同期表示はNTP=yes/NTPSynchronized=yes、約3秒31点のUTC順序・monotonic/perf_counter正方向を確認。
  精密校正や全時計資格ではなく、過去異常の解決にも使わない。時計研究は保留。

次は凍結したplanの暖機50＋逐次1,000要求、同artifactのDB/artifact対応復元とmetadata、
全22 prerequisite caseの実producer不足、警告reviewを進める。結果が揃うまで運用gateと採否を合格扱いにしない。
