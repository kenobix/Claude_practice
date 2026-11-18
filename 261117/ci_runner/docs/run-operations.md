# run取消とattempt履歴API

両操作は管理API `ci_runner.app:app` のloopbackで使う。公開Webhookアプリの受信口とは別。DB接続やtransactionに失敗した場合は共通の503応答を返す。

## runの取消

`PUT /runs/{run_id}/cancel` はリクエストbodyなし。ブラウザーからの同一オリジン操作とCLIに使う。許可Hostは `localhost`、`127.0.0.1`、`[::1]`。管理APIにはCORSを設定せず、loopbackだけで待ち受ける。

| 対象run | 結果 |
|---|---|
| 待機中 | 200。DB時刻を記録して `canceled`。新規attemptは作らない |
| 実行中 | 202。DB時刻に取消要求を記録し、実行権を閉じるまで `running` |
| 取消済み、または同じ取消要求の再送 | その時点の状態を返し、元の要求時刻を保持 |
| 成功・失敗・時間切れ | 409。正式結果と履歴を維持 |
| 対象なし、許可対象外、UUID不正 | 404または422 |

取消要求と結果確定は同じrun行ロックで順序が決まる。取消要求のcommit後はpreflight・heartbeat・成功報告が拒否され、期限監視が `canceled_lease_expired` を記録する。残存コンテナは通常の回収手順に従う。現在の常駐Workerはheartbeat拒否を受けると所有資源を回収し、安全停止する。後続処理には監視側からの再起動が必要。

取消の応答はDB commit後に返す。応答喪失時は同じPUTを再送できる。HTTP 202は取消要求の保存を示す。実行停止の確認には状態とattempt履歴を参照する。

## attempt履歴

`GET /runs/{run_id}/attempts` はattempt番号の降順で返す。 `limit` は既定20、範囲1〜100。次がある場合は `next_before` を排他的な `before` に渡す。存在するrunに履歴がなければ空の200、runがなければ404。

一覧はattempt番号、再試行種別、状態、開始・終了時刻、終了コード、理由、`official` を返す。 `official` は同じSQL snapshotで確認した正式attempt参照に一致する場合に真。実行権tokenやログ本体は含めない。正式ログは既存の [run IDによるログ参照](log-access.md) で取得する。
