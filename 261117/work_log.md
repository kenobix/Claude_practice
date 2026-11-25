# 作業記録：CI runner P0準備

## 2026-11-17：依頼と環境

- `261014/plan_ci_runner.md` に沿って、独立した開発環境とP0の設計を準備する。全機能の実装は対象外。
- 初期pwdは `/home/kenshin/work/Claude_practice-261117`。ユーザーが当初のprojectsをworkに訂正したため、このパスで作業を続けた。
- ブランチは `feat/ci-runner`、開始時のHEADは `b4c460a`。未コミットの変更はなく、261117は未作成だった。
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

直接の依存関係と推移的な依存関係は、すべてrequirements.lockに固定した。PythonのバージョンとDBイメージのdigestも記録した。
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

`verify_db.py` は一時資格情報をメモリ内で生成し、対象のComposeプロジェクトに既存の資源があれば実行を拒否する。
DB統合の検証にSQLiteは使わなかった。`.env` や実際の認証情報を含むファイルは作成せず、一時DBの資格情報は検証の終了時に破棄した。

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

## P2：DB時刻によるlease更新

- 開始HEAD `9b329f3826d58fa0e20c69c43fc3b9c6b101156d` のpush/PR CIは両方成功。Draft PR #1に未解決のレビュー指摘なし。
  P2の5秒heartbeatで必要になる、現在のattemptに限定したDB更新を今回の一単位に選んだ。
- `renew_lease(claim)` はrun→attemptの行ロック後にPostgreSQL時刻を取得し、期限内の初回attemptをDB時刻+30秒へ更新する。
  現在token、run/attempt ID、attempt番号、Worker、許可設定、running状態、取消なし、正式結果なしを条件とする。
  識別子・token・source/imageの由来・結果・ログは保持し、commit成功後に期限を返す。
- 新規専用テスト12件で、DB時刻と連続更新、別のID/番号/Worker/token、古いattempt、許可外run、確定後の拒否を検証した。
  `pg_stat_activity` で実際のロック待ちを確認し、先に確定した取消と待機中の自然失効のどちらも更新を拒否した。
  commit直前と直後に通信異常を注入し、直前はrollback、直後はDBに更新が残っても呼び出し元へ期限を返さないことを確認した。
- 既存の専用DB実停止試験にrenewalを追加し、例外を返し、DB再起動後も以前の期限が保持されることを確認した。
  通常回帰は163 passed、147 deselected、既知警告1件。pip check・compileall成功。
  `scripts/verify_db.py` は270 passed、40 skipped、既知警告1件（82.29秒）。固定ジョブの実Docker・実GitHub取得は今回未実施。
  PostgreSQL 17.12のreadiness 200、再起動後のmigration 1〜4保持、専用コンテナ・network・volumeの回収を確認した。
- P2は概算25%、MVPは計画工数を重みとする概算37%（残63%）。次は常駐Workerの5秒heartbeatとローカルwatchdogへの接続。
  期限監視、失効後の再試行と残存コンテナ回収、2Workerによる一連の故障復旧は未実装。

## P2：実行中heartbeatとローカルwatchdog

- 開始HEAD `5b8d4a458ac45e414b922ce7b81f3507b2f92bd7` のpush/PR CIは両方成功。Draft PR #1に未解決のレビュー指摘なし。
  DB更新の実装に続く一単位として、常駐Workerと一件Workerが共用する実行ループへheartbeatを接続した。
- 実値ガードと起動前/GO前の再照合を通った後、5秒間隔で同じClaimを使い、DB更新を別スレッドで最大1件ずつ行う。
  確認済みの更新は要求開始時の単調時計+30秒へ反映する。従来の期限または要求から5秒の早い方を応答締切とし、時刻・ログ上限の監視は続ける。
  拒否・通信エラー・応答不明時は所有資源を回収し、`stage: heartbeat` で終了1。正式結果を保存せず次のrunへ進まない。
- 通常回帰は166 passed、151 deselected、既知警告1件。pip check・compileall成功。
  更新の二重発行防止、dispatch起点の残時間、従来の期限や応答締切以降に届いた応答の拒否を確認した。
- 最初の実DB・実Docker選択回帰は51 passed、1 failed、265 deselected。匿名GitHub APIは開始時58回残り、通常の匿名HTTPSで
  受理commitの4モードと常駐WorkerのSIGTERM drain、実行中のDB期限延長を確認した。認証済みghの試験経路は使用していない。
  実停止ケース1件でheartbeat呼び出しまでの到達を確認できず、初回の原因は特定できていない。
  専用DBの停止を短い猶予で行い、停止成否とWorker終了段階を確認するよう試験を修正した。
- 修正後の選択再検証は7 passed、310 deselected、既知警告1件（50.88秒）。固定GitHub応答を使って受理commitを実Docker実行し、
  実行中の取消、専用PostgreSQL実停止、commit後の応答喪失、無応答を検証した。確定なし、次のrunはqueued、所有コンテナとimageタグの回収を確認した。
  commit応答喪失ではDB側の期限延長だけが残った。無応答注入ではDB処理が止まったままでもWorkerは終了した。
- 一件Workerの4モード、短縮lease、結果確定、claim競合と実行中障害を含む追加選択回帰は44 passed、273 deselected、既知警告1件（119.26秒）。
  PostgreSQL 17.12のreadiness 200、再起動後のmigration 1〜4保持、専用コンテナ・network・volumeの回収を確認した。P0隔離を含む全件回帰は今回未実施。
- P2は概算40%、MVPは計画工数を重みとする概算41%（残59%）。次はDB時刻による期限監視と失効attempt処理の競合検証。
  失効後の再試行・残存コンテナ回収、Worker強制終了やDocker daemon障害を含む2Workerの一連の復旧試験は未実装。

## P2：DB時刻による期限監視と失効記録

- 開始HEAD `3767018310a67154f3989216c5ac0e0c59cd51a7` のpush/PR CIは両方成功。Draft PR #1に未解決のレビュー指摘なし。
  heartbeatに続く一単位として独立プロセス `ci_runner.expiry_monitor` を追加。1回最大16件、`--watch` は走査後に5秒待つ。
- DB時刻で候補を選び、run→attemptの順にロックし、更新時のDB時刻で失効を再確認する。
  期限切れのrunとattemptを同一transactionで閉じ、取消要求が確定済みなら `canceled / canceled_lease_expired`、
  それ以外は `failed / infra_lease_expired` を記録。token・期限・run/attempt IDを維持し、正式結果やログ・由来の昇格は行わない。
  commit確認済みの行だけを出力する。DBエラーや確定不明は終了1、再走査しても確定済み行を二重処理しない。
- 専用PostgreSQLで、期限到達・未到達、取消要求、監視同士の競合、先行するheartbeat/結果確定のスキップ、
  失効確定後の遅延報告拒否、commit前rollbackとcommit後の応答喪失を確認した。既存の実DB停止試験にも監視を追加。
- 通常回帰は168 passed、160 deselected、既知警告1件。pip checkとcompileall成功。
  初回の専用DB全回帰は283 passed、1 failed、44 skipped。監視試験は全件成功し、既存の初回claim試験で1件取得できなかった。
  失敗時のDB時刻は記録されておらず、原因は未特定。取得試験の開始可能時刻をDB時刻より前に明示し、診断を追加。
  未来に設定したrunの除外とキュー先頭のロックスキップは従来どおり個別に検証する。
- 修正後の `scripts/verify_db.py` は284 passed、44 skipped、既知警告1件（81.94秒）。
  PostgreSQL 17.12でreadiness 200、DB再起動後のmigration 1〜4保持、専用コンテナ・network・volumeの回収を確認。
  今回の監視はDBだけを操作する。固定ジョブの実Docker実行・GitHubからのsource取得は今回未実施。
- 失効時のDB終端処理までを実装。再試行を接続するまで、監視で閉じたrunは自動再開しない。
  P2は概算55%、MVP全体は概算44%（計画工数で重み付け）。次はattemptラベルとDB状態で対象を特定し、残存コンテナの回収を検証する。

## P2の失効attemptの残存コンテナ回収

- 開始HEAD `afb946bf77e88ed4f1a883f3f5be8da67db57194` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  期限監視がDBで失効を確定した後も残るコンテナの回収を、再試行に先行する作業単位として選んだ。
- `orphan_cleanup --once` は、同じDocker daemonの準備sessionラベルで候補を選び、完全ID、定型名、run/attemptの
  UUIDを照合する。同じ組に許可repo/ref/workflowと監視の確定済み理由がある場合だけ、完全IDでforce rmする。
  DBで未確定・組が違う場合は保留。Docker失敗やDB断は終了1で通知し、復旧後の再走査を可能にした。
  receiptは完全IDをDockerが返した削除だけに出す。DB更新は行わない。
- 専用DBと実Dockerで、固定fixtureのコンテナ作成直後と実行開始後にWorkerをSIGKILLした。
  固定fixtureのSHA-256は `9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff`。
  初回の選択回帰は108 passed、1 failed、10 skipped。競合試験で、Docker 29.9.2は同じIDの削除後に
  `rm --force` を再度呼ぶと終了0・stdout空を返した。完全IDの一致をreceiptの条件とし、
  応答が空の場合はinspectで同じIDの不在を確認するよう修正した。
- 最初の通常回帰は制限環境で既存APIテストの進行が止まったため終了し、再実行した。停止原因は未特定。
- 最終の通常回帰は187 passed、162 deselected、既知のTestClient警告1件。pip checkとcompileallも成功。
- `verify_execution.py` で `orphan_cleanup or expiry_monitor or execution_docker or lease_renewal or runs_postgres or container`
  を選択した最終回帰は111 passed、10 skipped、228 deselected、既知警告1件（194.53秒）。専用PostgreSQL 17.12と実Docker
  29.9.2で、GO後の自然失効、取消要求ありの未起動コンテナ、DB停止中の回収保留、DB復旧後の並行回収を実測した。
  期限監視前の削除抑止、別attemptのコンテナ保持、失効後の結果確定拒否、コンテナ本体の不在と試験用タグの回収も確認。
  専用DBのreadiness 200、再起動後のschema 1〜4保持、専用DB資源の回収を確認した。
  ライブGitHub取得、Docker daemonの実停止、遅延create完了後の再走査は今回未実測。
- P2は概算70%、計画工数で重み付けしたMVP全体は概算48%（残52%）。次は遅延createを考慮した回収確認を
  再キューの前提にし、基盤障害に限定した最大3attemptとバックオフを実装する。その後に2 Workerで障害復旧を通し検証する。

## P2のDocker作成予約と完全ID記録

- 開始HEAD `93ab9e63e524d9d211ab36af082dca5697260b9c` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  旧attemptの回収を確定する前提として、Docker createの未送信と応答不明をDBで区別する単位を選んだ。
- migration 005で作成台帳を追加。claimと同時に新attemptを記録対象とし、既存のattemptは作成履歴不明とする。
  run→attemptロック後、現在のrun/attempt・受理SHA・token・取消・DB時刻を照合し、daemon IDとsessionを一度だけ予約する。
  予約commitの応答後だけcreateを呼ぶ。Dockerの完全IDとinspectが一致したら同じ台帳に保存する。
- 予約あり・完全IDなしは作成結果不明。Docker一覧が空でもこの状態は変えない。未確定のまま手動の回収コマンドで
  削除した場合も作成結果不明として残る。回収確認の永続化と再試行は後続。作成予約で使うdaemon IDはUUID形式に限定し、
  実行中は同じ信頼済みDocker contextを使う。
- 初回の専用DB・実Docker選択回帰は153 passed、1 failed、6 skipped、200 deselected。遅延createの回収自体は
  成功したが、Workerの後処理で汎用 `docker inspect` の小文字の不在応答をエラーと判定した。コンテナ専用inspectで
  対象名の不在を厳密に照合し、並行回収後に元のcreate例外を維持するよう修正した。
- 修正後の通常回帰は189 passed、173 deselected、既知のTestClient警告1件。pip checkとcompileallも成功。
- `CI_RUN_PREPARATION_TESTS=1` で `verify_execution.py` から作成台帳・残存回収・Docker準備/実行・検証付きclaim・
  migrationを選択した修正後の再検証は55 passed、4 skipped、303 deselected、既知警告1件（204.42秒）。
  PostgreSQL 17.12とDocker 29.9.2で、予約競合、ロック待ち中の失効、旧token・取消・SHA不一致の拒否、予約commitの
  前後の障害を確認した。Docker呼び出しの遅延を注入し、初回走査ではコンテナが見えず、失効後に実Dockerで作成された
  同じsessionのコンテナを未起動のまま回収できた。予約commit応答喪失時はcreateを呼ばなかった。
  専用DBのreadinessは200、再起動後にmigration 1〜5を保持し、試験資源を回収した。
  ライブGitHub取得（4件skip）とDocker daemonの実停止は今回未実施。
- P2は概算75%、計画工数で重み付けしたMVP全体は概算49%（残51%）。次は回収側で作成済みの完全IDを削除前に
  永続化し、同じdaemonでの不在確認を保存する。その確認を基盤障害に限定した再キューへ接続する。

## P2の失効コンテナ回収確認の永続化

- 開始HEAD `45c5e135b0c9ad8deb6aaef2df9deff536e7032a` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  基盤障害の再試行に先行し、回収プロセスによるDocker完全IDの照合と不在確認の保存を実装した。
- `orphan_cleanup --once` は、確定済みの失効・取消、所有ラベル、daemonとsessionを予約と照合する。
  遅延createで初めて観測した完全IDをDBへ保存し、そのcommit応答を得てから完全IDで削除する。
  同じdaemonのコンテナ専用inspectで不在を確認したDB時刻を、migration 006の `container_recovered_at` に保存する。
  削除直後に中断した場合は、Docker一覧から消えたIDも台帳から再確認できる。並行走査で確認時刻は更新し直さない。
- 予約ありで完全IDが未記録のまま一覧が空の場合は作成結果不明を維持する。旧attemptの従来回収には確認時刻を保存しない。
  run/attemptの状態、受理SHA、token、lease、正式結果・ログ・由来への遷移はこの作業単位の対象外。
- 初回の専用DB・実Docker選択回帰は66 passed、1 failed、4 skipped、295 deselected。新しい回収試験は成功し、
  既存の期限監視試験でcommit応答喪失後の状態が `running` のままだった。初回の原因は特定できていない。
  同試験では期限をDB時刻の1秒前に設定し、失効更新をtransaction内で確認してから注入した障害を照合するよう修正した。
- 2回目の選択回帰は67 passed、1 failed、4 skipped、295 deselected。期限監視と実Docker回収は成功した。
  新しいmigration制約テストの入力に既存の必須列 `retry_kind` が欠けていたため、試験データに初回種別を指定した。
- 通常回帰は192 passed、175 deselected、既知のTestClient警告1件。pip checkとcompileallも成功。
- 最終の対象再検証は8 passed、359 deselected、既知警告1件（49.63秒）。専用PostgreSQL 17.12とDocker 29.9.2で、
  DB停止と並行回収、遅延create、完全ID保存commitの応答喪失による削除保留、削除後のDB rollback・応答喪失からの再走査、
  予約だけでは回収済みとしないことを確認した。DB readinessは200、再起動後にmigration 1〜6を保持した。
  専用DBのcontainer・network・volumeとfixtureのコンテナ・imageは、試験終了後に残存0件を確認。
  ライブGitHub取得は選択回帰で4件skip。Docker daemonの実停止は今回未実施。
- P2は概算80%、計画工数で重み付けしたMVP全体は概算50%（残約50%）。次は作成要求が未送信、または回収確認済みの
  基盤障害だけを最大3attempt・上限付きバックオフで再試行する。その後に2 Workerで故障復旧を通し検証する。

## P2の基盤障害に限定した自動再試行

- 開始HEAD `20780b26436e773648fbc547b9be7c61af71f4d0` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  期限監視へ再キューを接続し、既存schemaの `available_at` と `retry_kind`、Docker作成台帳を使用した。
- 直近attemptが `infra_lease_expired`、取消・正式結果なし、create未送信または完全IDの不在確認済みの場合を対象とする。
  runをロックして直近attemptを読み直し、同じrun ID・受理SHAのままDB時刻から5秒または10秒後に再キューする。
  監視は1走査で失効と再キューを各最大16件処理する。3回目が失効した場合は `infra_exhausted` を記録する。
- 検証付きWorkerは再取得時に固定sourceを再検証し、同じrun ID/SHAに新しいattempt ID・番号・token・30秒leaseを発行する。
  2/3回目のpreflight・更新・回収・結果確定にも番号と種別の照合を適用。ローカルimageとsourceのない再試行結果を拒否する。
  チェック失敗、時間切れ、ログ超過、取消、作成の有無が不明なattemptは自動再試行から除外する。
- 初回の対象検証は43 passed、336 deselected、既知のTestClient警告1件（29.63秒）。固定のGitHub試験応答と実Dockerで、
  2回目のWorkerを作成直後にSIGKILLし、不在確認前の再キュー拒否と回収後の3回目の確定を確認した。
- 最終の通常回帰は192 passed、187 deselected、既知警告1件。pip checkとcompileallも成功。
- 専用PostgreSQL 17.12とDocker 29.9.2の選択回帰は182 passed、5 skipped、192 deselected、既知警告1件（257.97秒）。
  DB時刻の5秒・10秒の設定と待機中の取得拒否、再キュー・再取得の競合、3回目の上限、旧token/許可外SHAの拒否、
  rollbackとcommit応答喪失を確認した。Dockerの通し試験ではDBの待機時刻を過去に更新してから次のattemptを取得した。
  正式attemptの受理SHA・source digest・image ID・ログの記録と、旧報告の拒否を確認した。
  DB readinessは200、再起動後にmigration 1〜6を保持。試験用DBのcontainer/volume/networkとfixtureのコンテナ・imageは残存0件。
  skip内訳はライブGitHub取得4件とP0専用Dockerプローブ1件。Docker daemon自体の停止と2 Worker常駐の故障復旧は未実施。
- P2は概算90%、MVP全体は計画工数で重み付けした概算53%（残約47%）。次は常駐Workerが正常な取得競合から待機に戻れるようにし、
  同一ホストの2 Workerで停止位置別の復旧と古い結果の拒否を通し検証する。復旧45秒は仮目標のまま。

## P2の常駐Workerの取得競合時の継続

- 開始HEAD `ba5f4e74063516241163a599c67067ef384a681a` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  同じrunを先に取得された常駐Workerが終了する問題を修正した。
- 取得後の先頭変更と、image準備後に同一run/SHAをclaimできなかった場合に、元のrun ID・受理SHA・許可repo/ref/workflow・
  schema 004/006を再照合する。一致時だけ1秒待機に戻る。準備済みimageタグは再照合前に回収する。
  照合不一致、DBとcommit応答の異常、取得拒否、回収失敗では終了1。一件実行は競合時に終了1を維持した。
- 通常回帰は195 passed、192 deselected、既知のTestClient警告1件。pip check、compileall、差分検査も成功。
- 専用PostgreSQL 17.12・Docker 29.9.2で選択回帰は85 passed、5 skipped、297 deselected、既知警告1件（130.51秒）。
  固定GitHub試験応答を使い、取得後とclaim直前の2地点で常駐Workerを待機させ、別の常駐Workerに先に同じrunを処理させた。
  負けたWorkerは終了せずに次のrunを検証・処理した。各runのattemptは1件で、正式結果・ログ・受理SHA・source digest・
  image IDの対応を確かめた。受理SHA変更、許可ref変更、必要schemaの欠落、DB停止、heartbeat異常の回帰を実行した。
  DB readinessは200、再起動後にmigration 1〜6を保持した。試験用DBのcontainer/volume/networkとfixtureのコンテナ・imageは残存0件。
  skipはライブGitHub 5件。今回はGitHubライブ取得とDocker daemon自体の停止を実施していない。
- P2は概算95%、MVP全体は計画工数で重み付けした概算54%（残約46%）。次は同一専用DB・Docker上の2 Workerで
  停止位置を変え、時刻の書き換えを使わずに失効・回収・バックオフ・再取得を観測し、古いattemptの完了報告を拒否することを確認する。

## P2の停止位置別復旧と受入判定

- 開始HEAD `d8adf6026e111c30f606aae1c83fe82536363b4a` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  既存の常駐Worker用ハーネスを再利用し、使い捨てPostgreSQL 17.12・Docker 29.9.2で2つのWorkerと期限監視を起動した。
  復旧試験の取得は固定GitHub応答を使い、固定fixtureはDocker内で実行した。
- 3ケースともDB時刻は書き換えなかった。30秒のleaseが切れ、再試行まで5秒待った後、別のWorkerが同じrunと受理SHAを引き継いだ。
  実行中に停止したケースでは、失効前は回収が拒否され、コンテナが存在しないことをDBに保存してからrunがキューに戻った。
  結果確定の直前に停止したケースでは、回収済みコンテナの完全IDを記録と照合し直した。
  再取得中と正式結果の確定後に旧tokenの報告が拒否されることと、旧attempt、新しい正式結果、ログ、source digest、image IDの整合性を確認した。

| SIGKILLの位置 | 停止直前から正式結果までのDB時刻差 |
|---|---:|
| claimのcommit直後 | 43.41秒 |
| 固定コンテナにGOを送った直後 | 47.07秒 |
| 正常実行と回収の後、結果確定直前 | 45.74秒 |

- 45秒は仮目標。3回中2回で超過した。Docker daemon自体の停止は今回未実施。SIGKILLで残った専用imageタグは試験側が所有権を照合して削除した。
- 通常回帰は195 passed、195 deselected、既知のTestClient警告1件。pip checkとcompileallも成功。
- P2の全体回帰は378 passed、1 failed、1 skipped、10 deselected（738.31秒）。P0専用隔離の試験ファイルは既存の実測を再利用した。
  匿名GitHubでの常駐停止試験1件はskip。認証済み `gh` のライブ試験4件のうち、時間切れfixtureのケースがsource取得段階で失敗した。
  初回の診断から真因を特定できなかったため、試験用 `gh` のタイムアウトと異常終了を識別する診断を追加した。
  同じ1ケースの再検証は1 passed（31.65秒）。取得上限・本番の通信条件は維持した。
  専用DBのreadinessは200、再起動後にmigration 1〜6を保持。専用DBのcontainer/volume/networkとfixtureのコンテナ・imageは残存0件。
- 管理下の固定fixtureでP2の[全受入条件](ci_runner/docs/design.md#2-workerの故障復旧試験)を満たした。
  MVP全体は計画工数で重み付けした概算55%（残約45%）。次はP3のWebhook署名確認と配送の永続化から着手する。

## P3のWebhook受付とローカル受入

- 開始HEAD `b1920b4f4201166a544c7d5e5fcc3d3421edb95c` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
- 管理APIから分離したWebhook入口で、256 KiB・2秒のbody受信制限、元bytesのHMAC-SHA256照合、設定済みhookと署名済みrepository/ref/削除情報の検証を追加した。
  hook・配送ID、body hash、判定とrunを同じPostgreSQL transactionで保存し、commit後に応答する。既存のrun業務キーとmigration 001〜006を再利用した。
- 通常回帰は221 passed、199 deselected、既知のTestClient警告1件。pip check、compileallも成功。
- 使い捨てPostgreSQL 17.12で選択回帰28件成功（62.00秒）。初回配送が競合するケースを追加後、Webhook対象4件を再検証して成功（24.79秒）。
  証明書を検証するローカルHTTPSでは、同じ配送IDの10回同時再送と別配送IDの同一コミットを受理してrunは1件。最大応答0.206秒。
  不正署名では配送・runとも0件。同じIDでbodyを変えると409。commit前/後の受信プロセスSIGKILLとDB停止の後、同じ配送を再送してrunは1件。
  DB readinessは200、再起動後もmigration 1〜6を保持。試験用DBのcontainer・volume・networkは残存0件。
- 公開HTTPS受信先と既存のGitHub hookは未設定。GitHubからの実push配送、応答時間、受付停止後のGitHub画面からの手動再送は未検証。
  P3は概算75%、MVP全体は概算65%（残35%）。公開先の準備後に[実配送と復旧の受入](ci_runner/docs/webhook.md#起動と障害復旧)を行う。

## P4のrun取消とattempt履歴

- 開始HEAD `3b22b918ec4340664537536805125c16b8c626f0` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  P3に必要な公開HTTPS受信先が未確保のため、Webhookに依存しない管理APIの2操作を先行した。
- `PUT /runs/{id}/cancel` を追加。待機中は即時取消、実行中はDB時刻で要求を保存して202、他の終端状態は409。再送では元の要求時刻を保持する。
  取消と完了の順序は既存のrun行ロックで決め、heartbeat・期限監視・リソース回収を再利用した。
- `GET /runs/{id}/attempts` を追加。試行番号の降順ページングと、状態・終了コード・理由・正式参照を提供する。実行権tokenとログ本体は含めない。
- 通常回帰は222 passed、201 deselected、既知のTestClient警告1件。pip checkとcompileallも成功。
- 使い捨てPostgreSQL 17.12・Docker 29.9.2の選択回帰は47 passed、376 deselected、既知警告1件（158.49秒）。
  待機取消の並行再送、取消と完了それぞれが先行する行ロック競合、DB停止時の503、旧attemptと正式なチェック失敗の履歴表示を確認した。
  実Dockerでは固定試験応答で取得したfixtureを実行中にHTTPで取り消し、heartbeat拒否とコンテナ・image回収、旧報告拒否、
  DB時刻を書き換えずに期限監視で取消済みとなることを確認した。専用DBのreadinessは200、再起動後にmigration 1〜6を保持。
  専用DB・fixtureの試験資源は残存0件。
- P3は概算75%で実配送待ち。P4は概算25%、MVP全体は概算69%（残31%）。
  次は失敗・時間切れの手動再試行と履歴UI。P3では公開HTTPS確保後に実pushとGitHubの手動再送を検証する。

## P4の手動再試行と実行権の引き継ぎ

- 開始HEAD `81d8f4b18ecea576db99fbf663afbb16af93d7c7` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
  公開HTTPS受信先がないため、P3の実配送試験に依存しない手動再試行を先行した。
- `PUT /runs/{run_id}/retry/{attempt_id}` を追加。最新の確定済み失敗・時間切れ、または作成未送信・回収確認済みの基盤障害だけを受付。
  migration 007で受付記録と区間の開始番号を追加した。runの再キューと正式結果の参照解除を同じcommitにまとめ、同じ要求には元の受付を返す。
- 検証付きWorkerが同じrun IDと受理SHAを再確認し、次の通算番号と新しいtokenを取得する。初回投入と各手動再試行を起点に最大3attempt。
  5秒・10秒の自動再試行間隔、固定sourceの検証、旧実行権の拒否、過去のログ・由来・試行履歴を維持した。
- 通常回帰は222 passed、207 deselected、既知のTestClient警告1件。pip check、compileallも成功。
- 使い捨てPostgreSQL 17.12・Docker 29.9.2の選択回帰は151 passed、1 skipped、276 deselected、既知警告1件（315.17秒）。
  skipはP0専用の隔離プローブ。並行PUT・並行claim、DB停止時の503、commit前後のAPIプロセスSIGKILL、期限切れ・回収前の拒否、旧報告拒否を確認した。
  GitHubは固定試験応答を使用。実Dockerでチェック失敗を記録し、手動再試行でコンテナ作成直後にWorkerを強制停止した。
  不在確認を保存してから、別Workerが同じSHAを取得して成功し、最新attemptが正式結果・ログの参照先になった。
- 最終差分の選択回帰は36 passed、393 deselected、既知警告1件（80.29秒）。旧スキーマのattempt番号・token・状態の保持、
  手動再試行のDocker通し試験、完了処理、ローカルHTTPSのWebhook回帰を確認した。DB readinessは200、再起動後にmigration 1〜7を保持。
  専用DBのcontainer・volume・network、fixtureのcontainer・imageの残存は0件。
- P4は概算40%、MVP全体は計画工数で重み付けして概算72%（残28%）。次は管理UIからコミット・試行・理由・正式結果を追い、
  ライブと過去デモを識別して取消・再試行できるようにする。容量制限付き成果物と運用メトリクス、P5の比較計測と最終受入、P3の実配送が残る。

## P4の管理画面と正式ログ

- 開始HEAD `f2bd3affc1ac6cd47d991d6292e2ebece3197ab4` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
- loopback管理APIの `/ui` に実行一覧、実行ID検索、詳細、試行の履歴、正式ログを追加した。詳細と再試行の可否は同じ読み取り専用DB snapshotを参照する。
  チェック失敗と基盤障害を理由で区別し、取消・手動再試行は既存PUT APIを呼ぶ。固定の過去試験デモは表示専用で、DBが停止していても開ける。
- 正式ログは表示対象の試行IDとバイト位置をリンクに含める。再試行で正式参照が変わると古いリンクは409。ログ中のタグ・制御文字は表示用に変換する。
  管理画面とassetsはloopback Hostを要求する。外部リソース、インラインスクリプト、フレーム埋め込みをCSPで制限した。
- 通常回帰は224 passed、209 deselected、既知のTestClient警告1件。pip check、compileall、JavaScript構文検査は成功。
  オフラインでwheelを作成し、CSSと操作スクリプトを含むことを確認した。
- 使い捨てPostgreSQL 17.12の選択回帰は25 passed、409 deselected、同警告1件（87.62秒）。ページ送り、正式ログの試行固定、回収未確認時の操作非表示、DB停止の503とデモ閲覧を検証した。
  Chromiumで実行ID検索からコミット・試行・失敗理由・正式結果をたどり、ログ切替、再試行と取消の確認操作を実行した。1440pxと390pxの画面を確認し、キーボードフォーカスの表示も検証した。
  DB readinessは200、再起動後にmigration 1〜7を保持。専用DB資源は回収済み。今回はジョブの実Docker試験を再実施していない。
- P4は概算70%、MVP全体は計画工数で重み付けして概算77%（残23%）。P4の残りは容量制限付き成果物、運用メトリクス、全体受入。
  P3は公開HTTPS受信先の確保後に実配送と再送を確認する。今回のブラウザ検証は管理APIのloopbackで実施した。

## P4の成果物と運用指標の受入

- 開始HEAD `73bc75447716e05c284f4cb74cb965eb930f68bc` のpush/PR CIは成功。Draft PR #1に未解決レビューなし。
- migration 008で試行ごとの結果JSON、SHA-256と容量台帳、取得可能時刻・Worker実行時間を追加した。固定名のJSONを1試行1件・最大4KiB、合計16MiBの範囲で結果・ログと同時に確定する。
  再試行で正式参照が変わったURLは409。旧版の試行・成果物メタデータを保持する。並行確定の容量超過、commit前後の強制停止、DB/API再起動後の保持を確認した。
- ライブ管理画面に結果JSONのダウンロードと運用指標を追加した。許可workflowの直近24時間、完了した最新1,000試行までで、待ち時間・実行時間の平均とp95、基盤障害率、期限切れ件数を集計する。
  Chromiumで狭幅表示と正式な試行のJSON添付を確認した。[操作と集計定義](ci_runner/docs/run-operations.md)
- 通常回帰は227 passed、215 deselected、既知のTestClient警告1件。pip check、compileallも成功。
  最初の選択回帰で、migration 006の記録がなくても取得できる問題を検出した。readinessと取得で1〜8の全必須版を確認するよう修正した。
- 使い捨てPostgreSQL 17.12とDocker 29.9.2の全体回帰は427 passed、1 failed、15 skipped（1,026.09秒）。P0専用隔離プローブ10件とライブGitHub取得5件は今回未実施。
  claim直後にWorkerを停止するケースで試験側の25秒通知待ちが切れた。代替Workerが正式結果を保存したことは記録で確認した。
  待機条件と実行権・タイムアウトを維持して、3地点の停止、migrationの欠落、成果物と指標を再検証し、11 passed（179.25秒）。
  SIGKILLから正式結果まではclaim直後49.27秒、実行中43.78秒、確定直前44.56秒。45秒の仮目標は1件で超過した。
  DB readinessは200、再起動後にmigration 1〜8を保持した。試験用DBのcontainer・volume・networkとfixtureのcontainer・imageは残存0件。
- コミット・試行・失敗理由・正式結果の画面での追跡、取消・手動再試行、ログ、成果物、運用指標を確認し、P4を完了と判定した。
  MVPは計画工数で重み付けして概算81%（残19%）。次はP5の100ジョブ・1/2 Worker各3回の比較実験。
  P3のGitHub実配送と手動再送は承認済みの公開HTTPS受信先の確保待ち。

## P5の1/2 Worker比較と再現検証

- 開始HEAD `649dbd4f01abc6a98de071244879841633d4e8e7` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
- 実PostgreSQL 17.12・Docker 29.9.2で、同じ合成SHA 100個に同じ管理下fixtureを対応させ、1/2 Workerを各3回比較した。GitHubのHTTP応答だけを固定し、常駐Workerのソース検証、claim、Docker隔離、ログ・成果物の確定を通した。
  予備試験の計測2件、本計測600件と各回のウォームアップ12件で正式成功。各回の成功数・試行数、キュー待ちと実行のp95、CPU負荷とDB待ちを確認した。既存の計測ファイルを拒否して上書きしないことも確認した。
- 各3回の中央値で、2 Workerのキュー待ちp95は766.448秒から641.698秒、実行p95は2.744秒から3.222秒。バッチ所要は813.574秒から686.851秒。最後のペアは2 Workerが約3秒遅かった。
  2 WorkerでCPU競合の兆候が増え、0.5秒間隔のDB観測ではロック待ちを捕捉せず、WAL同期待ちを15サンプル捕捉した。ホスト全体の統計から遅延の原因までは特定できない。[6回分の実測と再現方法](ci_runner/docs/p5-validation.md#2026-11-19の実測)
- 一時アーカイブから新しいPython 3.12環境を作り、固定依存をPyPIから導入した。pip checkとcompileallは成功。通常回帰は227 passed、215 deselected、既知のTestClient警告1件。
  固定fixtureの初回実行、実行中WorkerのSIGKILL復旧、管理画面の過去デモ表示の選択回帰は3 passed、439 deselected、同警告1件（86.90秒）。DB readinessは200、再起動後にmigration 001〜008を保持した。
  復旧は45.91秒で仮目標の45秒を超過。旧報告を代替試行中と正式確定後に拒否した。選択回帰とDBの起動から終了・回収まで113.51秒。試験のコンテナ・imageおよび専用DBのコンテナ・volume・networkは回収済み。
- 比較実験、初回のローカル検証手順、3分の障害デモ、運用上の限界を記録した。P5は概算85%、MVP全体は計画工数で重み付けして概算94%（残6%）。
  公開HTTPS確保後のP3実配送・手動再送、第三者の環境での再現、P0〜P5の最終受入と独立レビューが残る。今回の回帰は対象を絞り、ライブGitHubと全体の重い統合回帰は実行していない。

## 管理APIのHost検証とP5比較グラフ

- 開始HEAD `09da8ee00d6528a750fccdcd86bdbc02e83911f6` のpush/PR CIは成功。Draft PR #1に未解決のレビュー指摘なし。
- 修正前の管理アプリではloopback Hostの検証が一部の経路に限られ、想定外のHostでlivenessとOpenAPIが200を返し、投入処理にも到達することをローカルで確認した。検証をASGIの入口に移し、すべての管理HTTPで単一のloopback Hostを要求する。違反はbodyの読み取りとDB接続前に403。管理サーバーはloopback bindを維持し、公開Webhookアプリは分離したまま。
- 通常回帰は228 passed、215 deselected、既知のTestClient警告1件。pip check、compileallも成功。IPv4・IPv6表記とlocalhostを許可し、外部名・重複Hostを拒否し、転送ヘッダーで許可が変わらないことを確認した。
- 専用PostgreSQLと実Dockerの選択回帰は40 passed、1 failed、402 deselected（333.21秒）。実HTTPで想定外のHostの投入を拒否し、履歴を作らず、通常の投入・再送・履歴参照が通った。ローカルHTTPSで有効な署名の10回再送はrunが1件、試験内の最大応答は0.346秒。不正署名とDB停止・受信プロセス停止からの再送も確認した。
- P2のclaim直後の停止だけ、再試行予定の通知から25秒以内に代替Workerの報告直前の通知を受け取れなかった。代替Workerの正式成功は予定記録の約25.34秒後。通知待ちがバックオフ、Docker準備・実行・回収を含むため、試験側の待機を60秒とし、代替Worker終了時にすぐ失敗するようにした。製品側の時間制限・lease・バックオフや、旧報告の拒否と復旧時間の検査を維持した。
- 修正後の3地点再検証は2 passed、1 failed、440 deselected（216.42秒）。実行中と確定直前は旧報告を拒否して正式結果を保存し、復旧は57.35秒・47.10秒で、いずれも45秒の仮目標を超過した。claim直後の停止では代替Workerも実行段階で終了1となり、その試行が失効して3回目の試行がDBに予定された。該当runのDocker作成イベントは観測されず、どの処理でleaseを使い切ったかと、3回目の正式結果は未確認。
- 管理HostとWebhookの分離を専用PostgreSQL 17.12で再確認し、2 passed、441 deselected（9.18秒）。ローカルHTTPSの最大応答は0.198秒、DB readinessは200、再起動後にmigration 001〜008を保持した。専用DBおよびfixtureの試験資源の残存は0件。
- P5の既存CSV 600件から、各100件のキュー待ち・実行p95を独立再計算し、既存表の12値と一致することを確認した。計画で求めた比較グラフを追加した。[比較結果](ci_runner/docs/p5-validation.md#2026-11-19の実測)
- P2の再検証結果を踏まえ、MVPの推定を概算93%（残7%）へ見直した。P2の作成前の遅延の切り分けと最終結果の確認、P3の承認済み公開HTTPSでの実配送・手動再送、P5の第三者再現・最終受入が残る。全体の重い回帰は再実行していない。

## P2のdaemon ID取得と作成前失効の再検証

- 開始HEAD `1ff56f25adf6e2e7818bee4bdb1e05e5488be2d5` のpush/PR CIは成功。PR #1の最新headとDraft状態を確認した。
- 変更前の3地点はすべて同じrunを復旧した（3 passed、440 deselected、228.38秒）。claim直後・実行中・結果確定直前の順に復旧69.59秒、48.97秒、47.13秒。claim後のdaemon ID取得はそれぞれ13.846秒、3.279秒、0.862秒で、作成予約は0.041秒、1.342秒、0.042秒だった。前回の代替試行の失効はこの計測では再現しなかった。
- ローカルの読み取り比較2回では、CLIのID取得0.639〜0.693秒、Engine APIのID取得0.054〜0.085秒で、IDは一致した。Docker CLI 29.9.2では `docker info` の表示をIDに限定してもCLI pluginを調べる。現在のcontextが通常のUnix socketの場合は `/info` からIDを取得する。作成予約と回収に使うIDを毎回照合し、DB時刻の30秒lease、5秒heartbeat、隔離制限、再試行上限を適用する。 [Docker CLI 29.9.2の実装](https://github.com/docker/cli/blob/v29.9.2/cli/command/system/info.go#L78-L105)
- 通常回帰は233 passed、216 deselected、既知のTestClient警告1件（3.32秒）。pip check、compileallも成功。Unix socketの選択、失敗時のCLI経路、SSH/TLS構成、UUID不正の拒否を検証した。
- 最初の全体回帰は、使い捨てDBがhealthyになった直後、migrationより前にホストからのTCP接続が切れて中断した。初期化用サーバーはUnix socketで応答するため、Composeのreadinessをコンテナ内TCPの `127.0.0.1` で確認するようにした。 [PostgreSQLイメージの初期化処理](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh#L264-L285)
- TCP readinessを使った実PostgreSQL 17.12・実Docker 29.9.2の全体回帰は431 passed、3 failed、15 skipped（961.28秒）。skipはP0専用プローブ10件とライブGitHub source取得5件。停止試験のclaim直後と確定直前は58.23秒と39.95秒で復旧し、代替Workerのdaemon ID取得は0.629秒と0.178秒だった。
- 実行中停止の試験では回収CLIの外側の12秒待ちが切れた。Dockerイベントのkillからdestroyには約10秒かかったため、この試験で失効後に回収する呼び出しの待ちを45秒とした。製品の制限と復旧時間の実測を維持する。DB停止注入の別試験では2件を明示的な `available_at` 順にそろえ、どちらも取得可能な状態にした。
- 手動再試行の回収ではDBの観測時刻が終了時刻より約0.165秒前となり、回収記録のCHECK制約が更新を拒否した。回収時刻は終了時刻とDBの現在時刻の遅い方を保存し、自動再試行は終了・回収・DBの現在時刻のうち最も遅い時刻から5秒または10秒を数える。同一daemon・完全IDでの不在確認、DB更新条件とCHECK制約を適用する。
- 影響範囲の回帰は26 passed、1 failed、423 deselected（396.32秒）。3停止位置の正式結果、旧報告の拒否、作成前失効から3回目だけが確定するケース、壁時計の逆行を模した回収・待機順序、DB停止注入を確認した。復旧はclaim直後43.16秒、実行中49.59秒、確定直前55.28秒。代替Workerのdaemon ID取得は0.148〜0.215秒、45秒の仮目標は2件で超過した。残った手動再試行のテストでは、APIが受付済みを返した直後の問い合わせで取得可能なrunが見つからず、DBの `available_at` に達するまで最大5秒ポーリングするようにした。
- 手動再試行の再検証は1 passed、449 deselected（32.24秒）。readiness 200、再起動後のmigration 001〜008保持を確認した。最終の通常回帰は233 passed、217 deselected、既知のTestClient警告1件。全体回帰の回収CLI打ち切りで残った専用imageはrunラベル・所有タグ・完全IDの照合後に回収した。専用DBとfixtureの試験資源の残存は0件。
- 取得競合、DB時刻による失効、停止3地点、古い報告の拒否、基盤障害だけの最大3回、DB断と安全な回収を照合し、P2を完了と判定した。MVPは計画工数で重み付けして概算94%（残6%）。P3のGitHub実配送と手動再送、P5の第三者再現と最終受入が残る。

## P5のGitHub-hostedクリーン環境検証

- 開始HEAD `dab04fa111aef9f6d6956e202e949287b4a2ce41` のpush・PR通常CIは成功。PR #1はDraft。P3の実配送に必要な承認済みの公開HTTPS受信先は引き続き未確保。
- P5の初回検証を、担当ブランチへのpush時に使い捨てUbuntu 24.04 VMで再現するworkflowを追加した。固定依存を新しいvenvへ入れ、専用PostgreSQL・実Dockerで固定fixtureを1件実行する。証拠のSHA、成功件数、正式結果を照合し、テストがskipのときは失敗する。[クリーン環境検証の実行一覧](https://github.com/kenobix/Claude_practice/actions/workflows/ci-runner-clean-fixture.yml)
- 既存の統合試験で処理対象と次のrunをDB時刻から明示的に順序付けた。ローカルの専用PostgreSQL 17.12・実Dockerで成功、失敗、タイムアウト、ログ上限の4件が成功（77.76秒）。readiness 200、再起動後のmigration 001〜008保持、試験資源の回収を確認した。
- 通常回帰233件、pip check、compileall、workflowのYAML構文とシェル構文が成功。100件の比較実験やP2の故障試験は測り直していない。P5の第三者による手順再現と全体受入は残る。
- GitHub-hostedでの初回はfixtureが1件成功し、readiness 200とDB再起動後のmigration保持を確認した。digest指定で取得したDBイメージにはタグが付かず、最後の証拠採取がタグ指定で失敗してjob全体は失敗した。Composeが作成したDBコンテナのイメージIDからdigestを確認するように修正した。[初回のGitHub Actionsログ](https://github.com/kenobix/Claude_practice/actions/runs/44536749997)
- 修正後のローカル初回検証は1件成功（16.05秒）。readiness 200、再起動後のmigration保持、使用イメージのdigest出力、専用DB資源の回収まで確認した。

## P5の公開用静的デモ

- 開始HEAD `ceea5328f509180aba141dd34dab5675ca0ba612` の通常CIと、GitHub-hostedのPostgreSQL 17.12・Docker Engine 28.0.4を使う初回検証は成功。PR #1はDraft。P3の承認済み公開HTTPS受信先は未確保。
- 計画§7に沿い、匿名化した復旧シナリオを静的HTMLにした。表示用の実行ID・コミットで、失効、別Workerの試行、旧報告の拒否、正式結果までたどれる。既存の比較グラフとサンプルを参照する。[静的デモ](demo/index.html)
- 公開ファイル4点だけをGitHub Pages相当のサブパスで配信し、Chromiumの1440・390・320pxで、横スクロールが発生しないこと、Spaceキーで手順を開きEnterキーで閉じられること、グラフ表示とCSV取得を確認した。ページ読み込み時はローカルHTML・CSS・PNGだけを取得した。
- 600サンプルからキュー待ち・実行のp95中央値4値を再計算し、表示と一致した。バッチ所要の中央値2値も元の記録と一致した。既存の管理画面テストは2件成功。実DB/Dockerの重い回帰や比較実験は手元で再実行していない。
- GitHub Pagesへの公開は保留。P3の実配送と停止後の手動再送、P5の第三者による初回手順の再現と最終受入が残る。MVPの概算94%（残6%）を維持する。

## P3公開試験前のCaddyローカル検証

- WSL2 x86_64で公式リリースのcloudflared 2026.11.1とCaddy v2.11.9を `artifacts/webhook-tools/` に配置した。配布物のSHA-256をGitHub Releasesと照合し、Caddyのアーカイブは配布元のSHA-512一覧でも確認した。 [cloudflared](https://github.com/cloudflare/cloudflared/releases/tag/2026.11.1) · [Caddy](https://github.com/caddyserver/caddy/releases/tag/v2.11.9)
- Caddyのloopback HTTPSから既存Webhookと使い捨てPostgreSQLへ接続した。試験証明書を信頼しない接続、許可外の12パターン、無効署名、256 KiB超過を拒否し、拒否時は配送・runを作成しなかった。正しい署名の初回と並行再送10回はすべて202でrunが1件。最長応答は0.237秒。
- DB停止中は503で保存せず、DB復旧後の同じ配送は202。受信アプリ停止中はCaddyが502を返し、再起動後は外部Host相当の値を付けたloopback HTTP転送で同じrunが返った。試験用の秘密値・署名・bodyの識別文字列は設定、応答、Caddy・受信アプリ・DBの試験出力に含まれなかった。
- 通常回帰233件と、使い捨てPostgreSQLを使う既存Webhook回帰4件が成功。両方で既知のTestClient警告が1件あった。readiness 200、DB再起動後のmigration保持、専用コンテナ・volume・networkの回収を確認した。公開トンネルの起動、GitHub設定変更、実配送は未実施。 [公開承認後の起動と停止](ci_runner/docs/webhook.md#公開試験用の起動と停止)

## 2026-11-21 P5の最終受入に残る証拠

- 実装SHA `9531a2ab48055655a5266fe342d88e9957893e53` の独立レビューでP0〜P4を合格と判定した。P3では元pushの実GitHub配送の失敗と復旧、ローカルHTTPSで10回再送したときの実DB一意性を確認した。復旧後の公開試験DB件数は未観測。 [実配送の受入記録](ci_runner/docs/webhook.md#実配送の受入記録)
- 元の実push `91afb731a8184717f86b88c04807207c18390e83` と同一GUIDの502・202、GitHub上の受理SHAとrun IDを読み取り確認した。確認時点で対象SHAは `main` の先頭にあり、固定fixtureの形式、2151 bytes、SHA-256を照合した。GitHubの再送期限内なら既存eventを起点にできる。旧公開先は接続不可で、新しい試験枠の個別承認を待っている。Webhook受付から正式結果までの同一runの実測は未実施。 [デモの合格条件](ci_runner/docs/p5-validation.md#実pushを起点にしたデモ)
- 対象SHAのクリーンVM CIと、同一ホストの一時clone・新規venvでの独立レビュー追試は、実DB・実Dockerで固定fixture各1件が成功した。既存の比較実測600件からp95を再計算して一致した。人による外部レビュー結果は未提出。 [再現手順と合格条件](ci_runner/docs/p5-validation.md#初回のローカル検証)
- Pagesの公開元は `main` のルートで、デモHTMLのGitHub Contents APIは404。静的デモ・CSS・比較図・CSVの相対参照と、外部レビュー手順のシェル構文を確認した。計画§7の公開は承認済み統合後の提出条件とした。実pushデモと人による再現結果の確認が済むまで、P5の完了とマージは保留する。 [公開時の確認項目](ci_runner/docs/p5-validation.md#pagesの公開)

## 2026-11-21 P5公開試験の終了

- `c37a4ccc3891e2fc85d7aaf6c69b3491d6c104dc` で承認済みの公開枠を開いた。HTTPSの対象POSTは未設定時503、対象GETと管理APIへのPOSTは404。Webhook `714755178` の配送先・受信側Secretの設定が揃わず、対象GUIDの今回の再送は0件。Workerを起動せず、同一runでの復旧と正式結果は未実測。 [P5の試験記録](ci_runner/docs/p5-validation.md#2026-11-21の公開試験)
- 終了予定の17:39:46 JSTまでに公開を停止し、試験用サービス・DB・volume・networkの残存がないことを確認した。P5は未完了、MVP完成とマージは保留。前回項目に記載した人による再現結果の提出は、元計画のMVP受入条件に含めない。

## 2026-11-24 P5最終受入とfixtureのファイルモード修正

- 承認済みの空コミット `0b775ce47360a1eb1c7ca72d8924c5abd1dca4e5` のGitHub実配送では、0.52秒でHTTP 202が返った。専用DBの記録は配送1件、run 1件、attempt 2件だった。Worker 1のコンテナはGOの前に終了コード2で終了した。同じrunで期限切れと回収の記録、Worker 2への引き継ぎ、旧tokenの拒否を確認し、正式結果を管理画面と照合した。DB受付から正式結果までは145.23秒だった。公開経路を回収し、既存Webhookが無効であることを確認した。 [実配送の記録](ci_runner/docs/p5-validation.md#2026-11-24のgithub実配送)
- `umask 0077` を継承したWorkerでは一時fixtureが `0600` となる。ローカルの実Dockerで `/opt/fixture.py` の `Permission denied` と終了コード2を再現し、`0022` では成功した。製品側で検証済みfixtureを `0644` に設定し、一時ディレクトリは `0700`。Dockerfile・元のfixture・固定digest・非root起動と制限は変更していない。
- 修正後も同じ `0077` を使い、実pushと同じSHAを指定した署名付きリクエストをローカルで受け付けた。通常の経路でGitHubからソースを取得し、使い捨てPostgreSQLと実Dockerを使ってGO後にWorkerをSIGKILLした。そのローカルrunでleaseの失効と回収、別attemptへの引き継ぎ、旧tokenの拒否を確認し、正式結果とログを管理画面で照合した。SIGKILLから正式結果までは48.99秒、ローカル送信から管理画面の照合までは62.34秒だった。試験資源は回収した。
- ローカルの通常回帰は234 passed、217 deselected、既知のTestClient警告1件。依存関係検査は成功。実GitHub配送とSIGKILLの証拠は別runとして扱い、永続化境界、P0〜P4の独立レビュー、比較実測、再現手順、3分デモを照合してP5およびMVPのマージ前受入を完了と判定した。Pagesの公開確認は承認済み統合後の提出条件。 [P5受入判定](ci_runner/docs/p5-validation.md#受入判定)
