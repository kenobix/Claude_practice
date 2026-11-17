# ローカルfixture結果の条件付きDB確定

今回の1単位は `src/ci_runner/finalization.py` の `finalize_fixture(claim, result)`。
内部の[上限付き実行](fixture-execution.md)が返した結果を、run/attemptへ同じtransactionで確定する。
API/claim-once・常駐Workerへ自動接続する機能は今回追加しない。既存migrationは変更しない。

## 入力と保存するもの

ExecutionResultへrun_id/attempt_idを追加し、提示Claimと一致することをDB接続前に確認する。
GO送信済み、確認済みcgroup値、image ID形式、終了理由とexit codeの組合せも確認する。
この型・検証は信頼済み制御面の誤配線を防ぐもので、外部から受け付ける認証方式や実行の暗号学的証明ではない。

| 実行結果 | run/attempt状態 | attempts.reason | exit_code |
|---|---|---|---|
| completed | succeeded | fixture_success | 0 |
| check_failure | failed | fixture_check_failure | 1〜255 |
| timeout | timed_out | fixture_timeout | 137 |
| log_limit | failed | fixture_log_limit | 137 |

lease_expired、ガード拒否、GO未送信、不正な組合せは確定しない。復旧処理の対象としてrunningに残す。
後続単位で[上限付きログDB保存](log-storage.md)を同じtransactionへ接続した。経過時間・image ID・一般のファイル成果物は未保存。
受理commitを取得・検査する経路はまだない。理由にfixture_を付け、ローカル固定fixtureの結果を記録していることを区別する。
workflowはfixture-v1固定。受理SHAのコード検査成功という意味ではない。

## 排他と確定条件

1. runをFOR UPDATE、次にそのrunに属するattemptをFOR UPDATEする。
2. 固定repository/ref/workflow、run/attempt ID、現在token、Worker ID、初回番号1・initialを確認する。
3. 新規確定はrun/attemptがrunning、取消要求なし、completed_attempt_id未設定、finished_at未設定であることを確認する。
4. attemptを終端状態・exit/reason・DB時刻finished_atへ更新する。WHEREで現在tokenと**DB現在時刻でlease期限内**を再確認する。
5. runの状態とcompleted_attempt_idを更新し、WHEREで取消なし・running・結果参照なし・現在token/DB期限内を再確認する。
6. COMMIT完了後にだけFinalizationReceiptを返す。途中の条件不一致・DB例外はtransactionをrollbackする。

run→attemptの順は既存設計と一致する。DBロックはこの短い確定transactionだけで、Docker起動/実行/回収中へ持ち越さない。
attempt更新とrun更新の間に期限が切れた場合も、attemptだけ終端になることはなく全体をrollbackする。
lease条件は更新時に判定する。COMMIT応答受信時まで期限内であることを保証する仕様ではない。

取消要求がrunロックを先に取得しcommitした場合、後続の確定は拒否する。
確定が先なら、終端状態に対する条件付き取消SQLは変更0件となる。
今回の試験の取消はテスト用SQLであり、取消APIや実行中の停止処理が完成したことを意味しない。

## 再送と障害

同じrun/attempt・token・Workerに対し、既存の正式結果が同じ状態/exit/reasonならreplayed=trueで返す。
stdout/stderr/observed_bytesも保存済みログと一致する必要がある。finished_atを更新せず、DB書込もしない。これは確定済みreceiptの再取得なので、後からleaseが切れていても応答する。
新しい実行権を与える処理ではなく、失効後の新規結果を受理する例外ではない。
ログ欠落/内容不一致・観測量不一致、状態/exit/reasonが異なる再送、古いtoken等の上書きは拒否する。過去のログなし結果を再送でbackfillしない。

DB通信例外・COMMIT応答喪失では例外を返す。未保存と断定せず、同じClaimと結果で再送する。
commit前ならrollback後に初回確定、commit後なら保存済みreceiptを返す。
意図的SIGKILL試験では前後の状態を専用DBで読み、同じ報告の再送で整合性を確認する。

## 検証

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
.venv/bin/python scripts/verify_execution.py
.venv/bin/python scripts/verify_preparation.py
.venv/bin/python scripts/verify_isolation.py
```

verify_executionは使い捨て専用DBの全回帰と固定fixtureの実Docker試験を行う。
DBのみの確定試験はverify_dbでも実行する。入力拒否試験はDB不要。

- 状態/exit/reason・正式attempt参照・finished_atを4種類の結果で確認。
- 同じ確定済み結果の再送、期限後のreceipt取得、競合する別結果の拒否、終了後のロック解放。
- 不正ID/token/Worker/番号、取消、期限切れ、終端状態、許可設定変更、新attemptへの交代を拒否。
- attempt書込直後の例外、2更新間の期限切れ、commit前/後の通信例外で部分更新なし/結果不明時の再送。
- 別接続が本当にLock待機していることをpg_stat_activityで確認し、取消/失効先行・確定先行を検証。
- 確定と同じ報告の同時再送で1回だけ新規確定。取消は終端状態を変更しない。
- 別プロセスをcommit直前/直後でSIGKILLし、rollback/保持と再送を確認。
- APIプロセス再起動と専用PostgreSQL再起動後、GET履歴とDBの終了時刻/理由を確認。
- 専用DBの実停止で正常receiptを返さない。
- 固定4fixtureを実Dockerで実行/回収した後、その実際の結果をDB確定し再送を確認。

この文書の実Docker試験は内部関数を順に呼ぶ検証。後続単位の[一件Worker](fixture-worker.md)では別プロセスで取得→実行/回収→確定まで接続した。
P1全体は未完了。一件Workerでも失敗時のrunning残存・確定不明を自動回復しない。
既存claim-once診断は変えない。一件Workerのimage準備は取得前へ分離し、container/imageの回収後に確定する。
受理commitの由来保存と常駐ループは実装済み。本格UI、heartbeat・期限回収・再試行は残件。

参照: [PostgreSQLの行ロック](https://www.postgresql.org/docs/17/explicit-locking.html)、
[READ COMMITTEDでの待機後の条件再評価](https://www.postgresql.org/docs/17/transaction-iso.html)。

最終検証（2026-11-17）: verify_execution.pyは156 passed、14 skipped、既知TestClient警告1件、124.01秒。
新規入力12件/DB26件と既存118件。別実行の準備41 passed、P0隔離10 passed。今回の試験失敗なし。
専用ラベルの残存資源0件、実行証拠のソースSHA一致・failure0も確認。詳細はwork_log.mdに記録。
