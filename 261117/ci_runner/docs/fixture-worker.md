# 一件だけ実行するローカル固定fixture Worker

`src/ci_runner/fixture_worker.py`はAPIから独立した信頼済みプロセス。
P1の取得・隔離実行・結果確定を接続する単位で、常駐ループやP2の復旧処理を含めない。
従来の[claim-once](worker-claim.md)の意味は変更しない。

## 契約と境界

1. CLIはworker-idと明示的な--run-onceを必須にする。任意URL・コマンド・YAML・image・環境注入・run ID指定は受け付けない。
2. 運用者のfixture-modeはsuccess/failure/timeout/logsだけ。既定success。モードはHTTP要求や受理repositoryから取得しない。
3. run_onceは入力を検証後、claim_nextを1回だけ呼ぶ。空なら正常終了・claimed=falseでDockerを呼ばない。
4. 取得のCOMMIT成功後、同じClaimでexecute_fixtureを呼ぶ。起動前/GO前のDB再照合、実値ガード、時間/ログ/lease上限を維持する。
5. 自分のcontainer/image tagを回収してから、同じClaimとExecutionResultでfinalize_fixtureを1回呼ぶ。回収失敗時は確定へ進まない。
6. 確定のCOMMIT成功後だけrun/attempt ID、state、exit_code、fixture_理由、DB終了時刻、replayedをstdout JSONに出す。
7. どの段階も自動再試行せず、別のrunを続けて取得しない。Docker中にDB行ロックを保持しない。

ジョブログ・token・DB接続文字列・生のdaemon/driver例外はCLIへ出さない。ログ保持は既存の合計1MiBだけで、
今回その永続化は追加しない。受理SHAを固定取得する処理もまだないため、fixture_successを受理commit検査成功と扱わない。
APIへDocker権限を追加せず、既存の入力許可・migration・lease30秒を変更しない。

| プロセス終了 | 出力 | 意味 |
|---|---|---|
| 0 | stdout claimed=false | 取得できるrunなし（ロック中等を含む） |
| 0 | stdout claimed=true + state/exit_code | 結果保存済み。failed/timed_outもWorker操作としては完了 |
| 1 | stderr status=incomplete + stage | 基盤障害・認可拒否・確定不明。成功receiptは出さない |
| 2 | argparseエラー | 明示フラグ不足・不正モード/ID。取得へ進まない |

## 障害と確認手順

| 止まった段階 | DBの可能性と対応 |
|---|---|
| claim | COMMIT前ならqueued、COMMIT後の応答喪失ならrunning。Claimを受け取れずrun ID不明の場合もある。後続実行へ進まない |
| execution | 取消/期限/preflight拒否、Docker障害や回収失敗は通常running・正式結果なし。期限後の新規確定は禁止 |
| finalization | COMMIT前ならrunning、応答喪失後なら終端保存済みの可能性がある。未保存と断定しない |
| プロセス終了シグナル/daemon断 | SIGTERM/SIGKILL等ではfinallyの完了を保証できず、ラベル付き資源が残り得る。自動回収/再試行は未実装 |

stderrで取得済みのrun/attempt IDを照会し、GET /runs/{id}の状態・completed_attempt_id、専用DBのattempt理由/終了時刻を確認する。
claim応答不明時はGET一覧とDBのworker_id・開始時刻も照合する。tokenをログへ出さない。
取消/期限切れやrunning残存を、運用者が無条件に成功へ変更したり、queuedへ戻したりしない。
別プロセスへ再起動しても結果オブジェクトは復元されない。CLI再実行を同じ報告の再送と扱わない。
内部finalize_fixtureの同一報告再送契約は維持するが、CLIの自動再送/再開は別途設計が必要。
残存Docker資源は[準備境界仕様](container-preparation.md)のsessionラベル・所有権を確認して個別に扱い、global pruneしない。

build/create/回収も取得後の30秒leaseを消費する。cold buildは180秒の操作上限より先にDB実行権が失効し得る。
その場合は実行/確定を拒否し、leaseを緩めない。取得前のimage準備・同一imageの安全な受渡しは次の改善単位。
実行中の取消/DB変化の即時検知、heartbeat、期限回収、基盤障害の再試行はP2の残件。

## 検証

通常テストは空キュー、入力/明示フラグ、各段階停止・秘密情報非出力、接続factory引継ぎ、回収失敗、確定できない結果を確認する。
専用PostgreSQLでは別プロセス空キュー（DockerのないPATH）・DB未設定、取得後失敗のrunning残存、
取得COMMIT応答喪失、取消先行、確定COMMIT前/後の通信異常を検証する。合成結果を使うDB試験を実Dockerの証拠と混ぜない。
実DockerではAPI投入→別プロセス一件Worker→4fixtureの結果保存→APIプロセス再起動後の参照・再送を確認する。
2件目のrunがqueued、attempt総数1件、出力にtokenなし、専用session資源の集合が実行前後で同じことも確認する。

再現はscripts/verify_execution.py。通常pytest/ActionsはDockerを起動しない。
最新の実行証拠はignored artifacts/fixture-worker-latest.json（source SHA-256・終了receipt・回収確認）、件数と実測はwork_log.mdに記録する。
P1全体は未完了。ログ永続化/表示時エスケープ、受理commitの対応付けも別単位で実装する。
