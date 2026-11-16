# P1最小実装計画（未実装）

前提: [P0隔離実測](isolation.md)は現在環境の管理下fixtureについて完了。
この文書は実装順序と受入条件のみ。今回はアプリへのジョブ実行機能追加をしない。

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

推奨する最初のcommitは「受付契約＋DB永続化＋POST /runsの冪等性」。Workerを同時に広げず、次のcommitで取得APIと故障試験を閉じる。
DDL変更が必要なら001を上書きせず後続migrationを追加し、適用ツールも後続版対応する。
入力の許可設定と実行境界をテスト用ハーネスへ依存させず、アプリ側で明示的に強制する。

## P1とP2の境界

P1でもleaseとtokenを発行し、古い/期限切れの完了は受け付けない。
自動期限回収、heartbeat、複数Worker競合、基盤障害のみ最大3attempt、残存コンテナのラベル回収、取消競合の一式はP2で完成させる。
P1段階で停止したWorkerの自動復旧が完成したとは説明しない。再起動時の残存検出と運用手順を先に決める。
通常チェック失敗とジョブ時間切れは自動再試行しない。外部副作用のexactly-once保証を追加しない。

WebhookはP3、UI/成果物管理はP4。任意repo・外部PR・秘密注入・CDは対象外。
非信頼コードを扱う前に専用の使い捨てVM等の環境を別途設計・検証する。
