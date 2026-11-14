# T10：固定原本の正式snapshot・事前protocolのreview

**2026-11-14方針改訂:** [製品作業の開始・停止条件](product-completion-policy.md)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

2026-11-08更新。[T09の取得](t09-october-acquisition.md)後、固定済みの47原本で正式CSV/manifestを独立照合し、事前protocolを固定した。
学習・採点・candidate作成は開始していない。SQL改善9147027の[CI・正式pair/protocol索引](reviews/20261108-t10-snapshot-checks.json)まで受入済み。
合成CI、実原本のデータ検査、実候補の学習/採点、実時計資格を別々に扱う。

## 変えない入力と判断条件

- 原本：202212〜202610の47 ZIP、合計881,747,861 bytes。各SHAは過去の取得記録と照合した。
  12月/10月のbufferを結果に応じて除外しない。
- target：2023-01-01 00時〜2026-10-01 00時未満。naiveなAmerica/New_Yorkの壁時計を使う既存契約。
  rawの文字列にUTC offsetが付いているという意味ではない。
- training cutoff：2026-06-01 00時。ラベル3時間遅延は測定済みSLAではなく実験仮定。
- validation：6月1日〜8月1日未満、final：8月1日03時〜10月1日未満。
  validation全ラベル到着後の選定時点は8月1日02時。境界3時間をfinalへ混ぜない。
- recipe：現行HGB Pipeline、learning_rate 0.08/max_iter 160/max_leaf_nodes 31/l2 1.0/early_stopping false/random_state 17。
  同一行baselineへMAE厳密改善かつRMSE以下。segmentは件数付き診断、運用gateは別の証拠を必要とする。
- 初回Capitalのincumbentは無し。UCI版3を代用せず、既評価のQ3/Q4・旧Capital期間を選定/最終評価へ再利用しない。

## 原本から独立に再計算したこと

既存producerとは別processで、標準CSV parserを使い全22,925,436行の構造・必須ID/開始日時・月別boundsを再計数した。
さらにpandas/SQLiteを使わず、privateな200,000行単位の外部ソートとheap mergeで全IDを照合した。
同じIDをmonth/hour順に並べ、最初の1件だけ数え、同IDの開始hour不一致は拒否する。
比較は既存の開始hour契約であり、全列が同じかを判定したものではない。

| 独立計算の項目 | 値 |
|---|---:|
| 元の行数 | 22,925,436 |
| 重複行 | 205（202406に141、202605に64） |
| unique ID | 22,925,231 |
| 固定window外のunique trip | 852,840 |
| DST除外のunique trip | 1,568 |
| targetへ数えるtrip | 22,070,823 |
| 有効なnaive hour | 32,849 |

重複0を仮定したraw histogramを最終CSVと同一視しない。
実際に205件があったため、重複除去後のhour histogramを作り、producerの完成後に全hour/月別収支と照合する。
ソート用115 fileは全てclose/削除済み。ID/個別rowは公開せず、histogramもgitignore下で保持する。

DSTは同じpinned TZif bytesを読み、標準`datetime`のfold/UTC round-trip法で独立にgridを作った。
2023/2024/2025年11月の重複01時3件と、2023/2024/2025/2026年3月の存在しない02時4件を除外する。
これは日付の計算であり、OSの時計や独立UTC源を観測・校正する処理ではない。

## 初回中断と集計処理の改善

最初の出力先は`capital-bootstrap-c66c8888e4c243d7ad88d527b1899c20`。
長い行ごとの時刻変換を改善するため、完了/timeout前に意図的にSIGINTした。
PID・開始tick・exe・namespace・exact argv・専用lockを照合した自分のprocessだけを対象にし、
終了後にprocess/temp/lock消滅とCSV/manifest不存在を確認した。
child `-2`、wrapper `254`を保持し、成功や自然T03再発へ読み替えない。
中断時のstackはCSV parse内で、Timestamp変換が全処理時間の原因と証明したものではない。

ID dedup/conflict照合を済ませた各chunkについて、同hourの件数を束ねて加算するようにした。
target・範囲外・DST全てで重みを保ち、schema/出力列/zero-fill/ID基準/期間は変えない。
合成90,000行では両計算の収支が一致し、Timestamp構築は90,000回対6回だった。
これは処理回数の比較であり、物理的な速度倍率やSLOの実測ではない。
spring/fall・複数trip・重複を含む9件、既存protocol10件、独立sort helper5件が成功した。

2回目の出力先は`capital-bootstrap-f5daf2e4114e4788b8c77f357249422f`。
同じ1800秒の上限を使い、専用TMPDIRのlease/cleanupを残す。旧attemptの出力を再利用しない。
開始時はsource未commit差分を含む36b9306だったことをplanに明記し、保存bytesのSHAを後の実装bae010aへ結合する。
[local索引](reviews/20261106-t10-grouping-checks.json)はCI/実pair確認前のsnapshotとして保持する。
実装bae010aは[CI42214102466](https://github.com/kenobix/Claude_practice/actions/runs/42214102466)の全569件/全systemで成功し、
[CI索引](reviews/20261106-t10-grouping-ci-checks.json)で16 artifact/hash/archive-only/cleanupも照合した。
この成功は合成契約の受入であり、実47原本のsnapshot/実候補採否の受入ではない。

## SQL計画の退化と正式pairの受入

重み付き加算だけの2回目は1800秒/exit124で終了し、CSV/manifestは存在しなかった。
専用tempは親が回収した。残った専用build lockも、終了値・exact argvのprocess不在・lockのstat/内容を照合して保存後に回収した。
失敗結果を成功に書き換えず、新しい出力先だけで3回目を行った。

同じDDLの使い捨てDBで、従来JOINが蓄積済みID全体を走査していた。
current10行を固定して履歴1,000→100,000行にするとSQLite VM命令数が4,012→400,012へ増加した。
currentから履歴の主キーを検索するEXISTSでは119→119。同hour再送許容/異hour拒否を保つ回帰は修正前FAIL、修正後10件成功、既存protocol10件も成功。
命令数とquery planによる機構の切り分けであり、実時計で測った速度倍率や過去T03の原因確定ではない。
[local索引](reviews/20261106-t10-query-checks.json)へ負例・失敗・source bytes・原本hashを保存した。

3回目は同じ47原本/窓/1800秒上限でexit0。実行開始時は1bf9584+未commit差分で、保存script SHAを後の実装9147027とbyte照合する。
正式出力は`data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/`。
2026-11-08にschema3 validatorによる47 ZIP SHA/sizeとstat・月別min/max/収支・pinned TZifを照合し、
独立した外部sort/merge後の全hour histogramとCSVの完全一致を確認した。専用temp/lockも回収済み。
7つの欠けhourは秋の曖昧3+春の存在しない4であり、ゼロを挿入しない。その他32,849 hourは既存の有効gridと一致する。

## 固定した事前protocolと確認場所

正式folderの`hourly.csv`、`manifest.json`、`protocol.json`はGit対象外で保存する。原本の再配布やMLflowへの登録はしていない。

| 保存物 | SHA-256 |
|---|---|
| hourly.csv | `df216153f825904d3cc06f4b20d4be9f3de8e9270b971ea6a11590b13dea87c0` |
| manifest.json（file bytes） | `721ee82537ddc4118f222b395d9b671e5d20a20b73cbdf84d5dad6b057b7edce` |
| protocol.json（file bytes） | `6cfb89a6a226209e2139abb64d40f9b3f7cdd72d781501d886c51ac18f59fcda` |
| protocol_sha256（自身のhash欄を除いた正規化本文） | `925fc86ff297bba922318c45bff95623e38471a09cec8c365f235ed84f112714` |

| 区分 | 件数 / 境界 |
|---|---|
| train | 29,919。最終targetは2026-05-31 21時、そのラベルが6月1日00時に到着する仮定 |
| validation | 1,464。6月1日00時〜8月1日00時未満。最後のラベル到着後、8月1日02時に選択 |
| reserved final | 1,461。8月1日03時〜10月1日00時未満。選択に使わず一度だけの同一candidate評価へ予約する設計 |
| 境界として使用しないhour | 5。5月31日22/23時と8月1日00/01/02時。訓練/validation/final間の重複は0 |

11 source/config/lockのbytesを実装9147027と照合してから、既存CLIでbootstrap protocolを保存した。
`incumbent`はnull。UCI版3のモデルを読み込まず、Capital初回は履歴baselineと比較する固定方針を維持する。
MAE厳密改善かつRMSE以下、segment診断のみ、training-only monitor policy、固定recipeは変更しない。
別processの標準CSV/datetime/hash計算でも、期間の件数/label到着/境界5時間/3 splitの非重複とpolicy/hashを照合した。
監視policyのdigestは末尾LFを含み、protocol/精度policyは含まない。最初の独立検算のLF見落としFAILもログに残した。

確認用の読取だけなら、project rootで次を実行する。

```bash
.venv/bin/python -m json.tool < data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/protocol.json
```

生logと実行command/終了値/UTC・MONOTONIC/hashは`data/reports/20261106-t10/`にある。
`aggregate-v1`/`aggregate-v2`/`aggregate-v3`、`formal-audit-v3`、`freeze-protocol-v3`、`independent-protocol-audit-v1/v2`を区別する。
時刻は未資格のlocal値。protocolの`created_at_utc`も真正性や実時計資格の根拠にしない。
MLflowサイトに新しい学習run/Capitalモデルはまだないため、そこで新しい精度結果を探す段階ではない。
今回見た原本集計値を隠さず`historic_source_aggregate_statistics_were_seen=true`を維持し、blindな将来試験と呼ばない。

## 実fit前に維持する停止条件

producerのschema3 manifest、全47 input SHA/stat、CSV/manifest pair、全hourの独立histogram一致、
月別unique/duplicate・範囲外/DSTの収支を確認してprotocolを新pathへ保存済み。
legacy/default消費台帳の重複拒否を維持し、保存しても消費開始や実fitの許可とは扱わない。
他checkoutも含む未消費の保証や実行真正性は、local hashの一致だけでは得られない。

原本fileの連続/min/maxは出版社の欠落ゼロを証明しない。
現在の開始条件は[作業別方針](product-completion-policy.md)へ変更した。履歴fit/精度評価に全時計資格を要求しない。運用測定・採否にはそれぞれの実証が必要。
確認できた範囲と残件は[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を正規の一覧とする。

実装9147027/[CI42447659584](https://github.com/kenobix/Claude_practice/actions/runs/42447659584)の全570件/対象pipeline10件は成功した。
全system/16 artifactのhash・archive-only検算・cleanupも照合した。CIで本47原本を集約したという意味ではない。
正式pair/protocolの独立照合はlocalの保存原本で、合成CI・実時計資格・実候補性能の証拠と分ける。
公開索引にはpath/size/SHA/コマンド/終了値と限界を残し、raw trip ID・ZIP原本・実DBや.envは含めない。
後続の文書commitを、先行9147027のCIで直接試験したとは呼ばない。
