# T09：2026年10月原本の取得・構造と右端境界の照合

2026-11-06。前回の404から約22時間後、公式S3のHEADが200へ変化した。
[公式ZIP](https://s3.amazonaws.com/capitalbikeshare-data/202610-capitalbikeshare-tripdata.zip)を既存の
`scripts/prepare_capitalbikeshare.py`の`acquire_archive()`で新規取得した。
**T09の取得・構造検査は完了。T10の正式集約/全manifest/protocol凍結、学習・採点はこの検査に含めない。**

## 原本の同一性

| 項目 | 確認値 |
|---|---|
| 公式HEADのDate | 2026-11-06 11:11:56 UTC（JST20:11） |
| Last-Modified | 2026-11-05 16:54:56 UTC |
| ZIP bytes | 32,848,117（HEADと取得後が一致） |
| SHA256 | `f57da7b7b93f95d3e14f4377369b1db2aeb2c5f6542d30a60ecd25f4507a69fe` |
| CSV member | `202610-capitalbikeshare-tripdata.csv`、1件 |
| CSV非圧縮bytes | 134,922,053 |
| CSV行数/列数 | 677,607行、13列 |
| 保管原本範囲 | 固定済みの202212〜202610、連続47件 |

取得先はgitignore下の`data/raw/capitalbikeshare/monthly/`。
既存ZIP/`.part`の上書きを拒否し、既存46件のfile名集合が変わらないことを確認した。
SHAは受領bytesの結合であり、HTTP Dateは公開状態の記録であって独立UTC校正の証拠ではない。
ZIPのCRC、member名重複なし、CSV1件、9月と同じ13列のheaderを照合した。
原本の再配布やtrip ID・station名・座標・個別rowの公開はしない。

## 右側2原本の品質・境界検査

`ride_id/started_at/ended_at`をpandasで分割読取し、専用の使い捨てSQLiteでIDと開始hourを照合した。
続いて別processで標準`csv`/`datetime`/整数のnanosecond計算とID mapを使い、同じ原本SHAから再確認した。
後者はpandas/SQLを使わず、全rowの列数13・必須値・日時形式も検査した。

| 検査 | 202609 | 202610 |
|---|---:|---:|
| 全行数 | 676,808 | 677,607 |
| 必須値欠測/日時parse不正/終了が開始より前 | 各0 | 各0 |
| 原本内ID重複 | 0 | 0 |
| 開始が表示月より前の行 | 50 | 0 |
| 開始が翌月以降の行 | 0 | 0 |

2原本間の共有ID/開始hour不一致はいずれも0。
合計1,354,415行と開始時刻bounds、上表の検査値が独立parserでも一致した。
両原本のSHA/dev/inode/size/mtime/ctimeは検査前後で不変。使い捨てSQLiteは削除済み。

**9月原本には8月30日開始を含む50行があった。**
これは「file名の月＝全rowの開始月」という仮定を置けない具体例である。
既存の202212左buffer/202610右bufferを維持し、10月の越境0件を理由に右bufferを除外しない。
正式集計で全原本間のID重複・conflict/収支を検査する必要がある。
同じ開始hourのID重複は既存のhourly target契約に従って扱い、境界確認のためにrecipeや評価窓を変えない。

## 証拠と限界

[検証索引](reviews/20261106-t09-checks.json)と`data/reports/20261106-t09/`に、
取得/主監査/独立照合のscript・command・終了値・log bytes/SHA・入力SHAを保存する。
公開HEADの原本は時計作業の`data/reports/20261106-clock-scheduler/t09-head-resume-v1.*`と結合する。
初期のschema previewはチャットのstdinコマンドと保存JSON、主要な取得/監査/検証は保存scriptとrunner記録を使う。
local UTC/MONOTONICは未資格である。2原本の検査だけで出版社の全母集団の完全性や、47件の正式manifest受入を宣言しない。

次は[固定済みT10手順](t10-input-readiness.md)で、新pathへ正式集計し、schema3/全原本hash・DST/収支/coverageをreviewする。
初回protocolを固定し、消費履歴・時計資格・必須producer等の前提が揃うまでfit/採点/実採否へ進めない。
OS/時計設定、実DB・実registry・現在の配信、`.env`を変更していない。
