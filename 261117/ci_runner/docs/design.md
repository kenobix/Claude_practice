# P0設計案：ジョブの正本と実行権

状態: P0検証とP1前半（受付/取得API・DB永続化・冪等性）は実装済み。Workerの初回取得と実行前DB preflightは実装済み。[固定fixtureの未起動コンテナ準備](container-preparation.md)と[内部の上限付きfixture実行](fixture-execution.md)も実装済み。[条件付きfixture結果確定](result-finalization.md)も実装済み。上限付きログDB保存を結果確定へ接続済み。正式ログ取得/最小HTML表示も実装済み。受理commitの常駐実行は実装済み。Webhook・履歴UI等は未実装。
実装したHTTP契約と競合・commit前後の障害証拠は[受付API仕様](intake-api.md)を参照。
[Worker初回取得](worker-claim.md)は履歴なしの許可queuedだけを取得し、attempt番号1・initial・DB期限30秒を同時commitする。期限監視を実装済み。再試行は未実装。
[実行前DB preflight](execution-preflight.md)は現在の実行権を再確認するsnapshotを返す。固定fixtureの起動前/ガード後にDBを再照合し、実行中はheartbeatと時間・ログ・残leaseを監視する。DB照合と外部起動は別操作。
計画書のP1/P2/P3/P4にまたがる条件を先に定義する。数値は実験初期値であり保証ではない。

## MVPの状態遷移

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
- 信頼済みのWorkerと残存コンテナ回収プロセスがDocker daemonを操作する。取得・作業領域準備とネットワーク無効の実行を分離する。
- heartbeat=5秒、lease=30秒、期限監視=5秒。期限の正本はPostgreSQL時刻。
- heartbeatと完了報告はrun行を先にロックし、running・現在のrunning attempt/token・期限内を再確認する。期限の更新は失効済みtokenを復活させない。
- 取消・完了・期限回収も同じrun→attemptのロック順。取消要求を先にcommitしたら成功更新を拒否する。先に完了commitしたら取消は終了済みと返す。
- 期限監視が失効attemptを閉じた後、ホストごとの回収プロセスが該当コンテナを処理する。再キューには回収確認と再試行予算が必要。
- DB断では新規ジョブを開始せず、lease更新の応答が不明なら成功とみなさない。Workerの単調時計watchdogで残存実行を止める。
- 外部の停止とDB更新は単一transactionにできない。取消は完了確定をまず防止し、実際の停止確認・残存回収を追跡する。

### Lease更新の実装範囲

Worker内部の `renew_lease(claim)` はrun→attemptの順に行ロックを取得する。現在の許可設定に合う
running runで、取消要求・正式結果がなく、run ID・attempt ID・番号・Worker ID・tokenが一致する
初回running attemptだけを対象にする。現在許可される取得は初回だけ。再試行attemptへの対応は再試行の実装時に追加する。

両方の行を確保してから `clock_timestamp()` を一度取得する。保存済みの期限がその時刻より後の場合だけ
期限をDB時刻+30秒に設定する。期限とDB時刻が等しい場合も拒否する。呼び出し元の保持する期限から延長幅を計算しない。
run/attemptの識別子、token、状態、由来・結果・ログは保持する。

commitが完了した場合だけ新しい期限を返す。条件不一致は `LeaseRenewalDenied`、DBエラーやcommit応答不明は例外で返す。
応答不明の場合の期限はDB側で延びている可能性がある。呼び出し元が実行継続に使える更新応答は発行されない。
専用DBで、取消による行ロック待ちと、attemptロック待ちの間の自然失効を確認した。
[実行ループ](fixture-execution.md#監視出力終了結果)は、GO送信から5秒ごとに同じClaimで更新する。
DB操作は最大1件を別スレッドで実行し、確認した更新だけをローカル期限へ反映する。基準は応答時刻でなく更新開始前の単調時計。
更新要求から5秒または従来のローカルlease期限の早い方までに応答を確認できなければ、実行を中断し所有資源を回収する。
実行時間とログ量の上限は継続し、更新を待つ間も監視する。失敗したWorkerは確定を呼ばず、後続runを開始しない。

### 期限監視の実装範囲

独立プロセスの `ci_runner.expiry_monitor --watch` は許可repository/ref/workflowの失効を1走査最大16件処理し、5秒待つ。
`--once` は1走査で終了する。1件ずつcommitし、確認できたrun ID・attempt ID・状態・理由だけをJSONで出す。
DBエラーやcommit応答喪失は終了1。再走査はrunningの組だけが対象なので、既に閉じたattemptを二重に処理しない。

PostgreSQLの文開始時刻で候補を選び、期限順にrun行を `FOR UPDATE OF r SKIP LOCKED` で確保する。
次にattemptをロックし、更新時の `clock_timestamp()` で期限以下かを再確認する。
期限切れを確定すると、runとattemptの両方を同一transactionで閉じる。
現段階は取消要求ありなら `canceled / canceled_lease_expired`、それ以外は `failed / infra_lease_expired`。
終了コード・正式attempt参照・ログ・由来を作らず、識別子・token・元の期限は保持する。

MVPの予算内再キューには、旧attemptの資源回収の確認と新attemptの発行が必要。

### 失効attemptのコンテナ回収

`orphan_cleanup --once` はWorkerと同じDocker daemon・専用DBで実行する。Dockerの一覧を
`dev.ci-runner.preparation-session` で絞り、停止済み・未起動を含めて完全IDで各候補を照合する。
32桁のsessionと `ci-runner-prepare-<session>` の名前、標準UUID表記のrun/attemptラベルを要求する。
同じrun/attemptの組について、DBで許可repo/ref/workflow、初回attempt、正式結果と終了コードの欠如、
期限以後の終了時刻を確認する。対象は `failed / infra_lease_expired` と、取消要求を伴う
`canceled / canceled_lease_expired` の確定済み記録。期限時刻を過ぎたrunning attemptは期限監視が先に処理する。

DB照合のtransactionが正常に戻った後、照合した完全IDを指定して `docker container rm --force` する。
同じIDの削除確認を得たときにだけreceiptを出す。競合時に同じIDの不在を確認できれば処理済みとして続ける。
DB接続異常やDockerの結果不明は終了1。周期的な実行は運用側から行い、障害復旧後は再走査する。
権限境界は既存Workerと同じで、コンテナのラベル・名前を設定できるDocker管理主体と、専用DBの書込主体を信頼する。
回収対象はコンテナ本体。imageタグ、volume、network、build cacheの回収は別途所有権を確認する必要がある。

回収のDB検索はread-only。一覧は取得時点の観測で、応答喪失後に完了するcreateは次の走査で拾う。
[作成台帳](#コンテナ作成台帳)が予約を記録していて完全IDが未記録なら、一覧が空でも回収は未確認。
回収側による完全IDの記録と不在確認の永続化を、再キューの前提として後続単位で扱う。

実Dockerと専用DBの回帰は `scripts/verify_execution.py` から `orphan_cleanup` を選択する。
作成後・GO後にWorkerをSIGKILLし、期限監視後の回収、DB停止中の保留、回収競合を検証する。

### コンテナ作成台帳

migration 005はattemptに作成台帳を追加する。新しいWorkerが発行したattemptはclaimと同時に `create_tracked=true` を
確定する。既存行は `false` で、作成の有無は不明として扱う。診断用の準備試験は実行を伴わない既存の分離経路を使う。

実行Workerは固定imageと受理SHAの照合後、同じDocker daemonのIDと一意のsessionをDBに予約する。
操作中は同じ信頼済みDocker contextを使用し、Dockerが返すdaemon IDはUUID形式を要求する。
予約はrun→attemptの順にロックし、許可設定・受理SHA・実行権のtoken・状態・取消・DB時刻の期限を照合する。
監視や取消が先に確定した場合、または同じattemptに予約がある場合は拒否する。
予約のcommit応答を確認してからDocker createを一度だけ呼ぶ。応答が不明ならWorkerは処理を中断する。
Dockerが返した完全IDとinspectが一致し、既存の設定検証を通過したら同じattempt・daemon・sessionへ完全IDを保存する。
これはコンテナの観測記録。起動前とGO前には実行権を従来どおり再確認する。

| 失効したattemptの台帳 | 作成と回収の判断 |
|---|---|
| 記録対象外（既存行） | 作成履歴不明 |
| 記録対象で予約なし | WorkerはDocker createを送っていない |
| 予約あり、完全IDなし | 作成結果不明。遅れて現れる可能性があり、応答喪失で実際には送信していない場合も含む |
| 予約あり、完全IDあり | 同じdaemon上のコンテナを照合するための識別情報がある。回収確認は別途必要 |

現段階の回収コマンドで削除しても、台帳に回収確認は保存されない。createの応答喪失後に回収され、完全IDを
永続化できなかった予約は作成結果不明のまま残る。再試行はこの不明状態を自動で解除しない設計にする。
台帳はコンテナ用で、claim前に準備したimageタグの所有権・回収は別に扱う。

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
| attempt_logs / log_storage_budget | attempt別の不変byteaログ、合計1MiB/attempt・最大64MiBのraw保持payload台帳 |
| attempt_sources | 受理commitの正式attempt別source・imageの由来。runとattemptの組はFKで対応 |
| schema_migrations | 適用版とDB時刻、transactionでDDLと同時記録 |

基礎DDLは `../migrations/001_initial.sql`（未変更）。版2で履歴用(created_at DESC, id DESC) indexを追加。版3でログと容量台帳を追加（[仕様](log-storage.md)）。UUIDはDB生成。日時はtimestamptz。
状態CHECKだけでは遷移順・期限・取消優先・正式結果の整合性を保証しない。
それらはP1/P2の条件付き更新と競合統合テストで検証する。実DB適用・二重適用・業務キー一意制約・状態CHECK・再起動後のschema保持は検証済み。結果確定と条件付き取消SQL、期限監視との競合を検証済み。取消API・再試行は未実装。
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
初回取得時のrun/attempt同時更新、rollback、ロック解放と取得競合は検証済み。ローカルfixtureの結果反映・再送・commit境界SIGKILL・取消/期限競合も検証済み。Worker全体の障害回復は未実装・未検証。[隔離実測](isolation.md)と[P1計画](p1-plan.md)を参照。
復旧45秒は未測定の仮目標。実装も測定も未実施。

## 参照

現在のP1実装では、[一件Worker](fixture-worker.md)が既存の取得・上限付きローカルfixture実行・条件付き結果確定を接続する。
候補の読取transactionを終了後に固定imageを準備し、その後に初回取得する。image IDを固定してcreateし、containerとunique imageタグの回収後に結果確定する。
claim-onceは取得だけを維持。受理commit経路では検証・buildを前置し、同じrun ID/SHAだけをclaimして既存の実行権照合を通す。migration 004で正式結果と同時に由来を保存する。常駐経路は一件ずつ処理し、停止時は進行中の一件を片付ける。自動復旧は未実装。正式ログの参照境界は[ログ取得仕様](log-access.md)を参照。

- [PostgreSQL 17 SELECT](https://www.postgresql.org/docs/17/sql-select.html): SKIP LOCKEDはキュー競合回避用途。一般の一貫した読取の代替にしない。
- [FastAPI virtual environments](https://fastapi.tiangolo.com/virtual-environments/): プロジェクト専用Python環境を使用。
