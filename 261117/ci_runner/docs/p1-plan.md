# P1実装進捗と単一Worker実装計画

前提: [P0隔離実測](isolation.md)は現在環境の管理下fixtureについて完了。
下表1〜4は実装・実DB検証済み（P1前半）。5は初回取得、6は実行前DB照合と未起動コンテナ作成/設定照合/回収まで実装・検証済み。常駐ループ・コンテナ起動/実行・7の結果保存・Webhookは未実装。
受付契約と障害検証は[受付API仕様](intake-api.md)。P1全体の完了条件は未達。

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

受付・取得APIと故障試験は完了。「初回queuedの原子的な取得とattempt発行」は完了（[仕様・検証](worker-claim.md)）。実行前DB preflightも完了（[仕様と限界](execution-preflight.md)）。[固定fixtureの制限付きコンテナ作成・設定照合](container-preparation.md)も完了。次は起動直前のDB再照合・実値確認を伴う上限付き実行にする。
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
2. **DB実行権preflightは完了**。起動前の現在token・状態・DB期限・取消要求を再照合するsnapshotを返す。未起動コンテナの設定検証/回収も完了。残りは起動直前のDB再照合・実行時制限の実値確認を接続する。固定イメージ/commit以外を実行せず、preflight不一致・DB断では開始を拒否する。テスト用ハーネスを本番からimportしない。
3. stdout/stderrの保持上限、時間上限、停止と一時領域回収を統合し、正常・チェック失敗・timeout・大量ログの4fixtureを試す。
4. token・状態・DB期限・取消条件に合う場合だけ結果を確定し、再起動後に履歴から追えることを確認する。lease失効後の自動回復はP2の残件と明記する。

受理SHAのコード取得/実行対象への対応付けは未実装。現在の準備境界は常にローカル固定success fixtureを作成するだけで、受理commitの検査完了ではない。
