# 証拠の入口と5分の説明手順（T14・初版）

現在の優先度は[計画§11.6の最新残件](../../261014/plan_mlops.md#remaining-work-latest)、コマンド・失敗・仮説の履歴は[作業log](../work_log.md)を正規の入口とする。
レビューJSONはその時点のsnapshotなので、後続のCI確認JSONと区別する。記録だけの後続commitを、先行CIで試験した実装commitとは呼ばない。

| 対象 | 実装commit / CI | 確認できる証拠 | 限界 |
|---|---|---|---|
| **全観測期間のoffline照合（最新）** | `f6b3cad` / [CI41939041432](https://github.com/kenobix/Claude_practice/actions/runs/41939041432) | [最終CI索引](reviews/20261105-clock-campaign-ci-checks.json)。37専用を含む519件、4区間/policy/plan/hash・UTC/校正/identity/欠測・strict future、16 member/hash・archive-only/cleanup。[操作](clock-campaign-review.md)・[次の採取契約](clock-campaign-acquisition.md) | 保存数値の合成照合のみ。全資格はfailed/incomplete、真正性/独立源/修復/実採取は未了 |
| **時計資格設計・offline進行率component** | `f26b334` / [CI41845551048](https://github.com/kenobix/Claude_practice/actions/runs/41845551048) | [CI索引](reviews/20261104-clock-rate-ci-checks.json)。最終18件含む全482件・全system、16 artifact member/SHA・archive-only検算・cleanup。[使用手順](clock-rate-review.md) | componentのみ。QPC校正・外部UTC・identity/採取真正性・全campaignと実時計資格は未了 |
| **backend時刻経路・左端buffer（891a420以降）** | 保存済みELF静的解析 / 左端原本2file / local pipeline8件 | [検証索引](reviews/20261104-backend-boundary-checks.json)。PIE relocation/interface/CALL照合・5拒否コピー、202212取得、2parserで379,310行の境界監査 | 新しいruntime setter/PHC所有・時計資格ではない。正式snapshot/protocol/fit・新しいCI受入は未実施 |
| **initd管理層とT10入力準備（2026-11-04）** | 基点`d8ec164`後のlocal読取・文書のみ | [検証索引](reviews/20261104-clock-readiness-checks.json)。Docker Desktopの35thread前後対応と負方向settimeofday、7拒否検証、既存45 ZIP/hash/header、T10開始手順 | PHC/socket採取・飽和時の内部理由・時計資格は未了。新protocol/fit・CIの新しい受入ではない |
| **chronyd配布buildと内部計算・reader（最新）** | local数値採取 / reader実装`f45733a`・[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704) | [local時計索引](reviews/20261103-chrony-runtime-checks.json)・[CI索引](reviews/20261103-chrony-runtime-ci-checks.json)。24対の非飽和計算、うち16対kernel書込、cleanup、reader17件/全464件・16artifact/hash | 稼働uprobeはlocal。tick11000時の内部値・管理者採取・時計資格は未完了 |
| **MLflow noload互換調査** | 基点`298c6c8`後の独立process | [ORM索引](reviews/20261103-orm-compatibility-checks.json)。通常import/strict負例、最新3.17.2 source、publisher6件 | 上流未修正・lock不変。未公開SQLAlchemyやMCP全機能の試験ではない |
| **CI artifact action保守・時計reader（最新）** | `7174a1f`→`5a687a7` / [41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089) | [CI索引](reviews/20261103-upload-action-ci-checks.json)。463件、Node24/ZIP維持、16member/hash・archive-only検算、正常/TERM/対応復元cleanup | 時計readerは合成16件。初期namespaceでのPHC/socket本採取・時計資格は未完了 |
| **現在の自然future拒否の変更元** | `7174a1f` / 読取・FakeRegistryのみ | [時計索引](reviews/20261103-clock-followup-checks.json)、[host追加読取](reviews/20261103-clock-extra-checks.json)、[説明](clock-followup.md)。生成/検証区間内のtimesyncd step、tag不変/blocked | 過去停止の全原因やchronyd内部理由・host精度は未確定。実DB/registry/時計設定は変更していない |
| **T17b/T07a/T16補足（最新）** | `4a4474e` / [41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990) | [最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)。447件、API/state/操作台帳、全体deadline、中断・遅延応答、現imageのCaddy/TERM cleanup、16 member/hash照合 | 合成受入。実Capital producer/時計資格は別条件 |
| T07a/T17a（2026-11-01時点） | `f341e94` / [41298859518](https://github.com/kenobix/Claude_practice/actions/runs/41298859518) | [local受入](reviews/20261101-t07-t17-checks.json)・[CI索引](reviews/20261101-t07-t17-ci-checks.json)。実Caddy切替/SIGKILLと台帳復旧はlocal、既存system/PGはCI | local復旧harnessのmodel/registryはdouble。新harness自体のCI接続は未了 |
| T13a | `f1cc9ff` / [41300378098](https://github.com/kenobix/Claude_practice/actions/runs/41300378098) | [HTTP障害CI](reviews/20261101-t13-http-ci-checks.json)。部分保存、commit後の応答喪失、同値retry、競合拒否、cleanup | 使い捨て実MLflow HTTPに対する合成決定。実人間の採否ではない |
| T16 | `2492abc` / [41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156) | [Sol review](reviews/20261102-t16-sol-review.json)・[CI索引](reviews/20261102-t16-ci-checks.json)。4原本/manifest/hash、archive-only別process再計算、cleanup | 1050応答からwarmup50を除く1000件。合成計測で実性能・時計資格・承認を証明しない |
| T07b/T17b部分（2026-11-02時点） | `2414b20` / [41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218) | [CI索引](reviews/20261102-t07b-ci-checks.json)・[復元契約](deployment-recovery.md)・[CLI診断](deployment-observability.md)。419件/対応復元/hash/cleanup成功 | T07実producer、T17b API metadata/全体timeoutは未完了 |

## T01〜T17の記録対応

以下は保存済みレビューJSONからの転記で、過去CIを今回再実行・再取得したという意味ではない。
SHA欄はartifact ZIPの先頭12文字。full SHA・member別SHA・raw logの相対pathは各索引にある。
同じtaskに複数commitがある場合、表にはその索引で最後にCI確認した実装を載せる。

| task | CI確認済み実装 / run | artifact ZIP SHA先頭 | 正規の索引と残条件 |
|---|---|---|---|
| T01/T02 | `29ed879` / [40977087079](https://github.com/kenobix/Claude_practice/actions/runs/40977087079) | `748cd5f0a3ee` | [必須証拠schemaと拒否](reviews/20261030-p0-evidence-checks.json)。実producer証拠は後続依存 |
| T03 | `24115cf` / [40999776550](https://github.com/kenobix/Claude_practice/actions/runs/40999776550) | `794bed34a655` | [要求journal](reviews/20261030-t03-checks.json)。自然停止の歴史的原因/時計資格は未解決 |
| T04 | `df292db` / [40980116840](https://github.com/kenobix/Claude_practice/actions/runs/40980116840) | `be1ff6fe039e` | [training reference/PSI](reviews/20261030-t04-checks.json)。実候補の監視は別証拠 |
| T05 | `2365d8e` / [41005083367](https://github.com/kenobix/Claude_practice/actions/runs/41005083367) | `0aca6e638a4b` | [合成監視10ケースと境界](reviews/20261030-t05-checks.json) |
| T06a | `df4c3b9` / [41038726909](https://github.com/kenobix/Claude_practice/actions/runs/41038726909) | `c7ab3ac28cea` | [SQLite読取照合](reviews/20261030-t06a-checks.json)の`sol_review.ci`。実OSのfile権限/busy timeout補足は4a4474e時点でlocal受入済み。最終CI索引も参照 |
| T06b | `164ac53` / [41068758500](https://github.com/kenobix/Claude_practice/actions/runs/41068758500) | `9d82e8d21062` | [PostgreSQL読取照合](reviews/20261030-t06b-checks.json)。実HTTP監視への接続は後続 |
| T07/T17 | `f341e94`→`2414b20`→`4a4474e` | 上段参照 | [切替/復旧](deployment-recovery.md)と[診断](deployment-observability.md)。subtaskと実候補の境界を保持 |
| T08 | `20abdab` / [41263492531](https://github.com/kenobix/Claude_practice/actions/runs/41263492531) | `f8b4d540112d` | [旧schema精度移行/別volume復元](reviews/20261101-t08-checks.json)。既存実DBへは未適用 |
| T09 | 実装CIではなく原本公開待ち | 対象外 | [計画§11.6の最新残件](../../261014/plan_mlops.md#remaining-work-latest)。HEAD成功だけでは原本取得/hash照合完了ではない |
| T10/T11/T12 | 実候補の独立protocol/一度評価/blocked運用測定は未実施 | 対象外 | [計画§11.6の最新残件](../../261014/plan_mlops.md#remaining-work-latest)。合成CIを実精度・実性能へ転用しない |
| T13a/T13b | `f1cc9ff`のHTTP合成受入 / 実採否は未実施 | 上段の索引参照 | [HTTP CI](reviews/20261101-t13-http-ci-checks.json)。実採否には同一candidateの必須producer証拠が必要 |
| T14 | この文書・計画・作業log | 対象外 | 文書の整理であり、新しい実験成功を作らない |
| T15 | `5cc5a89` / [41264954179](https://github.com/kenobix/Claude_practice/actions/runs/41264954179) | `62f54b9fe779` | [monitor persistence CI接続](reviews/20261101-t15-checks.json) |
| T16 | `2492abc` / [41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156) | `9d105b720202` | [archive3本/別process再計算](reviews/20261102-t16-ci-checks.json)。内側archive SHAと外側ZIP SHAを区別 |

artifactの公開保管は14日で失効し得る。索引のSHAは取得時のbytesを示し、期限切れartifactを再取得できる保証ではない。
raw logや一時dumpをcommitしたと主張しない。手元の`data/reports/`に残す原本は公開索引の相対path・bytes・SHAで照合する。
T16のarchiveだけが手元にある場合の再計算手順は[計測証拠アーカイブ](measurement-evidence-archive.md)を使う。
CI artifact以外の実DB原本、`.env`、認証値、実候補データを説明用にコピーしない。

## 5分で説明する順番

1. **0〜1分:** [README](../README.md)で、需要予測モデルと、それを安全に登録・監査・切替する基盤の目的を説明する。UCIの実験履歴と合成Capital試験の区別を先に伝える。
2. **1〜2分:** MLflowは実験・登録モデルの管理画面。`training-report.json`のMAE/RMSEは予測誤差で、APIの応答時間ではない。学習・評価済み期間を次候補の選択へ再利用しない。
3. **2〜3分:** T16のCI索引で実装commit・run・artifact SHA・1000 measured・p95・warmup除外を示す。p95は応答時間であり、合成PC試験から本番SLOを合格としない。
4. **3〜4分:** 配信復旧文書で、同一request IDの再送は元の予測を返すこと、台帳とartifactを対応させて復元すること、timeoutでは未確定を残すことを示す。
5. **4〜5分:** 計画書で、時計資格、独立した新データ、実候補の精度/監視/復旧/承認がまだ必要と説明する。合成成功を実採否へ格上げしない。

説明のためにlive環境を変更する必要はない。CLIの確認や新しい障害試験は、[runbook](runbook.md)に従い別途実行する。
用語は[用語集](glossary.md)、時計の限界は[中断と時計診断](interruption-clock-diagnostics.md)を参照する。

2026-11-03更新: 配信の名前/理由は申告情報で、APIのmatched_pendingは処理中、matched_completedも認証済み承認の証明ではない。新しいCI artifact ZIP SHA先頭は`ae1f101f77c7`。操作の読取とtimeout時の手順は[配信診断](deployment-observability.md)へ。

2026-11-05追補：[合成時計採取](clock-fake-acquisition.md)は、送信前intentを含むraw journalとidentity原本から
別processで全campaignを作り、既存offline CLIへ渡す受入です。
[試行/失敗/hash索引](reviews/20261105-clock-acquisition-checks.json)を入口とし、
[独立源の選定不足](clock-source-readiness.md)や実時計資格の未達と分けて説明します。

合成採取の最終受入は実装a5c1605/[CI42037278421](https://github.com/kenobix/Claude_practice/actions/runs/42037278421)。
[最終索引](reviews/20261105-clock-acquisition-ci-checks.json)の549件/専用30件、artifact/hash・cleanupと、
初回28件/追加30件のlocal snapshotを区別する。CIの既存16memberにはfake原本を追加しておらず、local原本の照合path/hashを別に保存している。

2026-11-06：[fake固定周期/終端](clock-fixed-schedule.md)は固定した仮想QPCの予定表だけを試験する。
予定番号と実送信数を区別し、欠測・応答遅延・中断/回収、同phaseの既知違反を保存する。
実sourceの真正性・不確かさや、実時間を観測した証拠と取り違えない。

固定周期はc1c5dcb/[CI42126656283](https://github.com/kenobix/Claude_practice/actions/runs/42126656283)の全568件/専用19件・全systemで受入済み。
[最終索引](reviews/20261106-clock-scheduler-ci-checks.json)へ16 artifact/hash/archive-only/cleanupとraw改変6件拒否を結合した。
中断したwatchの終了値は不明で、再開後に取得したexact SHA/runの成功を使う。T09の新公開/取得は別の証拠単位。

T09は[10月原本取得/右端境界検査](t09-october-acquisition.md)・[取得索引](reviews/20261106-t09-checks.json)を参照する。
実際の原本検査と、時計の合成CI/実Capital精度を区別する。47原本の保管だけでT10/学習/採否の完了を宣言しない。

T10は[原本snapshot review](t10-snapshot-review.md)で初回意図中断・独立sort照合・新producerを分けて読む。
[集計改善CI](reviews/20261106-t10-grouping-ci-checks.json)の569件/16artifact/hash/cleanupは合成契約の証拠。
実原本23百万行の照合はlocal保存原本が根拠で、実fit/採否/時計資格の証拠へ置き換えない。
