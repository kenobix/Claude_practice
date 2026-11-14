# 実候補の保存済み予測を使う監視診断（2026-11-14）

[初回精度評価](capital-first-evaluation.md)と同じcandidate/artifactのfinal行別予測から、固定監視関数で履歴窓を計算した。
**offline診断であり、HTTP/registry/DB監査を含むT02/T12の実producer受入は未完了**。
再fit・新しい候補predict・選定・基準変更はしていない。

事前plan：`data/reports/20261114-product-resume/offline-monitor-plan.json`。
train-only reference、PSI v2のbins、minimum count24、PSI≥0.2、validation MAE×1.25を固定した。
時点はdataset-local。窓は`(clock-168h, clock]`、正解はlabel_available_at≤clockだけ利用する。
finalの最初2026-08-01 03時から、最後の正解到着を含む10-01 03時未満まで1,464時点を計算した。

| 診断 | 結果 |
|---|---|
| 分布警告 | 1,336窓。警告なし105、母数不足23 |
| 誤差増加警告 | 234窓。警告境界内1,204、ラベル不足26 |
| 最初の分布警告 | 2026-08-02 02時。24予測が揃う最初の時点 |
| 誤差が評価可能になる時点 | 2026-08-02 05時。ラベル+3h後に24正解が揃う |
| 最初の誤差増加警告 | 2026-08-02 20時 |
| lag24/lag168/hourの最大PSI | 0.665503 / 1.729054 / 0.108145 |
| 最後の窓 | 2026-09-24 02時を含まず〜10-01 02時まで。予測165/正解165、欠測率0 |
| 最後の窓の候補MAE / baseline MAE | 224.346232 / 274.084848 |
| validation基準の誤差警告境界 | 137.392476×1.25 = 171.740595。最後の窓は超過 |

最終targetは09-30 23時なので、10-01 02時まで窓を動かすと古い3予測が外れ165件になる。
「168時間窓だから168正解」と仮定せず、各時点の予測数/到着済み正解数を記録した。

最後の窓では09-26/27/28の候補日別MAEが347.46/330.67/277.25件と大きい。
09-27/29/30は候補がbaselineより悪い。全体の精度gate合格だけで全ての日の改善とは言わない。
天候・イベント等の原因はこの保存予測だけでは未特定。新しい基準や閾値を後付けせず、実producer証拠と合わせて採否時に説明する。
PSIは分布差の指標で因果説明ではなく、分布警告と誤差警告の発生は別々に扱う。

## 確認場所と検算

同ignored rootの`offline-monitor-windows.csv`、`offline-monitor-summary.json`、`offline-monitor-diagnosis.json`を読む。
SQLite/CSV/JSON/complete markerを通したmonitor CLIの結果ではなく、DB commit markerは作っていない。
現MLflowにも登録していない。候補の採用・運用gate・本番SLO・時計資格の証拠へ格上げしない。

producer内で全窓の所属/到着済み正解数/MAEを別算術で確認した後、さらに別processの標準libraryだけで、
1,464窓の件数・MAE/RMSE・品質状態と、4,392個のfeature PSI/分布状態を再計算した。
後者はpandas/sklearn/製品monitor関数をimportせず、元CSVの件数からexact lagを作り、bisect histogramとmath.logを使った。
両方成功。referenceの再作成やbins変更はしていない。[証拠索引](reviews/20261114-capital-monitor-checks.json)。
次の作業は[実候補の隔離runtime手順](capital-runtime-handoff.md)を参照する。

追補：[実API・監視受入](capital-runtime-acceptance.md)で独立したHTTP/DB producerを実行し、全1,464窓の一致を確認した。
このoffline原本自体を実producerへ読み替えず、別run ID/CSV/marker/DBの証拠を保持する。
