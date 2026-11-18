# DB認可ゲートと上限付きローカルfixture実行

今回の1単位は `src/ci_runner/execution.py` の `execute_fixture(claim, mode)`。
先行する[DB preflight](execution-preflight.md)と[制限付きコンテナ準備](container-preparation.md)をつなぐ内部ライブラリ。
固定ローカルのsuccess/failure/timeout/logsだけを実行する。任意コード・コマンド・イメージ・YAMLは受け付けない。
HTTP API・claim-once CLIからは呼ばない。受理commitの固定fixtureとローカル開発経路の実行ループを共用し、実行中はheartbeatを監視する。後続の[条件付きDB結果確定](result-finalization.md)は専用Workerが呼ぶ。
DB確定時の理由は受理commit経路を `commit_*`、ローカル開発経路を `fixture_*` で識別する。

## 起動と処理開始の区別

1. DBから現在token/状態/取消/期限/固定policyを確認し、固定fixtureのコンテナを準備する。
2. 準備済みimageがあればID/所有sessionを再照合してcreateする。指定なしの内部互換経路はbuildも行う。create後にDBを再照合し、DB異常・失効なら起動しない。一件Workerはimageを取得前に準備して渡す。
3. Docker startで**信頼済み固定ガード**を起動する。ガードはcgroup等を読み、確認結果をstdoutに出してstdinを待つ。
4. Worker側で確認結果を厳密に照合し、DBを再照合する。取消・失効・DB異常なら処理開始のGOを送らず回収する。
5. 一致時だけ定型 `GO\n` を送って4種類のfixtureのうち指定した1つを開始し、stdinを閉じる。

ガードにはPID1でも動く明示handler付きSIGALRM 10秒を設定し、GO待ちを無期限にしない。GO受信後は解除し、ホストの監視へ移る。
stdinはこの固定ゲート専用。HTTP入力・実行権token・DB情報を流さない。TTYなし、stdin openをDocker createに明示しinspectで確認する。
DB照合前に起動されるのはcgroup/権限を調べて待機する固定ガードだけ。取消確認後にコンテナを一切起動しない保証とは区別する。
実行時の確認値は以下。存在しない/異なる場合はexit78、`runtime preflight refused`で終了しfixture本体へ進まない。

| 実値 | 条件 |
|---|---|
| cpu.max | 50000 100000 |
| memory.max | 134217728 |
| memory.swap.max | 0 |
| pids.max | 64 |
| UID/GID | 65534/65534 |
| CapEff/CapBnd | 0/0 |
| NoNewPrivs / Seccomp | 1 / 2 |

network none・read-only・tmpfs等はcreate時のinspectで再確認する。今回のガードは上表だけを実値確認する。
ネットワーク拒否・書込拒否・CPU抑制/OOM/PID拒否等の実負荷測定は別のP0回帰で維持する。
nosuid/nodevの個別強制検証や任意コードの完全隔離は今回も主張しない。

## 監視・出力・終了結果

ガード起動から壁時計10秒、またはDBが返したlease残時間に基づく単調時計期限の早い方でkillする。
DB SELECTは `lease_expires_at-clock_timestamp()` を返し、ホストは**DB照合呼出前**の単調時計へ残秒を加算する。
query/commit/応答待ち時間で権限を延長しない。再照合で得た期限が遅くても元のローカル期限を延長しない。

GO送信後は5秒間隔で同じClaimのDB更新を開始する。DB操作は別スレッドで行い、要求の重複は許さない。
commitの成功応答が確認できた場合にだけ、要求を出した時点の単調時計+30秒へローカルlease期限を更新する。
応答確認の締切は要求から5秒か直前のlease期限の早い方。遅れた応答や更新拒否、通信エラーでは例外を返し、`rm --force` で所有コンテナの回収に進む。
10秒上限と出力量監視は応答待ちの間も継続する。コンテナ終了時に送信済みの更新があれば、その成否も締切内に確認する。
DB側で更新がcommit済みでも応答を失えば正式な結果は保存しない。終了後にスレッドが戻る場合があるので、再開前にDB状態を照合する。
取消/DB断はheartbeatで検知する。DB時計の急変やWorkerの強制終了後の回復は別途検証する。

selectorsでstdout/stderrを並行して読み、合計保持を1MiBに制限する。超過分は捨て、log_limitでkillする。
ガードackは4KiB上限の内部通信であり、利用者ログへ含めない。
killは非同期に発行し、Docker attachを読み続ける。同期killの出力バックプレッシャーによる停止待ちを避ける。
Dockerログドライバはnone。観測バイト数はattachから読めた量であり、コンテナの総生成バイト数を厳密に制限する仕組みではない。

| 返却reason | 意味 |
|---|---|
| completed | ガード確認・GO後、exit0 |
| check_failure | GO後、自然終了のexit非0 |
| timeout | 10秒上限でkill |
| log_limit | stdout/stderr合計観測量が1MiBを超えkill |
| lease_expired | ローカルlease期限でkill |
| runtime_preflight_rejected | GO前にガードが終了 |

ExecutionResultはexit code、reason、合計上限内の生bytes、観測量、経過秒、GO送信有無、実値、image IDを返す。
生ログはHTMLとして扱わない。[正式ログ取得](log-access.md)ではbyte上限・文字変換/制御文字可視化・HTMLエスケープを行う。本格UIは未実装。
DB照合の例外、壊れたack、未確認の終了、停止/回収の失敗は例外となり、正常結果を返さない。
Docker終了状態exited/非running/PID0を確認し、attach/killプロセスとパイプを閉じた後、準備contextがcontainer/tagを回収する。
失敗の途中でもcontextを閉じる。daemon断・Worker SIGKILL等で回収できない残存の可能性と所有ラベルは準備境界仕様を参照。
10秒はkill要求までの上限で、build・DB準備・Docker応答/回収までを含む全関数の所要時間上限ではない。

## 実測と再現

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
.venv/bin/python scripts/verify_execution.py
.venv/bin/python scripts/verify_preparation.py
.venv/bin/python scripts/verify_isolation.py
```

verify_executionは既存verify_dbを使い、使い捨て専用DBを作って全回帰と実Docker実行試験を有効化する。
既存projectの資源があれば操作しない。fixtureはDocker内だけで実行し、各テストで固有container/tagの不存在を確認する。
通常pytest/verify_dbは実行試験11件をskipする。証拠はignored `artifacts/execution-latest.json`（ソースSHA256・image ID・実値・サイズ・時間・回収確認）へ出力する。
試験途中にGOなしガードの待機停止が失敗し、起動同期の競合とPID1のデフォルトSIGALRM無視を確認した。
起動ackで試験を同期し、ガードへ明示handlerを実装して単独の自律停止を確認済み。全回帰の最終結果と失敗履歴はwork_logへ記録する。

- success exit0とfailure exit1、10秒でSIGTERM無視fixtureをkill、4MiB定型出力を保持1MiBで打切りkill。
- 実コンテナのmemoryを64MiBへ変更し、テスト内だけinspect照合を迂回して独立したcgroupガードの拒否を確認。
- ガードack後に取消/DB期限切れをDBへcommitし、GOが呼ばれないことを確認。
- 同位置のDB通信例外注入に加え、専用ComposeのDB実停止でもGOが呼ばれないことを確認。
- GOを送らずstdinだけ開いた状態で、ガード自身が10秒後SIGALRMの明示handler（exit124）で終了することを確認。
- DB leaseを3秒に短縮し、10秒より先にlease_expiredで停止することを確認。
- 通常テストで任意mode・認可拒否・DB往復による残時間消費を確認する。

DB認可と外部のGO送信は単一transactionではない。照合後の取消/失効を原子的に防いだとは説明しない。
execute_fixture単体ではrun/attemptはrunningのまま。後続のfinalize_fixtureを明示的に呼ぶと条件付きで確定できる。
検証スクリプトの使い捨てDBだけで試し、共有DBへ手動実行しない。

## 次の1単位

条件付き結果確定と[一件だけのWorker入口](fixture-worker.md)による取得→実行/回収→確定の接続は後続単位で実装済み。
受理commit取得・常駐Workerと実行中のheartbeatは接続済み。期限回収・再試行はP2の残件。

参照: [Docker startのattach/interactive](https://docs.docker.com/reference/cli/docker/container/start/)、
[Python selectors](https://docs.python.org/3.12/library/selectors.html)、
[DockerのPID1とシグナル](https://docs.docker.com/reference/cli/docker/container/attach/)。

正常fixtureのガード後再認可が1件拒否された実行もあり、拒否時DB状態が未記録だったため原因は未確定。拒否時診断を追加し、認可条件の緩和や試験内リトライはしていない。経緯と最終結果はwork_logを参照。

最終検証（2026-11-17）：専用DB＋実Docker＋既存回帰118 passed、14 skipped、既知警告1件、107.47秒。
別実行の準備回帰41 passed、P0隔離回帰10 passed。最終timeoutは10.290秒/exit137、短縮leaseは3.186秒/exit137。
大量ログは観測4MiBに対し保持1MiB/exit137。GOなしガードは明示handlerでexit124を確認した。
11ケースのcontainer/tag回収確認、最新証拠のソースハッシュ一致、専用ラベルの残存資源0件も確認済み。
