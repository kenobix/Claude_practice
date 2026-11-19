# CIジョブ実行基盤

作業日: 2026-11-17。worktree: `~/work/Claude_practice-261117`、branch: `feat/ci-runner`。
基準: [開発計画](../261014/plan_ci_runner.md)。**P0完了（現在環境・管理下の固定fixtureによる開発検証範囲）**。**P1完了（管理下fixtureの受付から結果保存・単一Worker停止まで）**。
ヘルスチェック・受付/履歴API・PostgreSQL永続化・冪等性を実装済み（P1前半）。
Workerの初回queued取得・attempt/token/DB期限発行と、実行前DB preflightを実装済み。
P2では[実行中の5秒heartbeatとlease更新](ci_runner/docs/design.md#lease更新の実装範囲)を実装済み。更新を確認できなければ実行を打ち切り、次のrunへ進まない。
独立した[期限監視](ci_runner/docs/design.md#期限監視の実装範囲)は失効したattemptを閉じ、条件を満たすrunを[再試行](ci_runner/docs/design.md#基盤障害の自動再試行)のため再キューする。
失効確定後の残存コンテナは、Dockerデーモンごとの[回収コマンド](ci_runner/docs/design.md#失効attemptのコンテナ回収)で回収し、同じ完全IDの不在をDBに記録できる。
P2の[コンテナ作成台帳](ci_runner/docs/design.md#コンテナ作成台帳)は、WorkerがDockerへ要求を送る前に作成可能性を記録する。
固定fixtureのコンテナ作成・設定照合・回収まで実装済み。
内部ライブラリでDB再照合・実値ガード・時間/ログ/lease上限付きのローカル固定fixture実行も実装済み。
ローカルfixture結果の条件付きDB確定（状態/exit/理由/終了時刻・正式attempt参照）も実装済み。
別プロセスの一件実行入口で、取得→ローカル固定fixture実行/回収→条件付き確定まで接続済み。
上限付きログを結果と同一transactionでDB保存する。受理commitの固定fixtureを実行し、sourceとimageの由来を結果に保存できる。常駐Workerは検証済みcommitを順に実行する。管理UIで実行と試行の履歴を確認し、取消・手動再試行を行える。Webhookの受付は実装済みで、GitHubからの実配送は未検証。
常駐バックエンドの配置先は将来別途判断する。GitHub Pages向けの[静的デモ](demo/index.html)には再現データと比較実測を収録する。

## 受理commitのsourceを検証する

PR #4で許可mainに固定fixtureが公開された。専用開発DBにmainのSHAでrunを投入した後、
`ci_runner/` から以下を実行する（curl 8.4以上、GitHub HTTPS接続が必要）。

```bash
.venv/bin/python -m ci_runner.commit_source
```

先頭の取得可能なrunについて固定mainのsnapshotを取得し、受理SHAの所属・commit・tree・通常ファイル・
blob・2151 bytes・承認済みSHA-256を照合する。I/O前後のrun ID/SHAが一致するときだけ検証結果を返す。
候補なしは `verified: false`、取得や照合の失敗は終了1。検証結果にはsource本文を出力しない。
このコマンドはrunをclaimせず、実行権やleaseを発行しない。
[取得契約と検証範囲](ci_runner/docs/commit-source-readiness.md)。

## 受理commitの固定fixtureを一件実行する

専用DBで migration 008 まで適用し、許可main上のfixtureを含むSHAでrunを投入する。
WorkerにDocker権限とGitHub HTTPS接続を用意し、`ci_runner/` から実行する。

```bash
.venv/bin/python -m ci_runner.fixture_worker --worker-id commit-worker-1 --run-once --verified-commit
# 使い捨てDB・実Docker・実GitHubで回帰と受理commitの4モードを再検証
CI_RUN_LIVE_COMMIT_SOURCE=1 .venv/bin/python scripts/verify_execution.py
```

匿名GitHub APIの枠が足りない場合は、既存の認証済み `gh` を使う試験用の
`CI_RUN_LIVE_COMMIT_SOURCE=gh` でも再検証できる。Workerの通常の通信設定は維持する。

取得したbytesと固定Dockerfileだけで先にbuildし、同じrun ID/SHAを条件に30秒leaseを発行する。
競合で取得できなくなった場合は別runへ進まず、専用image tagを回収する。
結果の `commit_*` 理由は受理commit経路、`fixture_*` はローカル開発経路を表す。
正式attemptの `attempt_sources` にrun ID・受理SHA・main SHA・blob SHA・source SHA-256・image IDを保存する。
詳細は[一件Worker仕様](ci_runner/docs/fixture-worker.md)を参照。

## Workerを常駐させる

```bash
.venv/bin/python -m ci_runner.fixture_worker --worker-id commit-worker-1 --serve
```

専用DB・Docker・GitHub HTTPSの条件は一件実行と同じ。
空キューは1秒ごとに確認し、受理commitだけを一件ずつ処理する。結果確定後にstdoutへ1行ずつ出力する。
同じ専用DB・Docker daemonに接続する2つ目のWorkerには別の `--worker-id` を指定する。
取得競合で負けたWorkerは元のrun ID・受理SHA・許可設定を再照合し、一致すれば次のポーリングへ戻る。
作成済みの専用imageはその前に回収する。runやschemaの不一致では停止する。
2 Workerの取得競合と停止位置別の復旧については[故障復旧試験](ci_runner/docs/design.md#2-workerの故障復旧試験)を参照。
停止はWorker PIDにSIGTERMを送る。取得やbuildの途中なら完了・回収を待ち、claimを開始済みの一件は
既存の時間・lease上限で確定または失敗、回収してから終了する。
取得拒否・GitHubレート制限・DB/Docker異常・確定不明は終了1となる。実行中のheartbeat失敗は `stage: heartbeat` に出る。再起動する前にDB状態を照合する。
[常駐と停止の仕様](ci_runner/docs/fixture-worker.md#常駐実行と停止)。

## 実行権の期限監視と基盤障害の再試行

同じ専用DBへ接続する環境で、Workerとは別プロセスとして起動する。

```bash
.venv/bin/python -m ci_runner.expiry_monitor --watch
```

1走査で期限切れと再キューをそれぞれ最大16件処理し、5秒待つ。単発は `--once`。DBエラー時は終了1とし、再開前にDB状態を照合する。
基盤障害による失効後、Dockerへのcreate未送信または回収確認があるrunを同じID・受理SHAで再キューする。
`available_at` はDBの現在時刻と記録済みの終了・回収時刻のうち最も遅い時刻から、5秒後（2回目）または10秒後（3回目）。検証付きWorkerだけが新しいattempt・tokenを取得する。
初回投入と各手動再試行はそれぞれ最大3attempt。区間の3回目も失効した場合は `failed / infra_exhausted` を記録する。
取消・チェック失敗・タイムアウト・ログ超過は自動再試行の対象外。

## 失効後に残ったコンテナを回収する

Workerと同じDockerデーモンと専用DBに接続する環境で実行する。

```bash
.venv/bin/python -m ci_runner.orphan_cleanup --once
```

準備時の所有ラベルとDBの失効記録を照合する。作成を追跡しているattemptでは、完全IDを保存してから削除し、同じデーモンでの
不在確認を `container_recovered_at` に保存する。DB確定後に完全IDとrun/attempt IDをJSONで出力する。
削除後に中断した場合は、一覧から消えた完全IDも再走査できる。DBやDockerの異常時は終了1。作成台帳の対象外だった旧attemptは、
従来どおりDockerが削除を確認した場合にJSONを出力する。
作成予約があり、完全IDを観測できていない場合は、一覧が空でも作成結果は不明のままで再試行を保留する。
回収後は期限監視の次の走査で再キューできる。
[回収条件と再検証](ci_runner/docs/design.md#失効attemptのコンテナ回収)。

## Python環境とテスト

リポジトリのルートから開始する。Python 3.12、venv、Docker EngineとCompose v2を用意し、Dockerへ接続できる専用開発環境を使用する。

```bash
cd 261117/ci_runner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Python 3.12系を使用する。`requirements.lock`は実際に導入した開発依存と推移依存の固定一覧。
担当専用の[GitHub Actions](../.github/workflows/ci-runner-tests.yml)で通常テストを自動実行する。
DB・Dockerの強制力は通常CIの対象外。P5では[別のworkflow](../.github/workflows/ci-runner-clean-fixture.yml)で、専用DBと実Dockerを使う初回の固定fixture実行を1件確認する。
両方の実行条件・対象範囲は[自動テスト仕様](ci_runner/docs/automated-tests.md)を参照。
依存を更新するときだけ `pip install -r requirements-dev.in` の後、テストし `pip freeze --exclude-editable` で再生成する。
`.venv`、`.env`、ログ・成果物はGit対象外。APIだけならDBなしで起動できる。

```bash
.venv/bin/python -m uvicorn ci_runner.app:app --app-dir src --host 127.0.0.1 --port 18117
# 別端末
curl --fail http://127.0.0.1:18117/health/live
curl -i http://127.0.0.1:18117/health/ready
```

liveは200。readyは必要なmigration 001〜008をすべて確認して200、DB未設定・停止・版の欠落時は503。
管理アプリの全HTTP経路は、単一のloopback `Host`（`127.0.0.1`、`localhost`、`[::1]`、ポート指定可）を要求する。
管理アプリは引き続きloopbackにbindする。公開HTTPSの転送先には[Webhook専用アプリ](#github-webhookを受け付ける)を指定する。

## PostgreSQLの起動・初期化

通常サンドボックスではDocker daemonへの接続が拒否されたが、許可された実行経路では接続できた。
PostgreSQL起動・migration・統合テスト・再起動後のスキーマ保持を一時プロジェクトで確認済み。
OSへの導入やdockerグループ追加、socketの権限変更は行っていない。
Docker Desktopの現構成で固定fixtureの実行制限を実測済み。rootlessは導入せず、非信頼コードの受付は対象外。

```bash
bash scripts/check_environment.sh
cp .env.example .env
# エディタで .env の仮パスワードをローカル専用値へ変更（コミット禁止）
docker compose config --quiet
docker compose up -d --wait db
```

Composeは`.env`を読み込むがPythonは自動読込しない。
`DATABASE_URL`はローカルで設定したDBユーザー・パスワード・ポート・DB名から環境変数に設定する。
パスワード内の特殊文字はURLエンコードする。実値を履歴や文書へ記録しない。

```bash
# DATABASE_URL設定後
.venv/bin/python scripts/migrate.py
.venv/bin/python scripts/migrate.py  # 二重適用が無変更で終わることを確認
# 専用の空のテストDBを用意し、CI_TEST_DATABASE_URLを設定して実施
.venv/bin/python -m pytest -q -m integration
```

`CI_TEST_DATABASE_URL`は専用テストDBのみ。テストはschemaを適用するため既存プロジェクトのDBを指定しない。
新しい受付APIの破壊的テストにはCI_TEST_DISPOSABLE=1も必要。共有DBへ設定しない。
verify_db.pyを使い、既存の開発DBへテストを手動で向けない。従来の制約テストはrollbackするがschemaは残る。migrationはtransactionとadvisory lockで直列化する。
初期DDLは `migrations/001_initial.sql`。適用済み版は編集せず、後続版migrationを追加する。
版1に加えて002_run_history.sqlの履歴indexを適用する。001は変更していない。
003_attempt_logs.sqlでattempt別ログと容量台帳を追加する。既存履歴を保持し、過去ログはbackfillしない。
007_manual_retry.sqlは手動再試行の受付記録と、各attemptが属する区間の開始番号を追加する。
008_result_artifacts_and_timings.sqlは結果JSON、成果物の容量台帳、試行別の計測情報を追加する。
更新時は既存のAPI・Worker・期限監視・回収プロセスを停止してからmigrationを適用し、新しい版で再起動する。
適用ツールは番号順の前進適用と未知版の検出に対応し、downgradeは未実装。

## 他プロジェクトとの分離

| 対象 | 既定値・方針 |
|---|---|
| Compose project | `ci-runner-261117`（COMPOSE_PROJECT_NAMEで変更可能） |
| コンテナ名 | container_nameを指定せずComposeがprojectごとに生成 |
| DB公開 | `127.0.0.1:55417` → 5432 |
| API公開 | `127.0.0.1:18117`（起動引数で変更） |
| DB/ユーザー | `ci_runner_261117` |
| volume/network | Compose projectの接頭辞付きpgdata/default |

検証時は両ポートのloopback bindが可能だった。起動時にも衝突確認し、使用中ならAPI引数とCI_DB_PORT/DSNを合わせて変更する。
同じ構成をさらに複製するときはproject名・両ポートを変更する。
`docker compose down`はこのprojectだけを停止する。`down -v`はDB消去なので通常の停止には使わない。
このComposeはDBのみ。ジョブの隔離設定を検証した構成ではない。

## 設計資料と次段階

- [状態遷移・責務・データモデル](ci_runner/docs/design.md)
- [脅威モデルと実行境界](ci_runner/docs/threat-model.md)
- [fixture仕様](ci_runner/docs/fixtures.md)
- [隔離仕様・制限ごとの実測証拠](ci_runner/docs/isolation.md) / [生の証拠JSON](ci_runner/docs/isolation-evidence.json)
- [受付APIの契約・競合設計・故障検証](ci_runner/docs/intake-api.md)
- [Worker初回取得の仕様・検証と限界](ci_runner/docs/worker-claim.md)
- [実行前DB preflightとsnapshotの限界](ci_runner/docs/execution-preflight.md)
- [現在token/期限/取消条件を守る結果確定](ci_runner/docs/result-finalization.md)
- [上限付き固定fixture実行とDB認可ゲート](ci_runner/docs/fixture-execution.md)
- [固定fixtureのコンテナ作成・設定照合](ci_runner/docs/container-preparation.md)
- [P1進捗と次の単一Worker実装順序](ci_runner/docs/p1-plan.md)
- [調査・検証結果](work_log.md)

受付・永続化・冪等性、初回取得、一件だけのWorkerによるローカル固定fixture実行/確定は実装済み。認証なしAPIはloopback限定を維持する。

## 一時DBでの再検証

```bash
.venv/bin/python scripts/verify_db.py
```

Dockerへの接続権限が必要。project名 `ci-runner-261117-p0-check` の既存資源があれば変更せず停止する。
メモリ内で使い捨て資格情報を生成し、一時DBで受付API・同時再送・実DB停止・API再起動・commit前後のAPI強制停止と既存テスト、DB再起動を検証する。
Docker隔離の10テストは別スクリプトで実行するため、このDB検証中はskipされる。
終了時はこの一時projectのコンテナ・network・volumeを削除する。開発用DBは操作しない。
イメージはタグと取得済みdigestで固定した。イメージ本体はDocker cacheに残る。
通常テストにはStarletteのhttpx TestClient非推奨警告が1件ある（失敗ではない）。

## P0隔離・リソース制限の再検証

```bash
.venv/bin/python scripts/verify_isolation.py
```

Docker権限が必要。固定fixtureだけを専用の一時イメージ・コンテナで実行し、10テストを検証する。
CPU 0.5、RAM 128MiB（swapなし）、PID 64をcgroup実値と負荷で確認する。
非root・cap-drop・no-new-privileges・network none・read-only root・tmpfs 16MiBを適用。
10秒超過またはstdout/stderr合計保持1MiB超過で監視処理がkillする。
転送中の超過データは破棄するため、厳密な総生成バイト数の制限ではない。

通常pytestはDockerを自動起動しない。実測JSONはignored `ci_runner/artifacts/isolation-latest.json` へ出力する。
証拠の意図的な更新は `--evidence "$PWD/docs/isolation-evidence.json"` を指定する。
テスト終了時に自分が作ったcontainer IDと一時イメージだけを削除し、global pruneや既存環境の変更はしない。
Base image/build cacheは再利用用に残す。強制中断でfinallyも動かない場合は資源が残り得るため、JSONのsessionラベルで確認する。

このP0検証用ハーネスの実測だけでWorker経路も検証済みとはしない。後続単位の固定fixture実行・一件Workerの検証は各仕様を参照する。
コンテナ脱出や任意コードの完全隔離は保証しない。nosuid/nodevなど設定確認のみの項目と限界は隔離仕様に記載した。

## 受付APIの利用

DATABASE_URL設定とmigration 008までの適用後、前述のUvicorn起動コマンドで利用できる。
repositoryはkenobix/Claude_practice、refはrefs/heads/main、workflow版はfixture-v1に固定する。
入力にref・workflow・URL・command・YAMLを追加できない。受付ではSHAの構文を確認し、実在・許可mainへの所属・承認済みfixtureの検証はWorkerがclaim前に行う。

```bash
curl -i -X POST http://127.0.0.1:18117/runs \
  -H 'Content-Type: application/json' \
  -d '{"repository_id":"kenobix/Claude_practice","commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
curl 'http://127.0.0.1:18117/runs?limit=20'
# POSTのLocationに返された /runs/{id} をGETする
```

SHAは構文例。新規201、同じ業務キーの再送200で同じrunを返す。
一覧のnext_cursorをcursorクエリへURLエンコードして渡す。limitは1〜100、UUID不正422、未登録ID404。
bodyはJSONで4KiBまで。DB未設定・停止・query失敗は503で受理を返さない。
commit結果が不明な接続断では、未保存と断定せず同じ業務キーで再送する。
詳細なHTTP契約・ページングの限界・障害実験は受付API仕様を参照。

## GitHub Webhookを受け付ける

`ci_runner.webhook:app` を分離して起動し、専用DBとhook ID・共有秘密を環境変数で設定する。
運用時は承認済みの公開HTTPS受信口から `POST /webhooks/github` だけを転送する。
[受付仕様・起動・手動再送手順](ci_runner/docs/webhook.md)。

## 管理画面で実行を確認・操作する

API起動後、ブラウザーで `http://127.0.0.1:18117/ui` を開く。一覧または実行IDの検索から詳細に入り、
コミットSHA、試行履歴、チェック失敗と基盤障害の区別、正式ログを確認する。対象の状態に応じて取消・再試行を行える。
正式結果の「結果JSONを保存」から試行別の成果物を取得できる。「運用指標」では直近24時間の待ち時間・実行時間・基盤障害率・期限切れ件数を確認できる。
「過去のデモ」は障害試験を再現した表示専用の例で、DBなしでも開ける。 [管理画面と運用API](ci_runner/docs/run-operations.md)

### CLIから取り消す・再試行する・attempt履歴を見る

管理APIのloopbackアドレスから操作する。待機中の取消は200。実行中の取消要求は202で受理し、実行権を閉じるまでは `GET /runs/{id}` に要求時刻と `running` を表示する。

```bash
curl -X PUT "http://127.0.0.1:18117/runs/${RUN_ID}/cancel"
curl "http://127.0.0.1:18117/runs/${RUN_ID}/attempts?limit=20"
```

attempt履歴では終了理由と正式結果の有無を確認できる。次のページには `next_before` を `before` に渡す。
失敗または時間切れを手動で再試行するには、履歴から最新の失敗attemptのIDを指定する。

```bash
curl -X PUT "http://127.0.0.1:18117/runs/${RUN_ID}/retry/${ATTEMPT_ID}"
```

202は受付の永続化を示す。同じPUTを再送しても後から失敗した別attemptは再キューされない。
検証付きWorkerが同じ受理SHAを取得した後、次の番号と新しいtokenで実行する。以前のattempt・ログ・取得元の記録は保持する。
回収を確認できない基盤障害は409で再試行を拒否する。
[運用API仕様](ci_runner/docs/run-operations.md)。

## 比較実験と障害デモ

使い捨てPostgreSQLと実Dockerで1/2 Workerを比較できる。通常は100ジョブを各3回実行し、`--pilot` は2件の動作確認に使う。
固定fixtureの検証と実行制限を保ち、GitHub HTTP応答を試験データに置き換える。
[比較グラフ、クリーン環境の確認、3分の故障デモ](ci_runner/docs/p5-validation.md)

[公開用の静的デモ](demo/index.html)は試行1の失効、別Workerの取得、旧報告の拒否、試行2の正式結果を順に確認できる。
リポジトリのルートで、検証対象のcommitから公開用ファイルだけを一時ディレクトリに展開する。

```bash
preview_dir=$(mktemp -d)
git archive HEAD 261117/demo 261117/ci_runner/docs/p5-worker-{comparison.png,samples.csv} | tar -x -C "$preview_dir"
python3 -m http.server 8000 --bind 127.0.0.1 --directory "$preview_dir"
```

`http://127.0.0.1:8000/261117/demo/` でプレビューする。終了後は一時ディレクトリを削除する。
GitHub Pagesへの公開は保留中。

## Worker取得だけの診断

```bash
.venv/bin/python -m ci_runner.worker --worker-id local-worker-1 --claim-once
```

DATABASE_URL設定済みの使い捨て開発DBに限って利用する。
許可設定に合う初回queuedを1件だけrunningにし、attempt番号1・DB生成token・30秒leaseを同時commitする。
対象なしはclaimed=false。tokenは出力しない。DB停止時はexit 1で成功応答を出さない。
この取得専用コマンドの終了後はrunningが残る。失効後のDB処理は別プロセスの期限監視が行う。
検証と回収をまとめて行う場合はscripts/verify_db.pyを使用する。詳細はWorker初回取得仕様を参照。

## 実行前DB preflight

Worker内部向けのauthorize_execution(claim)で、DBにある現在token・Worker・状態・取消要求・lease期限・許可設定を再照合する。
現在の初回または基盤障害の再試行attemptに、DBから読み直した固定commit等のExecutionPermitを返す。DB異常や条件不一致なら返さない。
この照合はsnapshotであり、照合後の取消/失効を防ぐ予約ではない。内部経路では起動前/ガード後に再確認し、実行中はheartbeatとローカル上限で監視する。
内部の固定fixture実行ライブラリに接続済み。結果確定ライブラリも実装済み。API/claim-onceへの接続は未実装。通常の検証はscripts/verify_db.pyを使用する。

## 固定fixtureのコンテナ準備（起動なし）

```bash
# ci_runner/から。Docker権限が必要
.venv/bin/python scripts/verify_preparation.py
```

Worker内部のprepare_fixture(permit, mode)はローカル固定fixtureをbuildし、
制限付きcreateとinspect照合後に未起動コンテナを返すcontext manager。通常終了・例外時に自分のcontainer/tagを回収する。
P0のテストハーネスには依存せず、任意イメージ・コマンド・mount・環境変数を受け付けない。
API/claim-onceへは未接続。実行ライブラリの起動・実値確認・上限監視と、別の一件Workerへの接続は実装済み。受理SHAの取得検証から固定Docker実行まで一件Workerで接続済み。ログはDB保存・正式参照の取得・最小HTML表示まで実装済み。
設定確認を実行時強制の証拠としない。詳細と残存時のラベルは[準備境界仕様](ci_runner/docs/container-preparation.md)を参照。

## 上限付き固定fixture実行の検証

```bash
# ci_runner/から。使い捨て専用DBと実Dockerを使用
.venv/bin/python scripts/verify_execution.py
```

execute_fixture(claim, mode)はsuccess/failure/timeout/logsだけを扱う内部ライブラリ。
DB再照合とコンテナ内ガードの確認後、DBを再照合してから定型GOを送る。
CPU/RAM/swap/PIDの実値を確認し、壁時計10秒・合計保持ログ1MiB・DB残leaseの上限を適用する。
execute_fixtureだけではDBはrunningのまま。別途finalize_fixture(claim, result)で状態/exit/理由/終了時刻を原子的に確定できる。共有DBへ向けず上記スクリプトで検証する。
実行中の取消やDB断はheartbeatで検知する。失効後の再試行は独立した期限監視と[回収確認](ci_runner/docs/design.md#基盤障害の自動再試行)が扱う。
[実行仕様・証拠・限界](ci_runner/docs/fixture-execution.md)を参照。

## 固定fixtureの結果確定

finalize_fixture(claim, result)はrun→attemptを短くロックし、現在token・DB期限・取消条件を満たすときだけ両方を確定する。
確定済みの同じ状態/exit/理由の再送は終了時刻を変えず応答する。DB異常は成功扱いせず、同じ報告で再確認する。
ローカル経路はfixture_、受理commit経路はcommit_接頭辞で理由を記録する。ログと受理commitの由来は結果と同じtransactionでDB保存する。
API/claim-onceは結果確定へ自動接続しない。使い捨てDBのverify_execution.pyで実行から確定までを検証する。
詳細は[結果確定仕様](ci_runner/docs/result-finalization.md)を参照。

## 一件だけの固定fixture Worker

```bash
# ci_runner/から。migration済みの専用DBのDATABASE_URLとDocker権限が必要
.venv/bin/python -m ci_runner.fixture_worker --worker-id fixture-worker-1 --run-once
# 全回帰と一件Workerの実Docker検証（使い捨て専用DBを作成・回収）
.venv/bin/python scripts/verify_execution.py
```

待機中の初回runを最大1件取得し、ローカル固定fixtureを実行して回収後にDB確定する。
`--fixture-mode`は運用者が選ぶsuccess（既定）/failure/timeout/logsだけ。受理SHAのコードは取得・実行しない。
終了0はWorker操作完了であり、ジョブ成功判定はJSONのstate/exit_codeによる。基盤障害・認可拒否・確定不明は終了1。
空キューを読取確認し、候補があれば固定imageを準備してから取得する。buildは30秒leaseを消費しない。
取得後は検証したimage IDでcreateし、起動前/GO前のDB再照合を維持する。create・実行・回収が期限を超えれば拒否する。
取得後の失敗はrunningに残る場合がある。DBで状態を照合し、自動再投入・手動SQLリセットで履歴を壊さない。
このローカル経路と`ci_runner.worker --claim-once`は初回取得用。再試行の実行には受理commitを検証するWorkerを使用する。
詳細と故障時の確認手順は[一件Worker仕様](ci_runner/docs/fixture-worker.md)を参照。

## 上限付きログ保存

結果確定と同じtransactionでstdout/stderrをDBへ保存する。合計1MiB/attempt、DB全体は最大64MiBのraw payload上限。
ログ・容量台帳・run/attemptは一緒にcommitし、容量不足/失効/中断なら部分更新しない。同じ結果の再送でもログ不一致は拒否する。
正式ログ取得と最小HTML表示は実装済み。保持期限・自動削除は未実装。WAL/索引/履歴等を含むディスク総量保証ではない。
詳細・旧版データ・容量不足時の動作は[ログ保存仕様](ci_runner/docs/log-storage.md)を参照。


## 正式ログの参照

`GET /runs/{run_id}/logs?stream=stdout&offset=0&limit=4096`は正式attemptのログだけを返す。
text/plainが既定。`format=html`はHTMLエスケープ済みの最小pre表示。1回の読取は最大16384元bytes。
X-Log-Next-Offsetで続き、X-Log-Attempt-Idで参照先、X-Log-Truncatedで保存時の打切りを確認する。
不正UTF-8・文字途中のbyte境界は置換、制御文字は可視化し、元のDB保存内容は変えない。
未確定409・ログなし404・範囲超過416・不正入力422・DB停止503を区別する。
詳細は[ログ取得仕様](ci_runner/docs/log-access.md)。APIは引き続きloopback限定で運用する。

P0〜P5の[受入状況と進捗の概算](ci_runner/docs/progress.md)を参照。
