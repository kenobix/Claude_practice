# 需要予測モデルを継続運用するMLOps基盤

`261014/plan_mlops.md`に沿って、UCI Bike Sharingの時間別貸出台数を使うMLOps学習プロジェクトです。単にモデルを学習するだけでなく、特徴量の時点制約、モデル登録、版固定の予測API、手動承認・差し戻し、遅延ラベルを使う監視までを一続きにします。

これは公開過去データを使った運用再現実験です。現在の需要を予測するサービスでも、実店舗の売上や配置を改善した実証でもありません。性能結果は実行後に記録し、未実行の結果は記載しません。

## 構成

- `src/bike_mlops/`: データ契約、時刻付き特徴量、学習、API、DB、監視、運用CLI。
- `configs/`: 時系列分割と特徴量契約。
- `tests/`: 時刻リーク、欠損時間、入力重複、ストレージの回帰試験。
- `docs/`: データ・アーキテクチャ・運用判断。
- `compose.yaml`: MLflow、PostgreSQL、FastAPIの青緑2枠、Caddyを使うローカル運用構成。
- `data/`: データ、DB、モデル成果物。Gitへ追加しません。

## ローカルで始める

Python 3.12を使います。依存関係の取得にはパッケージインデックスへのネットワーク接続が必要です。

```bash
cd 261021
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
mlops-bikectl data download
mlops-bikectl data audit
python -m unittest discover -s tests -v
```

Windows上のWSLから作業するときの起動方法と、Composeを使った次の作業は[実行・運用手順](docs/runbook.md)を参照してください。

学習はデータ監査の完了後に行います。最初の指定実行だけ固定最終評価期間を報告し、以降の再学習では触れません。

```bash
mlops-bikectl train --evaluate-final-holdout
mlflow ui --backend-store-uri sqlite:///data/mlflow.db --host 127.0.0.1
```

継続学習候補は明示した既存モデル版と同じ将来期間で比べます。既定の候補は2012年7月時点までに利用可能なラベルだけで学習し、2012年第3四半期で評価します。登録しても昇格はされません。評価後の第4四半期は別の履歴リプレイに残します。

```bash
mlops-bikectl candidate --incumbent-version 1
```

Composeによる運用構成は[実行・運用手順](docs/runbook.md)を参照してください。DBやMLflowを外部へ公開する設定は含めていません。

## プロジェクト上の制約

- 時刻はデータセット内のnaive datetimeとして扱い、出典にないタイムゾーン変換をしません。
- 予測対象時点に利用できる情報だけを特徴量にします。前日・前週の観測は時刻で完全一致結合し、行番号の`shift`は使いません。
- 実績値は観測時刻から3時間後に利用可能になるという実験仮定を置きます。原データの実遅延を意味しません。
- `casual`、`registered`、予測対象時間の`cnt`、実測天気は特徴量にしません。
- モデル版は整数の登録版で固定し、`champion`等の別名だけで配信版を決めません。
- 自動昇格はしません。データ・モデル・配信条件を満たした後、人が理由を残して切り替えます。

## 進捗

- [x] データ契約・時系列実験条件・学習・API・モデル版固定・昇格/差し戻し・監視の実装。
- [x] 時刻リーク、欠損時間、DB再送、遅延ラベルの回帰テストを整備。
- [x] 学習パイプラインを人工データで動かす合成スモーク確認。これはUCIの性能結果ではありません。
- [x] UCI原本取得と実データ監査。2026-10-21確認: 17,379行、2011-01-01 00:00〜2012-12-31 23:00、重複0、欠損時間165。CSV SHA-256は`e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`。欠損は監査で報告され、行の補完や削除はしていません。
- [x] WSL 2上のDocker Desktop接続確認。2026-10-21確認: `docker version`でClient/Serverを確認し、`docker run --rm hello-world`が成功。Composeのバージョン表示も確認済み。
- [x] テストスイート13件成功（2026-10-21、ユーザー実行）。その後のskops保存・モデル読み込み変更は未再実行。
- [x] Compose設定検証（`docker compose config -q`）成功（2026-10-21）。
- [x] Compose上のPostgreSQL healthy、MLflow startedを確認（2026-10-21）。
- [x] 実データで初回学習と固定ホールドアウト評価を実行。初回runはMLflow/skopsの信頼型検査で登録に失敗したが、holdoutのモデルMAE 72.5362、RMSE 111.5060はMLflow artifactに記録された。
- [x] 信頼型を限定して再学習・登録。2026-10-21のrun `0db7aa07461b4b8785145156d8de9a66`はvalidation MAE 55.0728（ベースライン63.9628）、RMSE 88.6249（ベースライン107.8187）で品質ゲートを通過し、`bike-demand`のモデル版`1`として登録された。再runでは固定holdoutを再評価せず、`final_holdout`は`null`。
- [x] runtime initとCompose起動は成功。2026-10-27に観測17,379件を登録し、API青緑枠・Caddyも起動した。
- [ ] APIで登録版1をロードできることを確認する。現在MLflowがDocker内部の`mlflow:5000` Hostヘッダーを許可せず403を返すため、Composeの`--allowed-hosts`設定を追加。再作成後にreadinessを再確認する。
- [ ] MLflowを使った継続候補の登録・比較・第4四半期リプレイ。

未実行の項目は、完了したように表現しません。詳細な実行記録は[work_log.md](work_log.md)を参照してください。次は既存のモデル版1を読み込んでreadinessと配信版を確認します。再学習や固定holdoutの再評価は不要です。
