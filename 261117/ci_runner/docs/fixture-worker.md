# 固定fixtureを実行するWorker

`src/ci_runner/fixture_worker.py`はAPIから独立した信頼済みプロセス。
P1の取得・隔離実行・結果確定を接続し、一件実行と常駐の入口を持つ。P2では別プロセスで期限監視とコンテナ回収を行える。再試行は未実装。
従来の[claim-once](worker-claim.md)の意味は変更しない。

## ローカル開発経路

1. CLIはworker-idと明示的な--run-onceを必須にする。任意URL・コマンド・YAML・image・環境注入・run ID指定は受け付けない。
2. 運用者のfixture-modeはsuccess/failure/timeout/logsだけ。既定success。モードはHTTP要求や受理repositoryから取得しない。
3. run_onceは入力を検証後、has_queued_candidateの読取transactionを終了する。候補なしならclaimed=falseでDockerを呼ばない。候補があれば固定imageをbuild/inspectしてからclaim_nextを1回だけ呼ぶ。候補の読取は予約ではなく、build後に対象がなくなる場合もある。
4. 取得のCOMMIT成功後、同じClaimと準備済みimageでexecute_fixtureを呼ぶ。再buildせず、image ID/タグ所有sessionを再照合してID指定でcreateする。起動前/GO前のDB再照合、実値ガード、時間/ログ/lease上限を維持する。
5. 自分のcontainer/image tagを回収してから、同じClaimとExecutionResultでfinalize_fixtureを1回呼ぶ。回収失敗時は確定へ進まない。
6. 確定のCOMMIT成功後だけrun/attempt ID、state、exit_code、fixture_理由、DB終了時刻、replayedをstdout JSONに出す。
7. どの段階も自動再試行せず、別のrunを続けて取得しない。Docker中にDB行ロックを保持しない。

ジョブログ・token・DB接続文字列・生のdaemon/driver例外はCLIへ出さない。ログ保持は合計1MiBで、
[結果確定と同じtransactionでDB保存](log-storage.md)する。受理commit経路の実行結果は下記の `commit_*` 理由で識別する。
APIへDocker権限を追加せず、既存の入力許可とlease30秒を維持する。

| プロセス終了 | 出力 | 意味 |
|---|---|---|
| 0 | stdout claimed=false | 取得できるrunなし（ロック中等を含む） |
| 0 | stdout claimed=true + state/exit_code | 結果保存済み。failed/timed_outもWorker操作としては完了 |
| 1 | stderr status=incomplete + stage | 基盤障害・認可拒否・確定不明。成功receiptは出さない |
| 2 | argparseエラー | 明示フラグ不足・不正モード/ID。取得へ進まない |

## 障害と確認手順

| 止まった段階 | DBの可能性と対応 |
|---|---|
| queue | ローカル経路の候補確認失敗。image準備・取得へ進まない |
| source | 受理commit経路の候補確認・GitHub取得・照合の失敗。image準備・取得へ進まない |
| image | 固定imageのbuild/検証失敗。runを取得せず、attemptを作らない |
| claim | COMMIT前ならqueued、COMMIT後の応答喪失ならrunning。Claimを受け取れずrun ID不明の場合もある。後続実行へ進まない |
| execution | 取消/期限/preflight拒否、Docker障害や回収失敗は通常running・正式結果なし。期限後の新規確定は禁止 |
| heartbeat | 実行中の更新が拒否・不明。所有コンテナとimageタグを回収し、確定や次runの取得へ進まず終了1。応答不明ならDB側の期限が延びている可能性がある |
| image_cleanup | imageタグ回収失敗。取得済みならrunningのまま確定しない。別所有者へ差し替わったタグを削除しない |
| finalization | COMMIT前ならrunning、応答喪失後なら終端保存済みの可能性がある。未保存と断定しない |
| プロセス終了シグナル/daemon断 | SIGKILLやdaemon断でラベル付き資源が残り得る。期限確定後に同じホストでコンテナ回収を実行できる |

stderrで取得済みのrun/attempt IDを照会し、GET /runs/{id}の状態・completed_attempt_id、専用DBのattempt理由/終了時刻を確認する。
claim応答不明時はGET一覧とDBのworker_id・開始時刻も照合する。tokenをログへ出さない。
取消/期限切れやrunning残存を、運用者が無条件に成功へ変更したり、queuedへ戻したりしない。
別プロセスへ再起動しても結果オブジェクトは復元されない。CLI再実行を同じ報告の再送と扱わない。
内部finalize_fixtureの同一報告再送契約は維持するが、CLIの自動再送/再開は別途設計が必要。
残存Docker資源は[準備境界仕様](container-preparation.md)のsessionラベル・所有権を確認して個別に扱い、global pruneしない。

一件Workerではbuild（操作上限180秒）を取得前へ移した。候補確認のDB行ロック/transactionやleaseはbuild中へ持ち越さない。
image準備失敗時は未取得。build中に他Workerが取得/取消した場合は、取得条件を再確認し、対象なしなら自分のimageタグを回収して終了する。
準備済みimageは内部contextで寿命を管理し、container回収→imageタグ回収→DB確定の順序を守る。handleは公開APIの入力や認証ではない。
create・実行・両回収は取得後のleaseを消費する。GO後の実行ループでは5秒間隔でDBに更新を要求し、期限切れや応答不明時に確定を拒否する。
内部execute_fixtureを準備済みimageなしで直接呼ぶ互換経路ではbuildも取得後のまま。新しい一件Worker経路と混同しない。
失効のDB処理は[独立の期限監視](design.md#期限監視の実装範囲)が行う。DBの失効確定後は
[残存コンテナの回収](design.md#失効attemptのコンテナ回収)を実行できる。基盤障害の再試行はP2の残件。

## 検証

通常テストは空キュー、入力/明示フラグ、各段階停止・秘密情報非出力、接続factory引継ぎ、回収失敗、確定できない結果を確認する。
専用PostgreSQLでは別プロセス空キュー（DockerのないPATH）・DB未設定、取得後失敗のrunning残存、
取得COMMIT応答喪失、取消先行、確定COMMIT前/後の通信異常を検証する。合成結果を使うDB試験を実Dockerの証拠と混ぜない。
実DockerではAPI投入→別プロセス一件Worker→4fixtureの結果保存→APIプロセス再起動後の参照・再送を確認する。
2件目のrunがqueued、attempt総数1件、出力にtokenなし、専用session資源の集合が実行前後で同じことも確認する。
実build後に31秒の遅延を注入し、その間attempt0・queued・DB行ロックなし、build完了後の新規30秒lease、
build1回・createのimage ID一致・正常確定・資源回収も確認する。実キャッシュを破壊するcold build計測とは区別する。

再現はscripts/verify_execution.py。通常pytest/ActionsはDockerを起動しない。
最新の実行証拠はignored artifacts/fixture-worker-latest.json（source SHA-256・終了receipt・回収確認）、件数と実測はwork_log.mdに記録する。
正式ログ取得/最小HTML表示は実装済み（[仕様](log-access.md)）。ローカル経路の実測記録は上記の範囲。本格UIは後続フェーズで扱う。

## 受理commitの固定fixture経路

`--run-once --verified-commit` は許可mainのsnapshotから検証した固定fixtureを実行する。
curl 8.4以上とGitHub HTTPS接続、migration 005適用済みの専用DBとUUID形式のIDを返すDocker daemonが必要。
`--fixture-mode` は運用者が選ぶ既存4モードで、受付APIは実行指示を受け取らない。

1. DB transactionを閉じてから[固定取得](commit-source-readiness.md)を実施する。
2. sourceとGit blob digestを再確認する。承認固定Dockerfile（SHA-256
   `c6a7de570c22087ae5be90fd1d15cee425281ab10e939af526e6049abaddfecf`）と検証済みbytesだけを
   一時contextに置き、`--network=none` でbuildする。base imageはDockerfileにdigest固定。
   contextを削除し、imageタグ・ID・sessionとsourceラベルを照合する。
3. 由来保存用のmigration版をDBで確認し、検証したrun IDとSHAの組だけにDBの行ロックと30秒leaseを適用する。
   候補が取得済み・ロック中・SHA不一致・取消済みなどで取得できなければ、別runを選ばず終了1。
4. 起動前とGO前を含む既存のDB実行権照合でsourceとの一致も再確認する。既存のCPU・メモリ・PID、
   時間・ログ制限とnetwork noneを使う。コンテナ・専用image tagを回収してから確定する。
5. token・run・attempt・leaseによる結果確定のtransactionにログと `attempt_sources` を含める。
   由来はrun ID・受理SHA・main snapshot・blob SHA・source digest・実行image ID。
   受理commitの理由は `commit_*`、ローカルの理由は `fixture_*`。同じ結果の再送にはログと由来の一致が必要。

権限消失やDB断は成功扱いせず、claim済みの場合はrunningが残り得る。自動回復はP2。
Workerはsource本文をDB・ログ・応答に保存せず、一時build contextは削除する。
Docker base imageやbuild cacheは既存の運用どおり再利用され得る。

実GitHub試験は `CI_RUN_LIVE_COMMIT_SOURCE=1` で通常の匿名HTTPS・別プロセスCLIを使う。
`CI_RUN_LIVE_COMMIT_SOURCE=gh` では試験のHTTP取得だけを認証済みの `gh` に切り替える。
実GitHubの応答を既存のcommit/tree/blob検証に渡し、同じCLI入口から専用DBと実Dockerで実行する。
テスト用通信の切替はWorkerの公開引数やDocker build contextに含めない。


## 常駐実行と停止

- `--serve` は常に受理commit経路を使う。`--run-once` と同時には指定できない。
  一件の確定・回収とreceiptの出力を終えてから次の処理へ進む。空キューは1秒間隔で読み、空のreceiptは出力しない。
  保存済みのチェック失敗・timeoutは処理済みなので次へ進める。
- Worker PIDへのSIGTERM/SIGINTは停止フラグだけを設定する。取得後およびbuild後にフラグを確認し、
  claimに入る前なら専用リソースを解放して終了0。claimに入った一件は既存の認可・実行上限と結果確定に従い、
  終了・回収後に次のrunを開始せず終了0。停止要求とclaim開始が並行した場合は進行中の一件として扱う。
- 停止待ちには進行中の取得・build・実行・回収の操作期限が適用される。外部監視から停止するときは
  Worker PIDを対象にし、同時にDocker/Curl子プロセスへシグナルを送らない。
  SIGKILLやdaemon障害時の手順は、期限監視と同じDockerホストでの残存コンテナ回収。自動再試行はP2の残件。
- 取得拒否、claim競合、DB障害、Docker障害、確定失敗では安全な失敗情報を出して終了1。
  先頭が取得できないときは後続に読み替えない。GitHub匿名APIの制限がかかったときも同じ扱い。
  runの状態と正式attemptを照合してから運用者が再開する。
