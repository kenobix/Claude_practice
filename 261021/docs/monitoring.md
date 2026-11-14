# 監視・照合・運用測定の契約

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [Capitalの学習参照とPSI v2](#training-monitor-reference)
- [Capital監視の合成受入基準（T05）](#monitoring-acceptance)
- [監視保存の読取照合契約（T06）](#monitor-reconciliation)
- [Capital運用測定の中断・タイムアウト（T03）](#measurement-interruption)
- [T16: 合成計測証拠の保存と再計算](#measurement-evidence-archive)

<a id="training-monitor-reference"></a>

## training-monitor-reference

<a id="training-monitor-reference--capitalの学習参照とpsi-v2"></a>

### Capitalの学習参照とPSI v2

2026-10-30、計画T04の候補保存・登録・監視CLI接続を実装し、T05の10シナリオと2境界の合成受入をSol review後に確認した。実Capital候補はまだ作成しておらず、検証は合成データで行った。T06の異常保存照合、T02の実証報告producer、実候補/HTTP MLflowからの監視は後続課題である。[残作業計画](../../261014/plan_mlops.md#remaining-work-20261030)を正規の一覧とする。

<a id="training-monitor-reference--何を固定するか"></a>

#### 何を固定するか

PSIは「学習時と現在の入力分布がどの程度違うか」を調べる指標。比較の物差しとなる数値の区切り（bins）を後のデータに合わせて変えると、結果の意味が変わる。Capitalでは、実際にfitした学習行と特徴量だけから参照を作り、候補モデルと一緒に保存する。UCIの既存PSI計算・保存済み履歴は維持する。

| 固定する項目 | 値・結び付き |
|---|---|
| policy | `capital-training-monitor-v1`。新規protocolの`monitoring_reference_policy`に本文とSHAを保存。 |
| algorithm | `psi-v2-constant-bins`。Capital monitorでは明示指定が必須。 |
| 特徴量 | `lag_24`、`lag_168`、`hour`。候補の全9特徴量の順序・値も別hashに保存。 |
| 分布の区切り | 学習行の有限値から10分位を要求。重複する区切りはまとめる。定数参照は「小さい・等しい・大きい」の3区分。 |
| 最少件数 | 参照・監視対象それぞれ有限値24件。不足時にPSI=0を返さない。ラベル最少件数とは別。 |
| 調査閾値 | PSI 0.2以上、誤差はvalidation MAEの1.25倍を厳密に超過。採用の合否や自動昇格の条件ではない。 |
| 学習行集合 | timestampとcntの既存row-key SHA、実際の期間・件数・締切・ラベル遅延。 |
| 出典 | CSV/manifest/protocolのSHA、profile/target/feature契約、元candidateモデルファイルのSHA。 |

`build_capital_protocol`がpolicyを固定する。現行Capitalの`train_candidate`はpolicyがない、または現行仕様と異なるprotocolを、fit・消費台帳更新の前に拒否する。過去のprotocolは読取検証用に残し、書き換えて格上げしない。新規protocolを作れるかは未使用期間・原本・その他のT10条件も満たす必要がある。

<a id="training-monitor-reference--保存形式と流れ"></a>

#### 保存形式と流れ

候補保存のstaging directoryで`model.skops`のSHAを求めてから`monitor-reference.json`を作る。参照ファイルのdescriptorをcandidate identityへ追加し、候補IDを計算した後、候補directory全体を公開する。参照hashと候補IDの循環を避けるため、参照の中には候補IDを入れず、元モデルSHAなどの学習identityを入れる。

参照schema 1は次の内容を持つ。

- `kind=capital-training-monitor-reference`、policy本文、identity。
- `training_window`: 実際の学習行の最初、最後の次の時刻、最終ラベル利用可能時刻、遅延時間、行数。欠損時刻を補って連続行として扱わない。
- `training_features_sha256`: 全9特徴量の列順と行列。NaNはJSON nullへ変換し、Infinityは拒否する。
- `features`: 3特徴量それぞれのPSI v2 bins・件数・missing/nonfinite件数・状態・component hash。
- `feature_missing_rates`: 学習時の特徴量欠測率。
- `reference_sha256`: 自分自身のhash項目を除いた本文のSHA。

JSONはキー順ソート、空白なし、UTF-8、末尾改行1個でhash化する（`capital_deployment_evidence.canonical_bytes`）。PSI componentの自己hashは既存PSI v2方式（末尾改行なし）を維持する。candidate reportの`monitor_reference`には正規path、元ファイルSHA、本文SHA、algorithm、schemaを入れる。

参照を含まない歴史的候補のIDは変更しない。その候補のCapital監視は参照不足として拒否し、後から同じIDの参照を作って補わない。新しいCapital候補の保存には、正確なtraining frame/featuresが必要である。

最終評価では、参照がある候補は元CSVの学習行から参照を再構成し、reserved final消費台帳を開始する前に照合する。これは保存済み候補の検査であり、候補の再fitやfinalを使うbins選択ではない。

staging登録では参照ファイルを検査し、登録版の元runに`evidence/monitor-reference.json`を保存する。登録tagは`monitor_reference_sha256`、`monitor_reference_content_sha256`、`monitor_reference_path`、`monitor_psi_algorithm`。元モデルファイルのSHAと登録artifact directoryのSHAは役割が違い、同一視しない。

<a id="training-monitor-reference--監視コマンドでの照合"></a>

#### 監視コマンドでの照合

Capitalの監視は、同じtracking/registry URIのexact versionから参照と`evidence/candidate-report.json`を取得する。次の検査に失敗すると監視reportを書かない。

1. READY版・run・profile/target/feature契約・CSV・元モデルSHA・候補ID・protocolを照合。
2. candidate reportのファイルSHAとcandidate identityを再計算。
3. manifestの元ファイルSHAと参照descriptor・本文SHAを照合。
4. 元CSVから**学習行だけ**を再選択し、行hash・特徴量hash・bins・件数・期間・欠測率を保存参照と一致確認。監視/validation/finalの分布を区切り選択に使わない。
5. 取得前後のversion/run/tag変化と、読込中のCSV変化を拒否。
6. DBの保存済み予測のcandidate、登録artifact、profile/target、model name/version、feature契約、lag出典hashと全特徴量値を照合。

この検査では元学習行から区切りを再計算して一致を確かめるが、採用する区切りは保存済み参照のもの。照合に失敗したとき、新しい区切りへ置き換えて続行しない。

<a id="training-monitor-reference--操作と結果の場所"></a>

#### 操作と結果の場所

これは新しい実候補と保存済みリプレイ予測が揃った後の例。現在のUCI版3や使い終えた評価窓へ実行する指示ではない。261021から実行する。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  ./scripts/bikectl monitor \
  --csv data/derived/capitalbikeshare-next/hourly.csv \
  --manifest data/derived/capitalbikeshare-next/manifest.json \
  --replay-id '<保存済みCapitalリプレイID>' --model-version '<exact version>' \
  --psi-version psi-v2-constant-bins
```

Capital用DB接続を事前に設定する。`scripts/bikectl`のprofile別port設定と`.env`の取扱いは[runbook](runbook.md#runbook)を参照する。監視期間はリプレイreportを継承し、両方を指定した`--start/--end`による部分期間も使える。manifest省略時はCSVと同じdirectoryの`manifest.json`。UCIの呼び出しは従来どおりで、明示する場合は`--psi-version legacy-uci-v1`。

結果は`data/reports/capital-bikeshare/monitor-<replay>-v<version>-<run UUID>.csv`と同名JSON、`.complete.json`、Capital DBの`monitor_windows`に保存する。JSONには参照出典/hash/policyとlatest windowの`psi_algorithm`、`training_reference_sha256`、各特徴量の`psi_details`を含む。CSVには窓ごとの詳細を含む。DBの`psi_json`は従来の特徴量別PSI数値、`missing_json`は欠測率を保持する。DBの列を新設して参照本文を入れる変更はしていない。run IDとsummary/markerを合わせて読む。

これらの監視結果はMLflow UIへ自動登録しない。MLflow UIでは候補runのArtifactsにある参照とcandidate reportを確認でき、監視の全窓は上記ローカルCSV/JSONで確認する。markerがない保存はDB/fileのcommitが未確定であり、T06の照合対象になる。

<a id="training-monitor-reference--状態の読み方と後続の受入条件"></a>

#### 状態の読み方と後続の受入条件

| 観測 | 状態・意味 |
|---|---|
| 有限値24件未満 | componentは`insufficient_current_data`。参照側が不足なら`insufficient_reference`。 |
| 値が全欠測 | `no_current_data`または`no_reference`。0 PSIと同じ意味にしない。 |
| Infinityなど | `invalid_current_values` / `invalid_reference_values`、窓は`invalid_values`。 |
| 参照が小標本/欠落 | 窓は`reference_unavailable`。ファイル自体が欠落/変更されていれば計算前に拒否。 |
| 誤差の比較基準がない | ラベル数が足りる窓は`performance_reference_unset`。異常なしと断定しない。 |
| 分布だけが変化 | `investigate_distribution_change`と誤差statusを独立に記録。 |

現行UCIの定数参照は変化を0とすることがある。過去の数値は維持し、Capital v2では定数から上下に変化した場合を検知する。今回の合成testはこの違い、24件不足、欠測、非有限、基準未設定、参照の意味を変えて全hashを再計算しても元学習行との照合で拒否することを確認した。実CLIからSQLiteへ27窓を保存し、24ラベルの最終窓とmarkerを確認した。

T05では、時点ごとの期待窓・母数・警告・検知時刻を先に固定し、分布のみ/誤差のみ/両方、ラベル遅延/停止/重複、欠測増加をCLIまで通す。今回の部分ケースだけで、T02のmonitoring 6項目すべてをpassedにしない。T06のDB/file読取照合、実候補とHTTP MLflowでの監視、実証producerは未完了。

T05の具体的な受入期待値とSol reviewの判断は[Capital監視の合成受入基準](monitoring.md#monitoring-acceptance)を参照。fake registryと一時SQLiteを通る10ケースと2境界を追加し、対象26件13.348秒、全277件127.327秒がlocalで成功した。保存したCSV/JSON/markerとDB全窓の値・hashも照合した。[CI 41005083367](https://github.com/kenobix/Claude_practice/actions/runs/41005083367)でも全277件35.217秒と隔離Docker/両PostgreSQL/cleanupが成功した。[全試行の証拠](reviews/20261030-t05-checks.json)には既存T03/T13の一度ずつの失敗も残し、原因を残課題として扱う。これはCLIの合成経路の受入であり、実MLflow/Capital候補の監視証拠ではない。

<a id="monitoring-acceptance"></a>

## monitoring-acceptance

<a id="monitoring-acceptance--capital監視の合成受入基準（t05）"></a>

### Capital監視の合成受入基準（T05）

この文書は、`capital-training-monitor-v1` の既存仕様を変えずに、監視CLIが合成データで期待どおりの窓・件数・警告時刻を記録するか確認するための基準である。実Capitalのデータ・MLflow・APIを使った実証ではない。検証用registryは保存済みcandidate/referenceを返すfake、操作DBは一時SQLiteを使う。

<a id="monitoring-acceptance--固定済みの共通ルール"></a>

#### 固定済みの共通ルール

- 評価時計は1時間ずつ進む。各時刻 `t` の予測窓は `(t - 168 hours, t]`、monitor periodは開始時刻を含み終了時刻を含まない。
- ラベルは `available_at <= t` のときだけ採点する。Capitalの通常到着時刻は予測対象時刻の3時間後。
- 誤差の算出には同じ窓の採点済みラベルが24件必要。24件未満では `quality_status=insufficient_labels`、MAE/RMSEはnull。
- PSI v2は固定したtraining referenceを使い、reference/currentとも24件以上の有限値が必要。最大PSIが0.2以上なら `distribution_status=investigate_distribution_change`。
- 誤差調査は `MAE > validation_MAE * 1.25` のとき `quality_status=investigate_error_increase`。同値は調査警告にしない。誤差基準が未設定なら、24ラベル到着後に `performance_reference_unset` と記録する。
- 分布警告と誤差警告は別々に記録する。片方の警告からもう片方を推定しない。
- 欠測率は値として記録するが、既存policyに欠測率の警告閾値はない。PSIが有限標本不足で計算できないときは `insufficient_data` 等の明示状態にする。T05で新しい欠測警告閾値は導入しない。

<a id="monitoring-acceptance--cli受入シナリオ"></a>

#### CLI受入シナリオ

共通の時計開始は `2024-01-18 00:00:00`。標準終了は `2024-01-19 03:00:00`（終了を含まず27窓）。合成学習行と監視対象の実績は一定値10、validation MAEは1.0。分布のみ・定数・誤差基準未設定・欠測増加の予測は10、誤差悪化・両方・追加遅延は12（MAE 2.0）、停止・小標本の予測は11とする。監視対象は1月18日の24時間分の予測で、終了後も168時間窓内に残る。検出時刻は初めて条件を満たす `window_end` とする。

| ケース | 合成変更 | 期待窓・母数 | 期待状態／検出時刻 |
|---|---|---|---|
| 分布変化のみ | 1月17日の実績を20、1月18日の予測を実績と同じ10にする。lag_24のみtraining referenceの10から変わる。 | 1月18日23時の窓で24予測・lag_24有限値24件。誤差は24ラベルが揃う1月19日02時に初めて評価可能。 | PSI調査は1月18日23時に開始。1月19日02時も `within_review_threshold`。 |
| 誤差悪化のみ | 入力分布は変えず、1月18日の予測を12にする。 | 1月19日02時に24ラベル、MAE 2.0。 | 1月19日02時に `investigate_error_increase`。同窓の分布は `no_threshold_crossing`。 |
| 両方 | 分布変化のみの入力と、予測12を併用。 | 分布は1月18日23時、誤差は1月19日02時に各々条件成立。 | 1月19日02時に独立した分布・誤差の両方の調査状態。 |
| 追加ラベル遅延 | 通常の3時間にさらに3時間を加え、利用可能時刻を対象時刻+6時間にする。 | monitor periodを1月19日06時まで延長して30窓。24件目は1月19日05時に利用可能。 | 誤差悪化の検知は1月19日05時。1月19日04時までは24件未満。 |
| ラベル停止 | 標準終了までラベルを対象時刻+72時間で利用可能にする。 | 27窓、予測24件、採点済みラベル0件。 | 最新窓は `insufficient_labels`、MAE/RMSEはnull。誤差なしを正常扱いしない。 |
| 重複時刻 | 入力CSVの1月18日00時を、同値10と異値999でそれぞれ重複させる。 | `load_hourly` が監視report作成前に拒否。 | 両方ともduplicate入力のエラー。CSV/JSON/complete markerを作成せず、DBの監視行も0件。 |
| 定数分布 | 学習referenceと監視lag値をともに10に保つ。 | PSIに必要な有限標本24件到着後に比較。 | lag_24はconstant reference mode、PSI 0、`no_threshold_crossing`。 |
| 小標本 | 終了を1月18日23時にし、monitor periodを23時間にする。 | 23窓、最新は予測23件・利用可能ラベル20件・有限lag_24値23件。 | latestは `insufficient_labels` と `insufficient_data`、MAEはnull。 |
| 誤差基準未設定 | candidate reportのvalidation MAEをnullにする。 | 24ラベル到着時刻の1月19日02時。 | `performance_reference_unset`。正常/合格状態に置き換えない。 |
| 欠測増加 | 1月17日の行を落とし、1月18日のlag_24を欠測にする。 | 1月19日02時までに24ラベル。lag_24のcurrent入力24件は全件missing。 | 1月18日00時から欠測率1.0を記録。最新窓のPSI componentは `no_current_data`、窓は `insufficient_data`。 |

通常到着の母数は次のように固定する。到着時刻と同じ時計からラベルを含め、予測対象時刻と同じ時計から予測を含める。

| window_end | 予測件数 | 採点済み件数 |
|---|---:|---:|
| 1月18日00時 | 1 | 0 |
| 1月18日03時 | 4 | 1 |
| 1月18日22時 | 23 | 20 |
| 1月18日23時 | 24 | 21 |
| 1月19日00時 | 24 | 22 |
| 1月19日01時 | 24 | 23 |
| 1月19日02時 | 24 | 24 |

分布変化・誤差悪化の開始を1月18日00時とした合成条件では、分布調査の検知遅れは23時間、誤差調査は26時間。ラベル到着を6時間後にした場合は29時間で、通常条件より3時間遅れる。これはこの24要求fixtureにおける件数条件と到着条件の検証であり、一般的な検知時間保証ではない。

2つの境界受入も追加する。予測11.25・実績10のMAE 1.25は閾値と同値なので、24ラベル到着後も `within_review_threshold`。また終了を1月25日01時に延ばした169窓では、1月24日23時は24予測/24ラベル、1月25日00時は下端に達した1月18日00時の予測を除外して23予測/23ラベルとなり、`insufficient_data` / `insufficient_labels`に戻る。

<a id="monitoring-acceptance--sol-reviewの判断と保存範囲"></a>

#### Sol reviewの判断と保存範囲

欠測率の独立した警告閾値はT05では追加しない。合成データだけから実データの許容率を決める根拠はなく、既存policyの警告は有限値のPSIと誤差に限定される。全欠測は明示的な計算不足、小標本は最低件数不足として記録する。一部欠測でも有限値24件が残ればPSIは計算可能なので、PSIの `no_threshold_crossing` は欠測増加がないことを保証しない。欠測率の警告を導入する場合は、実監視の受入条件として別途policyを事前固定する。

validation MAE未設定のcandidateも、出典・hash・保存済み予測の照合を通る場合には診断用の監視を許す。24ラベル到着前は `insufficient_labels`、到着後は `performance_reference_unset`とし、reportは `automatic_promotion=false`を保つ。監視の完走はcandidateの採用・配信許可を意味しない。配信証拠bundleにはvalidation数値と保存済み行の一致が別途必須で、未設定のMAEはその検査を通らない。負値・文字列・非有限などの不正な基準を、未設定と同じ意味には扱わない。

正常保存した各caseでは、CSVとJSONを読み直し、complete markerのCSV/summary SHA・run ID・件数を照合する。一時SQLiteの全窓について、窓ID・時刻・件数・coverage・MAE/RMSE・PSI・欠測率をCSVと比較し、JSONの最新窓をCSV最終行と照合する。警告状態とPSI詳細はCSV/JSON側にあり、既存DB列へ警告状態を追加したわけではない。これは既知の正常fixtureの保存検査で、T06の未確定・破損・旧runを扱う読取照合コマンドの完成ではない。

<a id="monitoring-acceptance--解釈と範囲"></a>

#### 解釈と範囲

これらは閾値式を複製する単体testではなく、登録済み参照の取得、CLIの時刻・ID照合、window scoring、CSV/JSON/SQLite保存を通す合成シナリオである。ラベル停止と遅延の2caseだけはCLIが採点へ渡す1月18日の24ラベルのavailability timestampを意図的にずらして作り、通常caseは本物の+3時間処理を通す。既存予測のlag出典となる過去のラベルは遅らせず、特徴量の利用可能性と矛盾させない。これは実ラベルfeedの障害注入ではない。重複はsource loaderの契約どおり拒否する。sourceを変えたcaseでも元の学習行hash・学習特徴量hashは変わらないことを確認し、後の分布をtraining referenceへ混入させない。

2026-10-30のSol review後、対象26件は13.348秒、全277件は127.327秒でlocal成功。[CI 41005083367](https://github.com/kenobix/Claude_practice/actions/runs/41005083367)でも全277件35.217秒、隔離Docker/両PostgreSQL/cleanup成功。[証拠JSON](reviews/20261030-t05-checks.json)と[作業ログ](../work_log.md)に全試行を記録した。既存T03のSIGTERM待ちとT13相当の時刻検証の一度ずつの失敗は原因未特定として残す。合成結果を実データの品質・配信可否・運用SLOの証明へ転用しない。

<a id="monitor-reconciliation"></a>

## monitor-reconciliation

<a id="monitor-reconciliation--監視保存の読取照合契約（t06）"></a>

### 監視保存の読取照合契約（T06）

policy IDは `monitor-run-reconciliation-v1`。2026-10-30にSolで状態・比較・読取境界を固定した。SQLite版T06aに続き、PostgreSQL版T06bを実装し、使い捨てPostgreSQLで読取照合を検証した。最終の検証・CI状態は末尾と[検証記録](reviews/20261030-t06b-checks.json)を参照。設計時点の保存挙動確認は[設計証拠](reviews/20261030-t06-design.json)を参照。

<a id="monitor-reconciliation--目的と担当範囲"></a>

#### 目的と担当範囲

既存monitorはCSV、JSON、DB transaction、complete markerの順に保存する。ファイルとDBを一度にcommitできないため、marker不在だけではDBの成否を決められない。T06は観測したファイルとDB行を照合し、操作メモをstdoutのJSONへ出す。採点・再送・修復・marker再作成・DB更新・registry操作はしない。

- **T06a / P1 / D2 / Luna実装→Sol review**: ファイル検査、状態判定、SQLite読取、CLI、合成受入。T06aの受入時点ではPostgreSQL URLを接続せず `unsupported_input` とした。T06bで対応を追加した。
- **T06b / P1 / D3 / Sol**: PostgreSQLの読取専用snapshot、型別比較、使い捨てPostgreSQLでの中断・遅延commit受入と全体review。T06aの後に進める。
- T06全体の完了には両方が必要。保存の一致は監視品質の合格や実候補の配信許可を意味しない。T02の実証producerと、実候補/HTTP MLflow監視は別課題である。

<a id="monitor-reconciliation--入力と出力"></a>

#### 入力と出力

照合CLIは既存 `monitor` と並ぶ新しいコマンドとする。既存 `monitor` の引数構造は変えない。

```bash
# SQLite版。原本を保全した検証対象へ使う形式。
DATABASE_URL='sqlite:////absolute/path/ops.db' \
  mlops-bikectl monitor-inspect --csv /absolute/path/monitor.csv \
  --run-id '3ae0909d-7ae1-4a09-a756-96a7ed582edf'
```

`--csv`（小文字 `.csv`）と `--run-id`（正規小文字UUID）は必須。CSV自体がなくても指定できる。JSONとmarkerは必ず同じstemの `.json` / `.complete.json` とし、JSON内のpathを開くために使わない。profile/targetはactive config、DBは既存の `DATABASE_URL` / `DEFAULT_DATABASE_URL` を使う。DB URLを受け取る新しいCLI引数は追加せず、出力・例外へURLや認証情報を含めない。

部品の公開入口を次へ固定する。validな呼出しでの期待される欠落・不一致・DB接続失敗はJSONにし、成功に見せない。

```python
inspect_monitor_run(csv_path: Path, run_id: str, *, database_url: str,
                    data_profile_id: str, target_contract_id: str) -> dict
```

出力は `schema_version=1`、`kind=monitor-run-inspection`、上記policy ID、`observed_at_utc`、`monitor_run_id`、`status`、`reason`、`exit_code` を持つ。さらに次を記録する。

- `files`: csv/summary/markerそれぞれのpresent/missing、読み取ったbytes数とSHA-256、整合性。ユーザー指定pathは記録してよいがDB URLは出さない。
- `database`: backend、metadata/profile照合、観測件数、snapshot方式、列ごとの数値表現。照会できない件数を0へ置き換えずnullにする。
- `checks`: `file_snapshot, input_format, run_identity, file_hashes, database_profile, database_rows, latest_window` の各keyにpassed/failed/not_evaluated/unsupported。
- `differences`: window/field/expected/observedの先頭50件まで。総差分件数と打切りの有無を別記する。
- `decision=inspection_only_no_retry_or_approval`、`automatic_repair=false`、`automatic_promotion=false`、`later_commit_possible`、`limitations`。

JSONは有限値またはnullだけを使う。SQL NaNを表示する場合は数値でなく `"SQL_NaN"` の記述へ変換する。CLIはJSONを1個printして `exit_code` で終了する。既存mainの戻り値処理は変えず、新handler内の `SystemExit` で既存コマンドへの影響を限定できる。argparseの引数エラーは従来どおりstderr/exit 2でよい。

各file descriptorは `state=present/missing/invalid/changed`、`path`、`bytes`、`sha256` を持ち、不明な数値/hashはnull。databaseには `backend=sqlite/postgresql/unsupported`、`profile_status=matched/mismatch/missing_metadata/not_evaluated`、`row_count`、`snapshot=sqlite_read_transaction/postgresql_repeatable_read_only/not_queried`、`numeric_storage`（nullable 3列→binary64/binary32）、`missing_representations`（sql_null/sql_nan_missingの総件数）を持たせる。未照会なら件数はnull、mapは空とする。差分は `difference_count` と `differences_truncated` で打切りを説明し、expected/observedの巨大な表示は各512文字で切る。profile/targetの期待値はrootの `data_profile_id` / `target_contract_id` へ記録する。

<a id="monitor-reconciliation--読取手順と安全境界"></a>

#### 読取手順と安全境界

1. UUID/拡張子を検査し、3つの固定pathの有無を捕捉する。末尾がsymlink、通常ファイル以外、読めないファイルは採用しない。親directoryの明示pathはresolveしてよい。最終ファイルは `O_NOFOLLOW | O_NONBLOCK` で開き、fstatでも通常ファイルか確認する。FIFOへの読取待ちを起こさない。
2. CSV 128 MiB、JSON 16 MiB、marker 64 KiB、CSV 100,000行、1 field 1 MiBを上限とする。超過は `unsupported_input`。上限内でも読込前後でsizeを再検査する。
3. JSONは既存 `strict_json` で重複key/NaN/Infinityを拒否する。CSVは `csv.DictReader` で同名header、壊れた行、重複時刻、不正数値を検査する。浮動値は元の文字列から `float()` へ変換し、pandasの既定CSV丸めを介さない。
4. CSVのdict列は現producerのPython dict表現なので `ast.literal_eval` と型検査を使う。`eval` は禁止。psi/欠測率は文字列keyと有限数値/nullだけ、psi_detailsはJSON互換のlist/dict/有限数値/null/bool/stringだけを許す。呼出式などを拒否する。
5. DBのmetadataを先に照合し、同じsnapshotから対象runの行を読む。必ずparameter付き `WHERE window_id LIKE 'monitor:<UUID>:%'` 相当で限定する。正規UUIDにはLIKE wildcardが入らない。100,001行目があれば上限超過とする。他runや旧形式行を採用しない。
6. DB観測後に3ファイルの有無・device/inode・size・mtime_ns・SHAを再取得する。何か変わった場合は `changed_during_inspection` とし、最初の不一致から破損を断定しない。producerと共有する新しいlock fileは作らない。

SQLiteは既存の通常ファイルを `Path.as_uri()` と `mode=ro` で開く。入力の文字列をURI queryとして流さない。`PRAGMA query_only=ON` と `BEGIN` を使い、同一read transactionでmetadata/行を取得する。存在しないDB・directoryを作らず、`:memory:`も対象外。`Database.connect()`、`Database.initialize()`、CLIの `_database()` は呼ばない（既存実装はmkdir/CREATE/ALTERを行い得る）。`immutable=1`はlockと変更検出を省き、他processからもDBが変わらないと仮定するため、稼働中DBには使わない。SQLite自身のWAL/SHM・read lock動作を、logical rowの変更とは混同しない。[URI公式仕様](https://www.sqlite.org/uri.html#uriimmutable)と[WAL読取条件](https://www.sqlite.org/wal.html#read_only_databases)を根拠にする。受入はSQL行とreport bytesの非変更を検査する。

T06bのPostgreSQLはlocal/設定済み接続先のDBを `BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` で読み、接続timeout 5秒、statement timeout 5秒、lock timeout 1秒を適用する。SELECTとtransaction/session制御だけを使い、schema/table/列の確認もSELECTにする。型の確認とmetadata/行を同じsnapshotで読む。T06bでこの接続を実装した。URIの接続timeout/optionsより固定の接続5秒・statement 5秒・lock 1秒とdefault_transaction_read_onlyを優先する。接続timeoutはlibpqの接続試行ごとの設定で、複数hostやDNSも含めた処理全体の厳密な5秒deadlineではない。schemaはその接続のcurrent_schema()を使い、識別子をquoteする。型はpg_catalogのSELECTで調べ、profile確認前に対象runの行をSELECTしない。tableのSELECT権限がない場合も、catalogから形式を確認した後にdatabase_unavailableへ分類する。

DBの `data_profile_metadata(singleton=1)` がactive profile/targetと違う場合は `database_profile_mismatch`。metadataのない旧DBは `legacy_unverifiable`、monitor table/必須列のない形式は `unsupported_input`。いずれもinitializeで補わない。接続/SELECT権限/timeoutの失敗は `database_unavailable` として秘匿した診断codeを返す。

<a id="monitor-reconciliation--現cli-reportの検査"></a>

#### 現CLI reportの検査

CSVは `monitor_run_id, window_end, window_start, prediction_count, scored_count, label_coverage, mae, rmse, quality_status, psi, feature_missing_rates, distribution_status` が基本列。Capital v2だけ `psi_algorithm, psi_details, training_reference_sha256` が加わる。UUID列のない旧CSVは `legacy_unverifiable`、それ以外の未知の列構成は `unsupported_input`。

存在するJSONには `monitor_run_id, replay_id, model_name, model_version, data_profile_id, target_contract_id, data_sha256, window_count, monitor_period, latest_window, csv_report, csv_sha256, completion_marker, database_window_id_prefix` を要求する。現在のCLI形式だと分からない項目不足は `unsupported_input`、存在する項目の不正型/不正数値は `invalid_evidence`。JSONのUUID自体がない旧形式は `legacy_unverifiable`。ファイル自体の欠落は項目不足と区別し、後の状態表で扱う。低水準persist関数に任意のminimal reportを渡したもの全てを、現CLI形式と見なす必要はない。

- ユーザー指定UUID、CSV全行、JSON、存在するmarkerのUUIDを一致させる。JSONのprofile/targetもactive configと照合する。名前は指定CSV/markerのbasename、DB prefixは `monitor:<UUID>:` と完全一致。
- `model_version` は正の10進文字列、data SHAは小文字64桁。モデル名/版はreportの記載として出し、`model_binding=report_only` とする。DBのmonitor_windowsにはモデル版/候補/artifact列がないため、ここでregistryの版やモデルの真正性を証明したとはしない。
- `window_end` はnaiveの正時、開始を含み終了を含まないmonitor_periodの全時計と一致し、時刻順・一意である。window_startは各CSV窓のendからreportの `thresholds.window_hours` を引いた時刻。thresholds/window_hoursがない形式はunsupported。監視の閾値式やPSIを再計算する作業ではない。
- 予測/採点件数はboolでない非負整数、採点数は予測数以下。coverageは0〜1、MAE/RMSE/PSIは非負有限値または欠落、欠測率は0〜1またはnull。時刻のUTC変換・ラベルの再計算はしない。
- `window_count` はboolでない正整数で、CSV行数、存在するmarkerのwindow_countと照合する。空のcurrent CSVをmatchedにしない。JSONのcsv_sha、markerのcsv_sha/summary_shaは読んだ元bytesのSHAと照合する。markerはschema 1・status `database_commit_succeeded` のみを現形式として扱う。他schemaはunsupported、schema 1で型やstatusが壊れているものはinvalid_evidence。
- DBのwindow_idはprefix+CSV window_end、replay_idはJSONと一致。全件数・coverage・MAE/RMSE・psi_json・missing_jsonを比較し、余分/不足/値違いを区別する。created_atはCSV側に期待値がないため比較しない。
- latest_windowのkey集合はCSV列からmonitor_run_idを除いた集合とし、値をCSV最終行と比較する。警告状態やPSI詳細はCSV/JSONにあり、DBに存在しない列を要求しない。
- SHAの一致は内容の整合性を示す。署名・権限・候補採用の証明にはならない。quality_statusが警告でも、保存自体が一致すればこの部品のstatusはmatchedになり得る。

<a id="monitor-reconciliation--数値と欠落値の固定ルール"></a>

#### 数値と欠落値の固定ルール

保存挙動probeで、SQLiteはbinary64、PostgreSQLの3列 `label_coverage/mae/rmse` はREAL（binary32）と確認した。例えば1/3はSQLiteで `0.3333333333333333`、PostgreSQLで `0.33333334` になる。JSON text内のpsi/欠測率はbinary64の数値を保つ。T06でschemaを移行しない。

- SQLiteおよびPostgreSQL double precision列は、CSV元文字列をfloatへ戻したbinary64で比較する。0と-0は同じ値と扱う。広い `isclose` の許容差で違いを隠さない。
- DB数値欄に文字列がある場合は暗黙変換せずmismatchにする。fileの数値やDB JSONのpsi/欠測率にboolがある場合も数値1/0として比較しない。
- PostgreSQL REAL列はexpectedとobservedの両方を `struct.pack('!f', value)` で量子化した結果が一致するか比較する。出力へ `numeric_storage=binary32` と丸め後の期待値を記載し、元のbinary64値と完全一致したとは説明しない。未知の数値列型はunsupported。JSON textのpsi/欠測率にはこの丸めを適用しない。
- 現producerがDataFrameの未評価Noneを混在列のNaNへ変え、SQLiteはSQL NULL、PostgreSQLはSQL NaNを保存することもprobeで確認した。**CSVが空欄で、その行がlatestなら対応JSONもnullの場合に限り**、nullableな3列のSQL NULLとSQL NaNを「数値欠落」として比較する。過去窓の誤差はJSONにないためCSVを期待値とする。SQL NaNを正規化した件数は `database.missing_representations.sql_nan_missing` に残す。
- CSVが有限値なのにDBがNaN/NULL、CSVが空なのにDBが有限値の場合はmismatch。Infinityは欠落ではない。JSONやCSVに直接書いたNaN/Infinityはinvalid_evidence。0を欠落へ変換しない。

<a id="monitor-reconciliation--状態と終了値"></a>

#### 状態と終了値

validなCLI呼出しでは、次のstatus/exitを固定する。missing markerをmatchedにせず、存在しない行を未送信と断定しない。

| status | exit | 意味 |
|---|---:|---|
| matched | 0 | marker・CSV・JSON・DB全行の整合を読取時点で確認。 |
| unconfirmed | 2 | markerがなく保存の完了を確定できない。reasonで下表の状態を分ける。 |
| mismatch | 1 | UUID/identity/hash/件数/値、markerと実体などの確定した不一致。 |
| invalid_evidence | 1 | JSON/CSVの破損、不正値、symlink/FIFO等の不適切な入力。 |
| database_profile_mismatch | 1 | DB metadataがactive profile/targetと違う。対象runの行は読まない。 |
| database_unavailable | 2 | DB未取得/不在/接続・権限・timeoutのため照会不能。 |
| changed_during_inspection | 2 | ファイルが読取中に変わり、安定した照合にならない。 |
| legacy_unverifiable | 2 | UUID/DB metadataを持たない旧記録。保全して自動結合しない。 |
| unsupported_input | 2 | 対応外形式・サイズ・列型。T06a時点のPostgreSQL拒否もこの状態だった。 |
| inspection_error | 2 | 想定外の内部失敗。例外classのみを出し、message/DSNを漏らさない。 |

ファイル読取前の危険なpathは即invalid、size上限等は即unsupportedでよい。その他の安定性再確認後の優先順は、changed→ファイルの破損/不正値→ファイルidentity/hash不一致/markerありでの必要ファイル欠落→DB profile違い→DB照会不能/未対応backend→legacy/形式非対応→DB行の不一致→下表の一致/未確定。複数の失敗はchecksに残す。ファイルだけで不一致を確定できればDBを読まず返してよいが、読取済みファイルの変化は再確認する。markerがあって必要ファイル、または照会できたDBの必要行がない場合はmismatchである。

| CSV | JSON | marker | DB（対象UUID） | status / reason |
|---|---|---|---|---|
| 一致 | 一致 | 一致 | 全件一致 | matched / files_and_database_match |
| 一致 | 一致 | なし | 0件 | unconfirmed / files_only |
| 一致 | 一致 | なし | 全件一致 | unconfirmed / database_without_marker |
| 有無いずれか | 不足 | なし | 0件または不確定 | unconfirmed / incomplete_files |
| なし | あり | なし | 有無いずれか | unconfirmed / incomplete_files |
| なし | なし | なし | 1件以上 | unconfirmed / database_only |
| なし | なし | なし | 0件 | unconfirmed / not_observed |
| 一致 | 一致 | なし | 一部だけ/余分/値違い | mismatch / database_rows_differ |
| 不足 | 有無いずれか | あり | 有無いずれか | mismatch / marker_without_required_files |
| 一致 | 一致 | 一致 | 0件/一部/余分/値違い | mismatch / database_rows_differ |

上表は先に形式/identity/hash/profileの検査を通る場合の表。CSVだけがありJSONがない場合はCSVとDBが似ていても全体identityを確認できずincomplete_files。DB行の有無はsnapshot時点の観測に限る。marker不在のunconfirmedでは `later_commit_possible=true` とし、全件一致でもmarker生成や自動再試行を勧めない。操作メモは「producerが動いていないか確認し、原本を保全して再度読取照合する」とする。

<a id="monitor-reconciliation--受入ケース（実装後に確認するもの）"></a>

#### 受入ケース（実装後に確認するもの）

| ID | fixture/操作 | 期待結果 |
|---|---|---|
| S01 | current CLI形式、CSV/JSON/marker/SQLite全窓が一致 | matched/0。警告statusがあっても保存一致は一致。 |
| S02 | S01からmarkerを消し、DB対象行も消す | unconfirmed/files_only/2。再送不可。 |
| S03 | S01のmarkerだけ消す | unconfirmed/database_without_marker/2。全DB行を保全。 |
| S04 | CSVだけ/JSONだけ残しmarkerなし | unconfirmed/incomplete_files/2。DBの有無の双方で確認。 |
| S05 | 3ファイルなし、DB対象行だけあり | unconfirmed/database_only/2。 |
| S06 | 3ファイルなし、metadataのある既存DBに対象行なし | unconfirmed/not_observed/2。 |
| S07 | markerありでCSVまたはJSONだけ欠落 | mismatch/1。 |
| S08 | marker/JSONのSHAまたはUUIDを変える | mismatch/1。 |
| S09 | DB対象行の1件欠落、余分行、値/時刻/replay_idの変更 | 各々mismatch/1。markerなしでも部分一致は0にしない。 |
| S10 | 別profile/targetのDB metadata | database_profile_mismatch/1。対象run SELECT前に拒否。 |
| S11 | CSVに複数UUID、重複窓、不正数値、壊れたJSON/重複JSON key | mismatchまたはinvalid_evidence/1。UUID違いはmismatch、破損・重複窓・不正値はinvalid。 |
| S12 | CSV/JSONにrun UUIDがない旧形式、またはDB metadata tableがない | legacy_unverifiable/2。旧行・bytesは変えない。 |
| S13 | DB不存在、読取権限なし、table権限拒否、接続timeout | database_unavailable/2。新規DB/directoryを作らずDSNを出さない。 |
| S14 | 1回目と2回目の読取間にファイル交換/変更/marker新規出現 | changed_during_inspection/2。 |
| S15 | symlink、FIFO、上限超過、未知の列構成 | symlink/FIFOはinvalid/1、上限/未知形式はunsupported/2。待たない。 |
| S16 | 既存の旧形式行と別UUID行を同じDBへ置きS01/S03を照合 | 他run/旧行を選択せず全内容を保持。 |
| S17 | CSV空欄/JSON nullとSQL NULL、有限値0とSQL NULL | 前者は欠落一致、後者はmismatch。CSV NaN/Infinityはinvalid。 |
| S18 | PG REALに1/3と非整数MAE、JSON text内のpsi小数を保存 | T06b。binary32比較とJSON binary64比較を分けて一致。1 ULP違いはmismatch。 |
| S19 | PGに未評価NaNを保存、さらにCSV有限値に対するDB NaNも作る | T06b。欠落だけ正規化、有限値対NaNはmismatch。 |
| S20 | SQLite/PG read-only接続からUPDATEを試す | 使い捨てfixtureで必ず拒否。実照合は更新SQLを一切送らない。 |
| S21 | PGで照会snapshotの後に別接続のcommitが到着 | T06b。同snapshotでは未観測、次回に現れる。未送信/採用へ変えない。 |
| S22 | CSV dict列に呼出式を書き、エラーに認証情報入りDSNを含めるfake | 実行せずinvalid/1。接続失敗等は秘匿codeだけ、秘密文字列はstdout/stderrに出さない。 |
| S23 | T06aにPostgreSQL URLを渡す | 接続せずunsupported/2。接続/initializeへの呼出しをmockで拒否。 |

全caseで入力CSV/JSON/marker bytes、既存DB logical rows、旧runを前後比較する。read-only setupを検証するためのUPDATEはテストfixtureの別経路に限定する。T06aはS18/S19/S21の実PostgreSQL受入を未実施として記録し、T06完了にしない。合成受入やstorage probeを実Capitalの監視証拠へ転用しない。

<a id="monitor-reconciliation--実装履歴と検証範囲（2026-10-30）"></a>

#### 実装履歴と検証範囲（2026-10-30）

Luna実装時点ではT06aのSQLite読取照合部品と`monitor-inspect --csv PATH --run-id UUID` CLIを追加した。対象受入test 33件、既存monitor persistence 7件、monitoring window 6件、training monitor reference 26件、compileall、diff checkは成功。実行ログ・hashと初回失敗は[作業ログのT06a節](../work_log.md#t06a-実装と合成受入-2026-10-30)および`data/reports/20261030-t06a/`に記録した。

Luna引継ぎ時点は**T06a実装済み・Sol review待ち**であり、T06全体は未完了。S01–S17/S20/S22/S23の主要な状態・安全境界を合成fixtureで確認したが、各case表の下位条件をすべて独立に試したわけではない。これは実Capitalの監視結果ではない。PostgreSQLは接続しておらず、S18/S19/S21、T06b、全suite、CIは未実施。Sol review後にPostgreSQL snapshot・binary32・SQL NaN・遅延commit受入を行う。

使い捨ての手動fixtureに対してだけ、次の形式で実行する。実runを調査するときも原本を保全し、read-only結果だけで再送・修復・採用を決めない。

```bash
DATABASE_URL='sqlite:////absolute/path/ops.db' \
  mlops-bikectl monitor-inspect --csv /absolute/path/monitor.csv \
  --run-id '3ae0909d-7ae1-4a09-a756-96a7ed582edf'
```

<a id="monitor-reconciliation--sol-review後のsqlite受入"></a>

##### Sol review後のSQLite受入

Sol reviewでは期間末尾の未確認窓、JSON boolと数値の同値扱い、旧形式とDB障害の優先順、最終再読取中のinode交換、field上限の分類、欠落window_hoursの分類を修正した。固定した状態表・数値ルールを変えず、ファイル判定・DB profile/接続・行比較を順に検査し、最後にファイルsnapshotを再確認する。SQLiteの未知の数値列型はunsupported、DB JSONのbool/非有限/文字列は数値へ変換しない。

対象54 testsではUCI形式とCapital追加列、read-only接続へのUPDATE拒否、metadata/行の同一SQLite snapshot、他run/旧行とreportの非変更、CLI exit 0/1/2、許可・ロック失敗の秘匿、差分50件/値512文字上限も確認した。実行・失敗・修正と全suite/CIの最終成否は[検証記録](reviews/20261030-t06a-checks.json)と[作業ログ](../work_log.md)を参照する。PostgreSQL接続、REAL量子化、SQL NaN、遅延commitはT06bの残件であり、SQLiteや既存CIのPostgreSQL試験成功をT06bの受入へ転用しない。

実装commit df4c3b9の[CI 41038726909](https://github.com/kenobix/Claude_practice/actions/runs/41038726909)は全331 tests、既存の隔離Docker/PG試験とcleanupまで成功した。これはSQLite版T06aと既存機能の回帰確認である。T06bのPG inspectorは未実装のままで、S13のOS実権限/実busy timeoutの組合せも追加確認として残る。


<a id="monitor-reconciliation--t06b-postgresqlの実装と受入（2026-10-30）"></a>

#### T06b PostgreSQLの実装と受入（2026-10-30）

`postgresql://`と`postgres://`を同じ公開入口/CLIで受け付ける。DB初期化や既存writer/schemaの変更は行わない。明示的なREPEATABLE READ READ ONLYを開始し、current_schema、catalog、metadata、UUIDで限定したmonitor行を同じsnapshotから取得してrollback/closeする。接続を失った後のcleanup失敗で元の秘匿済み診断を隠さない。[PostgreSQLの分離レベル](https://www.postgresql.org/docs/17/transaction-iso.html)・[READ ONLY](https://www.postgresql.org/docs/17/sql-set-transaction.html)・[psycopg接続引数](https://www.psycopg.org/psycopg3/docs/api/connections.html)に沿う。

REALは列ごとにbinary32へ量子化し、DOUBLE PRECISIONとJSON text内の数値はbinary64のまま比較する。`database.binary32_examples`に各列の最初の有限値についてCSV元値・量子化後期待値・観測値を最大3例表示し、差分にも量子化後期待値を記録する。SQL NaNはCSV空欄（latestならJSON null）の場合だけ欠落として数え、有限値との不一致では`SQL_NaN`という文字列を表示する。

最終対象unit 60件、使い捨てPG受入25件を実行する。PG受入はS18/S19/S20/S21に加え、profile確認前の対象行SELECT拒否、未知型、1 ULP差、JSON精度/不正型、SQL NULL、同一snapshotのmetadata、権限・lock/statement timeout・実接続喪失、他run保全、CLI exit 0/1/2、marker出現、writer例外・commit前後の実SIGKILLを含む。全窓未観測から別接続がcommitしても同一inspectionでは未観測、次回ではDB全行一致/markerなしとしてunconfirmedになる。受入中も原本修復や自動再送は行わない。

S23はT06a時点のbackend拒否を記録した歴史的caseであり、T06b以降はPGの接続と照合が期待動作になる。旧T06a検証JSON/logは書き換えない。新検証JSON/logに変更後の結果・初回失敗と修正を記録する。OS実権限/実busy timeoutというSQLite補足と、実Capital候補/HTTP monitor・T02証拠producerは引き続き別の残課題である。使い捨て合成受入の成功を実監視・配信の証拠へ転用しない。

再現コマンド（既存local imageとDockerを使い、専用containerを作成・削除する）:

```bash
cd 261021
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python -m unittest discover -s tests -p 'test_monitor_inspection*.py' -v
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/storage_checks.py monitor-inspection --run-disposable
```

CIにT06b専用stepを追加し、`monitor-inspection-postgres-ci.log`を既存artifactへ保存する。実行済みの件数・終了値・cleanup・source/log SHA・post-push CIは[検証記録](reviews/20261030-t06b-checks.json)と[作業ログ](../work_log.md)へ記録する。ネットワーク上の実接続timeout満了は未注入で、接続5秒の指定はunitで検査し、実接続失敗・切断と実statement/lock timeoutはPG受入で確認する。

T06b実装commit `164ac53`の[CI 41068758500](https://github.com/kenobix/Claude_practice/actions/runs/41068758500)は全337 tests（39.970秒）、T06b専用PG25 tests（14.461秒）、既存system/PG試験とcleanupまで成功した。artifact ZIPのSHA-256と4 memberの内容・hashも検算済み。2026-11-01に観測し、[検証記録](reviews/20261030-t06b-checks.json)へ追記した。

<a id="monitor-reconciliation--sqlite-s13の実os補足（2026-11-03）"></a>

#### SQLite S13の実OS補足（2026-11-03）

`storage_checks.py sqlite-os --run-disposable`は、既存の合成fixtureが作った一時SQLite/CSV/JSON/markerだけを使う。非root実行でDB fileをchmod 000にした読取拒否と、DELETE journal modeの`BEGIN EXCLUSIVE`で実際の5秒busy timeoutを確認する。writer/schemaを変更せず、検査後に権限とlockを必ず戻す。DB bytes/全論理行/3 fileの不変と、権限・lock解除後のmatchedを要求する。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/storage_checks.py sqlite-os --run-disposable
```

今回の実測待機は約5.025秒で、失敗結果はいずれも`database_unavailable`/終了区分2だった。全filesystem/ネットワークmountの挙動や実Capital monitorの受入を代表するものではない。rootでは権限拒否の証明にならないので明示的に停止する。原本/hashは[2026-11-03索引](reviews/20261103-t17b-t07a-checks.json)を参照。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。

<a id="measurement-interruption"></a>

## measurement-interruption

<a id="measurement-interruption--capital運用測定の中断・タイムアウト（t03）"></a>

### Capital運用測定の中断・タイムアウト（T03）

この手順は隔離Capital staging専用です。UCI版3の配信・DBは変更しません。新しい実candidateの測定はT10/T11の完了後です。

<a id="measurement-interruption--実行前に固定するルール"></a>

#### 実行前に固定するルール

operational planの`policy.request_journal.policy_id=capital-request-journal-v1`に次の条件を固定します。従来の50暖機・1,000逐次要求、HTTPXの接続・読取・書込・接続待ちそれぞれの10秒timeout（要求全体の厳密な10秒期限ではない）、再送0、エラー0、nearest-rank p95 ≤250msは維持します。journalのない旧planは履歴として読めますが、新たな測定では拒否します。既存planを書き換えず、新しいplanと理由付きの別attemptを作ります。

- HTTP時間: HTTP呼出し開始からJSONを読み終えるまで。応答検証・監査照合・journal保存は除外します。例外時もHTTP呼出し終了時の時間を使います。
- 全体時間: `timing.request_loop_elapsed_ms`。各要求の検証・journal書込・fsync、逐次ループ終了時のclient closeを含みます。事前ready/version、事後resource取得・全件DB照合・report/台帳確定は含みません。「プログラム全体の起動から終了まで」の時間ではありません。
- `request_journal.write_and_fsync_ms`: journalのheaderとeventのwrite/fsync時間の合計。初回親directoryのfsyncは除外します。全体時間・この値に合格閾値はありません。
- 中断・タイムアウト・監査不一致・記録失敗は合格にしません。部分的なp95だけで1,000件の測定結果を置き換えません。測定完了でも配信はblocked、operational gateは別途reviewです。

journalはディスクへの記録を増やし、測定負荷にも影響します。新旧の全体時間を同一条件とみなさず、policy ID、資源、全attemptを併記します。

<a id="measurement-interruption--保存するもの"></a>

#### 保存するもの

`data/reports/capital-bikeshare/operational-<attempt UUID>.requests.jsonl`へ追記します。最初の行にcandidate/planとplanファイルのSHA・固定1,050要求のID/入力を保存し、各eventに順番・UTC時刻・前eventのSHA・自身のSHAを付けます。ファイルは新規作成のみ、permissionは600です。

| 状態 | ディスク記録と解釈 |
|---|---|
| `scheduled_only_no_durable_send_intent` | 固定予定のみ。送信直前の確定記録なし。「絶対に未送信」とは断定しません。 |
| `outcome_unknown_may_have_been_sent` | `sending`をfsyncしてからHTTPを呼ぶ。timeout、失敗応答、検証失敗、途中停止では結果未確定。DBへ後で保存される可能性があります。 |
| `response_verified` | 200応答と入力由来等の検証後、HTTP時間を含むeventをfsync済み。全件DB監査完了や配信承認は意味しません。 |

headerのfsyncと作成directoryのfsyncが終わるまで要求を送りません。各`sending`と結果eventもwrite→fsyncを完了してから次へ進みます。write/fsync失敗後はjournalを再利用しません。電源断やストレージ装置の虚偽応答まで保証するものではありません。

SIGTERMはCLIのmain threadでKeyboardInterruptへ変換し、可能なら失敗reportを保存します。SIGKILLではhandlerが動かず、台帳がstartedのまま、reportなしでもjournalが残ります。末尾の途中行は`torn_tail`として読み取り、ファイルを修復しません。完全な行のhash不一致・順序不正はinvalidです。SHA鎖は偶発破損の検知用で、管理者による書換えへの電子署名ではありません。

<a id="measurement-interruption--停止後の確認順"></a>

#### 停止後の確認順

まずプロセスと隔離サービスの状態を確認し、実行中のまま再試行しません。既存のcandidate lockを取れない間は検査も拒否します。

```bash
cd ~/work/claude_practice/261021
. .venv/bin/activate
python scripts/runtime_tools.py attempt-ledger --help
python scripts/runtime_tools.py inspect --candidate-id <64桁candidate ID>
```

`request_journal.status`（readable/torn_tail/missing/invalid）、要求別状態、linked reportとplanの照合結果を確認します。`started/interrupted`でreportがない場合もjournalを読み取れます。ファイルも台帳も更新せず、結果を自動採用しません。

未確定要求が1件あるときは、同じstaging DBの監査行をSELECTだけで確認します。既存のAPP_DB_PASSWORDを設定した環境で実行し、認証情報をログへ表示しません。

```bash
DATA_PROFILE_ID=capital-bikeshare python scripts/runtime_tools.py pending-audit \
  --candidate-id <candidate ID> --attempt-id <UUID>
```

`not_seen_at_query_time`は「その照会時点で見えない」です。後のcommitを否定しません。`identity_matches_unverified`も版等の一致のみで、予測値・全入力の検証や運用合格ではありません。DBが切断されていたら照会成功として扱わず、元の証拠を保全します。古い失敗reportだけがあるattemptも従来の読取手順を維持します。

再試行が必要なら、失敗原因・未確定要求・DBの観測・資源条件を調べ、人のoperator/reasonを付けて`runtime_tools.py attempt-ledger review-retry --help`の現行オプションに従います。同じ要求をその場で再送せず、新しいUUIDのattemptを作り、古いattempt・journal・reportを保持します。成功・高速なattemptだけを選びません。

完了したreportは`runtime_tools.py recheck`で固定1,050要求と保存HTTP時間からp95を再計算します。新policyのlinked reportではjournalのSHA・1,050応答確定・要求別HTTP時間も照合します。これも承認操作ではありません。

<a id="measurement-evidence-archive"></a>

## measurement-evidence-archive

<a id="measurement-evidence-archive--t16-合成計測証拠の保存と再計算"></a>

### T16: 合成計測証拠の保存と再計算

`synthetic-system` CI jobでは、T16を有効にした実行で、使い捨てCapital fixtureの合成計測原本4件を`synthetic-measurement-evidence.zip`へ保存します。対象は計画、計測report、operational attempt ledger、要求journalです。manifestには各原本の相対パス・byte数・SHA-256、candidate/attempt ID、source commitとdirty flagを含めます。ZIP外の`archive-sha256.json`はZIP全体のSHA-256を記録し、`recalculation.json`は別processの検算結果です。

<a id="measurement-evidence-archive--ローカル実行"></a>

#### ローカル実行

この追加保存は明示的に`--evidence-dir`を指定したsynthetic-system harnessだけで有効です。Docker Desktopが起動し、対象ユーザーからDocker daemonを利用できるWSL/Linux環境で、プロジェクトディレクトリから実行します。

```bash
cd 261021
. .venv/bin/activate
mkdir -p data/reports
set -o pipefail
timeout --signal=TERM --kill-after=10 1200 \
  python scripts/system_harness.py run --run-synthetic-system \
    --evidence-dir data/reports/t16-local 2>&1 \
  | tee data/reports/t16-local-harness.log
```

指定する出力directoryは新規でなければなりません。既存directoryは上書きしません。harnessは成功した1 attemptだけを明示選択し、出力archiveと再計算結果を生成してから既存のfinally cleanupを実行します。失敗した実行を成功archiveとして出力しません。成功後は次を確認できます。

- `data/reports/t16-local/synthetic-measurement-evidence.zip`
- `data/reports/t16-local/archive-sha256.json`
- `data/reports/t16-local/recalculation.json`
- `data/reports/t16-local-harness.log`の`cleanup_complete`

別processで手動再検算する場合は、検証processにarchive以外の計画・台帳・環境情報を渡さず、次を実行します。

```bash
env -i PATH="$PATH" .venv/bin/python -m bike_mlops.capital_measurement_evidence \
  --verify-archive data/reports/t16-local/synthetic-measurement-evidence.zip
```

検証processは明示memberだけをメモリ/一時directoryへ読み、`extractall`は使いません。DB、MLflow、Dockerへ接続しません。

<a id="measurement-evidence-archive--再計算条件と限界"></a>

#### 再計算条件と限界

検証はmanifestから4原本のbyte数/hashを検証し、台帳→candidate/attempt→plan/report/journalの結合を確認します。journalとreportの1,050要求順・HTTP 200・latencyをrequest IDごとに照合し、warmup 50件を除いた1,000件からnearest-rank (`ceil(0.95*n)-1`) でp95を再計算します。要求ID digest、エラー0件、reportに記録したp95も比較します。

これは保存済み合成行の数値・結合を独立processで再計算する検証です。実データの精度、実運用の応答時間、実DB監査の完全性、OS時計の適格性、モデルの承認・配信許可を証明しません。`operational_gate=not_evaluated`と`deployment_permission=blocked`を維持します。機密フィールド名、認証付きURI、外部URI、絶対パス、symlink、既存出力、内容改変、欠落/余分/重複memberを検出した場合は閉じて失敗します。原本を削除・変更してarchive hashを合わせることはしません。

GitHub Actions artifactは固定allowlistでZIP、外側digest JSON、再計算JSONだけを追加します。`data/reports/**`の再帰公開はしません。ローカル実行だけではT16完了ではなく、実装commitのCI artifact検査、cleanup確認をSol review後に確認します。

<a id="measurement-evidence-archive--sol-reviewでの補強（2026-11-02）"></a>

#### Sol reviewでの補強（2026-11-02）

export時に加え、archiveだけを検証する経路でも台帳とjournal全行を含む4原本の秘密情報検査を行います。manifestのdescriptor項目を固定し、report・台帳・journalの元パスを対象attemptへ結びます。workspace自体と途中directoryのsymlinkを拒否し、原本の読取はdirectory/fileを`O_NOFOLLOW`で開いて変更を検知します。

export失敗と部分出力削除の失敗が重なった場合は両方を記録して非0で終了します。resource cleanup失敗時のarchive削除失敗も握り潰しません。これらの検査は固定された合成出力用であり、任意の文章から全種類の秘密値を発見する汎用検出器ではありません。未知の原本を公開してよいと判断する用途には使いません。

Solの検証結果は[review索引](reviews/20261102-t16-sol-review.json)へ記録します。Luna索引は当時の検証記録として保持します。

<a id="measurement-evidence-archive--実装commitのci確認"></a>

#### 実装commitのCI確認

実装`2492abc`の[CI41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156)で399 testsと両job全stepが成功しました。T16の3ファイルを含むartifactの全13 memberとhashを照合し、ZIPだけを別processへ渡して1050応答・warmup50除外・1000測定件のp95を再計算しました。同じattemptの報告値と一致し、専用project cleanupも確認したため、T16を完了とします。[CI証拠](reviews/20261102-t16-ci-checks.json)に記録しています。上記のlocal/CI pending記述は受入条件・実施順の説明であり、最新の完了状態はこの節です。

2026-11-03補足: 通常のCPU名（例: `CPU @ 2.80GHz`）の`@`をURI資格情報と誤分類するケースを修正した。URI authorityのuserinfo（空usernameを含む）、埋込み資格情報URI、bare user@host/email、絶対path、外部URLは引き続き拒否する。CPU fieldを無条件で許可せず、同fieldへ資格情報URIを置いた負例も確認する。原本を正規化・削除してからexportする回避はしない。ec71 CIで拒否した具体的field/valueはcleanup後に回収できず、CPU原因との同一性は推定として残す。

最終実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・16 member/hash・archive-only再計算・cleanupまで成功した。[最新CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を参照。実CIの保存CPUはAMD EPYCで、Intelの@を含む原本の再計算は固定fixtureの別process試験で確認した。自然失敗の具体的field未採取という限界を、後続の成功で埋め合わせない。
