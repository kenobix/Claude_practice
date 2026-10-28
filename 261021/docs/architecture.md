# アーキテクチャと運用判断

初学者向けの用語は[用語集](glossary.md)を参照してください。

## 開発プロファイル

Python 3.12、SQLite、ローカルMLflow tracking/artifact storeで、データ監査・学習・特徴量と台帳をCPU上で検証できます。デフォルトのDBと成果物はGit対象外の`data/`以下です。HTTP APIはFastAPI依存を追加した後に`uvicorn`で起動します。

動作確認したNumPy・pandas・scikit-learnの版を固定し、Python・Dockerの主要イメージも版付きにしています。FastAPI・MLflow・psycopgは互換範囲で指定し、学習runには実際に解決された全パッケージ版を記録します。

MLflowのscikit-learnモデルはskops形式で保存します。HistGradientBoostingRegressorが使う`sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor`だけを、エラー内容を確認したうえで明示的な信頼型に加えます。この許可はプロジェクトが生成したローカルモデル用で、外部や未信頼のモデルartifactには使いません。包括的な信頼許可は使いません。配信時はモデル成果物のSHA-256を登録情報と照合してからskopsで読み込みます。

MLflow 3では学習runの通常artifactとログ済みモデルの保存場所が分かれます。登録時は`log_model()`が返す`model_info.model_uri`を使い、`runs:/<run_id>/<name>`という古い保存場所を仮定しません。[MLflow 3の移行ガイド](https://mlflow.org/docs/latest/ml/mlflow-3)を参照してください。

## Composeプロファイル

`compose.yaml`はローカル専用で、PostgreSQLを2つのDB・2つのログインロールに分けます。

- `mlflow` DB / `mlflow_app`ロール: MLflowの実験・モデル登録メタデータ。
- `bikeops` DB / `bike_app`ロール: 観測・予測・配信操作・監視窓。
- Docker named volume: MLflowが管理するモデル成果物、PostgreSQLデータ。
- Caddy: 127.0.0.1:8080だけで待ち受ける入口。
- API: 青・緑を別ポートで起動し、モデル版は環境変数で固定。

外部公開の認証・TLSや常時運用のbackupサービスはこのローカル学習構成の範囲外です。元計画にあるローカルDB/artifactのbackup・別環境復元実験は未完了で、[計画書R08](../../261014/plan_mlops.md#11-2026-10-28差分監査後の実行計画)に残します。Composeのポートはloopbackに限定しています。

## APIと再送

- `/health`はプロセスの生存、`/ready`はモデルとDBの使用可否、`/version`は固定モデル版と成果物ハッシュを返します。
- `/observations`は実績と`available_at`を受け取ります。この実験では利用可能時刻が観測時刻+3時間と一致する場合のみ登録します。
- `/predict`は入力版と時刻を検査し、利用可能な観測から特徴量を作成します。予測行を保存できない場合は成功応答を返しません。
- リクエストIDは一意です。同じID・同じ入力の再送は保存済みの予測・元のモデル版を返し、入力の違うID再利用は409にします。ユニーク制約とトランザクションで並行要求を扱います。Capital stagingでは候補IDも予測行へ保存し、候補を切り替えた後の再送でも元のIDを返します。古い予測行には候補IDが記録されていないため`null`です。

## 手動の青緑切り替え

`deploy promote`はLinuxのプロセスロックを取り、登録版・品質ゲート・ready・版・成果物ロードを確認してから、未使用側のAPIを起動します。候補の固定予測が成功した後だけ、Caddyfileを一時ファイルから置換し、Caddyのvalidate/reloadを行います。入口の`/version`で切り替わった版を確認後に配信台帳と実行状態を確定します。途中で失敗したときは旧ルートへ戻し、処理段階を残します。

DBとCaddyは共通トランザクションではありません。中断後に`deploy recover`を実行し、実際の入口が旧版なら再試行可能と記録し、新版なら台帳を確定します。未知の版を返す場合は自動変更せず停止します。旧API枠は次回候補を起動するまで残ります。無停止を保証する構成ではありません。

通常の昇格はMLflowモデル版に付いた`quality_gate=passed`が必要です。差し戻しは運用履歴にある以前の版だけに限定し、理由と承認者を必須で保存します。監視結果や学習完了をきっかけに自動昇格する処理はありません。

## 品質ゲートと監視

2026-10-28の[差分監査](reviews/20261028-review.md)は当時のUCI運用と移行候補を記録したものです。Capital初回候補の現在の基準と未完了作業は[初回ゲート](capital-bootstrap-gate.md)と計画書R06〜R09を参照します。

- UCIの初回学習では、検証期間のMAEが前日同時刻ベースラインを厳密に下回ることを品質条件とします。未達版も診断目的で登録されますが、通常の昇格CLIは拒否します。
- `train --evaluate-final-holdout`で初回だけ固定最終ホールドアウトを報告します。既定の`train`とrolling candidateはこの期間を評価しません。固定ホールドアウトはモデル登録・昇格条件に含めません。
- Capitalの`candidate`は凍結protocolと学習締切までに到着したラベルを使い、未登録の候補bundleを作ります。初回bootstrapはincumbentを使わず、validationと独立finalの各期間で候補MAEが履歴baselineより小さく、RMSEがbaseline以下であることを確認します。
- UCIの2012年第3四半期を候補評価、第4四半期を履歴リプレイに使った実行は過去の記録です。これらの期間は新候補の選定に再利用しません。
- API性能は別途運用計測が必要です。この実装の主監視は記録された予測、遅延ラベル、分布です。
- リプレイは7日窓で予測数・ラベル到着率・MAE/RMSEを集計します。24件未満では誤差値を判定に使いません。
- ラグ値のPSI、ラグ欠測率、曜日・時間帯等の入力分布を別々に記録します。PSI 0.2以上と検証時モデルMAEの1.25倍超過は調査用の閾値です。少数ラベルの不確実性や合否を表すものではなく、自動昇格には結び付けません。
- UCIの従来経路は`--diagnostic`による未達版の試験配信を区別します。Capitalの通常配信は全gateと配信許可を要求し、`--diagnostic`では未達条件を上書きできません。
