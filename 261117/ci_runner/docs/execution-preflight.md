# 実行前DB preflight：状態と実行権の再確認

今回実装した1単位は、固定fixtureの実行境界に先立つDB上の権限確認。
`src/ci_runner/preflight.py` のauthorize_execution(claim)が、初回取得時のClaimを現在のDB状態と照合する。
コンテナ作成・起動、コード取得、ジョブ実行、結果確定、lease更新・回収は行わない。

## 受け付ける実行権

1つのSELECTでrunsとattemptsを結合し、次の条件をすべて確認する。

- run ID、attempt ID、token、Worker ID、attempt番号が提示されたClaimと一致。
- runとattemptがともにrunning、attemptのfinished_atがnull。
- 取消要求がなく、正式結果attemptの参照も未設定。
- DB上のlease_expires_atがDBのclock_timestamp()より未来。
- 現在のサーバー許可設定のrepository/ref/workflowと一致。
- 今回の対象である初回attempt番号1、retry_kind=initial。

取得したcommit SHAも小文字16進40桁として確認する。
手元のClaimのstarted_at/lease_expires_atで認可しない。DBの現在値を正本にする。
一致した場合だけ、DBから読み直したrun/attempt識別子・Worker・token・期限と、固定repository/ref/commit/workflowをExecutionPermitにまとめて返す。
ExecutionPermitは変更不可の内部データであり、tokenはreprから除く。HTTP応答やログへ出す経路は追加しない。

## 失敗とDBロック

条件不一致はExecutionDenied。DB未設定やDB通信エラーは例外として伝播し、実行可能なsnapshotを返さない。
SELECT後のconnection context終了時にDBエラーが起きた場合も成功として返さない。
この関数はDBを書き換えず、leaseを延長しない。transactionを短く閉じ、外部処理に行ロックを持ち越さない。
呼出側は例外時に実行準備へ進まず、取得済みrunの復旧は将来の期限回収処理に委ねる必要がある。

## snapshotと外部起動の間の限界

この結果は照合時点のsnapshotであり、後続処理まで有効性を予約するtokenではない。
照合後に取消・期限失効・DB断が起きる可能性がある。実際のDocker起動とは単一transactionにできない。
今後の起動処理は直前に再照合し、残りlease時間に従うローカルwatchdog、実行中の停止、結果確定時の条件付き更新を別途実装する。
今回だけで「取消後の実行が常に起きない」「DB断でもホストで必ず停止する」とは説明しない。

P0で実測したCPU/RAM/PID等の隔離制限とは別の確認である。
本番側のコンテナ設定・cgroup確認・実行中監視はまだ接続していない。
claim-once CLIの挙動は変更せず、取得後はrunningのまま残る。HTTP APIからの起動もない。

## 専用PostgreSQLの検証

scripts/verify_db.pyで使い捨て専用DBを作成し、既存テストとtests/test_preflight.pyを実行する。

| 試験 | 確認内容 |
|---|---|
| 有効なClaim | DBのrepository/ref/commit/workflow、実行権と期限を返す。DB書込なし、tokenはrepr非出力 |
| ロック解放 | 戻った後、別接続でrun/attemptのFOR UPDATE NOWAITが成功 |
| 識別子不一致 | run ID/attempt ID/token/Worker/番号の不一致を拒否 |
| DB状態変化 | 取消、run/attempt終端、token変更、許可設定外への変更、結果参照設定を拒否 |
| DB期限切れ | 手元の期限を1日延ばしても、DBの期限切れを拒否 |
| 手元だけ期限切れ | DB上の有効な期限を返し、手元の時刻を認可根拠に使わない |
| 新attemptへの交代 | 古いattempt/tokenで現在のrunを認可しない |
| DB障害 | DB未設定・context終了時の通信例外・専用DB実停止で許可を返さない |
| 照合後の取消 | 最初の成功snapshot後に取消をcommitすると、次の照合は拒否 |

取消等は検証SQLで状態を用意している。取消API・再試行処理の実装を意味しない。
新attemptの実行許可は今回の範囲外。Workerのプロセス強制停止や通信分断中のコンテナ回収は未検証。

## 次の1単位

[固定fixture用の制限付きコンテナ作成・設定照合](container-preparation.md)は後続単位で実装済み。DB照合からの自動接続はまだない。
その後に、このDB preflightとcgroup等の実値確認を起動直前へ接続し、上限付き実行・停止/回収・結果保存を進める。
P1全体は未完了。再試行、heartbeat、期限回収はP2へ残す。

参考: [PostgreSQL clock_timestamp](https://www.postgresql.org/docs/17/functions-datetime.html)、
[psycopg transaction context](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)。

最終結果（2026-11-17）: 専用DB回帰は67 passed、10 skipped、既知のTestClient警告1件、30.59秒。
新規認可19ケースと既存48ケース。別実行のP0隔離回帰は10 passed、41.46秒。
途中で認可用Claimの取得準備が1件失敗したため、投入直後の時刻条件と認可試験を分離し、DB上で明示的に取得可能にしたfixtureへ修正した。
取得不可の根本原因は未確定で、取得失敗時の診断を追加してwork_log.mdに記録している。本番取得SQLの条件緩和や試験内リトライは行っていない。
