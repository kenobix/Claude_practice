# Worker初回取得：実装した1単位

状態: queuedの原子的な取得と初回attempt発行を実装。コンテナ実行・結果確定・heartbeat・期限回収・再試行は未実装。
受付APIとP0実行境界検証に続くP1の最小単位。migrationやAPI応答形式は変更しない。

## 条件とtransaction

`src/ci_runner/worker.py` のclaim_next(worker_id)を信頼済みWorker側から呼ぶ。
worker_idは1〜64文字のASCII識別子に制限し、DB接続より前に検証する。

取得可能なのは次の全条件に合うrunだけ。

- state=queued、取消要求なし、DBのclock_timestamp()でavailable_at到達済み。
- 現在の許可設定のrepository/ref/workflow版と一致。
- attemptsがまだ1件もない初回投入。

短いREAD COMMITTED transactionでavailable_at/created_at/id順に1件を選び、
`FOR UPDATE OF r SKIP LOCKED`でロック済み行を待たずに飛ばす。
run行を先にロックし、初回attempt番号1・retry_kind=initial・worker_id・DB生成UUID tokenを発行する。
開始時刻はINSERT文のstatement_timestamp()、leaseは同じDB時刻+30秒。
その後runをrunningに更新し、両変更を同じtransactionでcommitしてからClaimを返す。
取得可能な対象がなければNone（全行が他transactionでロック中の場合も含む）。途中の失敗では両変更がrollbackされる。成功後は行ロックを保持しない。

Claimは内部の実行権情報を保持するが、tokenはreprとCLI出力へ含めない。
この実装は権限を**発行**するだけで、コンテナ起動前のDB照合は後続の[preflight](execution-preflight.md)に実装済み。起動処理への接続と完了報告のtoken検証は未実装。
再試行の理由や予算が未実装のため、履歴ありのqueuedを初回扱いで再取得しない。
後続のretry実装で番号を単調増加させ、manual/infrastructureを正しく保存する必要がある。

## 別プロセスでの動作確認

migration済みの使い捨て開発DBでのみ診断する。

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
# DATABASE_URLを設定し、受付APIで検証用runを投入した後
.venv/bin/python -m ci_runner.worker --worker-id local-worker-1 --claim-once
```

--claim-onceを必須にし、暗黙の取得や常駐pollingは行わない。
成功時はclaimed=trueとrun/attempt ID・番号・期限をJSONで返す。
対象なしはclaimed=false、いずれもexit 0。DB未設定/停止はstdoutを出さずexit 1。
commit応答が失われれば結果不明になり得るため、DBエラーを「必ず未取得」と説明しない。

取得したrunはこのコマンドの終了後もrunningのまま残る。ジョブ実行も期限切れからの回復もまだない。
診断後に未完成の回復処理を期待せず、使い捨てDBで試す。既存データを自動で消す機能は付けない。
日常の検証は資源回収まで行うscripts/verify_db.pyを推奨する。

## 正常系・異常系の証拠

専用PostgreSQL 17.12でtests/test_worker_claim.pyと既存テストを実行する。

| 試験 | 確認する結果 |
|---|---|
| 別プロセスで取得 | run=running、attempt_no=1、retry_kind=initial、30秒差のDB期限、worker ID保存 |
| APIとの整合 | GETとPOST再送が同じrunningを表示し、再キューしない |
| ロック解放 | CLI終了後に別接続のFOR UPDATE NOWAITが成功 |
| 対象なし | claimed=false、attemptなし |
| 取得対象の除外 | 未来のavailable_at・取消要求・未許可workflow・終端・履歴ありを飛ばす |
| ロック競合 | 先頭runを別接続でロックしたまま、別プロセスが次のrunを取得 |
| 同時取得の競合 | 2接続を同時に取得開始しても、1つの初回attemptだけを発行 |
| 途中例外 | attempt INSERT直後とcommit直前で失敗させ、queued/attemptなしにrollbackし再取得可能 |
| DB異常 | DB未設定・専用DBの実停止時にexit 1、成功JSONも接続情報も出さない |
| token/識別子 | tokenのCLI/repr非出力、worker ID不正はDB接続前に拒否 |

取得競合の試験は排他条件の検証に限る。2Workerによるジョブ実行や故障後回復が完成したという意味ではない。
今回はWorkerのcommit前後SIGKILLや通信分断は未実験（受付APIの同名試験とは区別する）。

## 次の依存関係

DB期限/状態/token/取消要求の再確認はpreflightとして実装済み。次は固定fixture用の制限付きコンテナ作成・設定照合を用意し、後続の起動時にpreflightとP0の実値確認を接続する。
取得を済ませてから任意のホストコマンドを呼ぶ実装は行わない。
その後、単一Workerの上限付き実行・停止/回収・条件付き結果保存を進める。P1全体は未完了。
heartbeat、自動lease回収、基盤障害の再試行、古い報告拒否・取消競合の故障実験はP2で完成させる。

参考: [PostgreSQL SELECT](https://www.postgresql.org/docs/17/sql-select.html)、
[DB時刻関数](https://www.postgresql.org/docs/17/functions-datetime.html)。

最終検証（2026-11-17）: scripts/verify_db.pyは48 passed、10 skipped、既知のTestClient警告1件、28.21秒。
新規取得関連9ケースと既存39ケースを確認した。別実行のP0隔離回帰は10 passed、37.85秒。
専用の一時DB・Docker資源は回収済み。依存・既存migrationは変更していない。
