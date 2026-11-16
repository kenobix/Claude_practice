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
