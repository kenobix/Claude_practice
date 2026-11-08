# T09取得監査とT10開始手順

**2026-11-06の現在状態:** [T09の10月原本取得・構造/右端境界検査](t09-october-acquisition.md)は完了。
連続47原本が揃った。正式集約を新pathで開始した。初回は行ごとの変換を改善するため意図的に中断しcleanupを確認し、
同hourの重みを保つ加算へ変更して別pathで実行中。[中断とlocal検証](reviews/20261106-t10-grouping-checks.json)。
改善実装bae010aの[CI569件/全system/16artifact/hash/cleanup](reviews/20261106-t10-grouping-ci-checks.json)は受入済み。
先行再集計は1800秒/exit124でpairなし。SQLの蓄積ID全走査を使い捨てplan/VM試験で切り分け、
current chunkからのPK照合に修正した[local検証](reviews/20261106-t10-query-checks.json)後、別pathへ同条件で実行中。
2026-11-08再開時、v3のexit0/CSV・manifest完成とtemp/lock回収を確認。全47 SHA/stat・schema3/月別収支、独立IDソート後の32,849全hour一致も受入済み。
protocol凍結と新実装CIはまだ未受入。実fit/採点は行っていない。
以下の404/46of47等は当時の履歴として保持し、現状の公開待ちと解釈しない。

**891a420以降の更新:** 左側は202212を保守的な隣月bufferに採用し、公式原本を取得・202301との境界を監査した。
以下の開始手順を`202212`開始へ更新した。target/評価窓・recipeは変更していない。
追加の[検証索引](reviews/20261104-backend-boundary-checks.json)と、末尾の境界reviewを参照する。

**1e622dd以降のT09確認:** 2026-11-04 13:42:25UTCの公式S3 HEADも202610=404。
通常sandboxのDNS失敗を含む試行/終了値/hashは[追跡索引](reviews/20261104-followup-checks.json)へ保存した。
既存原本の追加取得・正式集計・protocol作成・fitは行わず、10月原本待ちを維持する。

2026-11-04、基点`d8ec164`。[今回の検証索引](reviews/20261104-clock-readiness-checks.json)。
**既存45 ZIPの完全性と既存手順を確認した。T09/T10の完了、protocol凍結、fit・採点はまだ行っていない。**
計画の[最新残件](../../261014/plan_mlops.md#remaining-work-latest)と
[固定済みの初回評価方針](capital-bootstrap-gate.md)を優先する。

## 今回確認したこと

- 公式S3 objectへのHEADは2026-11-04 12:34UTCで202609=200/25,848,624 bytes、202610=404。
  初回の通常sandboxはDNS接続失敗でexit1。外部接続可能な読取試行と分けて保存した。
- 手元の202301〜202609は連続45件、合計842,692,154 bytes。
  41件は既存manifest、6〜9月の4件は以前のwork_log記録のbytes/SHAと一致した。
  全ZIPのCRC、CSV member1件、member名重複なし、ride_id/started_at/ended_atのheaderを確認した。
  読取前後のdev/inode/size/mtimeも不変。CSV本文の再集計や精度の計算はしていない。
- 202604原本は存在する。過去の一時診断が4月を含まなかったことを「4月原本がない」と解釈しない。
- 既存legacy registryと提案validation/final窓は非重複。現在の既定消費台帳JSONは不存在。
  残っているlock fileを削除していない。JSON不在だけでは、他checkoutを含む過去の未消費を証明できない。
- 既存aggregation CLIのhelpは成功。protocol CLIはprofile未設定で拒否し、
  `DATA_PROFILE_ID=capital-bikeshare`を明示した別試行のhelpが成功した。拒否条件を変更していない。

ZIP検査は、行ごとの重複・時刻異常・月境界・DST・出版社の欠落を監査したことにはならない。
それらは10月原本到着後の正式snapshotで検査する。

## 開始時の停止条件と実施順

1. **T09:** 10月objectが公開されてから既存`acquire_archive()`で取得し、取得元・日時・bytes/SHA・ZIP/CSV schemaを新規logへ保存する。
   HEAD200だけで完了にせず、既存45原本と追加した202212 bufferとの組合せを固定する。原本の差替えやschema差があれば保存してSol判断へ戻す。
2. **T10の境界review:** 原本範囲は202212〜202610、対象target窓は2023-01-01〜2026-10-01未満。
   12月を左側buffer、10月を右側bufferとして採用する。採否理由と限界は末尾を参照する。
   月名の連続やmin/maxだけで全tripの完全性を宣言しない。追加の原本範囲変更が必要なら取得・監査へ戻し、評価窓は短縮しない。
3. **新pathへ集計:** 以下のコマンド案を既存原本・buffer review後に使う。古いdefaultの202604終了や、既存CSVを使わない。
   この文書内のblockは今回実行していない。集計が途中で失敗した場合もlog/途中状態を保持し、別の期間へ自動retryしない。

```bash
cd /home/kenshin/work/claude_practice/261021
TASK_T10_DIR="data/derived/capital-bootstrap-$(.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)')"
.venv/bin/python scripts/prepare_capitalbikeshare.py \
  --archive-start 202212 --archive-end 202610 \
  --data-start '2023-01-01 00:00:00' \
  --data-end-exclusive '2026-10-01 00:00:00' \
  --output-csv "$TASK_T10_DIR/hourly.csv" \
  --output-manifest "$TASK_T10_DIR/manifest.json"
```

4. **正式manifest review:** schema3、commit-marker相当のCSV/manifest pair、全原本hash、TZif identity、row収支、
   重複IDと値不一致、時刻coverage、DST除外・欠測理由を監査する。旧schema1/2を新protocolへ流用しない。
   想定件数と違えば原因を調べ、合格する件数へ窓を変更しない。bufferの根拠を含めた全snapshotを保存する。
5. **protocolの固定:** profileを明示し、Capital初回は`--incumbent-version`を省く。
   UCI版3をCapital incumbentへ代用しない。以下は前段のreview後のコマンド案で、まだ実行していない。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_MODEL_NAME=bike-demand-capital \
FEATURE_CONTRACT_VERSION=bike-demand-features-v1 \
MLFLOW_DISABLE_AGENT_HINT=1 \
.venv/bin/python scripts/create_capital_evaluation_protocol.py \
  --csv "$TASK_T10_DIR/hourly.csv" \
  --source-manifest "$TASK_T10_DIR/manifest.json" \
  --training-cutoff '2026-06-01 00:00:00' \
  --validation-start '2026-06-01 00:00:00' \
  --validation-end '2026-08-01 00:00:00' \
  --evaluation-start '2026-08-01 03:00:00' \
  --evaluation-end '2026-10-01 00:00:00' \
  --output "$TASK_T10_DIR/protocol.json"
```

`TASK_T10_DIR`は同じshellで保持する。新しいshellでは、採取logの実際のdirectoryを指定し直す。
初期profileのdefault入力は古い`capitalbikeshare-next`なので、後続CLIでも新pathを明示する。
この準備では`.env`・profile default・DB・registry・配信を変更しない。

## 凍結時に確認する値

| 項目 | 維持する条件 |
|---|---|
| training cutoff | 2026-06-01 00時。+3時間遅延仮定で最終利用可能targetは5月31日21時、22/23時を含めない。学習件数は正式CSVから確定 |
| validation | 6月1日00時〜8月1日00時未満、想定1464時間 |
| 選択判断 | validation最終targetのラベル到着は8月1日02時。全ラベル利用可能後に選択 |
| final | 8月1日03時〜10月1日00時未満、想定1461時間。00〜02時は境界として除外 |
| recipe | 現行HGB Pipeline、learning_rate0.08 / max_iter160 / max_leaf_nodes31 / l2_regularization1.0 / early_stopping false / random_state17。source snapshotと実際のprotocolにも結合 |
| 精度gate | 同一行baselineにMAE厳密改善かつRMSE以下。segmentは件数付き診断のみ。policy本体/ID/SHAを固定 |
| 監視 | training-only referenceと現行monitor policyを凍結。後続分布でbins/閾値を選び直さない |
| 消費 | 保存時の重複拒否を確認し、fit直前に消費台帳の開始記録。失敗・中断した窓も無断再利用しない |

今回の1464/1461は日時算術とlegacy/台帳の読取照合であり、実CSV/manifestのsplit件数ではない。
protocol作成だけでT11や運用合格を開始しない。計画の時計資格・必須証拠等の依存条件を満たし、
T10のレビューが完了してから一度だけcandidate fitへ進む。validation不合格時は同じ窓で調整し直さない。

時計資格の前提は[修復後の受入設計v1](clock-qualification-plan.md)を参照する。
設計と合成算術の検算は資格の合格ではない。採取器/外部UTC比較の実装・修復許可・実受入は未完了であり、
この設計追加によってT10以降の停止条件が満たされたとは扱わない。

## 891a420以降の左端buffer review（2026-11-04）

公式[system-data説明](https://capitalbikeshare.com/system-data)は月次公開と開始/終了日時を説明するが、
すべてのrowが`started_at`の年月で厳密にpartitionされることを保証していない。
以前に202510原本へ9月開始tripが含まれた記録もある。
そこで、**境界のrowを数える前に**原本範囲を202212〜202610とする保守的な判断を`boundary-design.json`へ保存した。
結果が0でも12月を後から除外しない。1か月bufferで公開母集団全体の完全性や最大遅延を保証するものではない。

- 202212を既存`acquire_archive()`で取得。6,207,590 bytes、SHA
  `f3044ca2af1e1688c3f305e5806ac256d3c8b327c63726e82523a954cb8ffc97`。
  取得時刻は2026-11-04 13:16:50UTC（未資格のlocal時計）。ZIP CRC/CSV memberを確認した。
- 202212の175,233行、202301の204,077行を`ride_id/started_at/ended_at`だけで監査。
  必須欠測、各file内ID重複、両file間の共有ID、開始年月の越境はいずれも0。
  read前後のstat不変、202301のSHAは前回索引と一致。別processの標準`csv` parserでも件数・ID集合・時刻boundsを照合した。
- これは左端2fileの入力監査。原本45件の全件再検査、全行の品質保証、hourly targetの再集約、
  validation/finalの採点、消費台帳の開始ではない。trip IDを公開log/derived CSVへ出力していない。
- 既存45件に今回1件を追加し、保管原本は46件になった。全範囲案47件のうち202610は13:15:46UTCの公式HEADで404。
  今回も10月待ちを維持し、正式schema3 manifest/全原本snapshot/消費確認/protocol凍結は未実施。
- 既存の合成pipeline回帰8件で、連続archive、月をまたぐID重複排除、conflict拒否、row収支/DST/hash、既存pair保護を確認した。
  最初はrepo rootから起動して`ModuleNotFoundError: scripts`となった。`261021`をcwdにした別試行で全8件成功。
  source・import条件・検証基準を変えて通したものではない。

今回の12月原本の開始時刻はすべてtarget開始より前なので出力targetへ含めず、学習開始を2022年へ延ばさない。
ラグの初期欠測を消すための追加学習データにも使わない。6月/8月の独立窓とlabel cutoffも維持する。
