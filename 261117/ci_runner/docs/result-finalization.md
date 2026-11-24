# ローカルfixture結果の条件付きDB確定

今回の1単位は `src/ci_runner/finalization.py` の `finalize_fixture(claim, result)`。
内部の[上限付き実行](fixture-execution.md)が返した結果を、run（実行記録）とattempt（個々の試行）に同じトランザクションで確定する。
この実装単位では、API・claim-once・常駐Workerへの自動接続は追加せず、既存migrationも変更しない。

## 入力と保存するもの

ExecutionResultへrun_id/attempt_idを追加し、提示Claimと一致することをDB接続前に確認する。
GO送信済み、確認済みcgroup値、image ID形式、終了理由とexit codeの組合せも確認する。
型と入力検証は、信頼済みの制御処理が異なるrunやattemptの結果を渡すなどの誤りを防ぐために使う。外部からの入力を認証したり、実行を暗号学的に証明したりする機能はない。

| 実行結果 | run/attempt状態 | attempts.reason | exit_code |
|---|---|---|---|
| completed | succeeded | fixture_success | 0 |
| check_failure | failed | fixture_check_failure | 1〜255 |
| timeout | timed_out | fixture_timeout | 137 |
| log_limit | failed | fixture_log_limit | 137 |

lease_expired、ガード拒否、GO未送信、不正な組合せは確定しない。復旧処理の対象としてrunningに残す。
[上限付きログDB保存](log-storage.md)を同じtransactionへ接続済み。表の `fixture_*` はローカル固定fixtureの結果を示す。
受理SHAを実行する[検証付きWorker](fixture-worker.md#受理commitの固定fixture経路)は `commit_*` の理由を使い、image IDなどの由来も同時に保存する。

## 排他と確定条件

1. runをFOR UPDATE、次にそのrunに属するattemptをFOR UPDATEする。
2. 固定repository/ref/workflow、run/attempt ID、現在token、Worker ID、番号・区間・種別の整合性を確認する。再試行は検証済みsourceを必須とする。
3. 新規確定はrun/attemptがrunning、取消要求なし、completed_attempt_id未設定、finished_at未設定であることを確認する。
4. attemptを終端状態・exit/reason・DB時刻finished_at・実行時間へ更新し、ログ・由来・容量制限付き結果JSONを保存する。WHEREで現在tokenと**DB現在時刻でlease期限内**を再確認する。
5. runの状態とcompleted_attempt_idを更新し、WHEREで取消なし・running・結果参照なし・現在token/DB期限内を再確認する。
6. COMMIT完了後にだけFinalizationReceiptを返す。途中の条件不一致・DB例外はtransactionをrollbackする。

run→attemptの順は既存設計と一致する。DBロックはこの短い確定transactionだけで、Docker起動/実行/回収中へ持ち越さない。
attempt更新とrun更新の間に期限が切れた場合も、attemptだけ終端になることはなく全体をrollbackする。
lease条件は更新時に判定する。COMMIT応答受信時まで期限内であることを保証する仕様ではない。

取消要求がrunロックを先に取得しcommitした場合、後続の確定は拒否する。
確定が先なら、終端状態に対する条件付き取消SQLは変更0件となる。
運用時の取消は[管理API](run-operations.md#runの取消)から要求し、heartbeatと期限監視で収束する。

## 再送と障害

同じrun/attempt・token・Workerに対し、既存の正式結果が同じ状態/exit/reasonならreplayed=trueで返す。
stdout/stderr/observed_bytesと由来を照合する。版8以降の結果は実行時間と結果JSONも照合する。finished_atを更新せず、DB書込もしない。これは確定済みreceiptの再取得なので、後からleaseが切れていても応答する。
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
受理commitの由来保存・常駐ループ・実行中heartbeatは実装済み。本格UI、期限回収・再試行は残件。

参照: [PostgreSQLの行ロック](https://www.postgresql.org/docs/17/explicit-locking.html)、
[READ COMMITTEDでの待機後の条件再評価](https://www.postgresql.org/docs/17/transaction-iso.html)。

最終検証（2026-11-17）: verify_execution.pyは156 passed、14 skipped、既知TestClient警告1件、124.01秒。
新規入力12件/DB26件と既存118件。別実行の準備41 passed、P0隔離10 passed。今回の試験失敗なし。
専用ラベルの残存資源0件、実行証拠のソースSHA一致・failure0も確認。詳細はwork_log.mdに記録。
