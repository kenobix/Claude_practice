# 需要予測モデルを継続運用するMLOps基盤

`261014/plan_mlops.md`に沿った、UCI Bike Sharingを現行データとするMLOps学習プロジェクトです。Capital Bikeshareは別targetとして扱う移行専用profileを追加中です。単にモデルを学習するだけでなく、特徴量の時点制約、モデル登録、版固定の予測API、手動承認・差し戻し、遅延ラベルを使う監視までを一続きにします。

これは公開過去データを使った運用再現実験です。現在の需要を予測するサービスでも、実店舗の売上や配置を改善した実証でもありません。性能結果は実行後に記録し、未実行の結果は記載しません。

専門用語は[初学者向け用語集](docs/glossary.md)、出典・targetの切替と隔離方法は[データプロファイル](docs/data-profiles.md)を参照してください。

旧PostgreSQL予測列の移行前は[T08の検証と復元](docs/prediction-precision-migration.md)を参照してください。保存値を保持してもAPIの十進表示が変わる場合があります。

配信中断後は[配信復旧の状態・承認metadata](docs/deployment-recovery.md)に従って入口/runtime/台帳を照合します。実Caddy/API/PostgreSQLと合成モデルを使う隔離受入も用意しています。実Capital/MLflow/skopsの復元証拠とは区別します。

MLflow決定の部分書込・応答喪失・競合は[T13aの使い捨てHTTP試験](docs/capital-decision-http.md)で確認します。失敗応答だけで保存失敗と断定せず、読戻しと同一要求retryを検証します。合成fixtureを実Capitalの承認証拠へ流用しません。

Capitalの運用測定を停止した場合は[中断・タイムアウトの確認手順](docs/measurement-interruption.md)を使います。要求ごとの永続記録から送信意図と応答確認を調べ、DBにまだ見えない要求を未送信と断定しません。

2026-10-28の[初回差分監査](docs/reviews/20261028-review.md)では、UCIの保存済み予測・誤差・PSIを照合し、当時の旧Capital実験は最終評価で再学習したため同一artifactの証明にならないと確認しました。その後、同一artifactを評価する候補bundleと消費台帳、隔離計測を合成データで実装しました。旧実験の結果を後から新しい合格証拠へ変更していません。

2026-10-30の[最新checkpoint差分監査](docs/reviews/20261030-checkpoint-review.md)では、前回監査以降の28コミットと最新221 tests/Docker/PostgreSQL CIを確認しました。同監査のP0に対し、報告書本体を束ねて照合する[配信証拠bundle](docs/capital-deployment-evidence.md)と決定schema 2を実装し、全241 testsが成功しました。監視・復旧等の実証がない候補はblockedを保ちます。実Capital候補の承認・通常配信は未実施です。優先度・難易度とSol/GPT-6 Lunaの担当推奨は[計画書11.6](../261014/plan_mlops.md#remaining-work-20261030)を参照してください。

## 構成

- `src/bike_mlops/`: データ契約、時刻付き特徴量、学習、API、DB、監視、運用CLI。
- `configs/`: 時系列分割と特徴量契約。
- `configs/data-profiles/`と`compose.capital.yaml`: UCIとCapital Bikeshareのデータ/targetを別DB・別Compose projectへ分離する設定。実Capital modelと参照artifactは未生成のため、移行実験専用です。参照の保存・検証仕様は[学習参照とPSI v2](docs/training-monitor-reference.md)を参照してください。
- `tests/`: 時刻リーク、欠損時間、入力重複、ストレージの回帰試験。
- `docs/`: データ・アーキテクチャ・運用判断。Capital監視の合成受入シナリオと期待時刻は[監視受入基準](docs/monitoring-acceptance.md)に記録しています。保存未確定時の[読取照合契約](docs/monitor-reconciliation.md)に沿う`monitor-inspect`のSQLite版T06aとPostgreSQL版T06bを実装しました。読取専用snapshot・型別比較・中断/遅延commitの[検証記録](docs/reviews/20261030-t06b-checks.json)と[操作手順](docs/runbook.md)を参照してください。
- GitHub Actionsの`synthetic-system` jobは、独立した使い捨てDocker/PostgreSQL検証として、既存のmonitor persistence保存試験も実行します。失敗を隠さずログを`bike-mlops-docker-integration-logs` artifactに含めます。対象は合成DBだけで、実運用監視や配信の証拠ではありません。 T15の[CI・artifact・cleanupの確認結果](docs/reviews/20261101-t15-checks.json)を保存しています。
- T16では同じ使い捨てsynthetic-system実行の計測plan/report/operational ledger/request journalだけを固定allowlistのZIPへbyte-preservingで保存し、archiveだけを入力とする別Python processでjournalとreportを照合して1,000 measured requestsのp95を再計算します。warmup 50件は除外します。これは合成データの保存値の再計算で、実データ精度・DB監査・時計資格・配信承認の証明ではありません。CI artifactの保存対象と限界は[計測証拠アーカイブ](docs/measurement-evidence-archive.md)を参照してください。
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

- [x] 2026-10-30、T01とT02の必須証拠schema/拒否処理を実装。承認後の証拠変更・不正p95・本文破損・監視/復旧証拠不足を拒否。一時SQLite MLflowの保存/取得/合成承認を確認。全241 tests成功。実監視/復旧とHTTP障害の検証は残ります。

- [x] 2026-10-30、T04の学習参照・PSI v2を候補保存/MLflow登録/monitor CLIへ接続。学習行・bins・policyをhashで結び、別参照・別版・特徴量変更を拒否。全255 tests成功。実Capital監視と全監視受入は後続課題です。
- [x] 2026-11-02、T16を完了。Sol reviewで検査漏れ・負例を補強し、実装2492abcのCIで399 testsと全system/PG stepが成功。追加3 artifactのhash、4原本、archive-only別processの1050応答/1000 measured/p95再計算とcleanupを確認しました。[CI証拠](docs/reviews/20261102-t16-ci-checks.json)。合成結果であり、実候補性能・時計資格・配信承認の証拠には使いません。

詳細な実行記録は[work_log.md](work_log.md)を参照してください。今回のvalidationとout-of-time評価期間はこの候補に使い終えています。今後の候補選択には新しい期間または別の外部データが必要です。データ品質監査で全期間集計を閲覧済みのため、未閲覧のblind holdoutではありません。2012年Q3/Q4は再利用していません。現在の稼働版は3です。

T16の[Sol review記録](docs/reviews/20261102-t16-sol-review.json)には、検査漏れ修正・専用23件/全399件のlocal検証・初回失敗を記録しています。実装2492abcの[CI artifact/cleanup確認](docs/reviews/20261102-t16-ci-checks.json)まで完了しています。

T07bでは[実MLflow/skops・DBの対応復元](docs/deployment-recovery.md)を合成fixtureへ追加しています。通常CLIの[段階表示・Docker timeout・旧stateの読取診断](docs/deployment-observability.md)も参照してください。APIの承認metadata提示と全体timeoutは残条件で、T17b全体は未完了です。

[証拠の入口と5分の説明手順](docs/evidence-guide.md)に、task・実装commit・CI・索引と、説明できる結果の範囲をまとめています。

2026-11-02、T07bは実装`2414b20`の[CI41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218)で419 tests・実MLflow/skops/対応DBの別3volume復元・破損拒否・archive再計算・cleanupまで成功しました。[CI証拠](docs/reviews/20261102-t07b-ci-checks.json)。T17b残件とT07aのCaddy復旧CIの具体的な受入条件は[Sol向け引継ぎ](docs/t17b-sol-handoff.md)にまとめています。

2026-11-03更新: 新規配信のAPI・state・台帳の操作ID照合と、CLI promote900秒/recover300秒の全体deadline、未確定時の手順は[配信診断](docs/deployment-observability.md#2026-11-03-t17b-操作の照合とcli全体の待機上限)を参照。最終受入状況は[検証索引](docs/reviews/20261103-t17b-t07a-checks.json)を確認する。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](docs/reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。

## 時計診断の追加記録

2026-11-03の[追加読取](docs/clock-followup.md)と[検証索引](docs/reviews/20261103-clock-followup-checks.json)に、
自然future拒否と現在のtimesyncd stepの同時照合、T03回帰、未採取範囲を記録した。
初期namespaceのPHC/socket所有の追加採取は[管理者手順](docs/clock-owner-admin-read.md#chrony-resources-admin)を参照。
時計修復・資格確認・実Capital候補の採否は未完了。

CIの独立保守は[artifact action更新](docs/ci-artifact-maintenance.md)を参照。
実装`5a687a7`の[CI41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089)で463件・隔離system・16member ZIP/digest・再計算・cleanupまで成功した。
この記録と時計の新しい用語は[用語集](docs/glossary.md)・[証拠入口](docs/evidence-guide.md)から確認できる。

298c6c8以降の[chronyd内部計算の観測](docs/chrony-runtime-control.md)では、対応debugと非飽和時の計算を照合した。
tick11000時の原因・時計資格は未解決。[MLflow警告の調査](docs/mlflow-orm-compatibility.md)も行い、上流修正待ちと現在の検証結果を区別している。

追加readerの実装`f45733a`は[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704)で全464件・隔離system・実artifact16member/hash・再計算・cleanupまで成功。
[CI記録](docs/reviews/20261103-chrony-runtime-ci-checks.json)を参照。内部数値のlocal診断と時計資格は別の確認である。

2026-11-04追補: [initdの時計setterとDocker Desktop管理層](docs/initd-owner-correlation.md)を実exe/親/namespaceで照合しました。
[T10の入力準備・開始手順](docs/t10-input-readiness.md)では手元45原本と固定窓を確認しています。
10月原本は今回も404で、正式protocol・fit・時計資格は未完了です。[今回の索引](docs/reviews/20261104-clock-readiness-checks.json)に全試行を残しています。

891a420以降は[backend時刻取得経路](docs/backend-time-path-review.md)を保存済みELFから静的照合し、
[T10の左端buffer](docs/t10-input-readiness.md)に202212を採用・取得して隣月2fileを監査しました。
新たな時計書込やbackend応答の観測ではなく、10月原本・管理者PHC/UDP採取・正式protocol/fitは引き続き待機です。
[追加索引](docs/reviews/20261104-backend-boundary-checks.json)で不採用解析・失敗と回帰8件の結果も確認できます。

24c30c0以降は[修復後の時計資格の計測設計](docs/clock-qualification-plan.md)を追加しました。
固定期間・bracketによる進行率・独立外部UTC・欠測/失効条件を具体化した設計であり、修復や資格合格の記録ではありません。

続いて[保存済み進行率phaseのoffline照合](docs/clock-rate-review.md)を実装しました。
指定JSONの固定窓/上下限/hashを検算するcomponentであり、時計資格全体の状態は常に`not_evaluated`です。
[実装f26b334のCI](docs/reviews/20261104-clock-rate-ci-checks.json)では全482件と全隔離system、artifact16 member/SHA・archive-only検算・cleanupまで確認しました。

2026-11-05、[全観測期間のoffline照合](docs/clock-campaign-review.md)を追加しました。
通常/再開後の4区間をpolicy・事前plan SHAと結合し、外部UTC・QPC校正・identity・欠測・厳密futureを再計算します。
数値/構造が揃っても時計資格はincompleteのままで、採取の真正性・独立源・修復/保守・人のreviewを省略しません。
[今回の試行と限界](docs/reviews/20261105-clock-campaign-checks.json)を参照してください。実時計の修復・資格判定やfitは未実施です。

補強後の実装`f6b3cad`は[最終CI索引](docs/reviews/20261105-clock-campaign-ci-checks.json)の全519件・全system/16 artifact/hash/cleanupで確認済みです。
次の[採取protocol契約](docs/clock-campaign-acquisition.md)にはnonce/sequence、identity元資料、独立源の不確かさ、負例/cleanupを記述しています。
契約は採取器の実装や実時計資格の取得ではありません。

2026-11-05追補：[fake readerから原本→全campaign→offline CLI](docs/clock-fake-acquisition.md)を接続しました。
送信前intent、nonce/sequence、identity元資料/6部品SHA、独立参照を模した不確かさ区間、
中断/IO失敗と子process回収を使い捨て環境で検証します。
[独立源の選定review](docs/clock-source-readiness.md)は採用保留で、合成成功でも実時計資格はincompleteです。
[今回の試行索引](docs/reviews/20261105-clock-acquisition-checks.json)を参照してください。

最終実装`a5c1605`は[CI42037278421](https://github.com/kenobix/Claude_practice/actions/runs/42037278421)で全549件（fake採取30件を含む）と全隔離systemが成功しました。
[最終証拠索引](docs/reviews/20261105-clock-acquisition-ci-checks.json)でartifact16 member/hash・archive-only再計算・cleanupを確認しています。
fake原本の代表はlocal保存で、独立源や実時計資格を認定したものではありません。

2026-11-06追補：[fake QPC固定周期/phase deadline](docs/clock-fixed-schedule.md)を追加しました。
`--schedule`で合成予定表を選び、遅いwakeは欠測、遅い応答は原本と損失を保持します。
catch-upや観測延長をせず、欠測と同じphaseのUTC/identity/future違反も隠しません。
独立UTC/elapsed源は未選定で、実host deadline/固定負荷や実時計資格の完成ではありません。

固定周期の最終実装`c1c5dcb`は[CI42126656283](https://github.com/kenobix/Claude_practice/actions/runs/42126656283)で全568件（専用19件）・全systemが成功しました。
[最終索引](docs/reviews/20261106-clock-scheduler-ci-checks.json)で16 artifact/hash・archive-only再計算・cleanupを照合済みです。
実時計資格と実候補採否は未達です。新用語は[用語集](docs/glossary.md#合成時計採取の固定周期で使う用語)へ補足しました。

2026-11-06：[T09の10月原本取得・構造/右端境界照合](docs/t09-october-acquisition.md)を完了しました。
固定範囲の47原本が揃いました。T10の正式集計/protocol凍結、実時計資格、学習・採点は別の未完了条件です。

[T10のsnapshot作業](docs/t10-snapshot-review.md)では、全原本を独立parser/外部sortで照合し、
重複205件を含む収支を確認しました。集計の重みを保つ改善bae010aは[CI569件/全system/cleanup](docs/reviews/20261106-t10-grouping-ci-checks.json)で受入済みです。
実snapshot/正式protocolはまだ作業中で、実fit・採点は開始していません。

2026-11-08：T10の[正式snapshot・事前protocol](docs/t10-snapshot-review.md)を保存しました。
47原本のSHAと32,849全hourを独立照合し、train29,919/validation1,464/final1,461を固定しています。
ログ・確認用の読取コマンドも同文書にあります。実fit・採点・Capitalモデル登録は未実施で、MLflowに新しい精度結果はまだありません。
時計資格等が未達のため、この事前固定だけで学習を開始しません。SQL修正9147027の[CI570件・全system・artifact/hash/cleanup](docs/reviews/20261108-t10-snapshot-checks.json)まで確認済みです。

## 追加の時計入力と合成原本の持ち運び（2026-11-08）

T09/T10の静的受入後も、独立時計源と時計資格が未達のため実fit/採点は保留している。
次に必要な管理者ログ・源の真正性/依存関係/不確かさの資料は[入力一覧](docs/clock-input-request.md)、
合成rawを別processで再計算する方法は[原本bundle](docs/clock-raw-bundle.md)を参照する。
bundleの数値が良好でも時計資格は`incomplete`。[今回の検証索引](docs/reviews/20261108-clock-bundle-checks.json)で試行/失敗/CI状態を区別する。
