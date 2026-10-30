# Capitalの学習参照とPSI v2

2026-10-30、計画T04の候補保存・登録・監視CLI接続を実装し、T05の10シナリオと2境界の合成受入をSol review後に確認した。実Capital候補はまだ作成しておらず、検証は合成データで行った。T06の異常保存照合、T02の実証報告producer、実候補/HTTP MLflowからの監視は後続課題である。[残作業計画](../../261014/plan_mlops.md#remaining-work-20261030)を正規の一覧とする。

## 何を固定するか

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

## 保存形式と流れ

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

## 監視コマンドでの照合

Capitalの監視は、同じtracking/registry URIのexact versionから参照と`evidence/candidate-report.json`を取得する。次の検査に失敗すると監視reportを書かない。

1. READY版・run・profile/target/feature契約・CSV・元モデルSHA・候補ID・protocolを照合。
2. candidate reportのファイルSHAとcandidate identityを再計算。
3. manifestの元ファイルSHAと参照descriptor・本文SHAを照合。
4. 元CSVから**学習行だけ**を再選択し、行hash・特徴量hash・bins・件数・期間・欠測率を保存参照と一致確認。監視/validation/finalの分布を区切り選択に使わない。
5. 取得前後のversion/run/tag変化と、読込中のCSV変化を拒否。
6. DBの保存済み予測のcandidate、登録artifact、profile/target、model name/version、feature契約、lag出典hashと全特徴量値を照合。

この検査では元学習行から区切りを再計算して一致を確かめるが、採用する区切りは保存済み参照のもの。照合に失敗したとき、新しい区切りへ置き換えて続行しない。

## 操作と結果の場所

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

Capital用DB接続を事前に設定する。`scripts/bikectl`のprofile別port設定と`.env`の取扱いは[runbook](runbook.md)を参照する。監視期間はリプレイreportを継承し、両方を指定した`--start/--end`による部分期間も使える。manifest省略時はCSVと同じdirectoryの`manifest.json`。UCIの呼び出しは従来どおりで、明示する場合は`--psi-version legacy-uci-v1`。

結果は`data/reports/capital-bikeshare/monitor-<replay>-v<version>-<run UUID>.csv`と同名JSON、`.complete.json`、Capital DBの`monitor_windows`に保存する。JSONには参照出典/hash/policyとlatest windowの`psi_algorithm`、`training_reference_sha256`、各特徴量の`psi_details`を含む。CSVには窓ごとの詳細を含む。DBの`psi_json`は従来の特徴量別PSI数値、`missing_json`は欠測率を保持する。DBの列を新設して参照本文を入れる変更はしていない。run IDとsummary/markerを合わせて読む。

これらの監視結果はMLflow UIへ自動登録しない。MLflow UIでは候補runのArtifactsにある参照とcandidate reportを確認でき、監視の全窓は上記ローカルCSV/JSONで確認する。markerがない保存はDB/fileのcommitが未確定であり、T06の照合対象になる。

## 状態の読み方と後続の受入条件

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

T05の具体的な受入期待値とSol reviewの判断は[Capital監視の合成受入基準](monitoring-acceptance.md)を参照。fake registryと一時SQLiteを通る10ケースと2境界を追加し、対象26件13.348秒、全277件127.327秒がlocalで成功した。保存したCSV/JSON/markerとDB全窓の値・hashも照合した。[CI 41005083367](https://github.com/kenobix/Claude_practice/actions/runs/41005083367)でも全277件35.217秒と隔離Docker/両PostgreSQL/cleanupが成功した。[全試行の証拠](reviews/20261030-t05-checks.json)には既存T03/T13の一度ずつの失敗も残し、原因を残課題として扱う。これはCLIの合成経路の受入であり、実MLflow/Capital候補の監視証拠ではない。
