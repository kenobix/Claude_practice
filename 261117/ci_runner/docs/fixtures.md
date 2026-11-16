# 固定fixture v1仕様（未実装・未実行）

管理下の公開サンプル1つ、固定workflow、外部依存なしのPythonを対象とする。
コードの取得はサーバー許可設定から行い、受理したcommit SHAに固定する。
fixtureイメージはPython版とdigestを固定予定。実行時はnetwork noneとする。

| 名前 | 固定動作 | 期待結果 |
|---|---|---|
| success | 定型1行をstdoutへ出しexit 0 | succeeded、exit_code=0 |
| failure | 定型1行をstderrへ出しexit 1 | failed/check_failure、自動retryなし |
| long_running | 60秒待機 | 壁時計上限10秒で停止、timed_out |
| large_log | 4MiB以上の定型文字をstdout/stderrへ出す | 合計1MiBで打切り、log_limit理由でfailed |

大量ログはパイプを読み続けながら上限判定し、メモリへの全量読込をしない。
上限到達時に停止・強制killし、truncated=trueを記録する案とする。
成功/失敗/時間切れ/大量ログでコンテナ・一時領域を回収できることをP1で検証する。
CPU/RAM/PID限界試験は別の管理下プローブとし、専用VM上で実施する。
