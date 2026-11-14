# Capital初回候補：実データの精度評価（2026-11-14）

固定した47原本のsnapshot/protocolを変えず、隔離したsource/venvでvalidationを1回、
その合格後に同じskops artifactを再学習せずfinalを1回評価した。**両精度gateは合格、配信許可はblocked**。
[開始条件の改訂](product-completion-policy.md)、[検証索引](reviews/20261114-capital-evaluation-checks.json)を参照。

| 期間 | 行数 | 候補MAE | baseline MAE | 候補RMSE | baseline RMSE |
|---|---:|---:|---:|---:|---:|
| validation：2026-06-01〜08-01未満 | 1,464 | 137.392476 | 226.003415 | 237.549095 | 361.975333 |
| final：2026-08-01 03時〜10-01未満 | 1,461 | 143.693098 | 228.689254 | 247.608899 | 371.052215 |

単位は1時間あたりのtrip開始件数。baselineは24時間前の実績、欠測時には訓練行だけの曜日/時刻平均等へfallbackする固定実装。
同じ行・同じ正解でMAE厳密改善かつRMSE以下の条件を満たす。曜日/週末/通勤帯は件数付き診断のみ。
finalの夕方通勤帯MAEは候補313.931412、baseline411.032787（183行）。全体より誤差が大きく、個別業務目標を満たすと宣言しない。
精度改善は現在の需要・本番SLO・事業効果・時計資格の証明ではない。

## どこに保存したか

repository rootからのpath。全実体はGit対象外で、現MLflowへ登録していないため既存localhost UIには新runは出ない。

- 専用root：`261021/data/reports/20261114-product-resume/`
- source：同rootの`project/`。commit `767760d04fcb3a20659d4f7a6d5188fb9b8de65c`から298追跡fileをコピーし、`.env`を含めない。
- venv：同rootの`venv/`。元venvコピー後、欠けたlock指定build tools2件だけをコピー先へinstall。105指定版とpip check成功。
- 候補：`project/data/candidates/e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b/`
- final：`project/data/reports/capital-bikeshare/final-evaluation.json`、行別の`final-evaluation.predictions.csv`
- **単一の正式消費台帳**：`261021/data/evaluations/consumption-ledger.json`。専用projectの`data/evaluations`はここへの参照。両窓completed、各1 attempt。
- CSV/manifest/protocolと原本は元の固定pathを読取参照。再集計・再発行・原本変更はしていない。

候補artifact SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
candidate report SHA：`74c0417ba661fd8b32d771349df092760fc7a7f8f3caf1d86b879df0031653cc`。
final report SHA：`8312ed9f22d9f93c1d55f736369ad5f2e66839626297b160800b5a49e9860893`。

## 検証と限界

正式CLIからledger開始→fit→immutable bundle→ledger完了を実行。
final CLIはartifact/validation/訓練専用監視referenceを検証してからfinal窓を開始し、candidateをfitしない。
別processの標準CSV/mathでvalidation/finalの全行、ラベル+3h、MAE/RMSE、finalの4 segmentと実CSVの正解、
hash/台帳を照合した。製品の`verify_candidate_for_staging`も読取で成功し、registry書込は実行していない。

既存のprotocol10・gate3・training monitor reference26テストを専用環境で実行し全39成功。
製品source/schema/writer/lock/workflowは変更せず、全suite/新CIを合格したとは主張しない。
計測対象は履歴予測精度。hostは旧DESKTOP-J589D75、精密時計は未資格・調査保留。
UTCは実行境界の順序を確認した未資格値で、実行秒数/性能倍率の測定結果には使わない。
既知の集計統計を見た履歴評価でありblindな将来試験でも、他checkout全ての未消費を認証したものでもない。

## 次にすること

**このvalidation/final窓で再fit・再採点して選定をやり直さない。** desktopへ移す場合も候補・final・消費台帳を一緒に移す。
次は同candidate/artifactのT02実producer/T12監視・予測監査・復旧・metadata・運用測定。
staging登録は既存実registryへ行わず、専用環境のidentityと分離を確認してからblocked状態で行う。
review/別承認と全実証が揃うまでT13b採否は未完了。時計研究を自動的に再開しない。

追補：[保存済み予測の監視診断](capital-offline-monitoring.md)を完了。誤差警告234窓を含むため、全体精度の合格だけで採用せず、実producer証拠とともにreviewする。[次のruntime作業](capital-runtime-handoff.md)の入力確認は成功、運用実測は未実施。
