# T06aをGPT-6 Lunaへ渡す依頼

T06のSol設計基点は `f9e228dd427d4853a4146356b0c2de07e71682d9`。この依頼書と[読取照合契約](monitor-reconciliation.md)を含む設計commit以降のHEADから始める。実装前にgit statusと最新commitを確認し、他の変更があれば混ぜない。policy ID `monitor-run-reconciliation-v1` と状態/終了値/比較ルールはSolで確定済みである。

以下をそのまま実装依頼として使える。

```text
GPT-6 Lunaで、計画T06のうちT06a（SQLite版）を実装してください。
最初にAGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.mdのT06節、
261021/docs/monitor-reconciliation.md、261021/docs/reviews/20261030-t06-design.jsonを読んでください。

追加する公開部品はsrc/bike_mlops/monitor_inspection.pyのinspect_monitor_run、
CLIは既存monitorと並ぶmonitor-inspect --csv PATH --run-id UUIDです。
上記契約のJSON項目、状態の優先順、exit 0/1/2、上限、S01〜S17/S20/S22/S23を実装・検証してください。
PostgreSQLは接続せずunsupported_input/2にし、S18/S19/S21とPostgreSQL統合はSolへ残してください。

SQLiteは既存DBをmode=ro、query_only、read transactionで読みます。
Database.initialize/connect、CLIの_databaseは使わないでください。
report/DB/marker/旧runの書換え、marker生成、再送、採点、MLflow操作は実装しないでください。
markerなしはDB全件一致でもunconfirmed、DB不在は未送信と断定しないでください。
UUID列のない旧形式はlegacy_unverifiableです。秘密情報入りDSNや例外messageを出さないでください。
通常ファイル以外や読取中の変更、重複窓、壊れたJSON、他profile、他UUIDを安全に扱ってください。

編集範囲は次の「許可する編集範囲」に従ってください。
新しい閾値、DB/schema/lock/依存の変更、契約状態の追加が必要なら変更せず、Solへの具体的な依頼文を残してください。
T03/T13の不安定な試験を直す作業はこの範囲に含めません。

対象testと既存monitor保存/CLIの回帰testを実行し、コマンド・stdout/stderr・exit code・
成功/失敗・制限を新規logへ保存してください。未実施PostgreSQLを成功扱いしないでください。
T06a実装完了時点でSol review待ちとして記録し、まだcommit/pushしないでください。
最後にSolへ、未commit差分・受入結果・残件をreview/検証してcommit/pushする依頼文を出してください。
```

## 許可する編集範囲

- 新規 `src/bike_mlops/monitor_inspection.py`、`tests/test_monitor_inspection.py`。
- `src/bike_mlops/cli.py` はhandler/import/新parser追加のみ。既存monitorの構造や共通mainの終了値処理を変更しない。
- `docs/monitor-reconciliation.md` は実装状態・実行結果・操作例の追記だけ。固定仕様の変更はSolへ戻す。
- `README.md`、`docs/runbook.md` の実装済みコマンド案内、`work_log.md`、`261014/plan_mlops.md`のT06a状態。
- 新規のT06a検証記録JSON（`docs/reviews/`）と、Git対象外の新規log（`data/reports/`）。既存証拠を上書きしない。

`db.py`、`monitor_persistence.py`、学習/配信/runtime、T03/T13のcode/test、依存lock、workflow、`.env`、実DB/既存reportは編集対象外。Solのstorage probe `scripts/test_monitor_reconciliation_storage.py` も変更しない。

## 実装の受入と確認方法

合成fixtureはTemporaryDirectory内に作り、**setupのwriterだけ**既存Database.initializeとpersist_monitor_runを使用できる。reportは現CLI形式を持ち、profile/target/run UUID/window_count/period/latest_window/hashを一貫させる。T05の正常出力検査をそのままコピーするのではなく、欠落・破損・他profile・旧run・差分を注入して状態表を確認する。

SQLite readerのSQL traceと、initialize/connectへの呼出しを禁止するmockを用い、検査中にSELECT/PRAGMA/read transaction以外が発生しないことを確認する。テストfixtureでread-only UPDATEを試すのはS20だけ。各testでreport bytesとDB logical rowsが変わらないことも確認する。CLIのsubprocess終了値とJSON、他コマンドのparser互換を確認する。stdout/stderrにfakeの秘密文字列が出ない負例も必須。

次は261021から実行する予定の確認コマンド。今回の設計段階でこれらの**未実装testを実行済みとはしない**。実行時に新しいlog名へstdout/stderrを保存し、pipelineは `set -o pipefail` を使う。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 120 .venv/bin/python -m unittest discover -s tests -p test_monitor_inspection.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 90 .venv/bin/python -m unittest discover -s tests -p test_monitor_persistence.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 90 .venv/bin/python -m unittest discover -s tests -p test_monitoring_window.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 120 .venv/bin/python -m unittest discover -s tests -p test_training_monitor_reference.py -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

全suiteとpost-push CI、PostgreSQLの受入はSol reviewで必要性と結果を確認する。FastAPI/実HTTPを含む全suiteは既知のsandbox待機制限がある。T03のSIGTERM待ちやT13のfuture時刻判定が再発したらraw logを保存し、Lunaで待ち時間・時刻拒否を緩和しない。

Solへの最終依頼には、設計commit、変更path、状態表caseの合否、全てのlog path/SHA、実行できなかった項目、PostgreSQLを未実装に保ったことを記載する。review待ちの段階なので、この作業単位ではcommit/pushを保留する。共通のGit運用はSol確認後の節目で再開する。
