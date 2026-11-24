# 固定fixtureログの上限付きDB永続化

P1のログ保存を結果確定に接続する単位。migration 003でattempt_logsとlog_storage_budgetを追加する。
001/002の内容・受付API・Docker制限・Workerの実行権条件は変更しない。
小容量のログをPostgreSQLへ格納し、ファイルとDBの二重更新、ホストpath/symlink、孤立ファイルの問題を今回増やさない。
一般のファイル成果物は引き続きP4の別設計とする。

## 容量と保存内容

| 範囲 | 強制する条件 |
|---|---|
| 1 attempt | stdout/stderr合計0〜1MiB、byteaとして元bytesを保持。NUL/不正UTF-8も変換しない |
| 観測量 | retained合計以上、符号付きbigint範囲。観測量が保持量より大きければ切詰めと判断できる |
| DB全体 | raw保持payload合計の既定/上限64MiB。容量台帳の条件付きUPDATEで並行予約を直列化 |
| 再送 | 状態/exit/reasonに加えstdout/stderr/observed_bytesも完全一致。行・終了時刻・台帳を更新しない |
| 保存後 | attempt IDがPK/FK。通常UPDATE/DELETEはDB triggerで拒否し、上書きしない |

Pythonは型・長さ・観測量を接続前に検証する。DBもCHECKで1MiB/観測量を検証し、INSERT triggerで容量を予約する。
台帳はsingleton行で、capacity_bytesは0〜64MiB、used_bytesは0〜capacity内。HTTPやジョブの入力から設定しない。
専用テストではcapacityを数バイトへ下げ、同じSQLで境界と競合を実証する。64MiBを実データで埋める試験は行わない。
空ログはpayload0として1行保存する。空行や履歴の件数制限・保存期限・自動削除はまだ実装しない。

この上限はrawログpayloadであり、PostgreSQLの行/索引/TOAST/WAL・backup・run履歴・Docker cacheのディスク量を含まない。
DBボリュームの物理quotaやホスト全体の空き容量保証を検証済みとは扱わない。長期運用前に監視・保持期限・容量運用を別途実装する。

## 結果との原子性

finalize_fixtureはrun→attemptをロックし、従来通り現在token/状態/DB期限/取消条件を確認する。
attempt更新→ログINSERT（容量台帳更新）→DB期限を再確認するrun更新を同じtransactionで行い、COMMIT後だけreceiptを返す。
正式ログはrun.completed_attempt_idからattempt_logsを参照する。別attemptのログをrunへ昇格しない。
INSERTやquota予約の失敗、後続のDB期限切れ、COMMIT前の中断はログ・台帳・結果すべてrollbackする。
容量台帳のロック順はrun→attempt→budget。Docker操作やホストファイル操作をこのtransactionへ入れない。

容量不足はDB例外となり、Workerは終了1/stage=finalization・成功receiptなし。先にcontainer/imageを回収し、通常run/attemptはrunningに残る。
自動再実行・履歴リセット・無条件の成功保存を行わない。容量運用・期限回収・復旧は残件。
COMMIT応答不明では保存済みの可能性がある。同じClaim/結果での再照合は、ログも一致するときだけreplayed=true。
CLI再起動でメモリ結果を復元する機能は追加しない。

## migrationと公開境界

schema版は3。既存のrun/attempt/正式参照は残し、過去のログを捏造してbackfillしない。
版2以前に確定したログなし結果は履歴として残るが、新しいfinalizerへの再送はmissing/mismatched logsとして拒否する。
既存runをqueuedへ戻したり終了状態を変えたりしない。
専用DB試験のTRUNCATE runs CASCADEはログも削除し、AFTER TRUNCATEで台帳をリセットする。これは本番運用の削除APIではない。
DB所有者の直接操作・trigger無効化等からの保護は主張しない。一般の削除/保持期限APIは未実装。

生ログをCLI・通常ログへ追加しない。後続単位で[正式ログ取得/最小HTML表示](log-access.md)を実装した。
元bytesをそのままHTMLへ入れず、bounded read・文字変換/制御文字可視化・HTMLエスケープを行う。本格UIはP4の残件。
認証情報のredaction機能ではなく、秘密注入しないローカル固定fixture専用の保存である。

## 検証と参照

scripts/verify_execution.pyで専用DB＋実Docker＋既存回帰を実施する。通常テストは入力拒否を、
実DBは0/1MiB・SQL直接入力制約・バイナリ保持・再送・不変性・小容量での競合・rollbackを検証する。
COMMIT前/後SIGKILLとAPI/DB再起動でログと台帳を確認し、4fixtureの実ログ保存と、実Dockerで容量不足時に成功応答しないことも確認する。
件数・未実施事項はwork_log.mdとDraft PRに記録する。証拠JSONへ生ログを追加しない。

triggerの戻り値/例外は[PostgreSQL 17 trigger functions](https://www.postgresql.org/docs/17/plpgsql-trigger.html)、
専用DBリセット時のtriggerとrollbackは[TRUNCATE仕様](https://www.postgresql.org/docs/17/sql-truncate.html)を参照。
