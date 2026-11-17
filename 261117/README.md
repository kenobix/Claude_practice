# CIジョブ実行基盤：環境整備とP0設計準備

作業日: 2026-11-17。worktree: `~/work/Claude_practice-261117`、branch: `feat/ci-runner`。
基準: [開発計画](../261014/plan_ci_runner.md)。**P0完了（現在環境・管理下の固定fixtureによる開発検証範囲）**。
ヘルスチェック・受付/履歴API・PostgreSQL永続化・冪等性を実装済み（P1前半）。
Workerの初回queued取得・attempt/token/DB期限発行と、実行前DB preflightを実装済み。
固定fixtureのコンテナ作成・設定照合・回収まで実装済み。
内部ライブラリでDB再照合・実値ガード・時間/ログ/lease上限付きのローカル固定fixture実行も実装済み。
常駐Worker・受理commitの取得/実行・結果保存・Webhook・UIは未実装で、P1全体は未完了。
GitHub Pagesへの公開対象ではない。常駐バックエンドの配置先は将来別途判断する。

## Python環境とテスト

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Python 3.12系を使用する。`requirements.lock`は実際に導入した開発依存と推移依存の固定一覧。
依存を更新するときだけ `pip install -r requirements-dev.in` の後、テストし `pip freeze --exclude-editable` で再生成する。
`.venv`、`.env`、ログ・成果物はGit対象外。APIだけならDBなしで起動できる。

```bash
.venv/bin/python -m uvicorn ci_runner.app:app --app-dir src --host 127.0.0.1 --port 18117
# 別端末
curl --fail http://127.0.0.1:18117/health/live
curl -i http://127.0.0.1:18117/health/ready
```

liveは200。readyはDB未設定・DB停止・migration未適用なら503。
本当のDB準備完了はスキーマ版2の確認後に200となる。一時検証DBでready=200を確認済み。

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
- [上限付き固定fixture実行とDB認可ゲート](ci_runner/docs/fixture-execution.md)
- [固定fixtureのコンテナ作成・設定照合](ci_runner/docs/container-preparation.md)
- [P1進捗と次の単一Worker実装順序](ci_runner/docs/p1-plan.md)
- [調査・検証結果](work_log.md)

受付・永続化・冪等性は実装済み。初回取得は実装済み。次は現在token/DB期限/取消条件を守る結果のDB確定へ進む。認証なしAPIはloopback限定を維持する。

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

時間/ログの停止処理はP0検証用ハーネスの実測であり、未実装のWorkerにも適用済みという意味ではない。
コンテナ脱出や任意コードの完全隔離は保証しない。nosuid/nodevなど設定確認のみの項目と限界は隔離仕様に記載した。

## 受付APIの利用

DATABASE_URL設定とmigration版2適用後、前述のUvicorn起動コマンドで利用できる。
repositoryはkenobix/Claude_practice、refはrefs/heads/main、workflow版はfixture-v1に固定する。
入力にref・workflow・URL・command・YAMLを追加できない。SHAは構文だけを検証し、実在確認や実行はまだ行わない。

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

## Worker取得だけの診断

```bash
.venv/bin/python -m ci_runner.worker --worker-id local-worker-1 --claim-once
```

DATABASE_URL設定済みの使い捨て開発DBに限って利用する。
許可設定に合う初回queuedを1件だけrunningにし、attempt番号1・DB生成token・30秒leaseを同時commitする。
対象なしはclaimed=false。tokenは出力しない。DB停止時はexit 1で成功応答を出さない。
取得後の実行・結果保存・期限回収はまだなく、コマンド終了後もrunningが残る。
検証と回収をまとめて行う場合はscripts/verify_db.pyを使用する。詳細はWorker初回取得仕様を参照。

## 実行前DB preflight

Worker内部向けのauthorize_execution(claim)で、DBにある現在token・Worker・状態・取消要求・lease期限・許可設定を再照合する。
有効な初回attemptだけに、DBから読み直した固定commit等のExecutionPermitを返す。DB異常や条件不一致なら返さない。
この照合はsnapshotであり、照合後の取消/失効を防ぐ予約ではない。内部経路では起動前/ガード後の再確認とローカル上限監視を行う。実行中DB再照合は今後必要。
内部の固定fixture実行ライブラリに接続済み。結果保存・API/claim-onceへの接続は未実装。通常の検証はscripts/verify_db.pyを使用する。

## 固定fixtureのコンテナ準備（起動なし）

```bash
# ci_runner/から。Docker権限が必要
.venv/bin/python scripts/verify_preparation.py
```

Worker内部のprepare_fixture(permit, mode)はローカル固定fixtureをbuildし、
制限付きcreateとinspect照合後に未起動コンテナを返すcontext manager。通常終了・例外時に自分のcontainer/tagを回収する。
P0のテストハーネスには依存せず、任意イメージ・コマンド・mount・環境変数を受け付けない。
API/claim-onceへは未接続。実行ライブラリの起動・実値確認・上限監視は実装済み。受理SHAのコード取得・結果保存は未実装。
設定確認を実行時強制の証拠としない。詳細と残存時のラベルは[準備境界仕様](ci_runner/docs/container-preparation.md)を参照。

## 上限付き固定fixture実行の検証

```bash
# ci_runner/から。使い捨て専用DBと実Dockerを使用
.venv/bin/python scripts/verify_execution.py
```

execute_fixture(claim, mode)はsuccess/failure/timeout/logsだけを扱う内部ライブラリ。
DB再照合とコンテナ内ガードの確認後、DBを再照合してから定型GOを送る。
CPU/RAM/swap/PIDの実値を確認し、壁時計10秒・合計保持ログ1MiB・DB残leaseの上限を適用する。
実行結果はメモリ上に返すだけでDBはrunningのまま。共有DBへ向けず上記スクリプトで検証する。
実行中の取消/DB断の即時検知、heartbeat/自動復旧、結果の永続化は未実装。
[実行仕様・証拠・限界](ci_runner/docs/fixture-execution.md)を参照。
