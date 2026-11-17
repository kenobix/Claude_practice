# P1前半：受付API・永続化・冪等性

実装済み: POST /runs、GET /runs、GET /runs/{id}、許可設定・入力上限・履歴ページング。
Worker初回取得は後続で実装済み（[取得仕様](worker-claim.md)）。
未実装: 常駐Worker、Webhook、clone、ジョブコンテナ実行、取消・手動retry・成果物管理。
P1全体の完了条件（実行から結果保存）はまだ満たしていない。

## 許可設定と入力

`src/ci_runner/settings.py` の不変なサーバー設定を使用する。

| 項目 | 値 | クライアントからの変更 |
|---|---|---|
| repository_id | kenobix/Claude_practice | この値のみ受付 |
| ref | refs/heads/main | 受付しない |
| workflow_version | fixture-v1 | 受付しない |

POSTはapplication/jsonのみ、ストリームで実際に読み取るbodyが4096 bytes以内。
Content-Lengthだけに頼らず、chunked相当の複数ASGIメッセージも加算する。
bodyはrepository_idとcommit_shaだけを持つobject。未知フィールドは拒否する。
commit_shaは小文字16進40桁。任意URL・コマンド・YAML・ref・workflowの入力は422、JSON以外の媒体は415。
超過は413、不正な長さヘッダーは400。検証エラーに元の値を反射しない。

SHAの**構文だけ**を検証している。GitHubへの接続・commitの存在・refへの所属・fixture内容はまだ検証しない。
現在のAPIはqueuedを保存するだけで、受理したSHAのコードを実行しない。
APIは認証なしのローカル開発用途で、127.0.0.1に限定して起動する。

## HTTP契約

```json
{"repository_id":"kenobix/Claude_practice","commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

上のSHAは構文例であり実在commitの例ではない。

| 操作 | 正常応答 | エラー |
|---|---|---|
| POST /runs | 新規201、再送200。いずれも同じRun形式、Location: /runs/{id} | 入力422、媒体415、body413、DB503 |
| GET /runs?limit=20&cursor=... | 200、itemsとnext_cursor（最終ページnull） | limit/cursor不正422、DB503 |
| GET /runs/{id} | 200、Run | UUID不正422、存在しないID404、DB503 |
| GET /health/live | 200、phase=P1-intake | DB不要 |
| GET /runs/{id}/logs | 正式ログの上限付きtext/HTMLページ（[仕様](log-access.md)） | 未確定409・ログなし404・範囲416・入力422・DB停止503 |
| GET /health/ready | schema版3が確認できれば200 | 未設定/DB停止/未適用503 |

Runはid、repository_id、ref、commit_sha、workflow_version、state、created_at、available_at、cancel_requested_at、completed_attempt_idを返す。
lease tokenやDB接続情報は返さない。新規はqueued、日時はDB時刻、取消・正式結果参照はnull。
GETは保存済みの状態を返す。HTTP APIに状態変更操作はないが、別プロセスの初回取得でrunningへ遷移する。
同じrunが既にfailed等でもPOST再送は状態を変更しない。retryは別操作として今後実装する。

## transactionと競合

業務キーは(repository_id, ref, commit_sha, workflow_version)。001の既存一意制約を維持する。
INSERT ... ON CONFLICT DO NOTHING RETURNINGを使い、新規行がなければ**別のSELECT文**で既存runを読む。
READ COMMITTEDを接続で明示し、一意キー競合で待機した後のSELECTは相手がcommitした行を参照する。
単一CTE内の同じsnapshotでINSERTとSELECTを代用しない。

connection contextを抜けてcommitが完了してから成功応答を返す。例外はrollbackする。
DBエラーは接続情報を含めず503、Retry-After: 1を返す。
接続timeoutは2秒、statement_timeout=5秒、lock_timeout=3秒。過負荷・ロック待ちでも成功を捏造しない。
commit応答が失われた場合は、503/接続断だけから未保存と断定できない。同じ業務キーで再送して結果を確定する。
自動接続リトライ・connection pool・受付スロットリングは今回実装していない。

## ページングとmigration

created_at DESC, id DESCのkeyset方式。limitは1〜100、既定20。
同時刻はUUIDで順序を固定する。limit+1件を読み、余分な行がある場合だけ末尾の日時/IDを次カーソルにする。
カーソルは日時（timezone必須）とUUIDのJSONをbase64url化し、最大256文字。破損・未知項目・不正型は422。
署名・認証tokenではなく公開の位置情報であり、書き換えを認可境界にしない。
ページをまたいだDB全体のsnapshotは保持しない。先頭側への追加による既読行の重複は防ぐが、外部の削除・過去日時への挿入の固定snapshotは保証しない。

001は未変更。002_run_history.sqlで並び順に対応するindexだけを追加した。
migrate.pyは番号順の前進適用とadvisory transaction lockを使用し、未知の適用版を検出したら停止する。
DDLと版記録を同じtransactionでcommitする。readyは版3を要求する。downgradeは対象外。

## 実DB検証（2026-11-17）

実行: `.venv/bin/python scripts/verify_db.py`。PostgreSQL 17.12の一時専用Compose projectを使う。
実HTTPはloopbackのOS割当ポートを使用し、既存APIの18117を使わない。

| ケース | 実測・確認内容 |
|---|---|
| 正常・直列再送 | 新規201、10回再送200、ID/日時一致、DB行1件。別commitは別run |
| 同時投入 | barrierで10 HTTPクライアントを同時投入、201が1件・200が9件、ID一意、DB行1件 |
| 競合待ち | 別接続で一意キーを未commitのまま保持し、APIのpg_stat_activity Lock待機を確認。保持側commit後200で同一ID取得 |
| 状態表示 | 検証SQLでfailedを用意し、GET/再送が保存状態を返し再キューしない |
| ページング | 同一日時3件をlimit=1でUUID順に取得。先頭側への追加後も既読行が重複せず元の3件を取得。空/最終ページも確認 |
| API再起動 | 実Uvicornプロセスを停止・再起動しGET/POSTで同じrunを取得 |
| commit前停止 | INSERT後・commit直前のテスト接続境界でAPIをSIGKILL。HTTP応答なし、DB行0、再送201で1件作成 |
| commit後停止 | commit完了後・HTTP応答前でAPIをSIGKILL。HTTP応答なし、DB行1、再送200で保存済みID取得 |
| 実DB停止 | 専用dbだけ停止しPOST/GETは503、ready503。再開後ready200、未受理の別commitは増えていない |
| migration・DB再起動 | 版1/2の適用・二重適用、DB再起動後に両版を保持 |
| 不正入力 | repository/SHA/未知項目、JSON以外、4KiB超過、chunk分割、長さ偽装、UUID/limit/cursorを拒否 |

fault factoryはtests/p1_fault_server.pyにだけ存在する。通常アプリにはHTTP/環境変数の障害スイッチを入れていない。
故障時刻は接続factoryをテストで差し替えてtransaction contextの出口で同期し、APIプロセス全体を停止した。
DB/HTTP応答をmockした試験ではない。DBへの物理電源断・commit応答中のネットワーク分断の実験は未実施。

破壊的なDBテストはCI_TEST_DATABASE_URLに加えCI_TEST_DISPOSABLE=1が必要。
verify_db.pyが専用DBを作成して両方を設定する。テストごとにTRUNCATEするので、開発用・共有・既存DBへ手動で向けない。
DB停止試験は専用project名とComposeパスの一致も要求する。一時資格情報はメモリ内生成、失敗時fixtureのreprにも出さない。
終了時にAPIプロセス・マーカー・専用DB container/volume/networkを回収する。既存環境やOS設定を変更しない。

参照: [PostgreSQL INSERT](https://www.postgresql.org/docs/17/sql-insert.html)、
[READ COMMITTED](https://www.postgresql.org/docs/17/transaction-iso.html)、
[psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)。

最終結果: DB/API・入力検証39 passed、10 skipped（別実行のDocker試験）、既知のTestClient警告1件、24.92秒。
P0実Docker回帰は別実行で10 passed、32.48秒。[回帰の実測JSON](p1-p0-regression.json)。
初回P0回帰はログ試験の同期docker kill待ちと後片付けでタイムアウトしたため失敗扱いにし、監視中は出力をdrainし続ける非同期killと、削除の成否によらないattach回収へ修正してから再実測した。
