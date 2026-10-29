# 需要予測モデルを継続運用するMLOps基盤

`261014/plan_mlops.md`に沿った、UCI Bike Sharingを現行データとするMLOps学習プロジェクトです。Capital Bikeshareは別targetとして扱う移行専用profileを追加中です。単にモデルを学習するだけでなく、特徴量の時点制約、モデル登録、版固定の予測API、手動承認・差し戻し、遅延ラベルを使う監視までを一続きにします。

これは公開過去データを使った運用再現実験です。現在の需要を予測するサービスでも、実店舗の売上や配置を改善した実証でもありません。性能結果は実行後に記録し、未実行の結果は記載しません。

専門用語は[初学者向け用語集](docs/glossary.md)、出典・targetの切替と隔離方法は[データプロファイル](docs/data-profiles.md)を参照してください。

2026-10-28の[差分監査](docs/reviews/20261028-review.md)で、UCIの保存済み予測・誤差・PSIと現在の配信状態の整合を確認しました。新データ候補は最終評価時に再学習しており、選定時と同一artifactという証拠が不足しています。評価の再実行防止、新データAPI/監視、復旧実験も残るため、全計画完了や新候補の昇格可能状態とは扱いません。残作業は[計画書R01〜R10](../261014/plan_mlops.md#11-2026-10-28差分監査後の実行計画)にまとめています。

## 構成

- `src/bike_mlops/`: データ契約、時刻付き特徴量、学習、API、DB、監視、運用CLI。
- `configs/`: 時系列分割と特徴量契約。
- `configs/data-profiles/`と`compose.capital.yaml`: UCIとCapital Bikeshareのデータ/targetを別DB・別Compose projectへ分離する設定。Capital用modelとmonitor参照が未準備のため、移行実験専用です。
- `tests/`: 時刻リーク、欠損時間、入力重複、ストレージの回帰試験。
- `docs/`: データ・アーキテクチャ・運用判断。
- `compose.yaml`: MLflow、PostgreSQL、FastAPIの青緑2枠、Caddyを使うローカル運用構成。
- `data/`: 原本、DB、モデル成果物はGit対象外。証拠として必要な公開可能な集計レポートだけを個別に保存します。

## ローカルで始める

Python 3.12を使います。依存関係の取得にはパッケージインデックスへのネットワーク接続が必要です。

```bash
cd 261021
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e '.[dev]'
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

2026-10-28に候補版3を版2と2012年第3四半期で比較し、合格後に手動昇格しました。その後、独立した2012年第4四半期を版2・版3の両方でリプレイしました。このQ3/Q4とCapital Bikeshareの既評価期間は再利用禁止台帳に登録しています。新しい候補には未使用期間を含むデータ、新しいprotocol、validation終了後の判断時刻が必要です。

```bash
# 過去の実行例。Q3の再学習・選択には再利用しない。
# 新規候補は docs/runbook.md の「凍結protocolに基づく新規候補」を使う。
mlops-bikectl candidate --help
```

Composeによる運用構成は[実行・運用手順](docs/runbook.md)を参照してください。DBやMLflowを外部へ公開する設定は含めていません。

## プロジェクト上の制約

- 時刻はデータセット内のnaive datetimeとして扱い、出典にないタイムゾーン変換をしません。
- 予測対象時点に利用できる情報だけを特徴量にします。前日・前週の観測は時刻で完全一致結合し、行番号の`shift`は使いません。
- 実績値は観測時刻から3時間後に利用可能になるという実験仮定を置きます。原データの実遅延を意味しません。
- `casual`、`registered`、予測対象時間の`cnt`、実測天気は特徴量にしません。
- モデル版は整数の登録版で固定し、`champion`等の別名だけで配信版を決めません。
- 自動昇格はしません。データ・モデル・配信条件を満たした後、人が理由を残して切り替えます。
- `uci-bike-sharing`と`capital-bikeshare`の観測値やmodelを同じDB・モデル名・target契約として混ぜません。CapitalとUCIの指標も同じtargetの精度比較として扱いません。

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
- [x] 2026-10-27、MLflow 3でモデル登録元を古いrun artifact URIにしていたため、版1に`MLmodel`ファイルがなくAPIでロードできないことを特定。登録URIのコードを`log_model()`が返すURIへ修正。
- [x] 2026-10-27、修正版でrun `f55812c02dac4084a8185856cf7b4793`を実行し、モデル版`2`を登録。validation MAE 55.0728がベースライン63.9628を下回り`quality_gate_passed=true`。固定holdoutは再評価せず`null`。
- [x] モデル版2をGreen枠へ手動昇格。2026-10-27にdeployment ID `dded8d98-6e6c-4082-9419-df62cc08adb7`で完了。Green枠とCaddy入口の`/ready`が200、`/version`は版2・artifact SHA-256 `42bb5f3c7dc5a28d562d3fe2b01fe2aaf832af34ef5496b05b0fe65e9a79b337`・品質ゲート`passed`。
- [x] APIの通常予測保存・同一request ID再送動作を確認。2026-10-28に予測がHTTP 200、同一入力・同一request IDの再送も同じ応答でHTTP 200、入力を変えた再送はHTTP 409。
- [x] 候補版3を版2と2012年第3四半期で比較。候補MAE 62.416、版2 MAE 84.168、ベースラインMAE 83.692でゲート通過。
- [x] 版2・版3を同一の第4四半期2,168時間でAPIリプレイ。HTTPエラーは両版0件。全期間MAE/RMSEは版2が70.501/113.270、版3が59.403/91.941。比較レポートは[`data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json`](data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json)。
- [x] 版3を2026-10-28にBlue枠へ昇格。deployment ID `8a6a63f0-875a-4c5b-8c85-bc0fe1538d1e`、Caddy入口の`/ready`は200、model version `3`、quality gate `passed`。
- [x] PSIと直近週の誤差差を保存済み予測で調査。詳細と結果の確認場所は[`monitor-investigation-and-next-data.md`](docs/monitor-investigation-and-next-data.md)。
- [x] Capital Bikeshare公式月次データ2023-01〜2026-05を取得し、2023-01〜2026-04の時間別データをローカル生成。候補validationと最終評価の期間を固定。元データと派生CSVはライセンスと`.gitignore`に従いGit対象外。
- [x] 版3を基準に新データ候補を2025年上期validationで比較。候補MAE/RMSE `168.266 / 265.934`、版3 `389.762 / 608.320`、履歴baseline `208.061 / 329.878`で品質ゲート通過。MLflow登録・昇格は未実施。
- [x] 同じ学習設定で候補を再学習し、2025年7月〜2026年4月の7,294時間をout-of-time評価。候補MAE/RMSE `126.941 / 211.578`、版3 `326.387 / 518.660`、履歴baseline `181.185 / 296.180`。選定時と同じ候補artifactかは未証明。JSONレポートを保存し、候補登録・API切替は未実施。
- [x] 2026-10-29、現行計測CLIから合成candidateの隔離system testを完了。744観測・blocked登録・staging API・offline/API一致・retry/409・runtime init拒否・1,050要求・監査・独立数値再確認・MLflow exact-version証拠tagを検証。同値再実行は成功し異なるreport hashは拒否。p95 88.947msは人工データの結果で、operational gateは`not_evaluated`、deploymentは`blocked`。ログは[`work_log.md`](work_log.md)参照。

詳細な実行記録は[work_log.md](work_log.md)を参照してください。今回のvalidationとout-of-time評価期間はこの候補に使い終えています。今後の候補選択には新しい期間または別の外部データが必要です。データ品質監査で全期間集計を閲覧済みのため、未閲覧のblind holdoutではありません。2012年Q3/Q4は再利用していません。現在の稼働版は3です。
