# 固定fixtureのコンテナ準備境界

`src/ci_runner/container.py` の `prepare_fixture(permit)` はWorker内部用context manager。
固定されたローカル `fixtures/job/` だけをnetwork noneでbuildし、取得したイメージIDでコンテナをcreateする。
イメージ名・任意コマンド・URL・YAML・マウント・環境変数を呼出側から指定する引数はない。内部mode引数は固定4種類だけを許可する。
P0のテスト用ハーネスをアプリからimportしない。現在はREADMEのsource checkout・editable installを前提とし、fixtureを含まないwheel単独配布は対象外。

## 今回の範囲と依存関係

受付・初回claim・[DB preflight](execution-preflight.md)が先行実装済み。
今回は制限付きcreate、inspect照合とcontext終了時の回収だけを実装した。
APIとclaim-once CLIには接続していない。後続の[実行ライブラリ](fixture-execution.md)がガードとfixtureを起動する。fixture.pyをホストで実行しない。

ExecutionPermitのrepository/ref/workflow・SHA形式・初回番号と識別子を検証する。
これは内部snapshotの形式確認で、現在のDB認可の代わりではない。build中にleaseが切れることもある。
受理SHAは実行するコードへ解決していない。作成するのは常にローカルの固定4fixtureであり、
「受理commitを取得・検証した」「そのcommitの検査に成功した」とは扱わない。
固定commitの取得/対応付けもP1残件である。

## 取得前image準備と内部での受渡し

prepare_image()は固定ローカルsourceだけをbuildし、unique tag・session・image IDを持つPreparedImageを返す内部context。
一件Workerは候補を読取確認後、このcontextを開いてからrunを取得する。準備失敗時はattemptを作らない。
prepare_fixture(permit, mode, prepared_image=image)は借用したimageを再buildせず、タグのIDと所有sessionを再照合し、
--pull=neverと検証済みIDでcreateする。形の不正なhandle・別所有者・IDの差替え・寿命後のタグ欠落は拒否する。
タグの再照合後に別主体がタグを変更しても、createはタグではなくIDを参照する。daemon操作権限を持つ悪意ある主体からの保護は保証しない。
借用側はcontainerだけを回収し、image context側は同じsession/IDのunique tagだけを回収する。タグが差し替わったら削除を拒否する。
一件Workerは両回収に成功した後だけ結果確定へ進む。SIGTERM/SIGKILL・daemon断・非同期の遅延完了による残存の限界は維持する。
prepared_imageなしの従来呼出しは内部でimage contextを開き、build/create/回収する互換経路。認可チェックは省略しない。

## 作成時に照合する設定

| 項目 | 設定・inspect条件 |
|---|---|
| CPU / RAM / swap / PID | 0.5 CPU / 128MiB / RAMと同値のmemory-swap / 64 |
| 権限 | UID/GID 65534、privileged=false、cap-drop ALL、cap-addなし、no-new-privileges |
| namespace / network | private cgroup/ipc、host PIDなし、network none、ポート公開なし |
| 書込領域 | root read-only、/tmpだけ16MiB tmpfs、rw/noexec/nosuid/nodev、mode1777 |
| 追加アクセス | binds/追加mount/volume/device/volumes-from/追加groupなし |
| ログ・再起動 | log-driver none、restart=no、固定GOゲートのためstdin open、TTYなし |
| 固定実行指示 | イメージID一致、定型Python entrypoint、Cmdはsuccess/failure/timeout/logsだけ、/tmp作業dir |
| 環境 | 固定イメージのEnvと一致。ホスト環境をジョブへ追加しない |
| 状態・追跡 | created、PID0、非running。ランダムsession・run/attemptラベル一致 |

照合不一致・Docker失敗・タイムアウトは例外としPreparedContainerを返さない。
Docker CLIは引数配列とDEVNULL stdinで実行し、shellは使わない。daemonの生stderrを公開例外に含めない。
Docker CLIの接続用環境はホストから利用するが、コンテナに--envや認証mountで継承しない。

**ここで検証したのはDocker設定と未起動状態だけ**。create時点で実行プロセスもcgroup負荷もない。
P0で同値の制限を実測した実績と、本番側の実行時強制確認を混同しない。
nosuid/nodevの個別強制検証や、コンテナ脱出耐性・任意コードの安全性は今回も保証しない。

## 回収・障害

名前・タグにはランダムUUIDを使用し、createの応答が失われても生成名でinspectする。
sessionラベルが一致するコンテナIDだけをforce rm --volumesする。別sessionなら削除を拒否。
最後に同じsessionの一時イメージタグだけを除去する。global prune・既存volume/networkへの操作はしない。
cleanup失敗は例外として伝播し、成功と扱わない。base image/build cacheは残る。
WorkerのSIGKILLやdaemon断でfinallyが完了しなければ残存し得る。
CLI timeout後にdaemon処理が遅れて完了する場合も、その時点のinspectだけでは残存を否定できない。ラベル
`dev.ci-runner.preparation-session` とrun/attemptを手掛かりに、[失効attemptのコンテナ回収](design.md#失効attemptのコンテナ回収)で再走査できる。
実行Workerは[コンテナ作成台帳](design.md#コンテナ作成台帳)に作成を予約し、照合済みの完全IDを起動前に記録する。
Docker daemonを操作できる他の信頼済み主体による競合改変までは保護しない。

## 再検証

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
.venv/bin/python scripts/verify_preparation.py
.venv/bin/python scripts/verify_db.py
.venv/bin/python scripts/verify_isolation.py
```

通常pytestは実Dockerの準備試験4件をskipし、専用opt-inスクリプトでのみ有効化する。
正常create/inspect、呼出側例外、設定不一致、create成功後の応答喪失を実Dockerで試験する。
各試験で未起動・PID0、終了後に固有container/tagが存在しないことを確認する。
通常テストは各制限の逸脱・不完全inspect・不正permit・CLI不在/timeout・生診断非露出・他session削除拒否を扱う。
最新結果はwork_log.mdへ記録する。

## 次の1単位

起動前/ガード後DB再照合とcgroup実値確認を伴う上限付き実行は後続単位で実装済み。
実値不一致の拒否、時間/合計ログ/DB残lease上限、停止/回収を検証済み。
現在token/DB期限/取消条件に従うfixture結果確定も実装済み。次は一件だけのWorker入口へ接続する。P1全体は未完了。

参照: [Docker create（起動を伴わない作成）](https://docs.docker.com/reference/cli/docker/container/create/)。
