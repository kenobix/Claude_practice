# P0設計案：ジョブの正本と実行権

P0〜P2は固定fixtureで受入確認済み。[Webhook受付と配送の永続化](webhook.md)、[run取消・手動再試行・試行履歴・成果物・運用指標](run-operations.md)を実装済み。GitHubからの実配送は後続作業。
実装したHTTP契約と競合・commit前後の障害証拠は[受付API仕様](intake-api.md)を参照。
[Worker初回取得](worker-claim.md)は履歴なしの許可queuedを取得し、attempt番号1・initial・DB期限30秒を同時commitする。
検証付きWorkerは、[基盤障害の自動再試行](#基盤障害の自動再試行)で再キューされたrunも取得する。
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
| running | lease期限切れ、取消なし、1または2回目 | failed | failed/infra_lease_expired |
| failed | 直近attemptが基盤障害、予算内、作成未送信または回収確認済み | queued | 既存attemptを保持し、次回取得時に新attemptを発行 |
| running | lease期限切れ、取消なし、3回目 | failed | failed/infra_exhausted |
| failed/timed_out | 最新の確定済み失敗または回収済み基盤障害を指定、取消要求なし | queued | 受付を永続化して正式結果の参照を解除。過去attemptを保持し、次回は新番号・token |
| succeeded/canceled | retry要求 | 変更なし | MVPは拒否 |
| 全終端 | 遅延完了・遅延取消 | 変更なし | 古い報告を拒否/終了済みを返す |

自動再試行は、初回投入と各手動再試行を起点とする区間ごとに最大3attempt。現在のバックオフは5秒と10秒。
`retry_start_no` を区間の開始番号とし、`attempt_no` は同じrunの通算番号を維持する。

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
running attemptを対象にする。初回は `1/initial`、基盤障害の再試行は `2〜3/infrastructure`。

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

独立プロセスの `ci_runner.expiry_monitor --watch` は許可repository/ref/workflowの失効と再キューを1走査で各最大16件処理し、5秒待つ。
`--once` は1走査で終了する。1件ずつcommitし、確認できたrun ID・attempt ID・状態・理由をJSONで出す。
DBエラーやcommit応答喪失は終了1。失効処理はrunningの組だけが対象なので、既に閉じたattemptを二重に処理しない。

PostgreSQLの文開始時刻で候補を選び、期限順にrun行を `FOR UPDATE OF r SKIP LOCKED` で確保する。
次にattemptをロックし、更新時の `clock_timestamp()` で期限以下かを再確認する。
期限切れを確定すると、runとattemptの両方を同一transactionで閉じる。
取消要求ありなら `canceled / canceled_lease_expired`。それ以外は `failed / infra_lease_expired`、上限の3回目は `failed / infra_exhausted`。
終了コード・正式attempt参照・ログ・由来を作らず、識別子・token・元の期限は保持する。

MVPの予算内再キューには、旧attemptの資源回収の確認と新attemptの発行が必要。

### 失効attemptのコンテナ回収

`orphan_cleanup --once` はWorkerと同じDocker daemon・専用DBで実行する。Dockerの一覧を
`dev.ci-runner.preparation-session` で絞り、停止済み・未起動を含めて完全IDで各候補を照合する。
32桁のsessionと `ci-runner-prepare-<session>` の名前、標準UUID表記のrun/attemptラベルを要求する。
同じrun/attemptの組について、DBで許可repo/ref/workflow、初回または再試行attempt、正式結果と終了コードの欠如、
期限以後の終了時刻を確認する。対象は `failed / infra_lease_expired` と、取消要求を伴う
`canceled / canceled_lease_expired`、上限到達時の `failed / infra_exhausted` の確定済み記録。
期限時刻を過ぎたrunning attemptは期限監視が先に処理する。

作成台帳の対象では、Dockerのdaemon IDと所有sessionを予約と照合する。遅延createなどで完全IDが未記録なら、
観測したIDを保存する。DB transactionの成功応答を確認した後、完全IDを指定して `docker container rm --force` する。
削除応答にかかわらず、同じdaemonで同じ完全IDのコンテナ専用inspectが不在を返してから、
migration 006の `attempts.container_recovered_at` にDBの現在時刻とattempt終了時刻の遅い方を保存する。DBの壁時計が逆行した場合も、終了・回収の順序を維持する。

既知の完全IDもDBから走査するので、削除後に回収プロセスやDB接続が中断されても、再走査で不在を確認できる。
確認時刻を設定したtransactionが成功した場合だけJSONを出力する。並行確認ですでに確定済みなら時刻を書き換えない。
作成台帳の対象外では従来の所有ラベル照合による削除を続け、Dockerが同じIDの削除を返した場合にJSONを出力する。
この場合にDBの不在確認は残さない。DB接続異常やDockerの結果不明は終了1。周期的な実行は運用側から行い、障害復旧後は再走査する。
権限境界は既存Workerと同じで、コンテナのラベル・名前を設定できるDocker管理主体と、専用DBの書込主体を信頼する。
回収対象はコンテナ本体。imageタグ、volume、network、build cacheの回収は別途所有権を確認する必要がある。

一覧は取得時点の観測で、応答喪失後に完了するcreateは次の走査で拾う。
[作成台帳](#コンテナ作成台帳)に予約があり完全IDが未記録なら、一覧が空でも回収は未確認。
自動再試行では、この不明状態を保留する。

実Dockerと専用DBの回帰は `scripts/verify_execution.py` から `orphan_cleanup` を選択する。
作成後・GO後にWorkerをSIGKILLし、期限監視後の回収、DB停止中の保留、回収競合を検証する。
遅延createと完全ID保存commitの応答喪失、削除後のDB rollbackとcommit応答喪失からの再走査も検証する。

### コンテナ作成台帳

migration 005はattemptに作成台帳を追加する。新しいWorkerが発行したattemptはclaimと同時に `create_tracked=true` を
確定する。既存行は `false` で、作成の有無は不明として扱う。診断用の準備試験は実行を伴わない既存の分離経路を使う。

実行Workerは固定imageと受理SHAの照合後、同じDocker daemonのIDと一意のsessionをDBに予約する。
操作中は同じ信頼済みDocker contextを使用し、Dockerが返すdaemon IDはUUID形式を要求する。
ID照合のたびに現在のcontextから接続先を解決する。通常のローカルUnix socketには `GET /info` で問い合わせ、TLS等を設定したcontextや直接取得に失敗した場合は同じ設定でDocker CLIを使う。作成予約と回収確認は、毎回観測したdaemon IDを照合する。 [Docker Engine API](https://docs.docker.com/reference/api/engine/version/v1.56/#tag/System/operation/SystemInfo)
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
| 予約あり、完全IDあり、確認時刻なし | 同じdaemon上で完全IDの不在を確認する必要がある |
| 予約あり、完全IDあり、確認時刻あり | 回収プロセスが同じdaemonで完全IDの不在を確認した |

作成要求の応答喪失後、完全IDの記録前にWorkerの通常後処理などでコンテナが消えた場合は、作成結果不明のまま残る。
予約commit自体の応答喪失で作成が行われなかった場合も、回収プロセスには作成結果を確定する証拠がない。
台帳はコンテナ用で、claim前に準備したimageタグの所有権・回収は別に扱う。

### 基盤障害の自動再試行

期限監視の各走査は、失効確定後に再キューを確認する。許可されたfailed runで、取消要求と正式結果がなく、
直近のattemptが現在の区間の1回目または2回目で、`infra_lease_expired` で閉じている場合を対象にする。
作成台帳でcreate未送信を確認できる場合、または同じ完全IDの不在確認が確定済みの場合だけ再キューできる。
チェック失敗、タイムアウト、ログ超過、取消、作成の有無が不明な旧記録は自動再試行の対象外。

再キューはrunを `FOR UPDATE OF r SKIP LOCKED` で確保し、直近attemptを読み直して `state` と `available_at` を更新する。
DBの現在時刻、記録済みのattempt終了と回収の時刻のうち最も遅い時刻から、2回目は5秒、3回目は10秒待つ。再キューのcommit後に `infra_retry_scheduled`、直近attempt ID、次の番号と時刻を出力する。
commit応答が消失して再走査しても、すでにqueuedのrunの待機時刻は変更しない。

待機時刻に達したrunは、検証付きWorkerが受理SHAから固定sourceを再検証して取得する。
同一run IDとSHAを指定し、runロック後に直近attemptを読み直して、新しいattempt番号・UUID token・30秒leaseを発行する。
再試行は検証済みsourceを持つimageと結果だけを受け付ける。古いattemptの状態やログは保持する。
診断用 `--claim-once` と検証付き指定のないローカルfixture実行は初回のみ。3回目の失効は `infra_exhausted` で終了し、コンテナは回収対象に残る。

## 冪等性と成果物

pushの業務キーはrepository/ref/commit/workflow版。Webhookのhook/delivery IDも一意。
同じdelivery IDでbodyハッシュが異なる場合は拒否する。配送結果とrunを同じtransactionで確定する。
保持方針と起動手順は[Webhook受付](webhook.md)を参照。
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
| attempts | run内番号一意、token一意、runningはrunごとに最大1件、期限・理由・retry種別・区間開始番号、Docker作成予約・回収確認、取得可能時刻・実行時間 |
| manual_retries | runと再試行対象attemptの組に一意制約、DB受付時刻。次のclaimが最新attemptに対応する受付を照合 |
| deliveries | hook/delivery一意、body SHA-256、結果、runへのFK |
| artifacts / artifact_storage_budget | attempt FK、固定名の不変JSON、SHA-256、保存キー一意、4KiB/attempt・最大16MiBのpayload台帳 |
| attempt_logs / log_storage_budget | attempt別の不変byteaログ、合計1MiB/attempt・最大64MiBのraw保持payload台帳 |
| attempt_sources | 受理commitの正式attempt別source・imageの由来。runとattemptの組はFKで対応 |
| schema_migrations | 適用版とDB時刻、transactionでDDLと同時記録 |

基礎DDLは `../migrations/001_initial.sql`（未変更）。版2で履歴用(created_at DESC, id DESC) indexを追加。版3でログと容量台帳を追加（[仕様](log-storage.md)）。UUIDはDB生成。日時はtimestamptz。
状態CHECKだけでは遷移順・期限・取消優先・正式結果の整合性を保証しない。
それらは条件付き更新と競合統合テストで検証する。実DB適用・二重適用・業務キー一意制約・状態CHECK・再起動後のschema保持は検証済み。[取消・手動再試行API](run-operations.md)は結果確定、取得、期限監視が使うrunロックを共有する。
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
初回取得時のrun/attempt同時更新、rollback、ロック解放と取得競合は検証済み。ローカルfixtureの結果反映・再送・commit境界SIGKILL・取消/期限競合も検証済み。[隔離実測](isolation.md)と[P1計画](p1-plan.md)を参照。

### 2 Workerの故障復旧試験

```bash
PYTEST_ADDOPTS='-k test_two_residents_recover_after_sigkill -rP' .venv/bin/python scripts/verify_execution.py
```

同じ使い捨てPostgreSQLとDocker daemonを使う2 Workerと、独立した `expiry_monitor --watch` を起動する。
停止するWorkerは、claimのcommit直後、固定コンテナへGOを送った直後、正常実行と資源回収を終えて結果を確定する直前の3地点で止める。
GitHub取得には固定試験応答を使う。DB時刻や期限を書き換えずに、必要な場合は `orphan_cleanup --once` で記録済みIDの不在を確認する。
待機時刻以後に別Workerが同じrunを取得し、新しいtokenで正式結果・ログ・由来を保存する。
旧attemptの報告は新attemptの実行権がある間と正式確定後に再送し、拒否と履歴の不変を確認する。

SIGKILLの直前から正式結果までを同じDB時刻で測る。45秒は実験上の仮目標で、測定値は[作業記録](../../work_log.md)に残す。
代替Workerのclaim後は、DB照合、image照合、daemon ID取得、作成予約などの開始時刻と所要時間をテストで記録する。時間の基準はclaim応答直後の単調時計。
強制終了時のimageタグはコンテナとは別に残ることがある。試験は控えた完全image ID・専用タグ・所有ラベルを照合してからそのタグだけを削除する。

| P2の受入条件 | 検証 |
|---|---|
| 2 Workerの原子的な取得 | `test_resident_worker_docker.py` の先頭変更・claim競合で、同じrunへのattempt発行は1件で、負けたWorkerが次のrunを処理することを確認 |
| DB時刻による期限、古い報告の拒否 | `test_resident_recovery_docker.py` の3地点で、30秒leaseの自然失効、失効後の回収・再取得、旧tokenの報告による上書きなしを確認 |
| 基盤障害だけを最大3attemptで再試行 | `test_retry_postgres.py` でDB時刻からの5秒・10秒、上限到達と対象外の失敗を検証。`test_retry_docker.py` では2回目の作成予約前に失効した場合と残存コンテナを回収した場合に、3回目だけが正式結果を確定することを検証 |
| DB断で実行を停止し新規処理を開始しない | `test_resident_worker_docker.py` のheartbeat試験で専用DB停止・応答欠落・commit応答喪失時に実行を停止し、次のrunがキューに残ることを確認 |

## 参照

現在のP1実装では、[一件Worker](fixture-worker.md)が既存の取得・上限付きローカルfixture実行・条件付き結果確定を接続する。
候補の読取transactionを終了後に固定imageを準備し、その後に初回取得する。image IDを固定してcreateし、containerとunique imageタグの回収後に結果確定する。
claim-onceは初回取得だけを維持。受理commit経路では検証・buildを前置し、同じrun ID/SHAだけをclaimして実行権照合を通す。migration 004で正式結果と同時に由来を保存する。常駐経路は一件ずつ処理し、停止時は進行中の一件を片付ける。正式ログの参照境界は[ログ取得仕様](log-access.md)を参照。

- [PostgreSQL 17 SELECT](https://www.postgresql.org/docs/17/sql-select.html): SKIP LOCKEDはキュー競合回避用途。一般の一貫した読取の代替にしない。
- [FastAPI virtual environments](https://fastapi.tiangolo.com/virtual-environments/): プロジェクト専用Python環境を使用。
