# デモの台本と再生成

4分の無音MP4。日本語字幕を画面下に焼き込んだ。構成図は説明用。端末画面は実演で取得した終了コード・標準エラーと、 `status`・`conflicts` の項目、読取専用SQLの受理履歴、本文の独立照合を抜粋して組み直した。コマンドの共通引数を省略し、一時パスや認証トークンは表示しない。障害試験と性能計測の場面は別に実行して得た結果を表示する。

| 時間 | 台本と画面 |
| --- | --- |
| 00:00–00:23 | 同じマシンでA・ハブ・Bを実行する。説明図と、実演に使った製品コードSHAを表示。 |
| 00:23–01:04 | Aのseed・Bのpull、受理2件を表示。Bの `abc` と空本文のサイズ、SHA-256、基準版1を照合。不在のパスと区別する。 |
| 01:04–01:48 | Aの `hello` が先に受理され、Bが終了コード3で保留。ハブの版2、Bの元パス `world` と基準版1、Q・Rの管理コピーとSHA-256を表示。 |
| 01:48–02:14 | 最新のハブ版2を指定してBの `world` を選択。A・B・ハブは版3へ進み、Bの状態は解決済みとなる。 |
| 02:14–03:06 | 別の初期化でB未同期の間にAが削除。B復帰後の基準版2と、削除退避・旧blobの `abc`、受理履歴2件を表示。 |
| 03:06–03:46 | コミット後の応答消失と同一IDの再送、復元後の旧世代拒否の結果を説明。空ファイル100件・1,000件・10 MiBファイル1件の実測を表示。 |
| 03:46–04:00 | 同期中の外部編集停止、対象環境と件数・サイズ、実機電源断とデバイス試料の扱いを表示。 |

各場面の秒数と字幕は別ファイルに収録した。 [字幕の原稿](scenes.json)・[実演と復旧の手順](../file-sync/docs/phase5-reproduction.md)・[性能計測](../file-sync/docs/measurements.md)

## 再生成

`261128/file-sync` から実行する。製品のGo環境に加え、Pillow 12.2.0、FFmpeg 6.1.1（`libx264` あり）、Noto Sans CJK JPとDejaVu Sans Monoを使った。Pythonは3.12.3。既定と異なるフォントのパスは `--font`、`--mono` で指定できる。 [固定Goの準備](../file-sync/docs/toolchain.md#goと依存の再現)

```bash
export CGO_ENABLED=1 GOTOOLCHAIN=local GOWORK=off
go mod download all
result=$(mktemp -d)
product=5534795714958e52e8c6f05fc9f7a4a740dabf89
python3 -B tools/verify_mvp.py demo --product-sha "$product" --output "$result/demo.json"
python3 -B tools/render_demo.py --evidence "$result/demo.json" \
  --measurement docs/measurement-results.json \
  --output "$result/file-sync.mp4" --poster "$result/poster.webp"
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate \
  "$result/file-sync.mp4"
ffmpeg -v error -xerror -i "$result/file-sync.mp4" -f null -
```

新しい試行では世代ID、操作IDと管理コピー名が変わる。計測の製品コードSHAと実演の製品コードSHAは合わせる。対応する観測結果と字幕から再生成できる。 [公開用MP4](file-sync.mp4)

## 完成版の確認

ffprobe 6.1.1でH.264、1920 × 1080、24 fps、5,760フレーム、240.000秒、音声なしを確認した。公開用ファイルは2,432,170バイト。ffmpegで全編をデコードし、最初と最後のフレーム、各場面の字幕と端末行の表示を確認した。公開用MP4のSHA-256は `739004c056e260b588985beb0c1af349e347651eeff4846ed263a86f78730ee3`。
