# 正式ログ取得と安全な表示

P1では、ログの参照とHTMLエスケープを1単位として実装し、`GET /runs/{run_id}/logs` を追加した。
APIはloopbackでのみ待ち受け、ジョブの実行対象は固定fixtureに限る。認証のないAPIは外部に公開しない。
ジョブ投入・実行・ログ保存・migration・削除/保持期限の仕様は変更しない。

## リクエストと状態

| 項目 | 契約 |
|---|---|
| run_id | UUID。attempt ID・path・URL・コマンドを指定する経路は追加しない |
| stream | stdout（既定）またはstderrだけ |
| offset | 元byte列の0始まり位置、0〜1048576。文字位置ではない |
| limit | 元byte列で1〜16384、既定4096 |
| format | text（既定）またはhtmlだけ |

SQLの列名は固定の対応表から選ぶ。offset、limit、run IDはパラメータとして渡す。
同じSELECTでrun.completed_attempt_idから同じrunのattemptをたどり、attempt_logsを参照する。DBでsubstringを適用し、指定ページの範囲だけを返す。
run/attemptが同じ終端状態で、正式参照先に保存ログがあるときだけ200。過去/未確定の別attemptのログを代わりに返さない。
DB行ロック・更新は行わず、ログ保存量/終了時刻も変更しない。参照はSELECT時点のsnapshotである。

| HTTP | 意味 |
|---|---|
| 200 | ページ取得。offsetが末尾と同じなら空ページ、空streamも正常 |
| 404 | runなし、または正式結果はあるが保存ログなし（旧schema等） |
| 409 | queued/running、正式attempt参照なし、run/attempt状態不一致 |
| 416 | 許容範囲内のoffsetが対象streamの保持量を超えた。X-Log-Total-Bytesを返す |
| 422 | UUID、stream/format、offset/limitの形式/上限不正。DBへ進まない |
| 503 | DB停止/通信失敗。共通JSONエラーとRetry-After: 1、生診断やログは返さない |

## ページ情報と表示

200のX-Log-*ヘッダにAttempt-Id、Stream、Offset、Bytes（今回の元bytes）、Total-Bytes（選択stream）、
Retained-Bytes（両stream合計）、Observed-Bytes（両streamの観測量）、Truncated（観測量が保持合計超過）を返す。
続きがある場合だけNext-Offsetを返す。表示後の文字数/UTF-8長からoffsetを計算しない。
ページ間で正式attemptが変わる場合に備え、clientはAttempt-Idを照合して異なるattemptのページを連結しない。

textはtext/plain; charset=utf-8。元bytesをUTF-8のerrors=replaceで変換し、不正列やページ境界で分断した文字をU+FFFDへ置換する。
LF/TAB以外のUnicode Cc/Cf制御文字（NUL/ESC/CR・双方向制御等）は可視の\\uコード表記にする。
元bytesはDBに保持したまま。厳密なバイナリdownloadや、任意境界ページの文字列連結での復元を保証するAPIではない。
htmlは同じ表示文字列をhtml.escape(quote=True)して固定pre要素へ入れた最小表示。ログ由来の属性・script・style・画像等は生成しない。
[管理画面](run-operations.md#管理画面)はrun ID検索と詳細から正式ログをたどれる。管理画面のログは4KiBずつ表示し、リンクとページ送りに表示対象の正式試行IDを含める。
そのIDを読み取り時の正式参照と照合し、変更・解除後のリンクには409を返す。表示文字列の制御文字変換・エスケープはAPIと同じ。

成功応答と取得判定の4xxはCache-Control: no-store、X-Content-Type-Options: nosniff、
CSP default-src 'none'; sandbox; base-uri 'none'; form-action 'none'を付ける。入力422/DB503は既存の共通JSONエラー。
元ページ最大16KiB、変換/escape後のbodyは最大98316 bytes（6倍＋固定pre wrapper12 bytes）。header長はこの数に含めない。
生ログをCLI・CI証拠・通常アクセスログへ追加しない。tokenやDSNを応答へ追加せず、ログの秘密情報redactionは別機能として未実装。

## 検証と限界

通常テストで入力拒否、制御文字/不正UTF-8、HTMLParserで固定pre以外の要素なし、quote/markupのescape、最大応答長を確認。
専用DB/実HTTPではstream選択、byte境界/EOF/空stream、正式参照、未commitの非公開、DB停止/再起動、保存内容/台帳不変を確認する。
過去attemptや未確定のログはSQLで用意する制御面fixtureで、Worker retryの完成証拠とは扱わない。
実Dockerは今回変更した4fixture→別Worker→保存→API再起動→ログ参照の経路だけを選択して検証する。
P0隔離、無関係なDocker故障試験、cold build計測を理由なく再実行しない。
最小HTML表示の初回実装時点ではブラウザ未検証。P4の管理画面と正式ログのブラウザ・実DBの検証は[作業記録](../../work_log.md#p4の管理画面と正式ログ)を参照。

[PostgreSQL bytea substring](https://www.postgresql.org/docs/17/functions-binarystring.html)、
[Python html.escape](https://docs.python.org/3.12/library/html.html)を参照。
