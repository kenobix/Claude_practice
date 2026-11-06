# T10：固定原本の正式snapshot・事前protocolのreview

2026-11-06。[T09の取得](t09-october-acquisition.md)後、固定済みの47原本でT10を進めている。
この文書の作成時点では、新producerのCSV/manifest受入とprotocol固定はまだ完了していない。
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

新しい出力先は`capital-bootstrap-f5daf2e4114e4788b8c77f357249422f`。
同じ1800秒の上限を使い、専用TMPDIRのlease/cleanupを残す。旧attemptの出力を再利用しない。
開始時はsource未commit差分を含む36b9306だったことをplanに明記し、保存bytesのSHAを後の実装bae010aへ結合する。
[local索引](reviews/20261106-t10-grouping-checks.json)はCI/実pair確認前のsnapshotとして保持する。
実装bae010aは[CI42214102466](https://github.com/kenobix/Claude_practice/actions/runs/42214102466)の全569件/全systemで成功し、
[CI索引](reviews/20261106-t10-grouping-ci-checks.json)で16 artifact/hash/archive-only/cleanupも照合した。
この成功は合成契約の受入であり、実47原本のsnapshot/実候補採否の受入ではない。

## 完了前の停止条件

producerのschema3 manifest、全47 input SHA/stat、CSV/manifest pair、全hourの独立histogram一致、
月別unique/duplicate・範囲外/DSTの収支を確認してからprotocolを新pathへ保存する。
legacy/default消費台帳の重複拒否を維持し、保存しても消費開始や実fitの許可とは扱わない。
他checkoutも含む未消費の保証や実行真正性は、local hashの一致だけでは得られない。

原本fileの連続/min/maxは出版社の欠落ゼロを証明しない。
時計資格・独立源/校正dossier・修復/保守・必須producer等が未達の間、実fit/採点/実運用gate/採否は開始しない。
確認できた範囲と残件は[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を正規の一覧とする。
