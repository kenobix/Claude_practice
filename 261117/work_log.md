# 作業記録：CI runner P0準備

## 2026-11-17：依頼と環境

- 目的: `261014/plan_ci_runner.md` を基準に独立開発環境とP0設計を準備する。全機能実装は対象外。
- 初期pwd: `/home/kenshin/work/Claude_practice-261117`。ユーザーが当初指定したprojectsからworkへ訂正し、現在パスで継続。
- ブランチ: `feat/ci-runner`、開始HEAD `b4c460a`。作業開始時の変更なし、261117は未作成だった。
- origin: `git@github.com:kenobix/Claude_practice.git`。
- `git worktree list` でmain、ci-runner、database-engine、pcap-analyzerが別worktreeであることを確認。他worktreeは操作していない。
- 共通AGENTS.mdと計画書を読んだ。今回の禁止指示を優先しcommit/push/PR、ルートREADME/共通CI/AGENTSの編集は行わない。新日付ディレクトリのルート一覧反映は範囲外として保留。

## 環境と依存

| 項目 | 結果 |
|---|---|
| Python | 3.12.3、ci_runner/.venvを新規作成 |
| システム側pip | 26.1.1（利用はバージョン確認のみ） |
| venv側pip | 24.0 |
| FastAPI / Uvicorn | 0.163.1 / 0.54.0 |
| psycopg / psycopg-binary | 3.3.9 / 3.3.9 |
| pytest / httpx | 9.2.0 / 0.28.1 |
| setuptools | 80.9.0 |
| PostgreSQL | 17.12 (Debian 17.12-1.pgdg12+1)、実コンテナで確認 |
| Docker CLI / Engine | 29.9.2 / 29.9.2 |
| Compose | v5.6.1 |
| daemon実行方式 | default context、cgroup v2/cgroupfs、SecurityOptionsにseccomp/cgroupns。rootless表示なし |

直接・推移依存はrequirements.lockに全件固定。Python版・DBイメージdigestも記録。
PostgreSQLイメージ: `postgres:17.12-bookworm@sha256:40865bc877a289da75963d97d72b59633f639fc5af7a4e856bfa3714cae5b536`。

## 実行結果

| 確認 | 結果・限界 |
|---|---|
| venv作成 | 成功 |
| pip install | 通常サンドボックスではDNS失敗。許可経路でvenv内への導入成功 |
| editable install / pip check | 成功 / broken requirementsなし |
| 通常pytest | 通常サンドボックスでは停止したため中断。許可経路では3 passed、DB接続先未指定の1 skipped |
| DB込みpytest | 一時専用DBで4 passed、1 warning（Starletteのhttpx TestClient非推奨）。失敗なし |
| Compose config --quiet | 仮の環境変数で検証成功。実認証情報のファイル保存なし |
| Docker接続 | 通常サンドボックスでpermission denied。許可経路でdaemon接続成功。OS権限変更なし |
| ポート | ssは通常サンドボックスでnetlink拒否。その後許可経路のloopback bindで55417/18117が利用可能と確認 |
| Uvicorn起動 | 127.0.0.1:18117で起動、実HTTP live=200、DB未設定ready=503、検証後停止 |
| PostgreSQL | 一時Compose projectで起動・healthy確認 |
| migration | 版1初回適用成功、2回目はalready appliedで成功 |
| DB制約 | 業務キー重複拒否、未知状態の拒否、テスト行rollbackを実DBで確認 |
| 実DB readiness | schema版1適用後200を確認 |
| DB再起動 | 再起動後schema_migrations版1が保持されることを確認 |
| 後片付け | 一時project ci-runner-261117-p0-checkのcontainer/network/volumeを削除。イメージcacheは再利用用に残す |
| 構文 | Python compileall、shell bash -n成功 |
| ジョブ隔離/資源制限 | 未実施。CPU/RAM/PID/時間/ログ上限、非root、read-only、network制限の有効性を主張しない |

`verify_db.py` は一時資格情報をメモリ内生成し、既存project資源があれば拒否する。
DB統合ではSQLiteへの代替は行っていない。`.env`や実認証情報を含むファイルは作成していない。一時DBの資格情報は検証終了とともに破棄した。

## 作成物と設計判断

- 日付直下: README.md、AGENTS.md、work_log.md。
- ci_runner: pyproject.toml、requirements.in、requirements-dev.in、requirements.lock、.gitignore、.env.example、compose.yaml。
- src/ci_runner: __init__.py、app.py（live/readyのみ、ジョブAPIは404）。
- tests: test_health.py、test_postgres.py。
- scripts: check_environment.sh、migrate.py、verify_db.py。
- migrations: 001_initial.sql（runs/attempts/deliveries/artifactsと適用履歴）。
- docs: design.md、threat-model.md、fixtures.md。
- Redisを増やさずPostgreSQLをキュー/履歴の正本にする計画を維持。
- fixtureは仕様のみ。ホスト上の任意コード実行経路は実装しない。
- lease/取消/再試行/成果物確定は設計段階。DDLのCHECKだけで状態遷移を保証したとは記載しない。

## 未解決と次の順序

1. P0残件: 専用VM/rootless判断と、ジョブ用コンテナのCPU/RAM/PID/時間/ログ上限・実行境界の実測。無効なら開始拒否を設計・実装する。P0全体は未完了。
2. P1最小タスク: 固定repository/ref/workflowとcommit入力検証、POST /runs・GET /runs・GET /runs/{id}、DB transactionによる永続化と冪等性。DB停止中503とcommit後応答前停止の再送試験を追加する。
3. P1: 管理下fixtureイメージ4種、単一Workerの原子的取得・token発行と固定コンテナ実行。ログ上限・時間切れ・回収・結果保存を統合する。
4. P2: heartbeat/期限監視、2Worker競合、基盤障害再試行、古い結果拒否・取消競合・孤立成果物回収の障害試験。
5. TestClientのhttpx非推奨警告を今後の依存更新時に解消。Webhook/UIはP3/P4まで追加しない。

最終確認: 変更対象は261117/内の新規ファイルのみ。仮想環境・cache・egg-info・.envのignoreを確認する。commit/pushはしない。

## 継続依頼：環境構築成果の再確認と保存

ユーザーの新しい指示によりcommit・push禁止を解除し、feat/ci-runnerのみへの保存を承認された。
既存README・設計・ソース・テスト・DDL・依存とGit状態を確認。pip check成功。
verify_db.pyの再実行で4 passed（既知のTestClient非推奨警告1件）、実DB readiness 200、migration再適用、DB再起動後の版1保持を再現した。
一時DB資源は検証後に削除。既存ファイルを確認したうえで、この記録と日付直下AGENTSの現在のGit方針のみ更新した。
環境構築成果を最初の節目としてcommit・pushし、次にP0隔離実測を進める。

## 継続依頼：P0隔離実測・P1準備

最初の節目は `a04d92a`（chore(261117): initialize CI runner development environment）としてcommitし、origin/feat/ci-runnerへpush成功。mainは操作していない。

### 実装と結果

P0専用のDockerfile・有限probe、tests/isolation_support.py、test_isolation.py、verify_isolation.pyを追加した。
本番API/Workerへ実行経路は追加せず、通常pytestからは明示opt-inまでDocker試験をskipする。
Docker Desktop/WSL2、cgroup v2/cgroupfsの現環境を使用し、rootlessやOS設定変更は行っていない。

初回は9 passedだったが、logs fixtureが上限検出後のkill前に自然終了していたため、停止の証拠が不十分と判断。
出力後に待機させ、exit 137を必須にし、設定不一致の拒否とtmpfs noexecも追加した。
最終実行は10 passed in 36.23s。証拠はdocs/isolation-evidence.json、設定・実測・期待動作と限界はdocs/isolation.md。

- CPU 0.5設定、3秒負荷の実測比0.497、throttle 30回。
- RAM 128MiB/swapなしで有限192MiB割当がOOMKilled=true、exit 137。
- 親1+子63=64 PIDで追加fork拒否EAGAIN、limit event増加。
- SIGTERM無視fixtureを10秒監視でkill、10.380秒で終了・exit 137。
- stdout/stderr合計保持1MiBで停止・exit 137。観測総量4MiBはpipe等の転送済みデータを含み、保持しない。厳密な総生成バイト数の上限ではない。
- uid/gid65534、CapEff/CapBnd=0、NoNewPrivs=1、Seccomp=2、setuid/raw socket拒否。
- loのみ、外向きTCP接続ENETUNREACH。root FS書込EROFS、tmpfs容量16MiBでENOSPC、tmpfs実行EACCES。
- Docker socketなし・接続ENOENT、host bind/device/環境変数継承なし。
- 全10コンテナで停止後Pid=0・削除確認、sessionの残存コンテナ0。一時イメージもテスト終端で削除。
- メモリを意図的に64MiBに変更したケースはpreflightで負荷開始前に拒否。未知のprobe名もDocker操作前に拒否。

DB/API回帰もverify_db.pyで再確認し、4 passed、10 skipped（別実行済みDocker試験）、既知のTestClient警告1件。
migration二重適用・readiness 200・DB再起動保持も再確認し、DBの一時container/volume/networkを削除した。
P0の10件とDBの4件は異なる実行で確認した結果であり、単一の14 passed実行ではない。

### 判定・残課題

管理下の固定fixtureによる現在環境のP0は完了。P1計画だけをdocs/p1-plan.mdで具体化し、本格実装は行っていない。
根拠はcgroup実値だけでなく実際の抑制・OOM・拒否・kill・削除結果。nosuid/nodevは設定確認のみ、全setuid/全seccomp攻撃やカーネル脱出は試験範囲外。
DockerのVM/カーネルを共有する構成であり、非信頼コードを受け付ける完全隔離基盤とは説明しない。
監視処理自体のSIGKILL/電源断時回収、Workerのlease/DB断/競合復旧は未実装。別環境では同じ検証を再実行する。
最初のP1は固定入力契約→POST /runsの永続化・冪等性→GET APIと再送/故障試験→単一Worker・制限付き実行→結果保存と回収の順。

生成物はvenv/cache/通常実測JSONをignoreし、レビュー可能なfixtureソースと必要な証拠JSONだけを保存する。
ベースイメージとDocker build cacheは再利用用に残し、global pruneや既存コンテナ操作は行わない。
変更は261117/内に限定し、今回の成果も差分・秘密情報確認後にfeat/ci-runnerへcommit・pushする。

最終の専用ラベル照会で、隔離テストのcontainer/image、およびDB回帰のcontainer/volume/networkの残存はすべて0件だった。
証拠JSONの全ソースSHA-256が保存対象コードと一致し、10ケースのPid=0/removed=true、相対ドキュメントリンクの存在を確認した。

## 継続依頼：P1前半（受付API・DB永続化・冪等性）

開始時のpwd/branchは指定どおり、HEADは31d3467、変更なし。計画・日付AGENTS・既存API/DDL/migration/テストを確認した。
変更は261117/に限定し、Worker・Webhook・clone・ジョブ実行経路を追加しない方針を維持した。

### 実装した契約

- サーバー許可設定はkenobix/Claude_practice、refs/heads/main、fixture-v1に固定。入力はrepository_idと小文字16進40桁commit_shaのみ。
- 任意URL/command/YAML/ref/workflow等の未知入力を拒否。JSONのみ、body 4KiB。Content-Lengthと実受信量の両方を確認し、複数chunk、ヘッダー偽装、巨大bodyをDB接続前に拒否する。
- POST /runsはcommit後に新規201、業務キー再送200で同一run。再送でfailed等の保存状態をqueuedに戻さない。
- GET一覧はcreated_at DESC/id DESCのkeysetページング（limit 1〜100）。日時/UUIDカーソルを検証し、詳細は不正UUID422・未登録404。
- DB未設定/停止/query失敗は情報を漏らさず503。commit結果不明時は同じ業務キーによる再送で確認する。
- READ COMMITTEDを明示し、競合INSERT後は別SELECTで相手のcommitを読む。SQL値はパラメータ化。
- 001は未変更。002で履歴indexを追加し、migrationツールを番号順の前進適用へ拡張。readyは版2を要求する。
- SHAの実在・ref所属は未確認。受付はqueuedの永続化だけで、Worker等の実行権・状態遷移処理は未実装。

### 実行した検証

1. 初回専用DB検証は34 passed、10 skipped。実HTTPで正常・再送・同時投入・API再起動・commit前後SIGKILL・DB停止/回復を確認。
2. 一意キーを別接続で未commitのまま保持し、pg_stat_activityでAPIのLock待ちを確認してからcommitする試験と、構造不正cursor試験を追加。最終は39 passed、10 skipped、1 warning in 24.92s。
3. 10同時HTTP要求は201が1件・200が9件、同一ID、DB行1件。直列10回再送も同一。異なるcommitは別run。
4. commit直前のAPI SIGKILLはHTTP応答なし・DB行0・再送201。commit直後/応答前はHTTP応答なし・DB行1・再送200で保存済みID。障害factoryはtestsだけに置き、アプリにHTTP/環境変数スイッチを追加していない。
5. 専用DBコンテナの実停止でPOST/GET/readyは503。再開後ready200、未受理要求の行は増えていない。APIプロセス再起動後も保存runと日時・状態を取得した。
6. 同一created_atのUUID順、limit=1、途中の先頭挿入、空/最終ページ、404/422を確認。
7. migration版1/2適用・再適用、DB再起動後の両版保持、pip check、editable install 0.1.0を確認。

P0回帰の初回は9 passed、1 failed、1 error（125.04秒）。ログ上限時の同期docker killが30秒でtimeoutし、rmとimage削除にも影響した。
attachの出力pipeをdrainせず制御コマンドを待つことによるbackpressureが原因と推定し、kill要求を非同期にして読取を継続するよう修正。
finallyでは削除の成否にかかわらずattach/controlプロセスを先に回収し、teardown失敗も証拠に残して失敗として扱うようにした。
初回のコンテナは検証終了後の照会時点で消えていた。既存のDocker/OS設定は変更していない。
修正後の最終P0回帰は10 passed in 32.48s。新しい証拠はdocs/p1-p0-regression.json、過去のisolation-evidence.jsonは履歴として保持。
APIの39件とP0の10件は別実行。TestClient非推奨警告1件は既知で、依存追加・変更はしていない。

### 判定と残り

今回のP1前半は実装・検証済み。P1全体の「固定ジョブ実行から結果保存」は未完了。
次は単一Workerの原子的なqueued取得・attempt/token/DB期限発行を先に実装し、固定fixture実行・上限・回収・条件付き結果確定へ進める。
heartbeat/自動期限回収/複数Worker/古い結果と取消競合はP2。物理電源断やcommit応答中の分断、非信頼コードの受付は検証していない。
状態・契約・実験はdocs/intake-api.md、残りの実装順序はdocs/p1-plan.mdへ記録した。

最終の専用ラベル照会で初回失敗の一時イメージタグが1件残っていたため、sessionラベルの一致を確認してそのタグだけ削除した。
再照会で今回の隔離container/imageとDBのcontainer/volume/networkはすべて0件。ベースイメージ/build cacheは既定どおり残した。
001の内容が開始HEADと完全一致すること、回帰証拠の全ソースSHA-256とcleanup成功、相対リンク、Python構文、git diff --checkを確認した。

## 継続作業：Worker初回取得の1単位

開始: ~/work/Claude_practice-261117、feat/ci-runner、HEAD 43c2035、未コミット変更なし。
ルート/日付AGENTS、261014計画、README、設計・P1計画・既存ソース/テストを確認した。
GitHub CLIでDraft PR #1を確認し、headが開始HEADと一致、P1受付APIが未完了表示のままであることを確認。
通常サンドボックスのghは接続エラーだったが、許可されたネットワーク経路で読み取り成功。

完了済みはP0と受付API。未完了の固定ジョブ実行は、queued取得・attempt/token/DB期限発行が前提になるため、今回はその初回取得だけを選択した。
Worker常駐ループ・コンテナ実行・結果確定・再試行・Webhookには進んでいない。

### 実装

- worker.pyにclaim_nextと--claim-once診断CLIを追加。HTTPアプリからは呼び出さない。
- 許可repository/ref/workflow、queued、DB時刻でavailable_at到達済み、取消要求なし、attempt履歴なしを取得条件とする。
- run行をFOR UPDATE SKIP LOCKEDで取得し、初回attempt番号1・initial理由・worker ID・DB生成UUID token・DB時刻の30秒leaseとrun runningを同時commitする。
- 履歴ありqueuedを初回として扱わず、再試行理由/番号/予算の後続実装まで取得しない。再試行・復旧を実装済みとはしない。
- tokenはCLI/reprに出さず、不正worker IDはDB接続前に拒否。DB異常時は成功応答を出さず、commit結果不明の可能性を残す。
- migration・依存・API応答形式は未変更。古い段階限定のAGENTSを継続開発向けに更新し、既存Draft PR更新の方針を明記。

### 検証

初回verify_db.pyは48 passed、10 skipped、1 warning in 31.78s。
実DB停止時のWorker CLI失敗とtoken非出力の確認を追加した最終実行は48 passed、10 skipped、1 warning in 28.21s。
新規Worker関連9ケースと既存39ケースの合計で、Docker隔離10件は別実行のためskip。TestClient非推奨警告は既知。

- 別Workerプロセスが取得し、API GET/POST再送でrunningを確認。終了後FOR UPDATE NOWAIT成功でロック解放を確認。
- 初回attempt/worker ID/initial理由を保存し、DB上で開始と期限の差が30秒、期限がDB現在時刻より未来であることを確認。
- 対象なし、未来/取消要求/未許可workflow/終端/履歴ありの除外、実DB停止・未設定での失敗を確認。
- ロック中の先頭を飛ばして別プロセスが次行を取得。2接続の取得競合でも発行は1attempt。これは複数Worker実行・復旧全体の完成ではない。
- attempt INSERT直後とcommit直前の例外でqueued/attemptなしへrollbackし、再取得時も番号1になることを確認。
- Worker SIGKILL・通信分断は今回未実験。既存の受付API故障試験と混同しない。

仕様・限界はdocs/worker-claim.md、残りの順序はdocs/p1-plan.mdに記録。
次は固定fixture実行境界の本番側実装とpreflight、続いて実行・停止/回収・条件付き結果保存。
P1全体は未完了。heartbeat/期限回収/再試行/取消競合の故障検証はP2へ残す。

既存P0のverify_isolation.pyは別実行で10 passed in 37.85s。既知のTestClient警告以外の失敗なし。
専用ラベル照会でDBのcontainer/volume/networkと隔離用container/imageはすべて残存0件。
既存migrationの内容が開始HEADと一致、相対文書リンク・Python構文・pip check・差分の空白検査も確認した。
差分/秘密情報/生成物を確認して担当ブランチへcommit・pushした後、Draft PR #1の進捗を受付API・初回取得の実績に合わせて更新する。
今回の単位はここで終了し、コンテナ実行の実装には進まない。

## 継続作業：実行前DB preflightの1単位

開始: ~/work/Claude_practice-261117、feat/ci-runner、HEAD 75daa32、未コミット変更なし。
ルート/日付AGENTS、261014計画、README、work_log、既存コード/テストとDraft PR #1を確認した。
PRはDraft、headは開始HEADと一致、レビュー/コメントはなし。既存の受付API・初回取得の完了表示は確認済み。

未完了の固定fixture実行には、取得した実行権がまだ有効かの確認が必要。
今回はその依存を先に閉じる「実行前DB preflight」を選択し、コンテナ作成/起動や結果保存には進まない。

### 実装と設計境界

- preflight.pyにauthorize_execution(claim)と内部ExecutionPermitを追加。
- run/attempt/Worker/token/番号、両state=running、取消なし、終了/正式結果なし、現在の許可設定、初回attemptを照合。
- leaseはDBのclock_timestamp()とDB上の期限で判定する。手元のClaimの期限を延長しても期限切れを回避できない。
- DBから再取得した固定repository/ref/commit/workflowと実行権snapshotを返す。tokenはreprに出さない。
- DB書込・lease延長はせず、DB異常やcontext終了時の通信失敗でも許可を返さない。
- snapshotなので照合後の取消/失効は起こり得る。Docker起動との原子性・継続監視は保証しない。
- API・claim-once CLI・依存・migrationは未変更。新しいHTTP起動経路やホストでのジョブ実行もない。
- README、AGENTS、設計、取得仕様とP1計画を更新し、詳細はdocs/execution-preflight.mdへ記録した。

### 検証と途中の失敗

初回verify_db.pyは67 passed、10 skipped、1 warning in 29.79s。
専用DB実停止時にも有効な取得済みClaimから許可が返らないことを追加して再実行したところ、認可テストの準備でclaim_nextがNoneとなり、66 passed、1 failed、10 skippedだった。
失敗はWorker ID不一致の認可判定前に発生した。取得不可となった瞬間のDB状態は記録できておらず、原因は確定していない。
投入直後のavailable_at条件と認可試験を分離するため、認可用fixtureのavailable_atをDB時刻より1日前に明示し、取得不能時にはstate/期限/DB現在時刻/attempt数を診断表示するようにした。
取得をリトライして隠す変更や本番SQLの条件緩和はせず、既存Workerの時刻条件/取得試験も維持した。
その最終実行は67 passed、10 skipped、1 warning in 30.59s。新規認可19ケースと既存48ケース。

- 有効な実行情報の返却、DB状態/期限の非変更、戻った後のrun/attempt行のNOWAITロック取得を確認。
- 提示ID/token/Worker/番号不一致、取消、終端、token交代、許可設定変更、結果参照ありを拒否。
- DB期限切れに対する偽の未来時刻、手元だけ過去の期限、新attemptに対する古いClaimの拒否/正本判定を確認。
- DB未設定、connection context終了時の注入通信エラー、専用DB実停止で許可なしを確認。
- 一度成功したsnapshotの後で取消をcommitすると次の照合が拒否されることを確認。取消APIや実行中監視の完成とはしない。
- API/Worker既存回帰、migration再適用、DB再起動後のschema保持も確認した。既知のTestClient非推奨警告1件。

### 残課題と次の単位

P1全体は未完了。次は固定fixtureの制限付きコンテナ作成・設定照合を本番側へ用意し、後続の起動直前にDB preflightとcgroup等の実値確認を接続する。
実行中の時間/ログ/lease監視、停止/回収、条件付き結果確定、再試行と復旧は未実装。
認可用fixtureの単発取得不可の根本原因は未特定。再発時に追加した診断で調査する。
今回の1単位をcommit/push後、Draft PRの最新進捗・テスト結果・残課題を更新し、この実行を終了する。

既存P0隔離回帰は別実行で10 passed in 41.46s。過去の証拠JSONは変更していない。
最終的な専用ラベル照会・差分と秘密情報/生成物の確認後にcommit/pushし、PR #1をDraftのまま更新する。
最終照会では今回のDB container/volume/networkとP0 container/imageの残存はすべて0件。P0実測JSONのfailure/cleanup errorも0件だった。

## 継続作業：固定fixtureの未起動コンテナ準備（2026-11-17）

### 調査・選択した1単位

cwdは指定worktree、feat/ci-runner、開始HEAD c46e035、未コミット変更なし。
ルート/担当AGENTS、261014計画、README、既存コード/テスト/設計/過去記録、gh CLIでDraft PR #1を確認。
受付・初回claim・DB preflightは完了、コンテナ準備/起動/実行/結果保存は未完了だった。
依存順で最優先の「固定fixtureの制限付きcreate・設定照合・回収」だけを選択した。
main/他worktree/他日付ディレクトリには変更しない。

### 実装・判断

- アプリ側container.pyを新設し、P0テストハーネスには依存しない。prepare_fixtureは未起動のcontext manager。
- 固定ローカルDockerfileだけをnetwork noneでbuildし、タグでなく取得したimage IDでcreate（pull never）。公式Pythonの既存P0同一base digestを固定。
- 非root/cap-drop/NNP/read-only/tmpfs/CPU/RAM/swap/PID/network none/private namespace/log none等を明示し、inspect不一致を拒否。
- 追加image/command/mount/envの入力引数を設けず、permitの固定policyと形式を検証。tokenはラベルへ渡さない。
- ランダムsession名と所有ラベルで、自分のcontainer/tagだけを通常終了・例外時に回収。create成功後の応答喪失も生成名から回収。
- API/CLIから自動起動へは接続しない。受理commitの取得は未実装で、ローカルsuccess fixtureの準備を受理commitの検査成功としない。
- 設定照合と実行時の制限強制を区別。SIGKILL/daemon断/timeout後の遅延完了の残存可能性も仕様へ記載。
- AGENTS/README/設計/脅威モデル/fixture仕様/P1計画を現状へ更新。依存追加・migration変更なし。

### 検証

最初の通常新規テスト35件成功、実Dockerと合わせ39 passed in 14.88s。
CLI不在・timeoutの拒否2件と、実際のpermitラベル検証を補強した最終準備試験は41 passed in 13.73s（通常37 + 実Docker4）。
実Dockerの4件は正常、呼出側例外、inspect設定不一致注入、create成功後の応答喪失注入。
正常ケースでcreated/PID0/非runningと設定一式を確認。各ケース終了後、固有container/tagの不存在を確認した。
固定fixtureの実行は今回一切していない。P0プローブの実行は専用Docker内だけ。

専用DB回帰の初回は102 passed、14 skipped、既知のTestClient警告1件 in 38.45s。
追加2件を含む最終DB回帰は104 passed、14 skipped、既知のTestClient警告1件 in 32.70s。
14 skippedは別opt-inのP0隔離10件と準備4件。DB readiness=200、migration二重適用・DB再起動後schema保持も確認。
既存P0隔離回帰は10 passed in 38.99s。過去のコミット済み証拠JSONは変更せず、ignored最新実測JSONに記録。
pip checkはNo broken requirements found（ユーザーcache権限によるcache無効化メッセージあり、OS変更なし）。

### 残課題と次の1単位

P1全体は未完了。次は起動直前のDB再照合・cgroup等の実値確認を伴う上限付きfixture実行。
時間/合計ログ上限、停止/回収を統合し、その次に現在token/DB期限/取消条件での結果確定へ進む。
受理commitの取得/対応付け、残り3fixture、常駐Worker、heartbeat/期限回収/再試行/キャンセル競合は未実装。
以前の認可fixture取得不可の根本原因は未特定。本番SQL緩和やリトライで隠さず既存診断を維持。

最終専用ラベル照会で準備/P0のcontainer・一時image、および専用DBのcontainer/volume/networkは全て0件。P0証拠のfailure/cleanup errorも0件。
ローカル文書リンク、compileall、diff whitespaceを確認済み。対象内の差分・秘密情報・生成物を確認してfeature branchへcommit/pushし、Draft PR #1の進捗を更新する。

## 継続作業：DB認可ゲート付きの上限実行（2026-11-17）

### 調査と今回の1単位

cwdは指定worktree、branch feat/ci-runner、開始HEAD f6189e7、未コミット変更なし。
ルートと担当AGENTS、261014計画、README/work_log、既存実装・試験・P1設計、gh CLIでDraft PR #1を確認した。
受付・初回取得・DB preflight・未起動コンテナ準備が完了済み。起動/上限監視/結果確定が未完了。
依存順で「起動前DB再照合・実値確認を伴う固定fixtureの上限付き実行」だけを実装した。
結果確定、常駐ループ、受理commit取得へは進まない。変更は261117内だけ。

### 実装と境界

- execution.pyのexecute_fixtureは内部ライブラリ。APIとclaim-once CLIから呼ばない。
- DB認可→制限付き準備→DB再認可→固定ガード起動→cgroup/権限確認→DB再認可→定型GOで処理開始。
- ローカルの固定4fixtureだけを実装。任意コード/URL/YAML/image/command/env/mountは受け付けない。
- ガードでCPU 0.5/RAM128MiB/swap0/PID64、uid/gid65534、CapEff/CapBnd0、NNP1/seccomp2を確認する。
- 非root/cap-drop/read-only/tmpfs/network none/log none等の設定は既存準備境界で検証。
- DBが返す残leaseをDB呼出前の単調時計へ加算し、往復待ちで期限を延長しない。lease延長処理は追加しない。
- 10秒/残leaseの早い期限、合計ログ保持1MiBで非同期killしattachを排出。exited/PID0を確認後、所有資源を回収。
- 実行結果はメモリ上だけ。run/attemptはrunningのままで、受理commitの取得・検査完了とは扱わない。
- DB照合とGOは原子的でなく、GO後の取消/DB断の即時検知も未実装。10秒と取得済みleaseのローカル上限までに停止する設計。
- 最終確認でDB応答待ち中のガードも独立して止まる必要を認め、GO待ちにSIGALRM 10秒を追加。GO受信後は解除しホスト監視へ移す。
- README/AGENTS/実行仕様/P1計画/既存設計を更新。依存追加やmigration変更なし。

### 検証と実測

通常の新規実行制御3件と既存準備37件は40 passed in 0.36s。
最初の専用DB＋実Docker統合は116 passed、14 skipped、既知のTestClient警告1件 in 88.61s。
ガード後の実DB停止とソースSHA付き証拠記録を加えた実行は117 passed、14 skipped、同警告1件 in 93.15s。

- success exit0・stdout13 bytes、failure exit1・stderr14 bytes。
- timeoutは10.462秒でexit137。ログfixtureは観測4MiBに対し合計保持1MiB（stdout573440/stderr475136 bytes）、exit137。
- 実コンテナmemoryを64MiBへ変え、テスト内だけinspect memory照合を迂回して、独立cgroupガードのexit78/GOなしを実証。
- ガードack後の取消commit・期限切れcommit・DB通信例外注入・専用DB実停止ではGOなし。異常を成功と返さず、資源を回収。
- DB leaseを3秒に短縮すると3.150秒でlease_expired、exit137。DB往復遅延で残時間を消費する通常テストも確認。
- 実Dockerで固有container/tagの不存在を各ケースで確認。ignored artifacts/execution-latest.jsonにimage ID、ソースSHA256、実値、サイズ、時間、回収件数を記録。
- 追加した独立ガード待機期限の最終試験結果は以下へ追記する。

既存準備回帰は41 passed in 18.04s、既存P0隔離回帰は10 passed in 46.44s。
P0のコミット済み証拠JSONは変更しない。pip checkは依存破損なし（cache権限による無効化通知のみ）。

### 残課題・次の1単位

次は現在token・状態・DB期限・取消条件によるrun/attemptの原子的な結果確定と故障試験。
受理commitの取得/対応付け、常駐Workerへの接続、実行中DB監視、heartbeat/期限回収/再試行等は残件。
P1全体は未完了。以前の認可fixture取得不可の根本原因は未特定のまま、診断を維持。今回の回帰では再発しなかった。

独立GO待ち期限の試験追加後、117 passed・1 failed・14 skipped（116.40s）。失敗はstdinを開いたinteractive Docker CLIのprocess.wait(18)がtimeoutしたもの。
修正試験でも117 passed・1 failed・14 skipped（93.09s）で、起動完了前のdocker waitが初期exit code0を返す競合を確認した。
ガードのackで起動を同期してからdaemonの終了コード/PID0を確認するよう試験を修正。実装の停止期限や期待exit142は緩和していない。

ackで同期した単独試験でもdocker waitが18秒timeoutし、試験手順だけでなくガード実装にも不備があると判明。
LinuxのコンテナPID1ではデフォルト終了シグナルが無視されるため、alarmを予約しただけでは止まらなかった。
Docker公式run/attach資料でもPID1の扱いを確認。SIGALRMにos._exit(124)の明示handlerを設定し、独立停止の期待値をexit124へ変更した。
CLI timeoutをガード停止と誤認しないよう、起動ack→daemon wait→exited/PID0→stdin close/CLI回収の順で実証する。

明示handlerの単独試験はGOなしでexit124/PID0とcontainer/tag回収に成功。
その後の全回帰は117 passed・1 failed・14 skipped（142.09s）。ガード独立停止を含む他ケースは成功したが、success fixtureのガード後authorize_executionがExecutionDeniedとなった。
この実行では並行した準備回帰も40.26sに延びていた。build/起動中の30秒lease消費が考えられるが、拒否時DB状態の記録がなく原因は未確定。
実装の認可条件やleaseを緩めず、正常fixtureの認可拒否時にDB状態・DB時刻/期限・token一致の真偽だけを記録する診断を追加した。token/DSN実値は出さない。
リトライは入れず、Docker buildの競合を避けて統合回帰を単独で再実行する。準備回帰の最終41 passed in 40.26sはこの状態で維持されている。

### 最終結果

診断追加後、Dockerの他回帰と並行せずに実施した全回帰は **118 passed、14 skipped、既知のTestClient警告1件 in 107.47s**。
既存104件＋新規通常3件＋新規DB/Docker11件。14 skippedは別実行の準備4件/P0隔離10件。
DB readiness=200、migration再適用、DB再起動後のschema保持も確認した。
最終imageでの準備回帰は **41 passed in 40.26s**。P0回帰は **10 passed in 46.44s**（P0ソースの変更なし）。

| 最終実測 | 結果 |
|---|---|
| success / failure | exit0・stdout13 bytes / exit1・stderr14 bytes |
| timeout | 10.290秒、exit137 |
| logs | 観測4194304 bytes、保持1048576 bytes（stdout606208 + stderr442368）、exit137 |
| DB lease3秒 | 3.186秒、lease_expired、exit137 |
| 実memory64MiBへの変更 | ガードexit78、GOなし |
| GOなし独立ガード | 明示SIGALRM handlerでexit124、PID0、GOなし |
| ガード後の取消/失効/通信例外/実DB停止 | GOなし、例外、所有資源回収 |

最新実行証拠のソースSHA256が現在コードと一致し、11ケース・11件のcontainer/tag回収確認・failure0を確認。
専用ラベルの最終照会で準備/実行/P0のcontainer・一時image、DB container/volume/networkはすべて残存0件。
base image/build cacheは再利用用に残す。生成された最新証拠JSONはignored artifacts内だけで、過去のコミット済み証拠を上書きしていない。
文書リンク/compileall/diff whitespace・依存整合を確認。差分/秘密情報/生成物とstaged scopeを監査後、feature branchへcommit/pushしDraft PRの進捗を更新する。
一時的な成功fixtureの再認可拒否の原因は未確定のまま残す。診断追加以外の条件緩和や再試行はしていない。

## 継続作業：現在の実行権に基づくfixture結果確定（2026-11-17）

### 調査・選択した1単位

cwdは指定worktree、branch feat/ci-runner、開始HEAD 7ce9cf5、未コミット変更なし。
ルート/担当AGENTS、261014計画、README/work_log、既存コード・schema・試験・設計、gh CLIでDraft PR #1を確認。
受付・初回取得・固定fixtureの上限実行まで完了済み。次の依存タスクとして「条件付きDB結果確定」だけを選んだ。
常駐Worker、受理commit取得、ログ永続化へは進まない。変更は261117内だけ。

### 実装・設計判断

- finalize_fixtureを信頼済み内部ライブラリとして追加。ExecutionResultへrun/attempt IDを追加し、結果の取り違えを接続前に拒否する。
- GO済み・確認済みcgroup値・image ID形式・終了理由とexit codeの組合せを検証。lease_expired/ガード拒否は終端確定しない。
- run→attemptの順に短く行ロック。現在token/Worker/初回番号/policy、running、取消なし、結果未設定を確認する。
- attemptの終端状態・exit/reason・DB終了時刻、runの終端状態・正式attempt参照を同一transactionで更新。
- 両UPDATEでDBのclock_timestampによるlease条件を確認。2更新間の失効も全体rollback。
- 同じ確定済みの状態/exit/reasonはreplayed=true、終了時刻も変えず返す。期限後のreceipt再取得は新規完了受理や権限延長と区別する。
- contextのCOMMIT完了後だけreceiptを返し、COMMIT応答不明なら同じ報告で再確認する。
- 理由はfixture_success/fixture_check_failure/fixture_timeout/fixture_log_limitとし、受理commitの検査完了とは扱わない。
- 生ログ・image ID・実行時間のDB保存は今回含めず、API/claim-onceや常駐処理へ自動接続しない。
- 001を含むmigration・依存は未変更。README/AGENTS/設計/実行仕様とP1計画を更新。

### 検証

入力拒否の通常テスト12件は12 passed in 0.31s。
専用DB・実Dockerを含む全回帰は **156 passed、14 skipped、既知TestClient警告1件 in 119.45s**。
新規38件（入力12・DB26）と既存118件。14 skippedは別実行の準備4件とP0隔離10件。

- 4種類の状態/exit/reason/正式attempt/DB終了時刻、同じ結果の再送、期限後receipt、別結果の上書き拒否、確定後ロック解放を確認。
- 不正ID/token/Worker/番号、取消、失効、終端、policy変更、新attemptに対する古い結果を拒否し、既存状態を保持。
- attemptだけ書いた直後の通信例外と2更新間の失効で部分更新なし。commit前/後の注入通信異常でも再送が整合。
- pg_stat_activityで実接続のLock待機を観測し、取消/期限先行の拒否、確定先行の取消変更0件、並行再送のreplayedを確認。
- 別プロセスをcommit前/後でSIGKILLし、rollback/保持と再送を確認。テスト専用barrierはHTTP等から設定できない。
- APIプロセス再起動と専用DB再起動後、GET一覧/詳細・DBの終了時刻が保持されることを確認。
- 専用DB実停止でreceiptなし。既存API/claim/preflight回帰も確認。
- 実Dockerの成功/失敗/timeout/logs結果を回収後に確定し、同じ結果の再送とrun状態を確認。
- DB readiness200、migration二重適用、検証末尾のDB再起動後schema保持も確認。

既存準備回帰は **41 passed in 12.68s**。P0隔離回帰は **10 passed in 34.49s**。
pip checkは依存破損なし（ユーザーcache権限の無効化通知のみ）。文書リンク・compileall・diff whitespaceも確認済み。
実行証拠JSONのfailure0、11件の回収確認とソースSHA256の一致を確認した。過去のコミット済み証拠は変更しない。

### 残課題・次の単位

次はclaim-onceを維持した別の一件実行入口で、取得→固定fixture実行→条件付き確定を接続する。
image事前準備による取得後遅延の削減、容量上限付きログ永続化、受理commit取得/対応付け、常駐処理、heartbeat/期限回収/再試行等も残件。
P1全体は未完了。過去の取得/実行認可の一時拒否の原因未確定事項は既存記録と診断を維持。今回の156件の実行では失敗なし。

最終ラベル照会で準備/実行/P0 container・一時image、専用DB container/volume/networkはすべて0件。P0証拠もfailure/cleanup error0件。
base image/build cacheは再利用用に残し、ignored最新証拠以外の生成物を追加しない。対象差分・秘密情報・staged scope確認後、担当branchへcommit/pushしDraft PR #1を更新する。

差分レビューで、確定先行の競合試験を補強した。取消接続に加え同時再送接続もpg_stat_activityでLock待機を確認してからcommitを解放する。
この最終試験状態で全回帰を再実行し、結果を以下へ追記する。実装の認可条件・lease値・期待結果は変更していない。

最終の待機観測補強後は **156 passed、14 skipped、既知警告1件 in 124.01s**。同時再送の実ロック待機も確認した。
DB readiness200・再起動後schema保持と、最終の専用ラベル全7照会で残存0件を再確認。今回の両回帰とも失敗なし。

## 継続作業：担当専用GitHub Actionsの最小導入

### 調査と選択

指定cwd・feat/ci-runner・開始HEAD 9f980e8、未コミット変更なしを確認。
ルート/担当AGENTS、261014計画、README・既存設計/記録・実装/テスト、gh CLIでDraft PR #1を確認した。
既存workflowは別プロジェクト専用のみ。過去の一時的な取得/実行認可拒否は原因未確定だが、
認可条件を緩めず診断と安全側停止を維持し、直近の統合回帰は156件成功している。
今回の新しい優先順位に従い、通常テスト自動化を1単位として選択した。P1機能追加は行わない。

### 変更と境界

- ユーザーが明示した例外範囲で.github/workflows/ci-runner-tests.ymlを新規追加。既存workflowは未変更。
- 担当path filter、push担当branch/mainとpull_request（Draftを含む）、Ubuntu 24.04・Python 3.12系、10分上限の1ジョブ。
- contents: read、checkoutのpersist-credentials: false、公式checkout v7.0.1/setup-python v7.1.0の40桁SHA固定。
- gh APIでタグが指すcommit SHAとリポジトリのActions有効を確認。キャッシュ・秘密注入・特権・外部サービスは追加しない。
- lock導入→追加依存なしeditable導入→pip check→strict-markersでDB/Docker除外pytest→compileall。
- README・P1計画・自動テスト仕様を更新。恒久ルールは変更せず、AGENTS・アプリ・migration・依存定義は未変更。

### 検証と未検証

既存venv・許可された実行経路で **82 passed、88 deselected、既知TestClient警告1件 in 1.14s**。
入力正常/異常・body/識別子境界・制限設定拒否・不正結果拒否等の既存検証を使用し、件数目的のテストは追加しない。
pip checkは破損なし、compileall成功。最初の制限sandbox内実行は52件相当の出力後に停止し、
自発的に終了しなかったため自分のpytestだけをTERMで終了した。停止原因は未確定で、この実行を成功に数えない。
実行経路変更後の同じテストは上記の通り成功した。テストや期待結果を緩めていない。

公式actionlint配布物のchecksum確認・ローカル実行を試みるツール呼出しは、自動承認審査が
第三者実行ファイルのローカル実行リスクを理由に拒否した。ツール呼出し自体が拒否され、取得/実行とも未実施。
迂回せずこの検査は省略し、GitHub側でのworkflow受付と結果をpush後に確認する。
GitHub-hosted runner結果はcommit SHAを対応させてPRに記録する。ローカル結果から推定しない。
今回はDB/Docker統合88件を再実行しない。アプリ変更がなく、過去の実測は過去の結果として保持する。

### 残課題と次

P0は既存の固定fixture検証範囲で完了、P1全体は未完了。
次はclaim-onceを維持する別の一件実行入口で、取得→固定fixture実行→条件付き確定を接続する。
image準備のlease予算、受理commitの取得/対応付け、容量上限付きログ永続化、P2のheartbeat/復旧は残件。
DB/Dockerの自動化はこの最小CIの受入条件へ混ぜず、別途使い捨て資源・失敗時回収・runner差異を検討する。

使い捨て/tmp venvでlock導入からの再現も試みたが、低速ダウンロード中に設定した180秒の上限で中断。
依存解決エラーは観測されなかったが導入完了しておらず、fresh installの成功とは扱わない。
TemporaryDirectoryの終了処理でvenvを削除済み。OSへパッケージは導入していない。
変更文書の相対リンク、credential pattern、git diff --checkを確認。対象5ファイルだけをstageし、
staged差分・秘密情報・生成物を確認後feat/ci-runnerへcommit/pushする。

## 継続作業：別プロセスで一件だけ実行する固定fixture Worker

### 調査・今回の1単位

指定cwd・feat/ci-runner・開始HEAD acf3081・未コミット変更なしを確認。
ルート/担当AGENTS、261014のP1計画、README/work_log、取得/実行/確定コードと試験・設計を確認。
既存Draft PR #1はopen/Draft、前回CIのpush/PR両チェック成功。前回導入時のGitHubログでの82件成功と
新規依存導入成功はPR記録を確認した。今回理由なく再導入せず既存venvを使用する。
原因未確定の過去の認可拒否は診断・fail closedを維持。現在の不具合/CI失敗は確認されないため、
P1の次の依存タスク「取得→固定fixture実行/回収→条件付き確定を一件Worker入口で接続」を選択した。

### 実装と判断

- fixture_worker.run_onceとpython -m ci_runner.fixture_workerを追加。--worker-id/--run-onceを必須とし、運用者の固定4modeだけを受け付ける。
- 取得は1回、空ならDockerなしで終了。取得COMMIT後に同じClaimで実行し、回収後に同じ結果を条件付き確定する。
- 既存worker --claim-onceは取得だけのまま。API・入力許可・Docker設定・migration001/002・依存・workflowは未変更。
- 状態/exit_code/fixture_理由/DB終了時刻をstdout JSONへ返す。チェック失敗・時間切れの保存成功もWorker操作の終了0とし、ジョブ状態で判定する。
- 認可拒否・基盤障害・回収失敗・確定不明はstderrの段階/既知IDだけ、終了1。生例外・token・ジョブログ・DSNは出さない。
- どの段階も自動再試行せず、別runを続けて取得しない。取得/確定COMMITの応答喪失を未保存と断定せず、DB照合が必要とする。
- claim後のbuildも30秒leaseを消費する契約は維持。cold buildで失効したら停止/確定拒否し、leaseを緩めない。
- 常駐ループ・受理commit取得・ログ永続化・heartbeat/期限回収/再試行へは進まない。
- README、P1計画、取得/実行/確定/共通設計と新規一件Worker仕様を更新。恒久ルールの変更はなくAGENTSは未変更。

### 検証結果

最初の通常回帰は追加途中の10件を含み92 passed、97 deselected、既知警告1件（1.45秒）。
その後、明示フラグ/モード拒否2件と取得応答不明/取消先行2件を追加し、以下の全回帰で検証した。
**scripts/verify_execution.py：179 passed、14 skipped、既知TestClient警告1件、176.97秒**。
今回追加は23件（通常12・専用DB7・実Docker4）。14 skippedは未変更の準備4件・P0隔離10件で、今回は別実行もしない。

- 通常：空キュー、入力拒否、明示--run-once不足、各段階停止と再試行なし、接続factoryの引継ぎ、回収失敗後の確定拒否、確定不能のガード/lease結果、token/生診断非出力。
- 専用DB：DockerのないPATHの空キュー、DB未設定、実行失敗時のrunning残存/次run queued、取得COMMIT応答喪失後に実行しないこと、取消先行の起動拒否。
- 専用DB：確定COMMIT前/後の通信例外注入でrunning/保存済みを照会し、次runを取得しない。合成実行結果を使い、隔離実行の実証とは分ける。
- 実Docker：HTTP投入→別Workerプロセス→success/exit1/timeout/log_limit確定→別API再起動後の参照・同じ投入の再送を確認。
- 実Docker：4ケースとも2件目queued・attempt総数1件、DBの理由/終了時刻とreceipt一致、token非出力、session container/image集合が実行前後で不変。
- 既存の受付/claim/preflight/実行/確定/競合/故障試験も同じ回帰で成功。DB readiness200、migration二重適用、DB再起動後schema1/2保持を確認。
- pip checkは破損なし（ユーザーcache権限通知のみ）、compileall成功。今回の試験失敗なし。
- ignoredのfixture-worker-latest.jsonは4ケース・failure0、execution-latest.jsonは11ケース・failure0。両証拠のsource SHA-256が現在ソースと一致。
- 専用ラベルのcontainer/image、専用DB container/volume/networkの5照会で残存0。global pruneや他資源の変更は行わない。

### 未完了・次の最優先単位

P0は既存の管理下fixture検証範囲で完了、P1全体は未完了。
次はcold buildが取得後のleaseを消費しないよう、取得前image準備と同一imageの安全な受渡しを設計・検証する。
その後に容量上限付きログ永続化/表示時エスケープ、受理commitの取得/対応付けを進める。
実行中の取消/DB断即時検知・プロセス停止からの自動回復・heartbeat等は今回未実装/未検証。
CLI再起動は同じ結果報告の再送ではなく、メモリ結果の復元機能もない。running残存時はDBを照合し、無条件に再キュー/成功へ変更しない。

## 継続作業：取得前の固定image準備・ID固定の受渡し

### 調査と選択した1単位

指定cwd・feat/ci-runner・開始HEAD 124247c・未コミット変更なし。ルート/担当AGENTS、261014計画、
README/work_log・設計・取得/実行/確定コードとテスト、Draft PR #1と前回push/PR CI成功を確認。
既知の残件である取得後buildのlease消費を対象に、取得前準備と安全な内部受渡しだけを選択した。
既存CIを作り直したり、ログ保存/受理commit取得/常駐Worker/P2復旧へ範囲を広げたりしない。

### 実装・安全境界

- has_queued_candidateは読取専用のhint。claim_nextと同じSQL対象条件を共有し、transactionを終了してからbuildする。予約/認可/lease発行はしない。
- 空候補ならDockerなしで終了。候補があればprepare_imageで固定sourceをbuildし、unique tag/session/IDを検証してからclaimする。準備失敗ならattemptなし。
- build中の競合/取消で候補は変わり得るため、取得時のSKIP LOCKED/許可条件を維持。取得できなければ自分のタグを回収して終了する。
- PreparedImageは内部contextのhandle。prepare_fixtureでタグID・所有sessionを再照合し、検証済みID/--pull=neverでcreate。再buildしない。
- 借用側はcontainerを、外側のimage contextはunique tagを回収。imageのID/所有者が変わったタグは削除を拒否。両回収後にだけ条件付き確定へ進む。
- queue/image/image_cleanupの失敗段階を追加。token・生例外・ログ非出力、自動再試行なし、確定不明のDB照合契約を維持する。
- execute_fixtureの準備済みimageなしの内部互換経路はbuild/create/回収を維持。API/claim-onceは実行へ接続しない。
- lease30秒、起動前/GO前DB再照合、実値ガード・制限、結果の条件付き更新は緩めない。migration/依存/workflowは未変更。
- README・関係設計文書を更新。恒久ルール変更なしでAGENTSは未変更。

### 検証

最初の通常回帰は98 passed、99 deselected、既知警告1件（2.48秒）。その後image handle拒否等を補強した。
専用DB＋実Docker＋既存回帰のverify_execution.pyは **190 passed、15 skipped、既知TestClient警告1件、213.41秒**。
15 skippedは準備5件と未変更のP0隔離10件。準備は別実行し、P0隔離10件は今回は再実行しない。

- 実build後に31秒遅延を注入。その間attempt0・queued、別接続のFOR UPDATE NOWAIT成功を確認。build後のstarted_at、新規lease30秒をDBで確認。
- 同じ試験でbuild1回、createで指定したIDとinspect IDの一致、正常確定、2件目queued、自分のcontainer/image集合が前後で不変を確認。
- これは遅延注入による順序/予算の検証。既存cacheを破壊したcold build性能計測とは主張しない。
- 実Dockerの固定4modeは別プロセスWorkerで継続成功。従来のガード拒否・DB停止・失効・結果確定/競合/故障試験も成功。
- 空キューのDockerなし、image準備失敗時の全run queued/attempt0、競合で対象なしの回収、回収失敗後に確定しないことを確認。
- 不正handle/タグ、別image ID、所有者変更を通常テストで拒否。image回収時のタグ差替え拒否を全回帰開始後に1件補強し、変更したtest_container.py全43件を別途実行して成功（0.37秒）。本番ソースは全回帰後に変更していない。
- 最終のverify_preparation.pyは **48 passed、19.44秒**。上記43通常テストと5実Docker（既存4＋借用image寿命/消滅handle拒否）を含む。
- DB readiness200、migration二重適用、DB再起動後schema1/2保持を確認。pip checkは破損なし（cache権限通知のみ）、compileall成功。今回の試験失敗なし。
- ignored fixture-worker-latest.jsonは5ケース、execution-latest.jsonは11ケースでfailure0。現在の本番/対象試験ソースSHA256との一致を確認。
- 最終の専用session container/image・専用DB container/volume/networkの5照会はすべて0。base image/build cacheを残し、global prune・OS変更・他資源の変更はしない。

### 残件と次の1単位

P0は既存の管理下fixture検証範囲で完了、P1全体は未完了。
次は容量上限付きログ永続化と結果確定との対応（attempt別保存・正式参照・失敗時の未確定扱い）を設計・検証する。
受理commit取得/対応付け、表示時エスケープ、常駐/heartbeat/期限回収/再試行は残件。
create・実行・回収は引き続き取得後leaseを消費し、失効なら実行/確定を拒否する。
SIGTERM/SIGKILL・daemon断・操作timeout後の遅延完了ではラベル付き残存があり得る。自動回復や悪意あるdaemon操作者からの保護は未実装/未検証。

## 継続作業：上限付きログDB永続化と結果確定の原子性

### 調査・選択した1単位

指定cwd・feat/ci-runner・開始HEAD a51ed1d、未コミット変更なし。ルート/担当AGENTS、261014計画、
README/work_log・設計・実装/テスト、open/Draft PR #1と直近push/PR CI成功を確認した。
重大な現在障害/CI失敗は確認されないため、次の依存タスク「容量上限付きログ永続化と結果確定」を選択。
HTTPログ取得/表示、受理commit取得、常駐Worker/P2復旧へは進まない。

### 実装と設計判断

- migration 003でattempt_logsとlog_storage_budget、容量予約/不変性/専用DB TRUNCATE会計リセットのtriggerを追加。001/002は未変更。
- 小容量のrawログをbyteaに保存し、ファイルとDBの二重更新やホストpathの問題を今回増やさない。一般のファイル成果物は別設計。
- stdout/stderr合計1MiB/attempt、DB全体の既定/上限64MiBのraw保持payload。capacityはDB管理者の設定で、HTTP/ジョブ入力からは受け付けない。
- Pythonでbytes型・長さ・観測量を接続前に検証。DB CHECKとsingleton台帳の条件付きUPDATEで、直接INSERTの上限と並行時の総量を強制する。
- 現在token/DB期限/取消を確認し、attempt更新→ログ/台帳→DB期限を再確認するrun更新を同じtransactionでcommit。capacity不足・失効・中断は全体rollback。
- 正式ログはcompleted_attempt_idから参照する。再送は状態/exit/reasonに加えbytes/観測量も完全一致を要求し、行/終了時刻/台帳を変更しない。
- 通常UPDATE/DELETEを拒否。容量不足はWorker終了1・成功receiptなし、run/attemptは通常runningに残る。自動再試行/削除/復旧はしない。
- schema版を3へ更新し、ready・migration適用/二重適用/再起動確認も版3へ更新。過去のrun/attempt/正式参照を保持し、過去ログを捏造しない。
- 過去のログなし終端結果への新finalizer再送は拒否し、状態を変えない。履歴GETは維持する。
- README・設計・受付仕様/脅威モデル/fixture仕様を更新。恒久ルール・依存・Docker制限・workflowは未変更、AGENTSも未変更。

### 検証結果

通常回帰：111 passed、112 deselected、既知TestClient警告1件（1.71秒）。
専用DB＋実Docker＋既存全回帰：**209 passed、15 skipped、既知警告1件、223.55秒**。
15 skippedは未変更の準備5/P0隔離10で、今回は別実行もしない。

- 0/1MiB境界、SQL直接INSERTの超過/観測量不正、NUL/不正UTF-8/HTML風文字列のバイナリ保持、再送時不変性、別ログ/観測量の拒否。
- 小容量4バイトの台帳に2つの4バイト結果を同時確定し、1件だけ成功・used4・他方running/ログなしを確認。full時の同じ報告再送は二重予約しない。64MiBを実データで埋める試験ではない。
- ログ書込直後・commit前/後の通信異常、commit前/後の別プロセスSIGKILLでログ/台帳/結果が同じ確定結果に従うことを確認。
- API/DB再起動後も非空ログ・観測量・容量台帳を保持。schema1/2/3保持、migration二重適用、readiness200を確認。
- 実Dockerの4fixtureで結果と実stdout/stderr/観測量の一致を確認。大量出力は保持1MiB/観測量超過として保存。
- 実Dockerでquota0時はcontainer/image回収後にWorker終了1、成功receiptなし、次run queued、ログ/台帳予約なしを確認。

全回帰開始後、schema2の終端attempt/正式参照を含む移行保持、過去のログ欠落再送拒否1件、
既存の通信/2更新間失効試験に非空ログのrollback確認を補強した。本番ソースとDDLは全回帰後に変更していない。
その最終試験状態を対象に絞り、PYTEST_ADDOPTSの-kでverify_db.pyを別実行：
**37 passed、188 deselected、既知警告1件、17.76秒**。単一transactionで移行前履歴を作る専用schemaはrollbackで除去した。
今回の新規19件は仕様境界/故障を対象とし、ログ全体表示やテスト件数増加のための模倣テストは追加しない。

pip checkは破損なし（ユーザーcache権限通知のみ）、compileall成功。今回の試験失敗なし。
fixture-worker最新証拠は6ケース、実行最新証拠は11ケースでfailure0。記録対象ソースSHA256と現在ソースの一致を確認。
生ログを証拠JSONへ追加せず、保存量/観測量と状態だけを記録する。
最終の専用session container/image・専用DB container/volume/networkの5照会は残存0。
OS/既存データ/他worktreeを変更せず、base image/build cache以外の一時資源は回収した。

### 限界・次の1単位

P1全体は未完了。次は正式attemptのログ取得APIと、安全なテキスト応答/表示・バイナリ変換方針を設計・検証する。
保持期限/削除/容量運用、受理commit対応、常駐/heartbeat/期限回収/再試行も残件。
64MiBはraw保持payloadだけで、DB行/索引/TOAST/WAL/backup/履歴/他資源を含む物理ディスクの上限保証ではない。
64MiB実データ充填・DBディスク枯渇・長期保持/性能は未測定。容量不足時の自動復旧やCLIの結果復元も未実装。

## 継続作業：正式ログ取得APIと安全な最小表示

### 調査・作業単位の選択

指定cwd・feat/ci-runner・開始HEAD 842c249、未コミット変更なし。
ルート/担当AGENTS、261014計画、README/work_log、保存/確定/APIコード・設計・テストを確認。
Draft PR #1はopen/Draft、開始HEADに対応するpush/PR両CIは成功（通常111件、DB/Docker対象外）。
現在の重大障害/CI失敗は確認されないため、P1の未達必須条件「正式ログの参照とHTMLエスケープ」を1単位として選んだ。
受理commit取得/常駐Worker/P2/本格UIへ範囲を広げない。

### 実装と契約

- GET /runs/{run_id}/logsを追加。UUID、stdout/stderr、byte offset 0〜1MiB、limit 1〜16KiB（既定4096）、format text/htmlを検証する。
- 1回のパラメータ化SELECTでrun.completed_attempt_id→同じrunの終端attempt→保存ログを参照。DB substringでページだけを取得し、更新/予約/行ロックをしない。
- 正式参照なし/非終端/状態不一致409、run/ログなし404、対象stream末尾超過416、入力422、DB停止503を区別。末尾ぴったり/空streamは200。
- X-Log-*で正式attempt・元byte数/次位置・両streamの保持/観測量・保存時の切詰めを返す。ページ間でclientがattempt IDを照合できる。
- UTF-8 errors=replace、LF/TAB以外のCc/Cf制御文字を可視化する。DB元bytesは変更しない。byte境界で分断した文字の完全復元は保証しない。
- text/plainが既定、htmlはhtml.escape(quote=True)した固定preだけの最小表示。ログ由来の属性/script/style等を生成しない。
- 成功/取得判定4xxはno-store/nosniff/制限CSP。422/503は共通JSONエラー。元ページ最大16KiB、変換後body最大98316 bytes。
- migration/依存/実行制限/実行権/保存処理/workflowを変更しない。恒久ルール変更もなくAGENTS未変更。
- README、参照/保存/受付/脅威モデル・各設計を更新。進捗推定の根拠をdocs/progress.mdへ記録。

### 検証・対象外の区別

実装初期の通常回帰：124 passed、114 deselected、既知TestClient警告1件（3.00秒）。
新規は仕様を対象とした19件（通常13・実DB6）。数量を目的に追加しない。

専用DBと実HTTPを含むverify_db.py：**212 passed、32 skipped、既知警告1件、67.13秒**。
通常124件と実DB88件を含む。32 skipは準備5/P0隔離10/実行11/一件Worker6で、Dockerジョブ実行成功とは扱わない。

- stream選択・ページ続き・0/末尾/1MiB・空stream・範囲超過、UTF-8 byte分断と不正文字変換を確認。
- 取得前後のログ/台帳不変、未確定/別attemptログの非公開、旧ログ欠落、正式attempt切替と状態不一致を確認。
- 終端結果をcommit直前で止め、実HTTP GETが待たずに409を返し、commit後に200となることを確認。
- 既存のDB実停止/回復・API/DB再起動試験へ新routeを接続して確認。migration二重適用、readiness200、schema1/2/3保持も成功。
- 人工的な過去/未確定attemptは専用DBの制御面fixtureであり、Worker retryの実装・実証ではない。

変更したAPI参照経路だけを選び、PYTEST_ADDOPTSの-kでverify_execution.pyを別実行：
**4 passed、240 deselected、既知警告1件、42.91秒**。
固定4fixtureのHTTP投入→別プロセスWorker→保存→API再起動→両streamと最小HTML表示を確認。
他のDocker故障/準備/P0隔離・cold build遅延試験は理由なく再実行せず、今回の成功件数へ混ぜない。
最新fixture-worker証拠は選択した4ケース・failure0、app/logsを含む記録対象ソースSHA256と一致。生ログは証拠へ追加しない。

最終レビューでHTML quoteの6倍展開がbody最大98316 bytesへ達する境界を補強し、対象通常試験のみ再実行：
**13 passed、既知警告1件、0.99秒**。本番ソースはDB/Docker試験後に変更していない。
HTMLParserで固定pre以外の要素なし、markup/quote escape・NUL/ESC/CR/双方向制御可視化と不正UTF-8を確認。
Chromium/Chrome/Playwright/browser toolが利用できず、実ブラウザ上のCSP/スクリプト実行試験は未実施。
OS/新規依存の導入は行わず、HTTPヘッダ/HTML構造の検証を実ブラウザ実証と混同しない。

pip checkは破損なし、compileall成功。今回の試験失敗なし。
専用session container/image・専用DB container/volume/networkの5照会は残存0。base image/build cacheは維持。

### 完了判定・全体推定・次

今回のログ参照/最小表示は完了。P0は既存の管理下fixture検証範囲で完了、P1全体とMVPは未完了。
次の最優先単位は管理下の受理commitの固定取得と実行対象への対応付け。任意URL/コマンド/YAMLへ拡張しない。
常駐/heartbeat/期限回収/再試行、Webhook、履歴/操作UI、保持期限/容量運用、比較計測・demo・第三者再現も残る。
進捗はP0〜P5の計画工数中央値13/23/30/16/20/21hを重みとし、達成度100/80/15/0/0/10%と主観推定。
38/123相当で約31%、報告は概算約30%・残約70%。実測工数・test件数・commit数による値ではなく、重複する基盤をP4へ再加算しない。
MVPの全受入条件が揃ったら独立レビューと完成判定を提案し、発展課題へ自動的に進まない。

## 継続作業：受理commit sourceの前提確認・安全契約案・外部依存で停止

### 現状と選択

指定cwd・feat/ci-runner・開始HEAD aaadaa77c703925e1ec7ebcac5153a1d7ae1fdc5、未コミット変更なし。
ルート/担当AGENTS、261014計画、README/work_log・設計・固定fixture/Worker/テスト構成を確認。
既存Draft PR #1はopen/Draft、開始HEADのpush/PR CIはともに成功（通常124件、DB/Dockerは対象外）。
次のP1必須条件である受理commitの固定取得/実行対象への対応付けを調査対象に選んだ。

### 確認証拠と停止理由

- POLICYはkenobix/Claude_practice・refs/heads/main・fixture-v1固定。GitHub APIでrepositoryはpublic/default_branch mainを確認。
- 2026-11-17T14:16:07.832500+00:00の確認でmain HEADはb4c460a52d5afc4d3623a35cc99655095d3a8e1b（committer date 2026-11-16T15:22:39Z）。local origin/mainも同じ。
- 固定したmain SHAでcontents/261117/ci_runner/fixtures/job/fixture.pyを照会するとHTTP404。local ls-treeでも対象なし。
- 作業HEADには同pathが追跡済み。2151 bytes、SHA256 9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff。
- merge-base --is-ancestor HEAD origin/mainは終了1で、担当HEADは許可mainのancestorではない。
- 現在のローカルfixture結果を受理commit実行成功と扱わない。mainへ反映するか許可先を変える判断が必要だが、今回main push/mergeと無断の仕様緩和は禁止されている。
- ユーザーの「重大な問題や外部依存で進められない場合は、理由を記録して停止する」に従い、この接続実装は停止。別branch・他日付のコードへ読み替えたり、追加機能で解消済みに見せたりしない。

### 作成したレビュー可能な成果と未実装

commit-source-readiness.mdにimmutableな照会対象/結果と、未実装の最小取得契約案を記録した。
既存の固定path/digestを候補に、ref所属/commit/tree/blobの対応、通常ファイル/size/digest検証、
取得先/timeout/応答量制限・失敗時回収・候補runとSHAの再照合・検証bytesからの固定buildを定義してから実装する案。
現行sourceはguardを含むため、digest承認を省いて任意のmainコードへ置換しない。可変workload対応はguard分離の別設計が必要。
README・P1計画・進捗文書に停止状態と再開条件を反映。恒久ルールの変更はなくAGENTSは未変更。

アプリ/テスト/migration/依存/workflow/許可設定/実行経路は未変更。取得実装・ネットワーク制限の実効性・受理SHA実行の成功は未実装/未検証。
GitHub/ローカルのmetadataとbytes hashを確認しただけで、リモートsourceのcheckout/import/実行はしていない。
404とancestor終了1はreadinessの否定結果として記録し、実行成功やアプリのテスト失敗とは扱わない。
コード変更がないため、完了済みのlocal pytest/DB/Docker検証は繰り返さない。今回の文書commitに対応する自動CIはpush後に照合する。
他worktree/既存データ/OS設定を変更せず、一時container/volumeを作らない。新規依存も導入しない。

### 完了判定と再開条件

調査・契約案・停止理由の記録という1単位で終了し、P1/MVPの機能完了は宣言しない。
推奨対応は許可repo/mainへ管理下の固定fixtureを通常レビュー経由で公開すること（agentはmain反映を行わない）。
別の管理下repository/ref/path/workflowを使う場合は明示的な許可設定変更の判断が必要。
次回はmain HEADとsourceの存在/ref所属/digestを再確認し、前提が揃ってから取得/誤対応拒否/通信異常の実装単位を進める。
全体概算は約30%・残約70%を据置き。文書追加で実装済み機能の割合を増やさない。
MVPの全受入条件を満たした後に独立レビューと完成判定を提案し、発展課題へ自動移行しない。

## 2026-11-18 受理commitの固定source取得とrun照合

- `git fetch origin` 後、PR #4のmainへのマージを確認。origin/mainは
  `3eb342a89fc0eb990c0a48a79dbea42e8be44283`。固定pathは通常ファイル100644、2151 bytes、
  blob `a2352427ae73ed9337e933d1dcefd49968b3fe8b`。SHA-256は承認値
  `9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff` と一致した。
- 読み取り専用の `ci_runner.commit_source` を追加。許可mainのsnapshot、受理SHAの所属、固定commit/tree/blob、
  通常ファイルの種別・サイズ・承認済みSHA-256・Git blob IDを照合。curl 8.4以上で固定HTTPS先、
  200のみ、各応答1 MiB、各5秒、取得全体30秒。リダイレクト追従と圧縮展開なし。
- キュー先頭のrun IDとSHAを取得前後に別transactionで確認し、違うrunやSHAには検証結果を渡さない。
  claimやlease発行はせず、Docker隔離・DB実行権・ログ保持上限の実装は維持した。
- 実GitHub HTTPSでmainマージcommitの取得と、許可main外のPR #4の元commit（97e8381）の拒否を確認。
  sourceをホストで実行・import・展開していない。
- 専用unit 25件成功。固定ref/commit、ancestor、許可外commit、symlink、submodule、byte改ざん、
  Git blob ID不一致、サイズ、リダイレクト、通信異常、deadline、run ID/SHA不一致を確認。
  最終コードの通常回帰は149 passed、122 deselected、既知警告1件。pip checkも成功。
- P1は未完了。次は検証済みrun/SHAの条件付きclaim、取得したbytesを使う固定build、
  結果への由来記録と一連のP1受入試験。README・固定取得仕様・P1計画・進捗を更新。
- 初回の実DB/Docker全回帰は252 passed、15 skipped、4 failed。失敗4件は新規CLIテストが
  pytestのargvを読み込んだため。引数を明示できるように修正し通常回帰で解消した。
  修正後の `verify_execution.py` は取得25件・実DB照合2件・既存Dockerの4モードを選択し、
  31 passed、240 deselected、既知警告1件。専用DB readiness 200、再起動後のmigration保持と
  専用コンテナ・network・volumeの回収を確認。取得済みsourceを使ったDocker実行は未検証。

## 受理commitを固定fixtureで実行・確定する一件Worker

- 開始時のfeat/ci-runnerは `8791396c2da9d530dd2ef031ccc7360d303020d3`。Draft PR #1のpush/PR CIは成功。
- `--verified-commit` は既存のGitHub取得検証を使用。検証済みsourceとdigest固定のローカルDockerfileだけを
  一時build contextに配置し、network noneでbuildする。context削除後、image ID・所有session・run/SHA/blob/digest
  のラベルを再照合する。コンテナcreateは検証済みimage IDを使用する。
- 取得とbuildを30秒lease発行前に終える。migration 004の適用と同じrun ID/SHAを条件にDBで原子的にclaimし、
  競合・SHA変更時は別runに切り替えない。起動前とGO前に現在のDB実行権とsourceの対応を再確認する。
- 既存のCPU/RAM/PID、時間、ログ保持、token/lease、コンテナ・専用タグ回収を維持。
  migration 004の `attempt_sources` へrun、受理SHA、main snapshot、blob SHA、source digest、image IDを
  ログ・正式結果と一緒に保存する。再送時は由来も一致させる。既存migrationは未変更。
- P1は約95%の見積り。残件は単一Workerの常駐と停止方針、P1全体の最終受入。
  heartbeat・失効回収・再試行はP2。取得したfixtureをホスト上で実行していない。
- 初回の統合回帰は268 passed、15 skipped、2 failed。実GitHubから取得した受理SHAについて成功・失敗・
  timeoutを確認。既存の取消試験が渡す模擬imageをPreparedImage型へ合わせ、取消前にDockerが呼ばれないことを再検証した。
  大量ログケースと後の一部ケースは匿名取得で拒否。GitHubのref/blob照会はHTTP 403かつ枠の残り0だった。
  取得拒否は固定の理由をCLIへ返し、別runやローカルfixtureへフォールバックしない。
- 最終の通常回帰は154 passed、134 deselected、既知警告1件。pip check、compileallも成功。
- 最終統合回帰は `CI_RUN_LIVE_COMMIT_SOURCE=gh .venv/bin/python scripts/verify_execution.py`。
  認証済みghで実GitHubのcommit/tree/blob応答を取得し、通常の検証・CLI入口・実Docker・DB確定を通した。
  許可mainの公開commit `3eb342a89fc0eb990c0a48a79dbea42e8be44283` の4モードで
  `succeeded/0`、`failed/1`、`timed_out/137`、`failed/137 (commit_log_limit)` を確認。
  由来の保存、APIプロセス再起動後の結果/ログ参照、各ケースの専用image・container回収も確認。
  競合claim、SHA不一致、migration未適用、実DB停止時の開始拒否と回収、由来INSERT後の障害による
  全rollback、由来が異なる再送の拒否を検証した。
- 最終統合回帰は273 passed、15 skipped、既知警告1件（311.53秒）。skipは別実行の準備5件・P0隔離10件。
  専用DB readiness 200、PostgreSQL 17.12、再起動後のmigration 1〜4保持、専用DB資源の回収も確認。
  匿名HTTPS経由の大量ログモードは枯渇したAPI枠のため完了できなかった。実GitHub・実Dockerによる4モード完了は
  認証済みghを通信に使う試験からの結果であり、通常のWorker取得設定を変更していない。

## 単一Workerの常駐・停止とP1受入

開始HEAD `cbaebed9ee8935a4ab53f95d934d5595292c63c3` のpush/PR CIは成功。
Draft PR #1に未解決のレビュー指摘なし。P1の残件だった常駐と停止を今回の一単位に選んだ。

- `fixture_worker --serve` は検証済みcommitの一件実行を繰り返す。
  空キューは1秒間隔、保存済みの結果は逐次出力。障害・取得拒否・確定不明は終了1とし自動再試行しない。
- SIGTERM/SIGINTで停止を要求する。取得後・build後の確認で新規claimを避け、claim中/取得済みの一件は
  既存の制限内で確定・回収まで進める。実行権の延長・期限回収・自動再試行はP2。
- 通常回帰は163 passed、135 deselected、既知警告1件。pip check、compileall成功。
- 専用PostgreSQL・実Docker・通常の匿名GitHub HTTPSによる選択回帰は53 passed、245 deselected、
  既知警告1件（147.41秒）。今回は認証済みghの試験経路へ切り替えず、4モードすべてを確認した。
  実稼働中の常駐WorkerにSIGTERMを送り、timeoutの結果・由来を確定し、コンテナと専用image tagを回収。
  次のrunがqueued・attemptなしのまま、API再起動後に結果とHTMLログを参照できることを確認。
- 同一runのclaim競合、SHA不一致、DB実停止、容量不足・競合、確定rollbackと再送、入力/HTTP冪等性を選択回帰。
  P0隔離・Docker準備を含む全件統合は今回再実行していない。基準commitの全件統合と今回の範囲を区別する。
- P1は管理下fixtureの受入を完了。rawログの1MiB/attemptと台帳上限・Dockerログ無効を確認した範囲。
  ホストディスク総量や64MiB実データの満杯試験は未実測。MVP全体は計画工数を重みとする概算35%。
  次の作業はP2のDB時刻に基づくlease更新と、失効/古いtoken/DB断での拒否を検証する。
専用DBのreadiness 200、PostgreSQL 17.12、再起動後のmigration 1〜4保持、専用DBコンテナ・network・volumeの回収を確認。
