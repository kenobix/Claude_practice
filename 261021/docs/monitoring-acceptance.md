# Capital監視の合成受入基準（T05）

この文書は、`capital-training-monitor-v1` の既存仕様を変えずに、監視CLIが合成データで期待どおりの窓・件数・警告時刻を記録するか確認するための基準である。実Capitalのデータ・MLflow・APIを使った実証ではない。検証用registryは保存済みcandidate/referenceを返すfake、操作DBは一時SQLiteを使う。

## 固定済みの共通ルール

- 評価時計は1時間ずつ進む。各時刻 `t` の予測窓は `(t - 168 hours, t]`、monitor periodは開始時刻を含み終了時刻を含まない。
- ラベルは `available_at <= t` のときだけ採点する。Capitalの通常到着時刻は予測対象時刻の3時間後。
- 誤差の算出には同じ窓の採点済みラベルが24件必要。24件未満では `quality_status=insufficient_labels`、MAE/RMSEはnull。
- PSI v2は固定したtraining referenceを使い、reference/currentとも24件以上の有限値が必要。最大PSIが0.2以上なら `distribution_status=investigate_distribution_change`。
- 誤差調査は `MAE > validation_MAE * 1.25` のとき `quality_status=investigate_error_increase`。同値は調査警告にしない。誤差基準が未設定なら、24ラベル到着後に `performance_reference_unset` と記録する。
- 分布警告と誤差警告は別々に記録する。片方の警告からもう片方を推定しない。
- 欠測率は値として記録するが、既存policyに欠測率の警告閾値はない。PSIが有限標本不足で計算できないときは `insufficient_data` 等の明示状態にする。T05で新しい欠測警告閾値は導入しない。

## CLI受入シナリオ

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

## Sol reviewの判断と保存範囲

欠測率の独立した警告閾値はT05では追加しない。合成データだけから実データの許容率を決める根拠はなく、既存policyの警告は有限値のPSIと誤差に限定される。全欠測は明示的な計算不足、小標本は最低件数不足として記録する。一部欠測でも有限値24件が残ればPSIは計算可能なので、PSIの `no_threshold_crossing` は欠測増加がないことを保証しない。欠測率の警告を導入する場合は、実監視の受入条件として別途policyを事前固定する。

validation MAE未設定のcandidateも、出典・hash・保存済み予測の照合を通る場合には診断用の監視を許す。24ラベル到着前は `insufficient_labels`、到着後は `performance_reference_unset`とし、reportは `automatic_promotion=false`を保つ。監視の完走はcandidateの採用・配信許可を意味しない。配信証拠bundleにはvalidation数値と保存済み行の一致が別途必須で、未設定のMAEはその検査を通らない。負値・文字列・非有限などの不正な基準を、未設定と同じ意味には扱わない。

正常保存した各caseでは、CSVとJSONを読み直し、complete markerのCSV/summary SHA・run ID・件数を照合する。一時SQLiteの全窓について、窓ID・時刻・件数・coverage・MAE/RMSE・PSI・欠測率をCSVと比較し、JSONの最新窓をCSV最終行と照合する。警告状態とPSI詳細はCSV/JSON側にあり、既存DB列へ警告状態を追加したわけではない。これは既知の正常fixtureの保存検査で、T06の未確定・破損・旧runを扱う読取照合コマンドの完成ではない。

## 解釈と範囲

これらは閾値式を複製する単体testではなく、登録済み参照の取得、CLIの時刻・ID照合、window scoring、CSV/JSON/SQLite保存を通す合成シナリオである。ラベル停止と遅延の2caseだけはCLIが採点へ渡す1月18日の24ラベルのavailability timestampを意図的にずらして作り、通常caseは本物の+3時間処理を通す。既存予測のlag出典となる過去のラベルは遅らせず、特徴量の利用可能性と矛盾させない。これは実ラベルfeedの障害注入ではない。重複はsource loaderの契約どおり拒否する。sourceを変えたcaseでも元の学習行hash・学習特徴量hashは変わらないことを確認し、後の分布をtraining referenceへ混入させない。

2026-10-30のSol review後、対象26件は13.348秒、全277件は127.327秒でlocal成功。[証拠JSON](reviews/20261030-t05-checks.json)と[作業ログ](../work_log.md)に全試行を記録した。既存T03のSIGTERM待ちとT13相当の時刻検証の一度ずつの失敗は原因未特定として残す。合成結果を実データの品質・配信可否・運用SLOの証明へ転用しない。
