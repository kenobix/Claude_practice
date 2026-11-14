# 証拠の読み方・過去review・引継ぎ

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [証拠の入口と5分の説明手順（T14・初版）](#evidence-guide)
- [T06aをGPT-6 Lunaへ渡す依頼](#t06-luna-handoff)
- [次の依頼: T08 旧REAL schemaの移行・復元検証](#t08-sol-handoff)
- [GPT-6 Lunaへの次の依頼（T15）](#t15-luna-handoff)
- [T16: 合成CI証拠の保存と別processでの再計算](#t16-luna-handoff)
- [T17b残件とT07a CI接続のSol向け引継ぎ](#t17b-sol-handoff)
- [前回確認後の差分レビューとR06の判断](#20261028-r06-delta-review)
- [2026-10-28 差分監査と残作業](#20261028-review)
- [R06 staging実装後のチェックポイントレビュー](#20261028-staging-checkpoint-review)
- [合成staging統合検証の実行スクリプト（記録）](#20261028-synthetic-staging-harness)
- [2026-10-30 checkpoint差分レビュー](#20261030-checkpoint-review)

<a id="evidence-guide"></a>

## evidence-guide

<a id="evidence-guide--証拠の入口と5分の説明手順（t14・初版）"></a>

### 証拠の入口と5分の説明手順（T14・初版）

**2026-11-14:** [Capital実候補のvalidation/final結果](capital.md#capital-first-evaluation)と[実行/検算索引](reviews/20261114-capital-evaluation-checks.json)を追加。両窓消費済み、精度gate合格。運用gateと最終採否は未完了、時計研究は保留。

追加：[実候補のoffline監視](capital.md#capital-offline-monitoring)と[索引](reviews/20261114-capital-monitor-checks.json)。数値診断と運用producer受入を分け、警告を残したまま次の隔離runtimeへ進む。

現在の優先度は[計画§11.6の最新残件](../../261014/plan_mlops.md#remaining-work-latest)、コマンド・失敗・仮説の履歴は[作業log](../work_log.md)を正規の入口とする。
レビューJSONはその時点のsnapshotなので、後続のCI確認JSONと区別する。記録だけの後続commitを、先行CIで試験した実装commitとは呼ばない。

| 対象 | 実装commit / CI | 確認できる証拠 | 限界 |
|---|---|---|---|
| **全観測期間のoffline照合（最新）** | `f6b3cad` / [CI41939041432](https://github.com/kenobix/Claude_practice/actions/runs/41939041432) | [最終CI索引](reviews/20261105-clock-campaign-ci-checks.json)。37専用を含む519件、4区間/policy/plan/hash・UTC/校正/identity/欠測・strict future、16 member/hash・archive-only/cleanup。[操作](clock-implementation.md#clock-campaign-review)・[次の採取契約](clock-implementation.md#clock-campaign-acquisition) | 保存数値の合成照合のみ。全資格はfailed/incomplete、真正性/独立源/修復/実採取は未了 |
| **時計資格設計・offline進行率component** | `f26b334` / [CI41845551048](https://github.com/kenobix/Claude_practice/actions/runs/41845551048) | [CI索引](reviews/20261104-clock-rate-ci-checks.json)。最終18件含む全482件・全system、16 artifact member/SHA・archive-only検算・cleanup。[使用手順](clock-implementation.md#clock-rate-review) | componentのみ。QPC校正・外部UTC・identity/採取真正性・全campaignと実時計資格は未了 |
| **backend時刻経路・左端buffer（891a420以降）** | 保存済みELF静的解析 / 左端原本2file / local pipeline8件 | [検証索引](reviews/20261104-backend-boundary-checks.json)。PIE relocation/interface/CALL照合・5拒否コピー、202212取得、2parserで379,310行の境界監査 | 新しいruntime setter/PHC所有・時計資格ではない。正式snapshot/protocol/fit・新しいCI受入は未実施 |
| **initd管理層とT10入力準備（2026-11-04）** | 基点`d8ec164`後のlocal読取・文書のみ | [検証索引](reviews/20261104-clock-readiness-checks.json)。Docker Desktopの35thread前後対応と負方向settimeofday、7拒否検証、既存45 ZIP/hash/header、T10開始手順 | PHC/socket採取・飽和時の内部理由・時計資格は未了。新protocol/fit・CIの新しい受入ではない |
| **chronyd配布buildと内部計算・reader（最新）** | local数値採取 / reader実装`f45733a`・[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704) | [local時計索引](reviews/20261103-chrony-runtime-checks.json)・[CI索引](reviews/20261103-chrony-runtime-ci-checks.json)。24対の非飽和計算、うち16対kernel書込、cleanup、reader17件/全464件・16artifact/hash | 稼働uprobeはlocal。tick11000時の内部値・管理者採取・時計資格は未完了 |
| **MLflow noload互換調査** | 基点`298c6c8`後の独立process | [ORM索引](reviews/20261103-orm-compatibility-checks.json)。通常import/strict負例、最新3.17.2 source、publisher6件 | 上流未修正・lock不変。未公開SQLAlchemyやMCP全機能の試験ではない |
| **CI artifact action保守・時計reader（最新）** | `7174a1f`→`5a687a7` / [41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089) | [CI索引](reviews/20261103-upload-action-ci-checks.json)。463件、Node24/ZIP維持、16member/hash・archive-only検算、正常/TERM/対応復元cleanup | 時計readerは合成16件。初期namespaceでのPHC/socket本採取・時計資格は未完了 |
| **現在の自然future拒否の変更元** | `7174a1f` / 読取・FakeRegistryのみ | [時計索引](reviews/20261103-clock-followup-checks.json)、[host追加読取](reviews/20261103-clock-extra-checks.json)、[説明](clock-history.md#clock-followup)。生成/検証区間内のtimesyncd step、tag不変/blocked | 過去停止の全原因やchronyd内部理由・host精度は未確定。実DB/registry/時計設定は変更していない |
| **T17b/T07a/T16補足（最新）** | `4a4474e` / [41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990) | [最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)。447件、API/state/操作台帳、全体deadline、中断・遅延応答、現imageのCaddy/TERM cleanup、16 member/hash照合 | 合成受入。実Capital producer/時計資格は別条件 |
| T07a/T17a（2026-11-01時点） | `f341e94` / [41298859518](https://github.com/kenobix/Claude_practice/actions/runs/41298859518) | [local受入](reviews/20261101-t07-t17-checks.json)・[CI索引](reviews/20261101-t07-t17-ci-checks.json)。実Caddy切替/SIGKILLと台帳復旧はlocal、既存system/PGはCI | local復旧harnessのmodel/registryはdouble。新harness自体のCI接続は未了 |
| T13a | `f1cc9ff` / [41300378098](https://github.com/kenobix/Claude_practice/actions/runs/41300378098) | [HTTP障害CI](reviews/20261101-t13-http-ci-checks.json)。部分保存、commit後の応答喪失、同値retry、競合拒否、cleanup | 使い捨て実MLflow HTTPに対する合成決定。実人間の採否ではない |
| T16 | `2492abc` / [41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156) | [Sol review](reviews/20261102-t16-sol-review.json)・[CI索引](reviews/20261102-t16-ci-checks.json)。4原本/manifest/hash、archive-only別process再計算、cleanup | 1050応答からwarmup50を除く1000件。合成計測で実性能・時計資格・承認を証明しない |
| T07b/T17b部分（2026-11-02時点） | `2414b20` / [41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218) | [CI索引](reviews/20261102-t07b-ci-checks.json)・[復元契約](runbook.md#deployment-recovery)・[CLI診断](runbook.md#deployment-observability)。419件/対応復元/hash/cleanup成功 | T07実producer、T17b API metadata/全体timeoutは未完了 |

<a id="evidence-guide--t01〜t17の記録対応"></a>

#### T01〜T17の記録対応

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
| T07/T17 | `f341e94`→`2414b20`→`4a4474e` | 上段参照 | [切替/復旧](runbook.md#deployment-recovery)と[診断](runbook.md#deployment-observability)。subtaskと実候補の境界を保持 |
| T08 | `20abdab` / [41263492531](https://github.com/kenobix/Claude_practice/actions/runs/41263492531) | `f8b4d540112d` | [旧schema精度移行/別volume復元](reviews/20261101-t08-checks.json)。既存実DBへは未適用 |
| T09 | 2026-11-06の公式10月原本取得・構造/右端監査 | 対象外 | [取得索引](reviews/20261106-t09-checks.json)。過去の404は履歴、47原本が揃った |
| T10 | 2026-11-08の正式snapshot/protocol静的受入 | 対象外 | [snapshot review](capital.md#t10-snapshot-review)。47原本・32,849全hour一致、固定29,919/1,464/1,461。実fitの許可や消費予約ではない |
| T11/T12 | 実候補の一度評価/blocked運用測定は未実施 | 対象外 | [計画§11.6の最新残件](../../261014/plan_mlops.md#remaining-work-latest)。時計資格等の前提待ち。合成CIを実精度・実性能へ転用しない |
| T13a/T13b | `f1cc9ff`のHTTP合成受入 / 実採否は未実施 | 上段の索引参照 | [HTTP CI](reviews/20261101-t13-http-ci-checks.json)。実採否には同一candidateの必須producer証拠が必要 |
| T14 | この文書・計画・作業log | 対象外 | 文書の整理であり、新しい実験成功を作らない |
| T15 | `5cc5a89` / [41264954179](https://github.com/kenobix/Claude_practice/actions/runs/41264954179) | `62f54b9fe779` | [monitor persistence CI接続](reviews/20261101-t15-checks.json) |
| T16 | `2492abc` / [41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156) | `9d105b720202` | [archive3本/別process再計算](reviews/20261102-t16-ci-checks.json)。内側archive SHAと外側ZIP SHAを区別 |

artifactの公開保管は14日で失効し得る。索引のSHAは取得時のbytesを示し、期限切れartifactを再取得できる保証ではない。
raw logや一時dumpをcommitしたと主張しない。手元の`data/reports/`に残す原本は公開索引の相対path・bytes・SHAで照合する。
T16のarchiveだけが手元にある場合の再計算手順は[計測証拠アーカイブ](monitoring.md#measurement-evidence-archive)を使う。
CI artifact以外の実DB原本、`.env`、認証値、実候補データを説明用にコピーしない。

<a id="evidence-guide--5分で説明する順番"></a>

#### 5分で説明する順番

1. **0〜1分:** [README](../README.md)で、需要予測モデルと、それを安全に登録・監査・切替する基盤の目的を説明する。UCIの実験履歴と合成Capital試験の区別を先に伝える。
2. **1〜2分:** MLflowは実験・登録モデルの管理画面。`training-report.json`のMAE/RMSEは予測誤差で、APIの応答時間ではない。学習・評価済み期間を次候補の選択へ再利用しない。
3. **2〜3分:** T16のCI索引で実装commit・run・artifact SHA・1000 measured・p95・warmup除外を示す。p95は応答時間であり、合成PC試験から本番SLOを合格としない。
4. **3〜4分:** 配信復旧文書で、同一request IDの再送は元の予測を返すこと、台帳とartifactを対応させて復元すること、timeoutでは未確定を残すことを示す。
5. **4〜5分:** 計画書で、新原本取得と事前protocolの静的受入は完了し、時計資格と実候補の精度/監視/復旧/承認がまだ必要と説明する。合成成功を実採否へ格上げしない。

説明のためにlive環境を変更する必要はない。CLIの確認や新しい障害試験は、[runbook](runbook.md#runbook)に従い別途実行する。
用語は[用語集](architecture.md#glossary)、時計の限界は[中断と時計診断](clock-history.md#interruption-clock-diagnostics)を参照する。

2026-11-03更新: 配信の名前/理由は申告情報で、APIのmatched_pendingは処理中、matched_completedも認証済み承認の証明ではない。新しいCI artifact ZIP SHA先頭は`ae1f101f77c7`。操作の読取とtimeout時の手順は[配信診断](runbook.md#deployment-observability)へ。

2026-11-05追補：[合成時計採取](clock-implementation.md#clock-fake-acquisition)は、送信前intentを含むraw journalとidentity原本から
別processで全campaignを作り、既存offline CLIへ渡す受入です。
[試行/失敗/hash索引](reviews/20261105-clock-acquisition-checks.json)を入口とし、
[独立源の選定不足](clock-operations.md#clock-source-readiness)や実時計資格の未達と分けて説明します。

合成採取の最終受入は実装a5c1605/[CI42037278421](https://github.com/kenobix/Claude_practice/actions/runs/42037278421)。
[最終索引](reviews/20261105-clock-acquisition-ci-checks.json)の549件/専用30件、artifact/hash・cleanupと、
初回28件/追加30件のlocal snapshotを区別する。CIの既存16memberにはfake原本を追加しておらず、local原本の照合path/hashを別に保存している。

2026-11-06：[fake固定周期/終端](clock-implementation.md#clock-fixed-schedule)は固定した仮想QPCの予定表だけを試験する。
予定番号と実送信数を区別し、欠測・応答遅延・中断/回収、同phaseの既知違反を保存する。
実sourceの真正性・不確かさや、実時間を観測した証拠と取り違えない。

固定周期はc1c5dcb/[CI42126656283](https://github.com/kenobix/Claude_practice/actions/runs/42126656283)の全568件/専用19件・全systemで受入済み。
[最終索引](reviews/20261106-clock-scheduler-ci-checks.json)へ16 artifact/hash/archive-only/cleanupとraw改変6件拒否を結合した。
中断したwatchの終了値は不明で、再開後に取得したexact SHA/runの成功を使う。T09の新公開/取得は別の証拠単位。

T09は[10月原本取得/右端境界検査](capital.md#t09-october-acquisition)・[取得索引](reviews/20261106-t09-checks.json)を参照する。
実際の原本検査と、時計の合成CI/実Capital精度を区別する。47原本の保管だけでT10/学習/採否の完了を宣言しない。

T10は[原本snapshot review](capital.md#t10-snapshot-review)で初回意図中断・独立sort照合・新producerを分けて読む。
[集計改善CI](reviews/20261106-t10-grouping-ci-checks.json)の569件/16artifact/hash/cleanupは合成契約の証拠。
実原本23百万行の照合はlocal保存原本が根拠で、実fit/採否/時計資格の証拠へ置き換えない。

2026-11-08：[T10の正式snapshot/protocol](capital.md#t10-snapshot-review)は保存値・splitと原本の静的照合です。
独立sortが検出した重複205行を除いて全hour一致、正式source11件とprotocol/CSV/manifestのSHAを結合しました。
時計資格・モデルの誤差・実運用の性能はまだ測定していません。過去の原本待ち/未固定という記載は当時の状態です。

SQL改善9147027/[CI42447659584](https://github.com/kenobix/Claude_practice/actions/runs/42447659584)は全570件/対象10件、全systemが成功。
[正式pair/protocol・CI最終索引](reviews/20261108-t10-snapshot-checks.json)で16 artifact/SHA・archive-only再計算・cleanupを照合した。
原本47件の実集計/独立照合はlocalの保存原本が根拠であり、このCIが実データを集約・学習したという意味ではない。

2026-11-08追補：[合成時計raw bundle](clock-implementation.md#clock-raw-bundle)と[最終CI索引](reviews/20261108-clock-bundle-ci-checks.json)。
修正後067d3ce/CI42449911296の全588件/固定16 artifact/hash/archive-only/cleanupまで受入。
raw4原本をZIPから別processで再計算する合成可搬性の証拠で、実源の真正性/時計資格/T02実producerの証拠ではない。
[次に必要な入力](clock-operations.md#clock-input-request)を未取得のまま、実fit・採点・採否へ進めない。

<a id="t06-luna-handoff"></a>

## t06-luna-handoff

<a id="t06-luna-handoff--t06aをgpt-6-lunaへ渡す依頼"></a>

### T06aをGPT-6 Lunaへ渡す依頼

T06のSol設計基点は `f9e228dd427d4853a4146356b0c2de07e71682d9`。この依頼書と[読取照合契約](monitoring.md#monitor-reconciliation)を含む設計commit以降のHEADから始める。実装前にgit statusと最新commitを確認し、他の変更があれば混ぜない。policy ID `monitor-run-reconciliation-v1` と状態/終了値/比較ルールはSolで確定済みである。

以下をそのまま実装依頼として使える。

```text
GPT-6 Lunaで、計画T06のうちT06a（SQLite版）を実装してください。
最初にAGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.mdのT06節、
261021/docs/monitor-reconciliation.md、261021/docs/reviews/20261030-t06-design.jsonを読んでください。

追加する公開部品はsrc/bike_mlops/monitor_inspection.pyのinspect_monitor_run、
CLIは既存monitorと並ぶmonitor-inspect --csv PATH --run-id UUIDです。
上記契約のJSON項目、状態の優先順、exit 0/1/2、上限、S01〜S17/S20/S22/S23を実装・検証してください。
PostgreSQLは接続せずunsupported_input/2にし、S18/S19/S21とPostgreSQL統合はSolへ残してください。

SQLiteは既存DBをmode=ro、query_only、read transactionで読みます。
Database.initialize/connect、CLIの_databaseは使わないでください。
report/DB/marker/旧runの書換え、marker生成、再送、採点、MLflow操作は実装しないでください。
markerなしはDB全件一致でもunconfirmed、DB不在は未送信と断定しないでください。
UUID列のない旧形式はlegacy_unverifiableです。秘密情報入りDSNや例外messageを出さないでください。
通常ファイル以外や読取中の変更、重複窓、壊れたJSON、他profile、他UUIDを安全に扱ってください。

編集範囲は次の「許可する編集範囲」に従ってください。
新しい閾値、DB/schema/lock/依存の変更、契約状態の追加が必要なら変更せず、Solへの具体的な依頼文を残してください。
T03/T13の不安定な試験を直す作業はこの範囲に含めません。

対象testと既存monitor保存/CLIの回帰testを実行し、コマンド・stdout/stderr・exit code・
成功/失敗・制限を新規logへ保存してください。未実施PostgreSQLを成功扱いしないでください。
T06a実装完了時点でSol review待ちとして記録し、まだcommit/pushしないでください。
最後にSolへ、未commit差分・受入結果・残件をreview/検証してcommit/pushする依頼文を出してください。
```

<a id="t06-luna-handoff--許可する編集範囲"></a>

#### 許可する編集範囲

- 新規 `src/bike_mlops/monitor_inspection.py`、`tests/test_monitor_inspection.py`。
- `src/bike_mlops/cli.py` はhandler/import/新parser追加のみ。既存monitorの構造や共通mainの終了値処理を変更しない。
- `docs/monitor-reconciliation.md` は実装状態・実行結果・操作例の追記だけ。固定仕様の変更はSolへ戻す。
- `README.md`、`docs/runbook.md` の実装済みコマンド案内、`work_log.md`、`261014/plan_mlops.md`のT06a状態。
- 新規のT06a検証記録JSON（`docs/reviews/`）と、Git対象外の新規log（`data/reports/`）。既存証拠を上書きしない。

`db.py`、`monitoring.py`、学習/配信/runtime、T03/T13のcode/test、依存lock、workflow、`.env`、実DB/既存reportは編集対象外。Solのstorage probe `scripts/storage_checks.py storage-probe` も変更しない。

<a id="t06-luna-handoff--実装の受入と確認方法"></a>

#### 実装の受入と確認方法

合成fixtureはTemporaryDirectory内に作り、**setupのwriterだけ**既存Database.initializeとpersist_monitor_runを使用できる。reportは現CLI形式を持ち、profile/target/run UUID/window_count/period/latest_window/hashを一貫させる。T05の正常出力検査をそのままコピーするのではなく、欠落・破損・他profile・旧run・差分を注入して状態表を確認する。

SQLite readerのSQL traceと、initialize/connectへの呼出しを禁止するmockを用い、検査中にSELECT/PRAGMA/read transaction以外が発生しないことを確認する。テストfixtureでread-only UPDATEを試すのはS20だけ。各testでreport bytesとDB logical rowsが変わらないことも確認する。CLIのsubprocess終了値とJSON、他コマンドのparser互換を確認する。stdout/stderrにfakeの秘密文字列が出ない負例も必須。

次は261021から実行する予定の確認コマンド。今回の設計段階でこれらの**未実装testを実行済みとはしない**。実行時に新しいlog名へstdout/stderrを保存し、pipelineは `set -o pipefail` を使う。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 120 .venv/bin/python -m unittest discover -s tests -p test_monitor_inspection.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 90 .venv/bin/python -m unittest discover -s tests -p test_monitoring.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 90 .venv/bin/python -m unittest discover -s tests -p test_monitoring.py -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 120 .venv/bin/python -m unittest discover -s tests -p test_monitoring.py -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

全suiteとpost-push CI、PostgreSQLの受入はSol reviewで必要性と結果を確認する。FastAPI/実HTTPを含む全suiteは既知のsandbox待機制限がある。T03のSIGTERM待ちやT13のfuture時刻判定が再発したらraw logを保存し、Lunaで待ち時間・時刻拒否を緩和しない。

Solへの最終依頼には、設計commit、変更path、状態表caseの合否、全てのlog path/SHA、実行できなかった項目、PostgreSQLを未実装に保ったことを記載する。review待ちの段階なので、この作業単位ではcommit/pushを保留する。共通のGit運用はSol確認後の節目で再開する。

<a id="t08-sol-handoff"></a>

## t08-sol-handoff

<a id="t08-sol-handoff--次の依頼-t08-旧real-schemaの移行・復元検証"></a>

### 次の依頼: T08 旧REAL schemaの移行・復元検証

推奨担当はGPT-6.1 Sol、P1/D3。T06b後のmain最新commitを基点にする。既存UCIへ新コードを適用する前に必要で、新しい実データを待たずに専用fixtureで検証できる。T03/T13の非決定的失敗の原因追跡、T07/T17の配信復旧、T02実証producerは別の残件として保つ。以下は実装前に作成した依頼文である。2026-11-01にT08を実施し、到達点とAPI十進表示の制約は[移行検証](runbook.md#prediction-precision-migration)と[検証記録](reviews/20261101-t08-checks.json)へ記録した。再実行の必要性は最新の計画/検証記録で判断する。

次をそのまま依頼できる。

```text
GPT-6.1 Solとして、T08（P1/D3：PostgreSQL旧REAL→DOUBLE PRECISION移行の検証）を進めてください。
作業開始時のmain HEAD、AGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.md、
docs/reviews/20261030-t06b-checks.json、src/bike_mlops/db.py、既存の移行・監査・再送・復元手順を確認してください。

既存UCI/CapitalのDB・volume・registry・reportには変更を加えず、使い捨てPostgreSQLに
旧schema相当（predictions.predictedがREAL）と旧予測・profile metadataを作ってください。
T06bのmonitor_windowsのREAL型を今回の移行対象へ広げないでください。
既存initializeの移行処理を確認し、必要な修正は根拠と再現例を示して最小限に行ってください。

受入条件:
1. 移行前のschema/全logical rowsとbinary32へ丸められた値を保存し、
   移行後のDOUBLE PRECISIONが保存済みbinary32値を正確に保持すること。
   元の丸め前binary64値が復元できたと説明しないこと。
2. metadata、request ID、入力hash、model/artifact/profile/feature契約、件数と他tableが保持されること。
   移行後の新規予測はbinary64を保持すること。
3. 同一request ID/同じ入力の再送が元の保存予測を返し、異入力は409相当で拒否すること。
   同じ版/契約と既存のidempotency hashを検証し、移行を理由に旧行を書き直さないこと。
4. 移行transaction途中の接続喪失/中断で部分schema・部分rowが残らず、再開も一貫すること。
   実装が外部atomicでない場合は限界と拒否/復旧手順を明示し、失敗を隠さないこと。
5. 専用backupを別の専用DBへrestoreし、型・値・metadata・件数・同一ID再送を照合すること。
   migration前/後のbackupを区別し、旧DBで新initializeを暗黙実行しないこと。
6. 使い捨て資源の所有label/ID・loopback接続・cleanupを検査すること。

コマンド・stdout/stderr・exit code・失敗もdata/reports/の新規pathに保存し、
source/log/hashと受入case対応を新しいdocs/reviewsのJSONへ記録してください。
対象test→必要な回帰test/CIを実行し、work_log.md、runbook、plan_mlops.mdを更新してください。
既知T03/T13失敗が再発したらraw証拠を保持し、timeout/future拒否を緩めて成功扱いにしないでください。
成功した節目はgit statusと全差分を確認し、T08対象pathだけをcommit/pushしてください。
無関係な未追跡pathを含めず、合成結果を実Capitalの配信証拠に流用しないでください。
最後に、達成条件・未確認事項・次の依頼文を示してください。
```

Lunaへ切り出す場合も、移行値・transaction・再送互換の判断はSolで行う。固定された受入結果の一覧・用語/リンク整理（T14）だけならLunaへ依頼できる。

<a id="t15-luna-handoff"></a>

## t15-luna-handoff

<a id="t15-luna-handoff--gpt-6-lunaへの次の依頼（t15）"></a>

### GPT-6 Lunaへの次の依頼（T15）

T15 / P2 / D2。T06bとT08は専用PG受入をCIへ追加したが、既存monitor persistence scriptのCI接続はまだ残っている。次の依頼はこの固定範囲だけを対象とする。

```text
GPT-6 Lunaとして、T15（既存monitor persistence PostgreSQL試験のCI接続）を進めてください。
開始時のgit status/main HEAD、AGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.md、
.github/workflows/bike-mlops-contracts.yml、261021/scripts/storage_checks.py monitor-persistenceを確認してください。

編集範囲はworkflow、READMEまたはrunbookのCI説明、work_log.md、plan_mlops.md、
docs/reviewsの新規T15検証JSONだけです。既存script/source/schema/writer/依存lockは変更しないでください。

既存のsynthetic-system jobへ、install/pip check成功後に独立実行するPG保存試験stepを追加し、
MLFLOW_DISABLE_AGENT_HINT=1、timeout --signal=TERM --kill-after=10 300、pipefail/teeを使って
data/reports/monitor-persistence-postgres-ci.logを保存してください。既存stepが失敗しても、
install済みでcancelされていなければこの検証を実行する既存のif条件に合わせます。
既存artifactの公開pathへこのlogだけを追加し、失敗をcontinue-on-errorで隠さないでください。

まず使い捨てlocal Dockerで既存scriptを実行し、旧行保全・独立run・unique衝突時rollback・
marker欠落/遅延commitと専用container cleanupを確認してください。
YAML parseと全run blockのbash -n、必要な対象回帰testを行い、全試行のstdout/stderr/exit/hashを新規logへ残してください。
本番/既存UCI・CapitalのDBやregistryは操作せず、合成結果を実候補の配信証拠にしないでください。

script自体の失敗、schema/依存/cleanup設計の変更、T03/T13の非決定的失敗が見つかったら、
条件を緩めず証拠を保存し、具体的なSolへの修正依頼文を残してください。
範囲内を完了したらSol review待ちとして差分・検証JSON・log/hashをまとめ、
commit/pushはSol review後に行ってください。無関係な未追跡pathを含めないでください。
最後にSolへ、未commit差分と検証結果をreviewしCI確認後commit/pushする依頼文を提示してください。
```

<a id="t16-luna-handoff"></a>

## t16-luna-handoff

<a id="t16-luna-handoff--t16-合成ci証拠の保存と別processでの再計算"></a>

### T16: 合成CI証拠の保存と別processでの再計算

担当候補はGPT-6 Luna、Sol review前にcommit/pushしない。作業基点はこの文書を含むcommitをgit logで確認する。T13aのHTTP試験と、合成1,050件計測の証拠を混ぜない。本依頼では後者のT16を進める。

<a id="t16-luna-handoff--先に読むもの"></a>

#### 先に読むもの

- 計画書11.6のT16、最新work_log、.github/workflows/bike-mlops-contracts.yml。
- scripts/system_harness.py runのrun_synthetic_system/main/cleanup。
- src/bike_mlops/operational_validation.pyのrecheck_measurement_records。
- operational.py、operational_validation.py、capital_request_journal.pyの読取契約。

既存CI artifactはlogとT08資料だけで、計測原本はharness cleanupで消える。f341e94のCIは1,050件、p95=33.469804999981534msと報告しているが、そのartifactだけでは要求別再計算ができない。値を今回の期待値に固定せず、新実行の原本から再計算する。

<a id="t16-luna-handoff--編集範囲"></a>

#### 編集範囲

新規の合成証拠export/inspect scriptまたは専用module、そのunit test、既存harnessへの保存hook、workflowの明示allowlist、README/操作資料/計画/log/検証JSON。既存cleanup安全境界・MLflowタグ・配信gate・時刻検証・計測policy・DB schema/writer・lock・依存版を変更しない。必要ならSolへ戻す。実Capital/UCI DB/registry/配信やOS設定は操作しない。

<a id="t16-luna-handoff--固定する保存契約"></a>

#### 固定する保存契約

1. 明示opt-inの合成harnessだけが、新しい証拠出力directoryを作る。既存pathを上書きしない。自分で作った一時workspaceを確認し、実project/外部rootを受け付けない。symlink、path traversal、読取中の変更、想定外file種別を拒否する。
2. 対象candidate/attemptをharnessの戻り値と保存済み台帳で特定する。globで「最新」を選ばない。原本plan、当該report、operational ledger JSON、当該T03 request journalを**byte変更せず**copyする。要求別latencyはreport.measurementsに1,050行あるため二重の独自CSVを正本にしない。必要なら派生CSVと明記し原本と行単位照合する。
3. archive名は`synthetic-measurement-evidence.zip`、中身は`manifest.json`と、明示した4原本（`payload/plan.json`、`payload/report.json`、`payload/operational-ledger.json`、`payload/request-journal.jsonl`）のみを基本allowlistとする。元relative pathをmanifestへ対応付ける。file名や実際のjournal descriptorは既存契約を読んで解決し、推測で補わない。manifestにはschema、synthetic_only、candidate/attempt、各原本SHA/bytes、source commit/dirty、検証の限界を書く。ZIP SHAは外側の独立descriptorへ保存する。
4. .env、harness.env、DSN/認証情報、token、DB dump、model binary、任意directory、元CSV、raw environment/commandlineをarchiveへ入れない。原本JSONに認証情報や外部pathが含まれるなら、削除して同じSHAの原本だと偽らずexportを拒否してSolへ戻す。公表するのは合成資料のみ。fixture用ランダムpasswordにも同じ禁止を適用する。
5. ZIP検証はextractallで行わない。重複member、絶対/../symlink/余分file、過大bytes、hash違い、欠落を拒否し、明示memberだけ読む。検証過程はDB/MLflowへ接続しない。
6. ZIPだけを渡した別processでmanifest→台帳のcandidate/attempt→plan/report SHA→journal SHA/要求順/1050応答→1,000 measured行のp95を照合する。既存recheck_measurement_recordsを使い、nearest-rankのceil(0.95*n)-1、warmup50除外、measured1000、エラー0、ID digest、reportと数値一致を確認する。既存journal verifierを使う際に必要なroot対応は一時展開先へ限定し、実root/global設定や元原本を書き換えない。新しい計測や速い結果の選別はしない。
7. これは保存値の数値・結合の検算で、DB監査・時計資格・実運用性能・認証済み独立承認を新たに証明しない。operational_gate=not_evaluated/deployment_permission=blockedを維持する。

<a id="t16-luna-handoff--cleanupとci"></a>

#### cleanupとCI

成功実行では原本の削除前にarchiveを確定し、別process検算を行い、どちらが失敗しても既存finallyのcleanupを必ず試す。export失敗を握り潰さず、cleanup失敗と元失敗を両方記録する。失敗attemptを成功archiveとして補完しない。harnessが失敗したときの不完全資料保存は別schema/未完了を明記し、基本成功archiveと混在させない。

CI artifactへ追加するpathはarchive、外側SHA descriptor、再計算結果だけを明示列挙する。reports/**等の再帰globは禁止。既存log/T08/T13 allowlistは保持する。pipefail/timeout/非0伝播を維持する。local検証後、Sol reviewでcommit/pushし、同commitのActions成功・ZIP digest/内容hash・別process再計算・全cleanupを確認して初めてT16完了とする。

<a id="t16-luna-handoff--受入テストと報告"></a>

#### 受入テストと報告

正常1050行、warmup混入、latency改変、report/plan/journal/ledger/hash改変、別attempt、部分journal、欠落/余分/重複ZIP member、symlink/traversal、外部root、既存出力拒否、export失敗でもcleanup実施を検証する。timeout/SIGTERMは成功扱いにしない。まず新target、次に既存harness/measurement/journalの必要な回帰を実行し、コマンド・全試行・初回失敗・UTC/MONOTONIC・exit・log/hashを新しいignored reportsと公開検証索引へ残す。

可能な限り多くの独立した実装・負例検証・文書整合まで進める。source/schema/writer/gate/時計に判断が及ぶ変更や実DBの必要が出たら実行せず、その部分と理由をSolへ返す。終了時は全差分/statusを示し、Solへのreview依頼文を提示する。ローカル結果だけでCI成功と書かない。

<a id="t17b-sol-handoff"></a>

## t17b-sol-handoff

<a id="t17b-sol-handoff--t17b残件とt07a-ci接続のsol向け引継ぎ"></a>

### T17b残件とT07a CI接続のSol向け引継ぎ

実装基点`2414b2031dd21cdf425761b1cd29d43c7a355f66`。後続の記録commitも読み、[計画§11.6](../../261014/plan_mlops.md#remaining-work-20261030)を正規の優先順位とする。
段階表示・Docker client timeout・read-only旧state診断の現状は[配信処理の診断](runbook.md#deployment-observability)、対応復元の境界は[配信復旧](runbook.md#deployment-recovery)を確認する。

<a id="t17b-sol-handoff--1-api・state・台帳の操作metadata"></a>

#### 1. API・state・台帳の操作metadata

現APIはmodel/version/hashを提示するが、配信操作のID・承認者/理由を台帳へ結び付けて提示する経路はない。
新候補を起動する前にprepared行の操作IDを固定し、APIの起動環境からその1行だけを読む方式等を検討する。
「同じ版の最新行」をUTC順で選んではいけない。再配信やrollbackで同じ版が再登場するため、版だけの検索も承認metadataの根拠にしない。

受入条件:

- DB profile/target、操作ID、to_slot/to_version、preparedの固定artifact identityと、実際にロードしたモデルhashが一致すること。
- 表示する承認者/理由はユーザーが記録した申告情報で、認証済みidentityや独立した人の審査とは呼ばない。
- API起動時は台帳がprepared/in_progressでもよいが、表示は未確定であること。API readyだけでcompleted/承認済みとしない。
- normal completion・proxy switch直後停止・state書込直後停止・recover後に、API/state/同じ台帳行のmetadataを照合する。
- 不一致、欠落、別profile、別artifact、別操作ID、legacy/bootstrap行なしは区別する。旧承認情報の自動捏造・上書きを行わない。
- 古いAPI/旧stateへの互換方針を先に明示する。旧版へ切戻せることと、その版の過去操作metadataを検証できることを混同しない。
- APIにDB初期化以外の新しい書込経路を増やさず、秘密情報・native SQL/URIをresponseへ出さない。

<a id="t17b-sol-handoff--2-registryartifact・予測probeの全体deadline"></a>

#### 2. registry/artifact・予測probeの全体deadline

現行600秒/30秒はDocker clientを待つ上限だけである。registry/artifactはheartbeatがあっても長時間ブロックし得る。
別processへの隔離とMONOTONIC deadlineを検討し、取得済みモデルをpickleで親へ渡さない。親が必要なversion/hash/gate/finite prediction等を検証可能なJSONとして返す方式を優先する。
argv・stdout・診断logへcredentialを出さず、最小の明示環境を使う。signal/killは自分が作ったprocess groupと開始identityを照合して行う。

受入条件:

- registry待機、artifact取得、hash照合、deserialize、probe HTTP、proxy/state/台帳確定の各段階を区別する。
- operation全体のdeadline・各段階の残時間・補償/cleanupの上限と例外を先に固定する。threadの待機終了だけをchild取消成功としない。
- HTTP/daemonがtimeout後に完了する負例を用意し、既に送った要求を未送信と決めず、固定request ID・台帳を調べられること。
- prepared後のtimeout/中断はpending/recovery_requiredを残す。registry確認前の失敗と、送信後の未確定を同一状態へ潰さない。
- 正常完了、TERM、KILL、timeout、応答喪失、子process未終了、cleanup失敗、UTC逆行の合成ケースを別attempt/logへ保存する。
- future拒否緩和・時刻clamp・成功するまでの自動retryで回避しない。過去T03の自然停止を未再発だけで解決扱いにしない。

<a id="t17b-sol-handoff--3-t07aの実caddy切替復旧harnessをciへ"></a>

#### 3. T07aの実Caddy切替復旧harnessをCIへ

`runtime_checks.py deployment-recovery`はlocalで実Caddy/API/PGを通しているが、現状はcached `bike-mlops-api-blue` imageを要求し、新harness自体はCI未接続。
使い捨てworkspaceから現Dockerfileで固有tag/label付きimageを作り、元stackのtagを上書きしない。
worker/復元APIにもimage identityを引継ぎ、TERMを含む失敗時に所有を確認してcontainer/network/volume/imageを回収する。
CI接続前に、同ID並行/409、3停止点、rollback/recover、5table/モデル資料復元、timeout終了値伝播を確認する。
公開artifactは明示allowlistとし、raw credentials・環境file・実DBを含めない。CI実装commitのlog/hash/cleanupまで確認して完了にする。
T07aのmodel/registry doubleと、T07bの実MLflow/skopsは異なる受入である。実候補producerとして使うには別の同一candidate結合が必要。

<a id="t17b-sol-handoff--共通の停止境界と報告"></a>

#### 共通の停止境界と報告

OS・時計設定・実DB・実registry・現在の配信は変更しない。実candidateのfit/評価はT09/T10等の前提を確認してから。
schema/writer・承認の信頼境界を変更する必要があれば、Solが仕様と負例を先に整理する。Lunaへ未定義の判断を委ねない。
独立して進められる実装・負例・文書整備を可能な限り多く行い、全試行・失敗・コマンド・結果・hash・限界を保存する。
確認済みの節目ごとに対象pathだけcommit/pushし、CIを接続した場合は同じ実装commitのartifactとcleanupまで確認する。
最後に完了項目・残件・次のSol/Lunaへの依頼文を提示する。

<a id="t17b-sol-handoff--2026-11-03実装後の追跡"></a>

#### 2026-11-03実装後の追跡

本書の「未実装」は基点の状態である。今回のAPI操作binding/schema3・CLI process全体deadlineと現image/TERM cleanup付きT07a CI接続の判定は[最新索引](reviews/20261103-t17b-t07a-checks.json)、残件の正規状態は計画§11.6の2026-11-03追記を参照する。CI artifactを確認するまで受入完了へ昇格しない。T02の実producer、時計資格、T09以降の実候補評価は別条件である。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。

<a id="20261028-r06-delta-review"></a>

## 20261028-r06-delta-review

<a id="20261028-r06-delta-review--前回確認後の差分レビューとr06の判断"></a>

### 前回確認後の差分レビューとR06の判断

確認日: 2026-10-28。差分の基準は`9131b1ff0a3f2036aab1c5acb4ca0bc641cb2963`、commit時刻は2026-10-28 15:38:10 JST（06:38:10 UTC）。今回のユーザー依頼は「前回以降のログだけを確認し、3点を判断して残作業を更新する」。過去の全モデルや全原本の再監査は行っていない。

<a id="20261028-r06-delta-review--前回以降の確認結果"></a>

#### 前回以降の確認結果

| 対象 | 方法 | 結果・証拠の範囲 |
|---|---|---|
| Git | `git status --short`、`git log`、`git diff 9131b1f --stat` | 作業開始時のHEADは基準commitと同じ。追跡ファイルの変更0。以前からある無関係な未追跡ファイルは対象外。 |
| ローカル成果物 | 基準commit時刻より後のmtimeを列挙 | reports 20件、raw ZIP 41件、derived 4件、UCI runtime 5件、Capital SQLiteに新しい更新はなし。candidate bundle/評価台帳はなし、Capital runtime directoryも未作成。mtime確認であり、全原本のhash再監査ではない。 |
| Docker差分ログ | `docker compose logs --since '2026-10-28T15:38:10+09:00' --timestamps --no-color postgres mlflow api-blue api-green caddy` | 取得成功、新しいログ0行。ログが増えていないため、新しい障害報告もなかった。ログ未出力の処理の成功までは証明しない。 |
| コンテナ状態 | `docker compose ps --format json` | UCIの5サービスがrunning、PostgreSQLはhealthy。今回API readyを再probeしておらず、単にrunningで配信可能とは判定しない。 |
| MLflow差分 | experiment一覧から`runs/search`を基準時刻以降でfilter。`model-versions/search`のcreation/last-update時刻を比較 | 新規run 0、新規/更新model version 0。どちらも残りpageなし。 |
| UCI運用DB差分 | `psql`によるSELECT。predictions.created_at、deployments.updated_at、monitor_windows.created_atを基準時刻と比較 | 新規prediction 0、配信作成/更新0、新規monitor window 0。DB内容は変更していない。 |
| 前回最終commitのCI | `gh run view 40645058486 --json headSha,status,conclusion,url,updatedAt,jobs` | HEAD `9131b1f`に一致、success。lock install、pip check、テスト、compileall成功、完了06:39:23 UTC。 |

確認した保存先・MLflow・運用DBには、前回以降の追加学習・評価・登録・配信の証拠がなかった。評価済み期間を再採点して確認する操作はしていない。外部の未記録処理まで含む全活動の不在を保証するものではない。

CI参照: [run 40645058486](https://github.com/kenobix/Claude_practice/actions/runs/40645058486)。公式月次archiveは今回再取得・再照会していないため、前回のHTTP 200/404を現在の公開状態として断定しない。

<a id="20261028-r06-delta-review--関連する前回文書の修正"></a>

#### 関連する前回文書の修正

1. **学習締切とラベル到着:** 前回案には5月31日23時のラベルが6月1日02時に利用可能とあったが、validationは6月1日00時開始だった。このラベルを含めると予測開始より後の情報になる。`evaluation.py`の`training_cutoff <= validation_start`に合わせ、締切を00時、最後の学習targetを5月31日21時と定義した。22時・23時を除外する。候補実験前の文書修正で、既存の評価値は変更していない。
2. **timezone再現性の説明:** 前回work_logは`tzdata==2026.5`のinstallだけでOSから独立したと説明していたが、これは誤り。今回の`zoneinfo.TZPATH`はOSの検索pathで、`/usr/share/zoneinfo/America/New_York`が存在する。Python標準ライブラリの`find_tzfile`もTZPATHを先に検索する実装だった。package版は固定されているが、利用providerの版を固定・記録した証拠はない。R04/P0に未完了項目を追加した。既存のDST集計が誤っていたという発見ではない。
3. **運用計測の仕様不足:** 旧案の「入力/応答固定」だけでは、同じrequest IDの再送で保存済み応答を測ってしまう。全要求を新規IDにし、暖機50件、測定1,000件、同時数1、接続再利用、再試行0、timeout10秒、HTTP/schema/識別検証、失敗を含む全標本のp95計算を定義した。
4. **現行状態の説明:** runbookに残っていた「現行の最終評価が再fitする」という説明を、過去の実験とP0修正後の保存済みartifact読み込みに分けて修正した。初回Capitalの経路と今回決めたgateは依然未実装。

日時算術はPythonの`datetime/timedelta`で確認した。validationは1,464時間、後続評価は1,461時間、選択ラベル到着時刻は8月1日02時。対象期間にDST遷移はない。実データ件数は取得・集計後に検証する。

<a id="20261028-r06-delta-review--3点の判断"></a>

#### 3点の判断

ユーザーがCodexへ判断を委任したため、追加の確認待ちにはせず次を採用する。詳細は[評価基準](capital.md#capital-bootstrap-gate)。今回は文書への仕様確定であり、実装済み・実測合格とは扱わない。

1. **RMSEもgateにする。** validationと後続評価のそれぞれで、同じ行のbaselineに対してMAEを厳密改善、RMSEは以下とする。MAEだけが改善しRMSEが悪化した候補を通さない。個々の最大誤差の改善は保証しない。指標の欠落/非有限、行不一致も拒否する。
2. **ローカル運用目標を採用する。** 暖機後の逐次1,000要求、エラー0、p95 250ms以下。これは事前に選ぶ学習用目標。実測から逆算した閾値や本番SLOではない。profile/model/hashの誤りもエラーに含める。
3. **区間別は診断値にする。** weekday/weekend、朝7〜9時・夕方16〜18時の件数、候補/baseline MAEと差を記録する。業務の優先区間・許容誤差が未定義のため自動gateは設けず、手動採否で偏りを説明する。全体gateの不合格を手動で上書きしない。

<a id="20261028-r06-delta-review--更新後の残作業"></a>

#### 更新後の残作業

正規の一覧は[計画書11.4](../../261014/plan_mlops.md#114-r06判断後の残作業の実行順序)。順番はR04 timezone固定→R06 bootstrap/gate実装→原本範囲・buffer確定と取得/監査/protocol凍結→validationと同一artifact後続評価→R05隔離runtimeとR06運用測定→R07監視→R08/R09復旧・運用metadata→R10成果説明とした。

今回、新しい候補fit・評価窓の消費・登録・配信・負荷試験は実施していない。ソース/依存は変更していないため、ローカルでの44テスト再実行は省略して前回CI結果を参照し、変更した文書のリンク・日時/件数算術・差分を検査する。push後のCIはworkflowにより自動実行される。

変更後の検査結果: 追加/変更行と新規レビュー内のローカルリンク11件（anchorを含む）は全て解決し、日時/件数算術と`git diff --check`も成功した。

<a id="20261028-review"></a>

## 20261028-review

<a id="20261028-review--2026-10-28-差分監査と残作業"></a>

### 2026-10-28 差分監査と残作業

<a id="20261028-review--対象と結論"></a>

#### 対象と結論

前回の独立した総合レビュー文書は見つからなかったため、版2のAPI・再送・再起動確認を記録した`cb79db0`を今回の基準とした。その後の`485286c`（版3・Q4評価）、`6b99746`（PSI調査・Capital Bikeshareへの拡張）の変更14ファイルと、それらが使用する既存の特徴量・監視・配信・DB・CIを確認した。過去の503対応全体を再監査するものではない。

**UCI版3の配信、Q4の保存済み予測、報告したQ4・直近週の誤差とPSIには、今回照合した範囲で不一致はない。一方、新データ候補を配信可能な完成品と扱うには重要な不足がある。Phase 3〜5は部分完了であり、全計画完了ではない。**

特に、前回「固定候補」と説明した新データ候補は、validation時のモデルファイルを保存しておらず、最終評価で同じ設定から再学習していた。同一のモデルartifactであることをハッシュで証明できない。「一度だけの最終評価」の実行制御も出力ファイルの存在確認だけだった。この説明は不十分だったため、今回関連文書に制限を追記した。元の実行結果と数値は変更していない。

集計証拠は[20261028-evidence.json](reviews/20261028-evidence.json)、実装・検証の残作業は[元計画の追補](../../261014/plan_mlops.md#11-2026-10-28差分監査後の実行計画)に記録する。

<a id="20261028-review--実際に確認できたこと"></a>

#### 実際に確認できたこと

| 確認対象 | 方法と結果 | 証拠としての範囲 |
|---|---|---|
| 現行環境 | Compose設定検証成功。全サービス起動、PostgreSQL healthy。MLflow health・Blue版3・Green版2・Caddy版3のreadyが全て200 | 読み取り時点の稼働。長期安定性や復旧能力の証明ではない |
| 配信履歴 | DBの版2・版3への昇格はcompleted、probe記録あり。Caddy・runtime state・版3artifact SHAが一致 | 正常昇格の実績。rollback/recover実行の証拠はない |
| Q4予測 | DBから版2・3各2,168件・一意時刻2,168件を読み出し、元CSVと再結合 | 保存済み予測の算術再集計。API再生・モデル推論は再実行していない |
| Q4の特徴量と出典 | 全4,336予測の特徴量を時刻キーで再構成し、参照観測ハッシュを照合。不一致0 | 対象時刻の正解を入力に混ぜた証拠はない |
| Q4誤差 | 版2 MAE/RMSE `70.500890957 / 113.270103352`、版3 `59.402697944 / 91.941320896`で既存レポートと一致 | 保存済みモデル出力についての一致 |
| 最終7日 | 各167予測・到着済み164ラベル。版2 MAE/RMSE `37.277751554 / 58.158205648`、版3 `44.176335568 / 74.285824874`で一致 | 最後の3ラベルを0誤差にしていない |
| PSI | 2011年全体参照`2.095416249`、同じ年末週を参照する診断値`0.198250418`で一致 | 季節差は説明仮説。PSIは誤差でも因果検証でもない |
| 新データ取得物 | 41 ZIP・742,925,529 bytesのSHAを取得manifestと照合。不一致0。CSV・manifest SHAも両評価reportと一致 | 取得時の記録との一致。配布元の署名検証ではない |
| 新データ時刻分割 | 29,177行。学習17,538・validation4,343・最終7,294、重なり0。cutoff時未到着の2行を除外 | +3時間の実験契約に一致。実際の月次データ配信遅延を再現したものではない |
| 夏時間 | America/New_Yorkの実在時刻を別に生成して突合。除外後の時刻集合差0 | ローカル壁時計と解釈する実験方針との一致 |
| 原本から別集計 | 7日・166時間を7 ZIPから別方式のID辞書で再集計。一致166/166。サンプル98,733 ID、重複204行。秋の除外01時は341件 | 月境界・重複・夏時間の標本検査。1,944万行全体の別方式再集計ではない |
| 新データreport | validationの比較式・期間・行数・hashを確認。最終評価の月別加重MAE`126.940988313`が全体値と一致 | 元の候補artifact・行別予測がないため、候補の元数値を独立再推論で確認できない |
| 依存とテスト | `pip check`成功、`compileall src tests scripts`成功、既存13テスト成功 | 後述する新規コードのテスト不足は残る |
| CI | 対象2コミットのGitHub Actionsはsuccess | workflowの試験範囲は既存13件とsrc/testsのcompile |
| ログ | 直近6時間・各サービス末尾300行、合計923行にERROR/Traceback/HTTP403/503なし | 保持範囲外の過去ログの不在を保証しない |

CI: [485286c](https://github.com/kenobix/Claude_practice/actions/runs/40615414384)、[6b99746](https://github.com/kenobix/Claude_practice/actions/runs/40622122344)。

<a id="20261028-review--未解消の指摘"></a>

#### 未解消の指摘

<a id="20261028-review--f01・高-選定した候補artifactを固定できていない"></a>

##### F01・高: 選定した候補artifactを固定できていない

- 対象: [candidateのreturn処理](../src/bike_mlops/training.py)、[最終評価script](../scripts/data_tools.py)の110〜112行。
- `candidate --no-register`は指標だけを保存し、学習済みモデルを保持しない。最終評価は`build_estimator().fit(...)`をもう一度実行する。
- パラメータ・特徴量列・学習行数・seedの一致は確認するが、候補artifact SHA・行別validation予測・環境・完全なソース差分の一致は保証していない。`FEATURE_CONTRACT_VERSION`一致の検査も最終scriptにない。
- 既存reportは「同じ学習条件で再学習した候補の後続期間評価」として残す。選定時と同じartifactを評価した証拠や、そのartifactの昇格承認には使わない。数値が誤りだと確認されたわけではない。
- 残作業: **R03**。no-registerでもmodel・hash・lineage・validation予測を保存し、最終評価は保存済みmodelのロードだけにする。既存候補を後から新しい登録版にして「当時のモデル」と認定しない。

<a id="20261028-review--f02・高-最終評価のプロトコル固定と消費記録が弱い"></a>

##### F02・高: 最終評価のプロトコル固定と消費記録が弱い

- 対象: [最終評価script](../scripts/data_tools.py)の40〜79行、[データ生成script](../scripts/data_tools.py)のmain。
- 既定出力が存在すれば停止するが、`--output`を変えると同じ対象を再評価できる。CLIはvalidation終了以降という条件だけで最終期間の変更も受け付ける。並行実行は計算前にロックされない。
- 合成CSVと固定モデルで同じ期間を`first.json`・`second.json`へ実行し、両方が成功することを再現した。実データの最終評価は再実行していない。
- `candidate_evaluation_plan`はローカルmanifestに存在するが、生成scriptには出力処理がない。再生成で計画欄が失われる。manifestの`final_evaluation_has_been_scored_for_model_error=false`は取得時点の状態として保存されており、現在の消費状態の台帳にはならない。
- 残作業: **R02**。事前の分割・判定方針を版付きprotocolへ保存し、モデルhash・データhash・protocolをキーに開始/失敗/完了を別台帳へ記録する。出力名を変えても同じ評価の消費状態を維持する。意図的な証拠改変を防ぐセキュリティ機能ではなく、誤操作・重複実行を防ぐ設計とする。

<a id="20261028-review--f03・高-新データ向けの配信・監視経路が未整備"></a>

##### F03・高: 新データ向けの配信・監視経路が未整備

- 対象: [monitor](../src/bike_mlops/cli.py)の参照期間、`_promote`の`DEFAULT_DATASET`、`_deployment_probe`の2012年以前という条件、[データ契約](architecture.md#data-contract)。
- 監視参照が`timestamp < 2012-01-01`に固定されるため、2023〜2026年CSVでは参照0行となりPSIが計算できない。配信CLIはUCI CSVをseedし、試験予測もUCIから選ぶ。
- DB観測はUCI17,379行のみ。新データのAPI入力・出典・ラグ・配信予測一致は未検証。モデルカードのtarget記述も汎用のrentalsのままで、trip startsへの変更を契約版として表現していない。
- 月次の公開履歴から作った台数とUCI `cnt`の定義差・時刻解釈・実験上の+3時間遅延は説明されている。これを同じ入力契約のまま本番相当の更新として扱うことはできない。
- 残作業: **R05**。データ/target契約版・独立した作業DBまたは明示的プロファイル・参照分布・probeを揃え、異なる出典の混在を拒否する。UCI版3を動かしたまま別Composeプロジェクトで移行を検証する。

<a id="20261028-review--f04・中-データ生成とmanifest入力の異常系が不足"></a>

##### F04・中: データ生成とmanifest入力の異常系が不足

- 対象: [aggregate_archives](../scripts/data_tools.py)の113〜224行、[manifest解析](../src/bike_mlops/training.py)。
- 合成検査で`aggregate_archives([], 2025-01-01, 2025-01-02, ...)`が24行の全0データを正常生成した。CLIからも対象期間を包含しないarchive範囲を渡せる。全ZIP取得完了と対象期間のカバーを検証してから0件時間を補う必要がある。
- manifestにJSON配列`[1]`を指定すると、入力契約エラーでなく`AttributeError`になる。必要項目・型・hash・時刻契約のスキーマ検査がない。
- CSVを先に書いてからmanifestを書くため、後段失敗で対応が不一致になる可能性がある。既定パスの上書きも拒否しない。
- 今回の実取得41ファイルと標本集計に不一致はない。この不具合が今回のCSVを壊したという証拠はない。
- 残作業: **R01**。archive範囲・非空・欠落月・時間境界を検証し、原本行数、ID重複、範囲外、DST除外、時間別合計の収支を残す。月境界の翌月bufferは採用理由と限界を明示する。CSV/manifest/protocolを一組で原子的に保存する。

<a id="20261028-review--f05・中-計画した昇格基準の一部しか実装されていない"></a>

##### F05・中: 計画した昇格基準の一部しか実装されていない

- 元計画は全体MAE、主要時間帯、欠測、応答時間に事前の許容悪化幅を置くとしている。現在のgateはvalidation MAEの比較だけ。RMSE・segment MAEは報告されるが合否には使わない。
- 既存の`passed`はこの狭い比較式には正しい。配信の全面的な品質保証ではない。新データの最終評価にも事前の合否基準はないと正しく記述している。
- Q4のAPI p50/p95はその逐次再生で観測した時間。固定した同時数・暖機・負荷・ハードウェア条件による性能比較ではない。改善の統計的な不確実性も評価していない。
- 残作業: **R06**。比較gateと配信許可を区別し、対象・指標・件数・許容差・理由をprotocolへ事前固定する。既に見た最終期間へ合否条件を後付けしない。

<a id="20261028-review--f06・中-再現性と新規コードの回帰検証が不足"></a>

##### F06・中: 再現性と新規コードの回帰検証が不足

- 前回は新コード変更後にテストスイートを実行していなかった。今回13件は成功し、両コミットのCIも成功していた。ただしtestsにはcandidate学習、manifest、ZIP集計、最終評価script、配信/recoverのケースがない。CIのcompile対象にもscriptsがない。
- 新データ候補runはdirty worktreeのhashを記録したが、その時点のソース全体またはpatchは保存していない。FastAPI・MLflow・psycopgなどはバージョン範囲のままで、依存解決を再現するlockがない。実行パッケージ一覧の記録はある。
- 欠測をHistGradientBoostingRegressorのnative NaN処理で扱う実装はリーク防止に矛盾しない。元計画の「補完器」は実装選択に合わせた説明更新が必要。
- 残作業: **R04**。合成データで新コードの成功/拒否ケースをCIへ追加し、依存と実行コードを再構成できる形で固定する。過去最終期間を再採点して回帰試験にしない。

<a id="20261028-review--f07・中-監視期間の指定漏れとpsiの退化ケース"></a>

##### F07・中: 監視期間の指定漏れとPSIの退化ケース

- Q4 replay IDに対する実際のmonitorは7月開始の既定値を使った。DBには各版4,416窓、そのうちQ3の空窓2,208が存在する。両版合計8,832窓。最終週の誤差とPSIは正しく、Q4全期間誤差は別集計なので影響しない。
- `population_stability_index([1,1,1], [100,100,100])`は`0.0`になる。参照が定数のとき1つの無限区間へまとめるため大きな変化を見逃す。今回警告対象のlag_24参照は非定数なので、このバグで既存2.095が無効になるわけではない。
- PSI参照・ビンがモデル/データプロファイルの保存物になっていない。missing率は記録するが異常判定は未整備。誤差参照が未設定でも`within_review_threshold`になる仕様は、移行時に誤読しやすい。
- 残作業: **R07**。replay manifestから監視範囲を一致させるか明示指定を要求し、定数参照・参照なし・少数件を別の状態で扱う。過去の空窓を無断削除しない。

<a id="20261028-review--f08・低-runtime表示と旧版の運用状態が不十分"></a>

##### F08・低: runtime表示と旧版の運用状態が不十分

- active versionは3だが、runtime stateの`reason`は版1bootstrap時のまま。配信DBの操作ごとの記録が根拠になる。`_promote`がactive版を更新しても理由・承認者を更新しないためである。
- 読み込み不可と判明済みの旧版1も、MLflow上のstatusはREADY、quality gateはpassed、runtimeのprevious_versionsに残る。モデルロード/probeで拒否される設計だが、rollback候補一覧として誤解を招く。
- 残作業: **R09**。台帳から現在の承認情報を同期し、旧版1を保持したまま配信不可と明示する。過去runの品質指標を改ざんして扱いを変えない。

<a id="20261028-review--f09・中-評価判断時刻と境界のラベル到着を固定していない"></a>

##### F09・中: 評価判断時刻と境界のラベル到着を固定していない

- candidate reportには学習cutoffとvalidation期間があるが、validationの正解をいつまで待って採否を判断するかという仮想時刻がない。
- UCI Q3の最後の対象時刻は`2012-09-30 23:00`、ラベル到着は`2012-10-01 02:00`。Q4開始`2012-10-01 00:00`の後になる。新データも最後のvalidation対象`2025-06-30 23:00`の正解が`2025-07-01 02:00`に届き、最終評価開始の00時より2時間遅い。両方とも最初の2対象時刻が、この全validationラベルを待つ判断より前に位置する。
- 最終期間の正解を候補選択に混ぜた証拠はなく、後ろの期間で行う事後的な予測比較としての分離は保たれる。ただし「当時の採否判断を終えて配信していた」という仮想運用の時刻整合は未確認。過去の数値や期間を事後に変更して改善を主張しない。
- 残作業: **R02**。学習締切と評価判断時刻を別々に固定し、採否に使うラベルは判断時刻までに到着済み、後続の配信評価対象はその判断時刻以降という条件を試験する。境界の待機時間またはvalidation末尾の扱いは、新しい評価protocolの確定時に決める。

<a id="20261028-review--計画との差分と完成判定"></a>

#### 計画との差分と完成判定

| Phase | 現時点 | 残る証拠・作業 |
|---|---|---|
| 0 データ・契約 | UCIは実装・実測あり。新データは暫定プロファイル | 新target契約、source範囲/収支検査、生成可能なsplit protocol |
| 1 学習・登録 | UCI登録と同一データの指標再現実績あり。新候補は未登録 | 候補artifact固定、実行環境lock、ソースsnapshot、再現性許容差 |
| 2 特定版API | UCIのready・予測・保存・再送・offline/API probeあり | 新データAPI一致、異source拒否、PostgreSQLでの並行・版切替再送 |
| 3 昇格・復旧 | 正常昇格の実績あり | rollback、切替途中停止/recover、破損artifact拒否を隔離環境で実証 |
| 4 再生・監視 | UCIの再生・遅延ラベル・PSI診断あり | F07修正、入力/ラベル/API停止の障害注入、通知の誤警報・見逃し・検知遅延 |
| 5 候補・説明資料 | UCI版3の選定とQ4確認あり。新データ評価は暫定 | 合否基準、採否記録、上記不足の解消、5分デモ・復旧/制約報告 |

バックアップからDBとartifactを別環境へ復元する実験は元計画にあるが、完了の証拠を見つけられなかった。ローカル学習構成でも再現・復旧の課題として残す。大きな管理画面、公開API、Kubernetesは今回の必須残作業に追加しない。

<a id="20261028-review--今回の検証コマンドとログ"></a>

#### 今回の検証コマンドとログ

```bash
cd ~/work/claude_practice/261021
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python -m pip check
docker compose config -q
docker compose ps
curl -sS --max-time 10 http://127.0.0.1:8080/ready
curl -sS --max-time 10 http://127.0.0.1:8080/version
gh run list --repo kenobix/Claude_practice --workflow bike-mlops-contracts.yml --branch main --limit 5
```

Pythonは実際にはプロジェクトの`.venv/bin/python`を使用した。DBは`BEGIN READ ONLY`または`default_transaction_read_only=on`でSELECTした。Q4数値は保存済み予測から再集計し、新候補の再学習・最終期間の再推論は行っていない。

初回の隔離環境でのunittestは最初のAPIテストで進行しなくなったため、自分が起動したプロセスを終了し、許可付き実行で同じ13件を再実行した。後者は13件全て成功。隔離環境固有の停止原因は確定しておらず、アプリのテスト失敗とは断定しない。Starletteのhttpx非推奨警告は残るが試験は成功した。

ローカルの詳細ログは`data/reports/review-20261028-unit-tests.log`、`review-20261028-compose.log`、`review-20261028-live.json`、`review-20261028-source-sample.json`。data/以下はGit対象外。原本・乗車ID・認証値を含まない集計証拠だけをこのディレクトリへ保存した。

<a id="20261028-review--次に実施する順序"></a>

#### 次に実施する順序

1. **R01〜R04**: データ境界・評価protocol・モデルartifact・テスト/環境再現性を先に整える。
2. **R05〜R07**: 別プロファイルで新データのAPI・監視と事前の配信基準を完成させる。
3. **R08〜R09**: 隔離環境でrollback・中断復旧・バックアップ復元、運用メタデータを検証する。
4. **R10**: 根拠が揃ってから採否・5分デモ・成果説明をまとめる。

現在の新データ候補をそのまま登録・昇格する段階ではない。既存Q3/Q4、新データ2025年上期validation・2025年7月〜2026年4月の最終評価は、今後の新しい独立評価には数えない。詳細な作業・完了条件は元計画のR01〜R10に従う。

<a id="20261028-staging-checkpoint-review"></a>

## 20261028-staging-checkpoint-review

<a id="20261028-staging-checkpoint-review--r06-staging実装後のチェックポイントレビュー"></a>

### R06 staging実装後のチェックポイントレビュー

確認日: 2026-10-28（JST）。前回の正式レビューを含むcommit `7609af5d175216052485df1060c4148fb4ccbeb5`の後から、`5df03f7852ccbf9107f88d7bae6ee7aa3f5506b0`までを対象にした。13コミット、45ファイル。前回レビューが比較に使った`9131b1f`以降をもう一度全部やり直すのではなく、前回レビューの確定後に増えた実装・記録を確認した。

結論: 実装の方向、評価期間を保護する判断、隔離環境での試験は妥当。ただしR06は未完了で、Capitalを実測・承認・通常配信できる状態ではない。現在のCLI停止と`not_evaluated`の扱いはコードと一致する。直近の細かな部品追加を、次は「候補の証拠→固定DB→HTTP→監査行→運用report」を通す一つの完成条件にまとめる必要がある。

集計証拠は[チェックポイント証拠JSON](reviews/20261028-staging-checkpoint-evidence.json)。今回の全テスト出力はGit対象外の`data/reports/review-20261028-staging/unit-tests.log`に保存した。レビューでは実候補のfit、実評価期間の再採点、登録タグ変更、配信切替は行っていない。

<a id="20261028-staging-checkpoint-review--コミットと計画への適合"></a>

#### コミットと計画への適合

| コミット | 主な内容 | 確認と判定 |
|---|---|---|
| `13ea918`、`b43cb76` | timezone供給元の固定と記録 | `ZoneInfo.from_file`でlocked tzdataの実bytesを読み、schema 3とAPIへidentityを記録。R04の目的に合致。今回は全原本の再集計を繰り返していない。 |
| `513436b` | incumbentなしbootstrap protocol | UCIと異なるtargetを比較する問題を避け、同target baselineへ移行。未使用期間を保護する順序は妥当。 |
| `35065bd`、`0cdac25` | MAE/RMSE gate、final CLI、配信拒否、測定部品 | 同じartifactのfinal採点とrow/ledger照合を試験。通常Capital配信はgate tagを要求するが、最終の証拠更新・承認経路は未完了。 |
| `d263f8e`、`2a193c0`、`f365dfa` | blocked候補の登録と隔離staging、実施順序 | 実候補前に合成system test、実候補後に実測という依存関係を整理。通常runtimeの未達gateを緩めない方針は妥当。 |
| `a07c581`、`41bf7e9` | 合成Compose実検証、候補ID保存、Dockerのprofile path修正 | 保存済み報告とコードを照合。候補切替後の再送は保存IDを返す。API imageはテスト用overrideを併用しており、最新HEADの本来のDockerfileで全system testした証拠にはならない。 |
| `ea722b4` | candidate/CSV/manifest/registry/DBのseed照合 | 余分な既存行を含め不一致をtransaction内で拒否。SQLite試験はouter CLIと実registryを通す試験ではない。 |
| `85b6e7c` | `--verify-only`と一時PostgreSQL検証 | 全行照合は実装済み。ロックは検査transaction終了で解放され、測定中の固定にはまだ使われていない。 |
| `5df03f7` | 凍結training行と168時間の照合、実測停止 | cutoffまでのラベル到着・row hash/countを検査。CLIは入力確認専用。内部の数値成功も運用gateを`passed`にしない。安全側の途中状態として妥当。 |

上記13コミットには各々contract CIとPages workflowがあり、計26 runはすべてsuccess。最新contract CIは[40693844897](https://github.com/kenobix/Claude_practice/actions/runs/40693844897)、Pagesは[40693843604](https://github.com/kenobix/Claude_practice/actions/runs/40693843604)。CI成功はlock install・editable install・pip check・unit tests・compileallの成功であり、Docker buildやMLflow/PostgreSQL/Caddy統合の成功は含まない。

<a id="20261028-staging-checkpoint-review--今回再確認した証拠"></a>

#### 今回再確認した証拠

| 対象 | 結果と確認範囲 |
|---|---|
| テスト | 対象HEADで86 tests、10.996秒、OK。実行ログSHAは証拠JSONに保存。FastAPIの既知の制約を避け通常実行した。 |
| 静的/依存検査 | `compileall`、`pip check`、`sh -n scripts/bikectl`、3-file Compose `config -q`、`git diff --check`が成功。既存のStarlette/httpx非推奨警告とpip cache書込不可警告はあり。 |
| 既存UCI | 5 container稼働、PostgreSQL healthy、入口ready 200、版3、artifact SHA `79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`。Capital/テストcontainerは残っていない。 |
| UCIの差分 | 基準commit時刻以降のprediction/deployment/monitor追加更新は0。UCI MLflowの新run・model version作成更新も0。今回の読み取りの範囲で確認。消去済み一時registryの履歴や外部処理の不在までは証明しない。 |
| Composeログ | 基準時刻以降の取得可能なログは10行。Traceback/ERROR/permission denied/Invalid Host header/No such artifactの各検索0件。ローテーション等で失われた過去ログは復元していない。 |
| Capital成果物 | 原本ZIP 41個。canonical candidate report 0、Capital report JSONは既存の`data-audit-v2.json`のみ。新しい実candidate/final reportはない。今回は原本全件のhash監査はしていない。 |
| 合成測定 | 保存JSONのSHAは記録どおり`cc68b465ae2dc1b819148f367361d1c92677731d6a883964a9383bf9b503c8f5`。1,050新規ID、1,000測定、HTTP 200、error 0、nearest-rank p95 `106.46448500119732ms`を再計算で確認。 |

合成測定の最後2対象時刻`2024-01-14 22:00`、`23:00`は、3時間遅延のためtraining cutoff `2024-01-15 00:00`までにラベルが到着しない。旧JSON内の`operational_gate.status=passed`は当時の数値判定として履歴を保ち、実候補の運用合格証拠から除外する。現行コードは同条件を入力確認で拒否する。

<a id="20261028-staging-checkpoint-review--指摘事項と優先度"></a>

#### 指摘事項と優先度

<a id="20261028-staging-checkpoint-review--s01--p1-実測の入力・db・api・監査がまだ一つにつながっていない"></a>

##### S01 / P1: 実測の入力・DB・API・監査がまだ一つにつながっていない

[DB照合](../src/bike_mlops/db.py)は全行を検査するがtransactionの終了とともにロックを解放する。[測定部品](../src/bike_mlops/operational.py)は応答の`data_version_sha256`の形式だけを確認し、候補データから再計算した値との一致を要求しない。合成の任意の正しい64桁hashが通ることを今回pure-function probeで確認した。これは既知の未完了条件で、CLIが停止しているため現段階で運用合格を作る経路にはしない。

次の完成条件は、候補/report/registryの版・artifactを固定→同じ観測DBを全行照合→測定中の観測書換えを防止→候補CSVからlag出典hashを計算して全応答を比較→request IDごとの監査行を照合→reportを確定、までを一つの実行で通すこと。DB接続喪失・ロック取得timeout/喪失・途中中断・不一致は合格にしない。観測表の固定が予測監査表へのINSERTを妨げないことも試験する。`data_version_sha256`はCSV全体のSHAではなく、利用した24/168時間lagの時刻・値・source hashから作るhashなので、比較対象を取り違えない。

<a id="20261028-staging-checkpoint-review--s02--p1-配信可否判定はstatus-tagの整合検査に留まる"></a>

##### S02 / P1: 配信可否判定はstatus tagの整合検査に留まる

[require_deployment_eligible](../src/bike_mlops/deployment.py)へ7個の期待status/policy tagだけを渡すと、report hash・candidate ID・承認記録なしでも関数は受理する。pure-function probeで確認した。今回registryを書き換えたり、配信を試したりはしていない。

現時点ではCapital候補がblockedで、eligible更新機能も未実装なので、現在配信されたという発見ではない。今後の終端処理をtag更新だけで完成させてはいけない。正確なmodel/version/candidate/artifact、final/operational reportのSHAと内容、測定plan、policy、監査整合、承認者・理由・時刻・証拠hashを照合し、最後にeligibleを設定する。失敗・不足・値違いを人の承認で上書きしない。通常loader/runtime/promoteで同じ証拠条件を要求し、READY状態と復旧検証の条件も整理する。

<a id="20261028-staging-checkpoint-review--s03--p1-測定条件を保存してから実行する仕組みが不足"></a>

##### S03 / P1: 測定条件を保存してから実行する仕組みが不足

測定仕様は文書と定数にあるが、対象168時間・順序・body生成規則・暖機件数・資源条件・測定policyを版付きhashへ固定して、最初の要求より前に保存する実装はない。現在の入力確認はfinal reportを要求するため、実験前の計画固定そのものには使えない。

候補fit前の精度protocolと、測定前の運用planを区別する。運用planは少なくとも最初の要求の前に不変ファイルとして保存し、候補/データ/期間/policyを結び付ける。未使用データ取得前に実装・合成試験を終える。測定週や閾値を実測結果に合わせて差し替えたり、速い再実行だけを採用したりしない。失敗・中断後の再試行は別attemptとし理由を残す。

<a id="20261028-staging-checkpoint-review--s04--p1-最新headで再実行できるsystem-testが必要"></a>

##### S04 / P1: 最新HEADで再実行できるsystem testが必要

合成Compose試験の記録は残る一方、固定一時パス・project名を含む歴史的スクリプトで、一般の再実行手順ではない。後続のPostgreSQL lock試験は一時スクリプト削除済みで、最終版のコードを再取得できない。過去実施の記録は尊重するが、独立した再現証拠は弱い。最新seed/preflightを本来のDockerfileでbuildしてMLflow/PostgreSQL/API経由で通したことも、unit CIだけでは確認できない。

一意のproject/port/volume、対象所有確認、秘密値非出力、失敗時cleanupを備えた再利用可能なintegration harnessを保存する。最新DockerfileそのものでUCI/Capital profile解決を確認し、S01/S02の正常・不一致・中断を実stackで試験する。PostgreSQLのlock試験と、APIの同一request ID並行再送の試験は別物なので、後者はR08の未完了として維持する。

<a id="20261028-staging-checkpoint-review--s05--p2-現行説明に一部古い状態が残る"></a>

##### S05 / P2: 現行説明に一部古い状態が残る

runbookには「実候補とseedデータの一致強制が未完成」とあり、最新実装のseed時照合と測定中照合の違いが分かりにくかった。計画書にも「scriptが1,050要求を送る」とある一方で停止中の注記が別行にあった。今回、現行説明を入力確認専用へ揃えた。過去の作業ログと合成JSONの当時の結果は書き換えない。

<a id="20261028-staging-checkpoint-review--s06--p1-手動承認と復旧検証の順序を揃える"></a>

##### S06 / P1: 手動承認と復旧検証の順序を揃える

計画書は配信許可に復旧の証拠も要求する一方、旧11.4の順番7にmanual approval、順番9に復旧試験があり、順序が不明確だった。今回、実候補の運用測定はblockedのまま行い、監視・隔離復旧の証拠を揃えてから実候補の採否を決める順序へ訂正した。復旧能力の試験には先行して合成fixturesを使い、実候補の承認待ちを理由に検証できなくなる依存の循環を避ける。今回、実配信操作はしていない。

<a id="20261028-staging-checkpoint-review--ワークフローとデバッグの評価"></a>

#### ワークフローとデバッグの評価

- 小さく対象を指定してcommit/pushし、無関係な4未追跡ファイルを含めなかった運用は妥当。基準時刻以降のUCI DB/registry差分も0だった。
- syntheticを実精度と混同せず、Q3/Q4や過去Capital評価期間を再採点してデバッグしなかった点は適切。供給元tzdata・source snapshot・artifact固定・遅延ラベルの条件は計画の目的に沿う。
- Dockerのprofile path、候補切替後のID、pandas datetime64[us]拒否を実行結果から修正したのは妥当。反対に、syntheticの入力期間違反とseedのデータ版不足はsystem test後に見つかった。先に受入条件の負例を列挙し、それを満たしてから計測する工程へ改める。
- FastAPIがsandbox内で停止し通常実行で成功したという観測は再現しているが、記録だけで原因を特定のsocket syscallまで断定できない。環境要因と推定する範囲に留め、必要時はtimeoutとstack traceで確認する。依存の非推奨警告は失敗と混同せず、lock更新時の互換確認に残す。
- 全テスト再実行は変更の影響確認として妥当。ただしmock中心のテスト件数増加をsystem testの代わりにしない。次の節目はhelper追加ごとに「次回へ」とする区切りから、測定CLIが証拠を伴って動くところまでを完成単位にする。commitは引き続きレビュー可能な単位で行う。
- status tagだけを信用しない証拠検証は必要だが、ローカルの利用者がDB・原本・tagを任意に書き換えられる構成を、外部の未信頼者へ耐える認証基盤と説明しない。今回の範囲はローカル学習用の整合性と誤操作検出。

<a id="20261028-staging-checkpoint-review--更新した残課題"></a>

#### 更新した残課題

当時の順序・依存関係・完了条件は計画書11.5へ反映した（後続更新後の正規一覧は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)）。次の一つの完成節目はS01〜S04を結んだ「再現可能な合成system testで測定CLIと証拠保存を復帰」。その後に初めて未使用原本を取得し、protocol freeze→一度のvalidation→合格時だけ同一artifactの独立final→実候補の運用測定へ進む。監視・rollback/recover・backup/restore・採否デモも未完了として維持する。

<a id="20261028-staging-checkpoint-review--今回の主なコマンド"></a>

#### 今回の主なコマンド

```bash
git log --reverse --format='%h %s' 7609af5..5df03f7
git diff --name-only 7609af5..5df03f7
gh run list --limit 50 --json databaseId,workflowName,headSha,status,conclusion,url
gh run view 40693844897 --json headSha,status,conclusion,url,jobs
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
sh -n scripts/bikectl
docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
curl -fsS --max-time 5 http://127.0.0.1:8080/ready
curl -fsS --max-time 5 http://127.0.0.1:8080/version
```

UCI DBは基準時刻を条件とするSELECT、MLflowは検索APIを全page取得、Composeログは同時刻以降を取得した。集計したpure-function probeと測定JSON再計算の結果も証拠JSONへ保存した。rawログ・原本・実`.env`はコミットしない。

<a id="20261028-synthetic-staging-harness"></a>

## 20261028-synthetic-staging-harness

<a id="20261028-synthetic-staging-harness--合成staging統合検証の実行スクリプト（記録）"></a>

### 合成staging統合検証の実行スクリプト（記録）

2026-10-28に一時ディレクトリと使い捨てCompose projectで実行したスクリプトを、その時点のまま記録する。合成データだけを扱った。記載された一時パス、候補ID、project名、port、versionは今回限りの値で、環境は検証後に削除した。これらのコードは実行環境の所有確認を自動で行わないため、既存のMLflowやDBに向けて実行せず、再利用時は隔離・安全確認とパラメーター化が必要である。

実行結果と制約は[作業ログ](../work_log.md)および[合成ベンチマークの全要求記録](reviews/20261028-synthetic-staging-benchmark.json)を参照。

<a id="20261028-synthetic-staging-harness--人工データ・候補・評価記録の生成"></a>

#### 人工データ・候補・評価記録の生成

```python
from __future__ import annotations
import contextlib, io, json, sys, zipfile
from pathlib import Path
import pandas as pd
from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET, DATASET_MANIFEST
from bike_mlops.evaluation_protocol import build_capital_protocol, save_protocol
from bike_mlops.training import HYPERPARAMETERS, train_candidate
from scripts.prepare_capitalbikeshare import aggregate_archives
from scripts import evaluate_capitalbikeshare_holdout

raw = PROJECT_ROOT / 'data/raw/capitalbikeshare/monthly'
raw.mkdir(parents=True, exist_ok=True)
lines = ['ride_id,started_at']
ride_id = 0
for t in pd.date_range('2024-01-01', '2024-02-01', freq='h', inclusive='left'):
    n = 5 + t.hour % 12 + (0 if t.dayofweek >= 5 else 25)
    for _ in range(n):
        lines.append(f'ride-{ride_id},{t:%Y-%m-%d %H:%M:%S}')
        ride_id += 1
with zipfile.ZipFile(raw / '202401-capitalbikeshare-tripdata.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('trips.csv', '\n'.join(lines) + '\n')
aggregate_archives(['202401'], pd.Timestamp('2024-01-01'), pd.Timestamp('2024-02-01'), DEFAULT_DATASET, DATASET_MANIFEST, raw_dir=raw)
protocol = build_capital_protocol(DEFAULT_DATASET, DATASET_MANIFEST, training_cutoff='2024-01-15 00:00:00', validation_start='2024-01-15 00:00:00', validation_end='2024-01-22 00:00:00', evaluation_start='2024-01-22 03:00:00', evaluation_end='2024-01-29 00:00:00', incumbent_version=None, incumbent_artifact_sha256=None, incumbent_quality_gate=None, candidate_recipe={'model_family':'scikit-learn HistGradientBoostingRegressor in a Pipeline','hyperparameters':HYPERPARAMETERS}, raw_dir=raw)
protocol_path = PROJECT_ROOT / 'data/evaluations/synthetic-protocol.json'
save_protocol(protocol, protocol_path)
report = train_candidate(data_path=DEFAULT_DATASET, incumbent_version=None, source_manifest_path=DATASET_MANIFEST, register=False, evaluation_protocol_path=protocol_path)
print(json.dumps({'validation_gate':report['validation_gate_status'], 'validation':report['validation']['model'], 'baseline':report['validation']['baseline']}, ensure_ascii=False))
if not report['quality_gate_passed']:
    raise RuntimeError('Synthetic validation gate did not pass')
selection = PROJECT_ROOT / report['candidate_artifacts']['candidate_report']
output = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-final.json'
sys.argv = ['data_tools.py evaluate', '--csv', str(DEFAULT_DATASET), '--source-manifest', str(DATASET_MANIFEST), '--selection-report', str(selection), '--output', str(output)]
with contextlib.redirect_stdout(io.StringIO()):
    evaluate_capitalbikeshare_holdout.main()
final = json.loads(output.read_text())
print(json.dumps({'candidate_id':report['candidate_artifacts']['candidate_id'], 'selection_report':str(selection), 'final_report':str(output), 'final_gate':final['final_accuracy_gate']['status'], 'final_candidate':final['candidate']['model'], 'final_baseline':final['historical_baseline']['model']}, ensure_ascii=False))
if not final['final_accuracy_gate']['passed']:
    raise RuntimeError('Synthetic final gate did not pass')

```

<a id="20261028-synthetic-staging-harness--mlflow・db・http・配信拒否の検証"></a>

#### MLflow・DB・HTTP・配信拒否の検証

```python
from __future__ import annotations
import json, math, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pandas as pd
import psycopg
from mlflow.tracking import MlflowClient
from bike_mlops import capital_staging, cli
from bike_mlops.candidate_artifacts import load_candidate_model
from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET
from bike_mlops.data import load_hourly
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model, predict_non_negative

candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
selection_path = PROJECT_ROOT / 'data/candidates' / candidate_id / 'candidate-report.json'
final_path = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-final.json'
selection = json.loads(selection_path.read_text())
env_file = PROJECT_ROOT / 'data/runtime-capital-staging/staging.env'
for line in env_file.read_text().splitlines():
    key, value = line.split('=', 1)
    os.environ[key] = value
version = os.environ['CAPITAL_STAGING_MODEL_VERSION']
artifact_sha = os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256']
client = MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'])
result = capital_staging.register_blocked_capital_candidate(selection_path, final_path)
versions = client.search_model_versions("name = 'bike-demand-capital'")
matching = [v for v in versions if v.tags.get('candidate_id') == candidate_id]
assert result['model_version'] == version and len(matching) == 1
registered = client.get_model_version('bike-demand-capital', version)
assert registered.tags['deployment_permission'] == 'blocked'
assert registered.tags['operational_gate_status'] == 'not_evaluated'
assert registered.tags['artifact_sha256'] == artifact_sha

model, loaded_version, loaded_hash, _, _ = capital_staging.load_staging_registered_model()
assert loaded_version == version and loaded_hash == artifact_sha
try:
    client.set_model_version_tag('bike-demand-capital', version, 'candidate_id', '0' * 64)
    try:
        capital_staging.load_staging_registered_model()
        raise AssertionError('Changed candidate tag was accepted')
    except ValueError as error:
        assert 'candidate_id' in str(error)
finally:
    client.set_model_version_tag('bike-demand-capital', version, 'candidate_id', candidate_id)
try:
    client.set_model_version_tag('bike-demand-capital', version, 'artifact_sha256', '0' * 64)
    os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256'] = '0' * 64
    try:
        capital_staging.load_staging_registered_model()
        raise AssertionError('Changed artifact hash was accepted')
    except ValueError as error:
        assert 'artifact SHA-256' in str(error)
finally:
    client.set_model_version_tag('bike-demand-capital', version, 'artifact_sha256', artifact_sha)
    os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256'] = artifact_sha
try:
    load_registered_model('bike-demand-capital', version, os.environ['MLFLOW_TRACKING_URI'])
    raise AssertionError('Normal model loader accepted blocked candidate')
except ValueError as error:
    assert 'not eligible for deployment' in str(error)

request_id = 'synthetic-system-check-001'
target = '2024-01-10 10:00:00'
payload = {'request_id':request_id, 'target_hour':target, 'as_of':target, 'feature_contract_version':'bike-demand-features-v1', 'data_profile_id':'capital-bikeshare', 'target_contract_id':'capital-trip-starts-hourly-v1'}
with httpx.Client(base_url='http://127.0.0.1:8110', timeout=15, trust_env=False) as http:
    ready = http.get('/ready'); ready.raise_for_status()
    active = http.get('/version'); active.raise_for_status()
    assert ready.json()['candidate_id'] == candidate_id and ready.json()['deployment_permission'] == 'blocked'
    assert active.json()['model_version'] == version and active.json()['artifact_sha256'] == artifact_sha
    first = http.post('/predict', json=payload); first.raise_for_status()
    retry = http.post('/predict', json=payload); retry.raise_for_status()
    assert first.json() == retry.json()
    body = first.json()
    assert body['candidate_id'] == candidate_id and body['deployment_permission'] == 'blocked'
    changed = dict(payload, target_hour='2024-01-10 11:00:00', as_of='2024-01-10 11:00:00')
    conflict = http.post('/predict', json=changed)
    assert conflict.status_code == 409
frame = load_hourly(DEFAULT_DATASET)
features = feature_frame_for_dataset(frame)
idx = frame.index[frame['timestamp'] == pd.Timestamp(target)]
assert len(idx) == 1
candidate, _ = load_candidate_model(selection_path.parent / 'model.skops', selection['candidate_artifact']['sha256'])
offline = predict_non_negative(candidate, features.loc[idx, list(FEATURE_COLUMNS)])
assert math.isclose(offline, body['prediction'], rel_tol=1e-6, abs_tol=1e-5)
password = os.environ['APP_DB_PASSWORD']
with psycopg.connect(host='127.0.0.1', port=55434, dbname='bikeops', user='bike_app', password=password) as conn:
    row = conn.execute('SELECT candidate_id,model_version,artifact_sha256 FROM predictions WHERE request_id=%s', (request_id,)).fetchone()
    count = conn.execute('SELECT count(*) FROM predictions WHERE request_id=%s', (request_id,)).fetchone()[0]
    obs = conn.execute('SELECT count(*) FROM observations').fetchone()[0]
assert row == (candidate_id, version, artifact_sha) and count == 1 and obs == 744
with psycopg.connect(host='127.0.0.1', port=55433, dbname='bikeops', user='bike_app', password=password) as conn:
    has_table = conn.execute("SELECT to_regclass('public.predictions')").fetchone()[0]
    if has_table:
        assert conn.execute('SELECT count(*) FROM predictions WHERE request_id=%s', (request_id,)).fetchone()[0] == 0

state_path = cli.RUNTIME_DIR / 'deployment-state.json'
assert not state_path.exists()
try:
    cli.command_runtime_init(SimpleNamespace(version=version, approver='synthetic-tester', reason='isolated test', diagnostic=True))
    raise AssertionError('runtime init accepted a blocked candidate')
except ValueError as error:
    assert 'not eligible for deployment' in str(error)
assert not state_path.exists()
cli.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps({'active_slot':'blue', 'active_version':'1', 'previous_versions':[]}))
original_state = state_path.read_bytes()
class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({'model_version':'1', 'data_profile_id':'capital-bikeshare', 'target_contract_id':'capital-trip-starts-hourly-v1'}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass
server = ThreadingHTTPServer(('127.0.0.1', 0), ProxyHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
try:
    with patch.object(cli, '_proxy_url', return_value=f'http://127.0.0.1:{server.server_port}'), patch.object(cli, '_compose', side_effect=AssertionError('must not run Compose during gate rejection')):
        try:
            cli._promote(version, 'synthetic-tester', 'isolated test', rollback=False, diagnostic=True)
            raise AssertionError('promote accepted a blocked candidate')
        except ValueError as error:
            assert 'not eligible for deployment' in str(error)
finally:
    server.shutdown(); server.server_close(); thread.join()
assert state_path.read_bytes() == original_state
print(json.dumps({'candidate_id':candidate_id,'model_version':version,'same_candidate_registry_versions':len(matching),'tags':'blocked and not_evaluated','tag_mismatch_rejected':True,'artifact_hash_mismatch_rejected':True,'normal_loader_rejected':True,'runtime_init_rejected':True,'promote_rejected_with_synthetic_proxy':True,'ready_http':ready.status_code,'predict_http':first.status_code,'retry_equal':True,'changed_input_http':conflict.status_code,'offline_prediction':offline,'api_prediction':body['prediction'],'absolute_difference':abs(offline-body['prediction']),'staging_observations':obs,'staging_matching_prediction_rows':count,'capital_main_prediction_table':str(has_table)},indent=2))

```

<a id="20261028-synthetic-staging-harness--tag不一致時のready拒否と復帰"></a>

#### tag不一致時のready拒否と復帰

```python
from __future__ import annotations
import json, subprocess, time
import httpx
from mlflow.tracking import MlflowClient

project = 'bikemlopsit4utzqoxs'
compose = ['docker','compose','-p',project,'-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
name = 'bike-demand-capital'
version = '2'
candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
client = MlflowClient(tracking_uri='http://127.0.0.1:5001')

def restart():
    subprocess.run(compose + ['restart','api-staging'], check=True, capture_output=True, text=True, timeout=30)

def wait_for_status(expected, seconds):
    end = time.monotonic() + seconds
    last = None
    with httpx.Client(timeout=3, trust_env=False) as http:
        while time.monotonic() < end:
            try:
                response = http.get('http://127.0.0.1:8110/ready')
                last = (response.status_code, response.text)
                if response.status_code == expected:
                    return response
            except httpx.HTTPError as error:
                last = type(error).__name__
            time.sleep(2)
    raise TimeoutError(f'API did not return {expected}; last={last}')

try:
    client.set_model_version_tag(name, version, 'candidate_id', '0' * 64)
    restart()
    rejected = wait_for_status(503, 85)
    assert 'candidate_id' in rejected.json()['detail'], rejected.text
finally:
    client.set_model_version_tag(name, version, 'candidate_id', candidate_id)
    restart()
    restored = wait_for_status(200, 60)
    assert restored.json()['candidate_id'] == candidate_id
print(json.dumps({'mismatched_registry_tag_ready_http':rejected.status_code,'rejection_mentions_candidate_id':True,'restored_ready_http':restored.status_code,'restored_candidate_id':candidate_id}, indent=2))

```

<a id="20261028-synthetic-staging-harness--候補切替後の同一要求再送"></a>

#### 候補切替後の同一要求再送

```python
from __future__ import annotations
import json, subprocess, time
from pathlib import Path
import httpx
from mlflow.tracking import MlflowClient

root = Path('/tmp/bike-mlops-it-4UTZQoxs/261021')
env_file = root / 'data/runtime-capital-staging/staging.env'
original = env_file.read_text()
client = MlflowClient(tracking_uri='http://127.0.0.1:5001')
tags = client.get_model_version('bike-demand-capital','1').tags
old_candidate = tags['candidate_id']
current_candidate = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
values = {'MODEL_VERSION':'1','CAPITAL_STAGING_MODEL_VERSION':'1','CAPITAL_STAGING_CANDIDATE_ID':old_candidate,'CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256':tags['candidate_model_sha256'],'CAPITAL_STAGING_SELECTION_REPORT_SHA256':tags['candidate_report_sha256'],'CAPITAL_STAGING_FINAL_REPORT_SHA256':tags['final_report_sha256'],'CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256':tags['evaluation_protocol_sha256'],'CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256':tags['artifact_sha256']}
compose = ['docker','compose','-p','bikemlopsit4utzqoxs','-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
def recreate():
    subprocess.run(compose + ['up','-d','--no-build','--force-recreate','--no-deps','api-staging'], check=True, capture_output=True, text=True, timeout=30)
def wait_ready(version):
    end = time.monotonic() + 60
    with httpx.Client(timeout=3, trust_env=False) as http:
        while time.monotonic() < end:
            try:
                r = http.get('http://127.0.0.1:8110/ready')
                if r.status_code == 200 and r.json().get('model_version') == version:
                    return r.json()
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise TimeoutError('staging API did not become ready with version ' + version)
payload = {'request_id':'synthetic-system-check-001','target_hour':'2024-01-10 10:00:00','as_of':'2024-01-10 10:00:00','feature_contract_version':'bike-demand-features-v1','data_profile_id':'capital-bikeshare','target_contract_id':'capital-trip-starts-hourly-v1'}
try:
    env_file.write_text(''.join(f'{key}={value}\n' for key,value in values.items()))
    recreate()
    ready = wait_ready('1')
    assert ready['candidate_id'] == old_candidate
    with httpx.Client(base_url='http://127.0.0.1:8110', timeout=15, trust_env=False) as http:
        saved = http.post('/predict',json=payload); saved.raise_for_status()
        fresh = http.post('/predict',json=dict(payload, request_id='synthetic-version1-check-001')); fresh.raise_for_status()
    assert saved.json()['model_version'] == '2' and saved.json()['candidate_id'] == current_candidate
    assert fresh.json()['model_version'] == '1' and fresh.json()['candidate_id'] == old_candidate
    assert saved.json()['deployment_permission'] == fresh.json()['deployment_permission'] == 'blocked'
finally:
    env_file.write_text(original)
    recreate()
    restored = wait_ready('2')
    assert restored['candidate_id'] == current_candidate
print(json.dumps({'temporary_active_version':ready['model_version'],'saved_retry_model_version':saved.json()['model_version'],'saved_retry_candidate_id':saved.json()['candidate_id'],'fresh_model_version':fresh.json()['model_version'],'fresh_candidate_id':fresh.json()['candidate_id'],'restored_model_version':restored['model_version']},indent=2))

```

<a id="20261028-synthetic-staging-harness--合成1050要求の測定"></a>

#### 合成1,050要求の測定

```python
from __future__ import annotations
import json, os, platform, subprocess, sys
from pathlib import Path
import psycopg
from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.config import PROJECT_ROOT
from bike_mlops.operational_benchmark import run_capital_operational_benchmark, write_benchmark_report

candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
compose = ['docker','compose','-p','bikemlopsit4utzqoxs','-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
def resources():
    ids = subprocess.check_output(compose + ['ps','--status','running','-q','postgres','mlflow','postgres-staging','api-staging'], text=True, timeout=15).split()
    inspected = subprocess.check_output(['docker','inspect','--format','{{.Name}}|{{.Config.Image}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}',*ids], text=True, timeout=15).splitlines()
    stats = subprocess.check_output(['docker','stats','--no-stream','--format','{{.Container}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}',*ids], text=True, timeout=20).splitlines()
    return {'scope':'synthetic disposable project', 'host':{'os':platform.platform(),'cpu_count':os.cpu_count(),'python':sys.version.split()[0]}, 'containers':inspected,'stats':stats}
report = run_capital_operational_benchmark(api_url='http://127.0.0.1:8110', model_version='2', candidate_id=candidate_id, start='2024-01-08 00:00:00', end='2024-01-15 00:00:00', resource_fn=resources)
output = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-operational.json'
write_benchmark_report(output, report)
observed = report['operational_gate']['observed']
assert report['completed'] and observed['warmup_requests'] == 50 and observed['measured_requests'] == 1000
assert observed['warmup_errors'] == 0 and observed['measured_errors'] == 0
with psycopg.connect(host='127.0.0.1', port=55434, dbname='bikeops', user='bike_app', password=os.environ['APP_DB_PASSWORD']) as conn:
    rows = conn.execute('SELECT count(*) FROM predictions WHERE request_id LIKE %s', (f"capital-op:{report['benchmark_id']}:%",)).fetchone()[0]
assert rows == 1050
print(json.dumps({'scope':'synthetic fixture only; not a real candidate operational result', 'benchmark_id':report['benchmark_id'], 'status':report['operational_gate']['status'], 'warmup':observed['warmup_requests'], 'requests':observed['measured_requests'], 'warmup_errors':observed['warmup_errors'], 'errors':observed['measured_errors'], 'p95_ms':observed['p95_ms'], 'max_ms':observed['max_ms'], 'audit_rows':rows, 'deployment_permission':report['deployment_permission']['status'], 'report_sha256':sha256_file(output)}, indent=2))

```

<a id="20261030-checkpoint-review"></a>

## 20261030-checkpoint-review

<a id="20261030-checkpoint-review--2026-10-30-checkpoint差分レビュー"></a>

### 2026-10-30 checkpoint差分レビュー

確認日: 2026-10-30（JST）。前回の正式レビューを記録した10dfb8fを基準に、070b033までの28コミット・65ファイル（8,060行追加、119行削除）を対象にした。前回レビュー内のコード確認終点は5df03f7である。10dfb8f以前の全作業は再監査していない。

結論: 入力固定・監査付き計測・隔離system test・CIの整備は計画に沿って進んでいる。一方、通常配信に使う承認判定には、証拠の結び付けと監視・復旧条件の強制に未解決の差がある。**実Capital候補のeligible化・通常配信より前にA01/A02を修正する。** 今回はレビューと計画更新を行い、製品コードは修正していない。

集計証拠は[checkpoint証拠JSON](reviews/20261030-checkpoint-evidence.json)、最新の実行順は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)を参照。差分一覧、主要な正常/失敗経路のソース、保存ログ、CI artifact、合成負例を照合した。全追加行の形式証明や第三者による独立監査ではない。

<a id="20261030-checkpoint-review--コミットと前回指摘の進捗"></a>

#### コミットと前回指摘の進捗

| 対象 | 差分内の主なコミット | 判定 |
|---|---|---|
| S01: 固定入力・DB・HTTP・監査 | 1401ebe、dd5a3c6、b3c0acf、a0e6041、9bf8649 | 観測snapshot lock、lag出典hash、1,050監査行、保存reportと数値再計算が合成systemで接続済み。実候補の証拠は未取得。 |
| S03: 測定planと試行台帳 | 34b7547、a81b524、a6c5c5d | 事前plan固定、開始前の永続記録、協調lock、理由付きretryを実装。要求ごとの強制終了時journalは残る。 |
| report点検・部分証拠・数値照合 | 748f2f8、8a8a458、42cfd60、c01b2d1、3035ab1 | 孤立reportを自動採用せず、pending IDのDB照会も不在を未送信と断定しない。実HTTP遅延commitとSIGKILLの通し検証は未完了。 |
| R07監視 | 63656ca、d2bb73b、4d9b543 | 期間継承とrunごとの保存を追加し、使い捨てPostgreSQLで保存を確認。PSI v2は部品試験のみで既存monitorに未接続。 |
| S04: 再実行できるDocker/DB試験 | 5f3ba9a、9b169e6、a1aac78、9bf8649、dd1741e、b290dc5、9692c4f、a0344f9、6079903、0cbcdbc | 最新Dockerfileを使う隔離harnessとCIを追加。cleanup不具合を検出・修正し、現在はCI成功。実Caddy切替・backup/restoreとは別の試験。 |
| S02: review/approvalと通常loader | 77fe955、dae897a、070b033 | candidate/version/artifact/operational reportを結ぶ決定hashと別名承認を追加。部分tag書込試験もある。A01/A02/A03が残り、配信前条件の全面実装完了ではない。 |
| S05/S06: 文書と承認順序 | 上記の各文書更新 | 11.5は進捗を追っていたが、11.3にはCLI停止等の古い記載が残った。今回修正し、監視/復旧を承認より先に要求する実装課題をP0へ上げた。 |

<a id="20261030-checkpoint-review--実行結果・ciの再照合"></a>

#### 実行結果・CIの再照合

- 対象28コミットに関連する52 runは、GitHub表示でsuccess 50、failure 1、cancelled 1。9bf8649と9692c4fは個別のpush先頭ではないため直接runがなく、後続コミットのrunで検証された。「全コミットの全runが成功」とは表現しない。
- success表示だった40874109839と40874601329のartifactを再取得した。両方にTracebackがありcleanup_completeはなかったため、system全体の成功証拠から除外する。ZIPの実SHAはGitHubのdigestと一致した。
- 40876827423はpipefail有効後にcleanup失敗をfailureとして検出した。ログ内にimageが複数repositoryから参照されているというDockerエラーを確認した。先の2 runのartifactには同じ詳細文言はなく、過去の全失敗が同一原因だったとまでは断定しない。
- 最新070b033の[run 40966966672](https://github.com/kenobix/Claude_practice/actions/runs/40966966672)は221 tests、compileall、pip check、synthetic system、PostgreSQL snapshot-lock、cleanup、artifact保存が成功。221 testsは9.850秒。Pagesの[run 40966966169](https://github.com/kenobix/Claude_practice/actions/runs/40966966169)も成功。
- 最新synthetic candidateはad46f6f2ebbd984010153e613c418e9659a9fbb2fadc12ac4cfe554e8214deb0、744観測、1,050要求/監査行、offline/API差0、p95 31.070696000028875ms、numeric recheck一致。gateはnot_evaluated、deploymentはblocked。人工データとGitHub runnerの結果であり、実精度や本番SLOの証拠にはしない。
- 最新artifact IDは12938433488、ZIP SHA-256は40171b0546328a39b813a26815fc32036c248c18b75269167c4b9e9ee33afe79。GitHub digestと一致、ZIP integrityも正常。保存ログ2本を実際に開いて成功行を確認した。
- この監査ではdeployment系11 testsを再実行し1.420秒で成功。最新CIの全221件を確認済みのため、変更のない全suiteとDocker systemのローカル再実行は重ねなかった。pip checkとシェル構文確認も成功した。

<a id="20261030-checkpoint-review--指摘事項と残課題"></a>

#### 指摘事項と残課題

<a id="20261030-checkpoint-review--a01--p0-承認hashが全証拠を固定せず、runtime検証が負のp95を受理する"></a>

##### A01 / P0: 承認hashが全証拠を固定せず、runtime検証が負のp95を受理する

[deployment.py](../src/bike_mlops/deployment.py)のreview/approval payloadはcandidate、version、registered artifact、operational report SHA等を含むが、final report SHAと運用plan SHA等の全証拠は含まない。fake registryでreview→approveした後、final_report_sha256またはcapital_operational_plan_sha256を別の64桁値へ変えても[require_deployment_eligible](../src/bike_mlops/deployment.py)は通った。変更後も決定hashは同じだった。

同じ合成fixtureで承認後のcapital_operational_p95_msを-1にすると、runtime用validate_capital_decision_tagsも通った。review/approveの開始時は負数を拒否するため、「最初から負数の候補をapproveできる」という発見ではない。承認後のmetadata不整合をruntimeが検知できないという問題である。

計画T01で証拠の必須項目・canonical payload・report本体・数値境界の検証を揃え、通常loader/runtime/promoteから同じ規則を使う。実registryの編集や実配信を試したわけではなく、ローカルの誤操作・metadataずれの検出要件に対する負例である。

<a id="20261030-checkpoint-review--a02--p0-計画上の監視・復旧証拠をapproveが要求していない"></a>

##### A02 / P0: 計画上の監視・復旧証拠をapproveが要求していない

review/approveの検証は精度・運用tagを中心にしており、監視・rollback/recover・backup/restoreの証拠を入力に要求しない。合成fixtureではそれらの証拠やreportファイルが存在しなくてもeligible判定まで到達した。report本体を読まないfixtureが通ることも確認した。

現時点で実Capital candidate reportはなく、この不足による実配信は確認されていない。しかし、前回S06で定めた「監視・隔離復旧の証拠を揃えてから採否決定」をコードが強制していない。T02で証拠schemaと依存順を確定し、欠落・不一致をapproveと通常runtimeで拒否する。時刻付き決定記録も必要。自己申告の別名は認証された別人を証明しないため、合成actor名を実承認者の代用にしない。

<a id="20261030-checkpoint-review--a03--p1-通常model-loaderのregistry-uri固定が残る"></a>

##### A03 / P1: 通常model loaderのregistry URI固定が残る

[model.py](../src/bike_mlops/model.py)とcliの通常registry clientはtracking URIだけを設定し、registry URIを明示しない。一方、今回追加したCapital preflight/seed/記録scriptは両方を同じURIへ固定している。

ローカルのMLflow clientコンストラクタだけを使い、tracking=localhost:5001、環境変数のregistry=localhost:5999とすると、registry先が5999になることを確認した。API呼出しは行っていない。現在のComposeで誤接続が起きているという観測ではない。T01に通常Capital経路の同一registry固定と、異URI環境変数の負例を追加する。

<a id="20261030-checkpoint-review--a04--p1-強制終了と実http-timeout後の未確定要求は未検証"></a>

##### A04 / P1: 強制終了と実HTTP timeout後の未確定要求は未検証

要求開始前のattempt記録は永続化されるが、runnerのpartial_evidenceはメモリ由来である。SIGKILLやOS停止では現在のpending ID・応答記録が失われうる。固定request IDのprefix/順序から調べられる範囲はあるが、最後に送信した要求の確定証拠とは別である。pending audit部品は失敗reportを前提に一件を照会する。

T03でjournalの範囲と計測時間への影響をpolicyへ固定し、使い捨てHTTP/PostgreSQLで応答timeout後の遅延commit、SIGTERM/SIGKILL、中断後照会を実証する。見つからない監査行を「送信されなかった」と断定せず、失敗attemptを最速の再実行結果で置き換えない。

<a id="20261030-checkpoint-review--a05--p1-psi-v2の接続と未確定monitor保存の照合が残る"></a>

##### A05 / P1: PSI v2の接続と未確定monitor保存の照合が残る

[monitoring.py](../src/bike_mlops/monitoring.py)は旧PSI関数を呼び、Capitalのprofileは固定monitor referenceがない場合にmonitorを拒否する。v2の部品実装は有効だが、Capital監視の完成を意味しない。

合成の定数reference=1、current=9の各20件で旧PSI=0、v2 PSI=27.630940223106876を確認した。T04/T05で候補のtraining reference・固定bins・algorithm versionを接続し、遅延ラベルや不足母数を別状態にする。T06ではmarkerなし/DBあり等を読み取り照合する。過去UCI値を置き換えない。

<a id="20261030-checkpoint-review--a06--p1-既存real列への型移行のリハーサルが必要"></a>

##### A06 / P1: 既存REAL列への型移行のリハーサルが必要

今回の差分はpredictions.predictedをDOUBLE PRECISIONに変更し、Database.initializeで既存REAL列をALTERする。新しい使い捨てDBのsystem testは成功しているが、既存REAL表からの移行・既存ID再送・復元をまとめた証拠はない。

既存UCI DBをREAD ONLYで確認するとpredictedの型はrealだった。今回initializeやALTERは実行していない。T08で旧schemaを使った一時DB、丸めを含む過去値、同一IDの再送、backup/restoreを確認する。REALを拡張しても過去の丸め前の値は復元されない。

<a id="20261030-checkpoint-review--a07--p1-ci-artifactはsummary中心で、外から数値再計算する材料が足りない"></a>

##### A07 / P1: CI artifactはsummary中心で、外から数値再計算する材料が足りない

最新artifactはZIP 1,844 bytes、ログ2本である。harness内では1,050件のrecordから数値を再計算しているが、workspace削除後のartifactにはplan・ledger・要求別latency等が含まれない。このためartifactだけを取得した人がp95を再計算することはできない。

T16で合成データに限定した公開可能なplan/report/ledger/要求recordの許可リストを作り、credentialや.envを含めずcleanup前に保存する。CIログ成功は尊重しつつ、同じ処理内のrecheckと保存物からの独立再計算を区別する。

<a id="20261030-checkpoint-review--a08--p2-作業ログのコマンド・件数と計画の古い状態を訂正する"></a>

##### A08 / P2: 作業ログのコマンド・件数と計画の古い状態を訂正する

- work_logの2026-10-30節は単独test_capital_deployment_decisionsを実行して11件成功と書くが、当該ファイルは7 tests、test_deployment_gatesが4 tests。今回2ファイルを対象とするglobで11件成功を再確認した。過去に単独コマンドで11件動いたという意味では訂正が必要。
- 220件testと221件testの記録が同じunit-tests-review-approval.logを参照する。現在のSHAは970a8bf...で221件のもの。220件時のcc910fc...のbytesは同pathに残らない。220件は当時のCIで補強できるが、元ローカルlogの同一bytesを復元したとはしない。
- R06本文に「実測CLI停止」「入力固定・tag記録は未実装」「実原本未取得」等の記述が残っていた。合成で完了した部分と実候補の未完了部分を今回分け直した。
- deployment decisionのfake registry testも公開関数のversion lockを通すため、既定のproject data下にlock fileを使う。registry書込とは異なるが、unit fixtureのファイル状態も一時directoryへ分離する保守をT15へ追加した。
- 今後のlogはcommit/UTC時刻/試行IDを含む新規pathへ保存し、pipelineの先頭失敗はpipefail等で検知する。履歴の誤りは訂正を追記し、当時の失敗や旧結果を消さない。

<a id="20261030-checkpoint-review--既存サービスと原本への影響"></a>

#### 既存サービスと原本への影響

5 containerはrunning、PostgreSQLはhealthy、UCI入口はready 200・版3・artifact SHA 79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43だった。checkpointの2026-10-28 13:23:39 UTC以降に見えるprediction新規行、deployment新規/更新行、monitor新規行は各0件。これは取得時のSELECT結果であり、削除済み行・すべての外部操作の不在は証明しない。

6〜9月原本4 ZIPは作業ログのbytes/SHAと一致した。canonical candidate reportは0件。10月archiveの404は前作業の10月30日HEAD記録を参照し、今回は再取得・再集約・fit・評価消費台帳更新を行っていない。以前の診断CSV/manifestは削除済みで、hashと集計値だけを履歴として確認した。

<a id="20261030-checkpoint-review--ワークフローと検証方法の評価"></a>

#### ワークフローと検証方法の評価

小さなcommitと対象pathのstage、未使用評価窓をデバッグへ使わない判断、合成環境の所有確認・cleanupは妥当である。最初のsuccess表示をartifactまで見て訂正し、pipefailとexact image tag削除へ修正した流れも適切だった。

一方、テスト成功件数が増えても、A01/A02のような計画と受入条件の差は残りうる。以降は先に必須証拠・状態遷移・拒否ケースを表にし、Solでreviewした固定仕様に沿って実装する。Lunaには範囲が決まったfixture、読取照合、資料整備を割り当てる。通常sandboxでTestClientが止まり権限付きで完了する観測はあるが、特定syscallだけが原因と断定せず、実行環境差として記録する。

今回の監査で、モデルの変更・別モデルへの自動委譲はしていない。モデル分類はプロジェクト上の推奨であり、このrepoを両モデルで同条件比較した実測ではない。

<a id="20261030-checkpoint-review--主な確認コマンド"></a>

#### 主な確認コマンド

A01/A02のprobeは次のように再現できる。261021から実行し、test fixtureのfake clientだけを使う。実registry、report、DB、配信状態は操作しない。

~~~bash
.venv/bin/python - <<'PY'
from tests.test_capital_deployment_decisions import eligible_registry
from bike_mlops.capital_deployment_decisions import (
    _record_operational_review_locked, _record_deployment_approval_locked,
)
from bike_mlops.deployment_gates import require_deployment_eligible

for change in (None, ('final_report_sha256', '9'*64),
               ('capital_operational_plan_sha256', '8'*64),
               ('capital_operational_p95_ms', '-1')):
    client = eligible_registry()
    _record_operational_review_locked(client, '2', 'fixture-reviewer', 'passed', 'synthetic only')
    _record_deployment_approval_locked(client, '2', 'fixture-approver', 'synthetic only')
    if change:
        client.tags[change[0]] = change[1]
    require_deployment_eligible('capital-bikeshare', client.tags,
                               model_name='bike-demand-capital', model_version='2')
    print(change, 'accepted by examined 070b033')
PY
~~~

上記は監査対象commitでの不足を再現するprobeであり、修正後も通すべき正常条件ではない。修正時はT01/T02の拒否テストへ変更する。

~~~bash
git log --reverse --format='%h %aI %s' 10dfb8f..070b033
git diff --stat 10dfb8f..070b033
gh run list --branch main --limit 100 --json databaseId,headSha,status,conclusion,workflowName,url,createdAt
gh run view 40966966672 --log
gh api repos/kenobix/Claude_practice/actions/runs/40966966672/artifacts
gh api repos/kenobix/Claude_practice/actions/artifacts/12938433488/zip
.venv/bin/python -m unittest discover -s tests -p 'test_*deployment*' -v
.venv/bin/python -m pip check
sh -n scripts/bikectl
~~~

gh操作はrepo root、Python/test操作は261021で行った。raw CI ZIP/log、合成probe結果、READ ONLYのDB/runtime JSONはGit対象外のdata/reports/review-20261030へ保存し、hashと判定を証拠JSONへ転記した。DB確認はBEGIN READ ONLY内のSELECTのみ。4 ZIPのSHAも再計算した。secret/.envは公開証拠へ含めない。

更新した7 Markdownの相対リンク85件、新規の明示anchor、T01〜T17の表構造を検査し不一致0件。HTML renderはローカルにMarkdown parserがなく未実施で、新しい依存は導入しなかった。公開時のJekyll buildはpush後のPages CIで確認する。
