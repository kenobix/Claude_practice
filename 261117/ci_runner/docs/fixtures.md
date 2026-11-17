# 固定fixture v1仕様とP0検証fixture

P1の実行対象は管理下の公開サンプル1つ、固定workflow、外部依存なしのPythonとする。公開サンプルへの接続は未実装。
コードの取得はサーバー許可設定から行い、受理したcommit SHAに固定する。
P0用は `fixtures/isolation/` に実装済み。公式PythonベースのdigestをDockerfileに固定し、network noneで実測した。
このfixtureは隔離検証だけに使い、API/Workerから実行する経路はない。P1用のローカル4fixtureは `fixtures/job/` に分離して実装した。
[準備境界](container-preparation.md)でbuild/create/設定照合/回収を検証済み。[実行ライブラリ](fixture-execution.md)で4fixture・ガード・上限・回収まで検証済み。受理commit取得と結果保存は未実装。

| 名前 | 固定動作 | 期待結果 |
|---|---|---|
| success | 定型1行をstdoutへ出しexit 0 | succeeded、exit_code=0 |
| failure | 定型1行をstderrへ出しexit 1 | failed/check_failure、自動retryなし |
| long_running | 60秒待機 | 壁時計上限10秒で停止、timed_out |
| large_log | 4MiB以上の定型文字をstdout/stderrへ出す | 合計1MiBで打切り、log_limit理由でfailed |

大量ログはパイプを読み続けながら上限判定し、メモリへの全量読込をしない。
上限到達時に停止・強制killし、truncated=trueを記録する案とする。
P0ではsuccess/failure/timeout/logsモードで正常終了・exit 1・10秒kill・ログ上限killと回収を確認済み。
P1ではrun/attemptへの結果保存まで統合して再検証する。CPU/RAM/PID等の有限プローブも実装・実測済み。
P0のlogsは4MiB出力後60秒待機し、自然終了と監視によるkillを区別する。tmpfs容量・noexecと設定不一致の起動拒否も検証した。
詳細と限界は[隔離仕様](isolation.md)を参照。
