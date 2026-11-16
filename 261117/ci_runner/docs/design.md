# P0設計案：ジョブの正本と実行権

状態: P0検証とP1前半（受付/取得API・DB永続化・冪等性）は実装済み。Worker・Webhook・ジョブ実行等は未実装。
実装したHTTP契約と競合・commit前後の障害証拠は[受付API仕様](intake-api.md)を参照。
計画書のP1/P2/P3/P4にまたがる条件を先に定義する。数値は実験初期値であり保証ではない。

## 状態遷移

| 現在 | 事象・条件 | 次のrun状態 | attempt処理 |
|---|---|---|---|
| queued | 待機取消 | canceled | 作成しない |
| queued | available_at到達、取消要求なし、原子的取得 | running | 新番号・新token・期限で作成 |
| running | 現在token、DB時刻で期限内、取消要求なし、exit 0 | succeeded | succeededで終了 |
| running | 同条件、チェックexit非0 | failed | failed/check_failure、自動再試行なし |
| running | 同条件、実行時間上限到達 | timed_out | timed_out、コンテナ停止・回収 |
| running | 取消要求確定 | running（要求あり） | Workerが停止処理、成功確定は拒否 |
| running（取消要求あり） | Worker停止確認または期限監視 | canceled | canceledで終了。未回収なら別途追跡 |
| running | 基盤障害/lease期限切れ、取消なし、予算内 | queued | failed/infra理由で終了、新attemptは次回取得時 |
| running | 同上、予算超過 | failed | failed/infra_exhausted |
| failed/timed_out | 明示的な手動retry、取消要求なし | queued | 過去attemptを保持、次回は新番号・token |
| succeeded/canceled | retry要求 | 変更なし | MVPは拒否 |
| 全終端 | 遅延完了・遅延取消 | 変更なし | 古い報告を拒否/終了済みを返す |

自動再試行は初回を含む最大3attempt。手動retryで新しい予算区間を開始する。
区間はattempt.retry_kind=initial/manualを境界に数え、試行番号はrun全体で単調増加。
バックオフは基盤障害後に5秒、10秒（上限30秒）をavailable_atに設定する設計案。
通常チェック失敗・ジョブ時間切れは自動再試行しない。

## 責務と排他

- API: 固定repository/ref/workflowの許可設定、入力上限とcommit SHA検証、受付とrunの同時commit後に受理。
- Worker: 短いtransactionの `FOR UPDATE SKIP LOCKED` でqueuedを取得し、run更新とattempt発行を同時commit。実行中は行ロックを保持しない。
- WorkerだけがDocker daemonを操作する。取得・作業領域準備とネットワーク無効の実行を分離する。
- heartbeat=5秒、lease=30秒、期限監視=5秒。期限の正本はPostgreSQL時刻。
- heartbeatと完了報告はrun行を先にロックし、running・現在のrunning attempt/token・期限内を再確認する。期限の更新は失効済みtokenを復活させない。
- 取消・完了・期限回収も同じrun→attemptのロック順。取消要求を先にcommitしたら成功更新を拒否する。先に完了commitしたら取消は終了済みと返す。
- 期限監視は失効attemptを閉じてから再キューする。一意なrunning attempt制約と組み合わせる。
- DB断では新規ジョブを開始せず、lease更新の応答が不明なら成功とみなさない。Workerの単調時計watchdogで残存実行を止める。
- 外部の停止とDB更新は単一transactionにできない。取消は完了確定をまず防止し、実際の停止確認・残存回収を追跡する。

## 冪等性と成果物

pushの業務キーはrepository/ref/commit/workflow版。Webhookのhook/delivery IDも一意。
同じdelivery IDでbodyハッシュが異なる場合は拒否する。署名検証後に許可設定を照合する（P3）。
delivery保持は30日を暫定案とし、runの業務キーはMVP中保持する。期間外再送もrun重複を防ぐ。
手動投入も同じ業務キーで同一runを返す。明示retryを別操作に分ける。

成果物はサーバー生成名とattempt別領域を使い、1ファイル10MiB・attempt合計20MiBを初期案とする。
DDLは1ファイル上限のみ、合計容量とディスク割当は後続実装で強制する。
run.completed_attempt_idを現在の有効attemptの確定transactionでのみ設定し、古い成果物を昇格しない。
孤立成果物はattemptラベル・保存キーで追跡し、24時間後の削除を暫定案とする。
実行は複数回起こり得る。副作用のないfixtureに限定し、外部副作用のexactly-onceは主張しない。

## PostgreSQL初期モデル

| 表 | 役割・制約 |
|---|---|
| runs | 業務キー一意、状態CHECK、再取得可能時刻、取消要求、正式結果attempt参照 |
| attempts | run内番号一意、token一意、runningはrunごとに最大1件、期限・理由・retry種別 |
| deliveries | hook/delivery一意、body SHA-256、結果、runへのFK |
| artifacts | attempt FK、サーバー生成名、サイズCHECK、SHA-256、保存キー一意 |
| schema_migrations | 適用版とDB時刻、transactionでDDLと同時記録 |

基礎DDLは `../migrations/001_initial.sql`（未変更）。版2で履歴用(created_at DESC, id DESC) indexを追加。UUIDはDB生成。日時はtimestamptz。
状態CHECKだけでは遷移順・期限・取消優先・正式結果の整合性を保証しない。
それらはP1/P2の条件付き更新と競合統合テストで検証する。実DB適用・二重適用・業務キー一意制約・状態CHECK・再起動後のschema保持は検証済み。遷移競合は未検証。
現在のDDLはSHA-1の40桁commitを許容し、他形式は将来再検討する。

## 故障・中断時の期待動作

| 停止位置/故障 | 期待結果・回復 |
|---|---|
| API commit前に停止/DB断 | 未受理。transaction rollback、再送可 |
| API commit後・応答前に停止 | 再送で既存runを返す |
| Worker取得commit前に停止 | rollbackされqueuedが残る |
| 取得commit後・コンテナ作成前に停止 | lease失効でinfra attemptを残し再キュー |
| 作成後・起動前/実行中にWorker停止 | attemptラベルでコンテナ回収、失効時再試行 |
| heartbeat断/DB断 | 新規開始停止、watchdogで実行停止。復帰後DB状態と照合 |
| 古いWorkerが復帰 | 古いtoken/失効報告を拒否、正式結果上書きなし |
| 成果物作成後・結果commit前に停止 | 未確定成果物は正式参照されず、期限後回収 |
| cancelと完了が同時 | 同じ行ロックと更新条件により定義した一方のみ確定 |
| PostgreSQL再起動 | volumeから履歴復元、lease失効処理を再開 |
| CPU占有・大量ログ・時間超過 | 各上限で停止し理由を記録、他ジョブとディスクを保護 |

P1前半ではcommit前/後のAPI SIGKILL、実DB停止、10 HTTP同時投入と一意キー競合待ち、API再起動後の保持を実測済み。
P0ではDB再起動後のschema保持、CPU/RAM/PID制限、時間/ログ上限と回収を固定fixtureで実測済み。
run/attemptへの反映とWorker障害回復・競合は未実装・未検証。[隔離実測](isolation.md)と[P1計画](p1-plan.md)を参照。
復旧45秒は未測定の仮目標。実装も測定も未実施。

## 参照

- [PostgreSQL 17 SELECT](https://www.postgresql.org/docs/17/sql-select.html): SKIP LOCKEDはキュー競合回避用途。一般の一貫した読取の代替にしない。
- [FastAPI virtual environments](https://fastapi.tiangolo.com/virtual-environments/): プロジェクト専用Python環境を使用。
