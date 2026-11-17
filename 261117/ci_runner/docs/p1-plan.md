# P1実装進捗と単一Worker実装計画

前提: [P0隔離実測](isolation.md)は現在環境の管理下fixtureについて完了。
下表1〜4は実装・実DB検証済み（P1前半）。5は初回取得、6はDB照合・準備・ローカル固定fixtureの実値ガード/上限付き実行/回収まで実装・検証済み。7は条件付きのローカルfixture結果確定まで実装済み。受理commitの固定fixture実行も一件Workerへ接続済み。単一Workerの常駐と停止を接続済み。Webhook・本格UIは未実装。ログ永続化と正式参照の取得/最小HTML表示は接続済み。
受付契約と障害検証は[受付API仕様](intake-api.md)。管理下fixtureと現在の検証環境におけるP1の受入は完了。
継続開発のための[通常テスト自動化](automated-tests.md)を追加済み。DB/Docker統合試験と区別する。

## 順序と受入条件

| 順 | 小さな実装単位 | 具体的な結果とテスト |
|---|---|---|
| 1 | 許可設定と受付契約 | 管理下fixture repository 1件、ref 1件、workflow版をサーバー設定に固定。入力はrepository識別子と40桁commit SHAのみを基本に、任意URL/コマンド/YAMLを拒否。body 4KiB上限、未知フィールド拒否と識別子検証を追加 |
| 2 | DB永続化とPOST /runs | 初期schemaからrepository/ref/commit/workflow版を業務キーに作成。INSERT ON CONFLICTと同一transaction内の既存行取得で競合を処理。commit後だけrun IDを返す。新規/同一run再送の応答を契約化し、DB未保存で受理を返さない |
| 3 | GET /runs・GET /runs/{id} | UUID検証、404、limit 1〜100と安定した作成日時/id順、runとattemptの分離を実装。API再起動後も同じrunと状態を取得できる |
| 4 | 冪等性と故障の統合試験 | 同じ業務キーを直列10回・同時投入してrun 1件。別commitは別run。DB停止は503。commit前停止で未受理、commit後応答前停止から再送で同じID。専用PostgreSQLを使いSQLiteで代替しない |
| 5 | 実行処理の抽出と単一Worker | APIとは別プロセス。queued行を短いtransactionのSKIP LOCKEDで取得し、attempt番号/token/DB時刻のleaseとrun runningを同時commit。ジョブ実行中に行ロックを保持しない。Docker権限はWorkerのみ |
| 6 | 固定コンテナ実行と制限 | 受理commitを固定取得し、取得段階とnetwork noneの実行を分離。P0の非root/cap/read-only/tmpfs/CPU/RAM/PID制限を本番経路へ実装し、実値不一致なら開始拒否。stdout/stderr合計1MiB・時間10秒、停止・回収を統合する |
| 7 | 結果確定とP1完了試験 | 現在token・running・lease内・取消要求なしをDB更新条件にし、exit 0/1・timeout・log_limitを保存。正常/失敗/長時間/大量ログの4fixtureで履歴・終了理由が再起動後も残り、一時資源とログ容量が回収される |

受付・取得APIと故障試験は完了。「初回queuedの原子的な取得とattempt発行」は完了（[仕様・検証](worker-claim.md)）。実行前DB preflightも完了（[仕様と限界](execution-preflight.md)）。[固定fixtureの制限付きコンテナ作成・設定照合](container-preparation.md)も完了。[上限付き固定fixture実行](fixture-execution.md)も完了。[条件付き結果確定](result-finalization.md)も完了。[一件Worker入口](fixture-worker.md)へ接続済み。
再取得防止、失敗時rollback、取得後のロック解放は専用DBで確認済み。履歴ありのqueuedは再試行の理由・予算を実装するまで取得しない。
DDL変更が必要なら001を上書きせず後続migrationを追加し、適用ツールも後続版対応する。
入力の許可設定と実行境界をテスト用ハーネスへ依存させず、アプリ側で明示的に強制する。

## P1とP2の境界

P1でもleaseとtokenを発行し、古い/期限切れの完了は受け付けない。
自動期限回収、heartbeat、複数Worker競合、基盤障害のみ最大3attempt、残存コンテナのラベル回収、取消競合の一式はP2で完成させる。
P1段階で停止したWorkerの自動復旧が完成したとは説明しない。再起動時の残存検出と運用手順を先に決める。
通常チェック失敗とジョブ時間切れは自動再試行しない。外部副作用のexactly-once保証を追加しない。

WebhookはP3、UI/成果物管理はP4。任意repo・外部PR・秘密注入・CDは対象外。
非信頼コードを扱う前に専用の使い捨てVM等の環境を別途設計・検証する。

## 次の単一Workerの小さな単位

1. **初回取得は完了**。固定Worker IDを持つ別プロセスでqueuedを1件取得し、DB時刻のlease・新token・attempt番号を同じtransactionで永続化する。取得後停止の状態を記録し、APIにはDocker権限を与えない。
2. **DB実行権preflightは完了**。起動前の現在token・状態・DB期限・取消要求を再照合するsnapshotを返す。未起動コンテナの設定検証/回収も完了。起動前とガード後のDB再照合・実値確認も内部実行ライブラリに接続済み。固定イメージ/commit以外を実行せず、preflight不一致・DB断では開始を拒否する。テスト用ハーネスを本番からimportしない。
3. **内部の固定fixture実行は完了**。合計ログ保持1MiB・10秒/DB残leaseで停止し回収、4fixtureと拒否系を専用DB/実Dockerで確認。API/claim-onceへは未接続。
4. **内部の条件付き結果確定は完了**。token・状態・DB期限・取消条件を確認し、run/attemptの結果を同時commit。再送・競合・commit前後SIGKILL・再起動後の保持を検証。lease失効後の自動回復はP2の残件と明記する。

claim-onceを維持した[別の一件実行入口](fixture-worker.md)で取得→実行/回収→条件付き確定を接続済み。
一件Workerの取得前image準備・ID固定の受渡しも実装済み。[上限付きログDB保存](log-storage.md)も結果確定と同一transactionへ接続済み。[正式ログ取得/最小HTML表示](log-access.md)も実装済み。
PR #4で許可mainに固定fixtureを公開済み。読み取り専用で受理SHAの所属・commit/tree/blob・承認済みbytesを検証し、
取得後にrun ID/SHAを再照合する処理を実装済み。[取得仕様と検証範囲](commit-source-readiness.md)。
検証したrun ID/SHAだけを原子的にclaimする一件Workerを追加。取得と固定Dockerfileによるbuildはclaim前に行う。
受理commitの固定fixture経路では、正式attemptの結果・ログ・source/imageの由来を同じtransactionで保存する。
[一件Worker仕様](fixture-worker.md#受理commitの固定fixture経路)。
`--serve` は受理commit経路を直列に使用し、SIGTERM/SIGINTで新規受付を止める。heartbeat、期限監視、再試行はP2で扱う。

## P1受入判定

| 計画書の受入項目 | 確認した証拠 |
|---|---|
| 手動投入・一覧・詳細・入力上限 | 通常のAPI入力試験、実PostgreSQLのHTTP冪等性・同時投入、commit前後のプロセス停止、DB停止と復旧の回帰。 |
| 単一Workerと制限付き実行 | 許可mainの受理commitを匿名HTTPSで取得し、同一run/SHAをclaimして実Dockerで4モードを完了。別プロセスの常駐Workerに稼働中SIGTERMを送り、現在のrunだけを確定・回収し次をqueuedに保持。 |
| 終了理由・保持・HTML表示 | 成功・失敗・timeout・ログ超過の状態、終了コード、正式attempt、source/imageの由来を保存。Worker終了・API再起動後に結果とログ、最小HTML表示を照会。 |
| 大量ログと回収 | Dockerの永続ログ無効、保持1MiB/attempt、raw payload台帳64MiB上限。実Dockerでログ超過停止と一時資源回収、容量不足の確定拒否、実DBで容量境界・予約競合・rollbackを確認。 |

管理下fixtureにおけるP1は完了。容量判定はジョブログのraw payloadを対象とし、WAL・索引・Docker cache等を含む
ホスト総ディスク容量の測定・保証は対象外。64MiB実データによる満杯試験は行っていない。
[P1作業ログ](../../work_log.md)と[上限付きログ保存仕様](log-storage.md)を参照。
次はP2の実行権更新と、期限切れ・古いtoken・DB断時の拒否を検証する。
