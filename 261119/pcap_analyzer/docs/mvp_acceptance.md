# PCAP Analyzer MVP受入記録

2026-11-18（日本時間）。対象: tool 0.3.0 / JSON Schema 0.4.0。
[元計画](../../../261014/plan_pcap_analyzer.md)の受入証拠と、Draft PR #3の独立レビュー後の修正を記録する。
初回レビューは承認不可。IEEE 802.3、レコード長矛盾、DNSイベントのメモリ増大への対策を実装・検証し、修正後の独立レビューに引き継ぐ。

| Phase | 計画の受入条件 | 検証と証拠 |
|---|---|---|
| 0 | 指標の計算式と解析できない場合の表示を説明できる | [仕様・指標・構成](spec.md)、[14 fixtureの設計と手計算](fixtures.md)、[期待値](../expected/fixtures.json)。新規環境で生成した全14件が保存版とバイト一致。Phase 0試験で固定時刻・長さ・基準DNSを確認 |
| 1 | 正常・空・未対応・破損で規定どおり終了し、読めた件数と集計が一致する | [Phase 1試験](../tests/test_phase1.py)でIEEE 802.3混在入力の継続・件数・根拠番号と、caplenがwirelenを超える地点で終了2・正常prefix保持を確認。空ファイル、pcapng、部分レコード、整数ns、上限も回帰検証 |
| 2 | 双方向フローとDNS照合を行い、重複ID・逆順・記録終端・フラグメントを成功応答と混同しない | [Phase 2の受入記録](phase2_acceptance.md)と[CLI受入テスト](../tests/test_phase2_acceptance.py)。同一IDの別ポート/名前/type/classを分離、重複は曖昧、孤立・期限外・終端は別状態。時刻逆行で全時間結果を破棄。フラグメント・捕捉不足・不正DNSを照合から除外。方向別/未帰属の分割、TCP flags、根拠、Schema、キー上限と観測数上限も確認 |
| 3 | TSharkと同じfixtureを比較し、対応範囲の違いを説明して未説明差を残さない | [14保存fixtureの比較](tshark_comparison.md)と[DNS比較4ケース](tshark_dns_comparison.md)をTShark 4.2.2実体で再実行。malformed_lengthは解析器が拒否し、TSharkが1件を抽出する差を期待値に明記。DNSの抽出結果は保存版と同一。未説明差分0 |
| 4 | 実通信の捕捉なしに、成功例と解析限界を新規環境から再現できる | 修正前に[READMEの新規環境手順](../../README.md#新規環境からの受入再現)でクリーン導入と実CLIを検証。修正後は固定依存環境でpip check、14 fixtureの再生成と照合、全465テスト（22.13秒）に成功 |
| 4 | 固定規模の処理時間・最大メモリ・解析状態割合を記録し、3分動画を添える | [計測条件と結果](benchmark.md)：500/5,000/15,000件各3回。処理時間と最大RSS、40/20/20/20%の分類を記録。[3分MP4](demo.mp4)・[字幕原稿](demo.ass)を収録。全4,320フレームをデコードし、9場面を目視確認 |

通常GitHub Actionsが実施するのは固定依存での回帰テストと小規模ハーネス試験。実TShark、性能の全9試行、動画の生成・目視確認はローカル証拠であり、CIによる実施ではない。
孤立DNS応答の負荷と上限停止は[計測記録](dns_observation_limit.md)に記載。実通信、別OS・Python版、最大長DNS名、多数フロー、入力全体の上限付近の性能は未検証。対応範囲と観測限界は[仕様](spec.md)を参照。

## 3分動画

[再生・ダウンロードするMP4](demo.mp4)。字幕による無音のデモ。新規環境で実行したCLI出力の抜粋と、Phase 3で保存したTShark実測記録を使う。ライブ操作や今回のTShark再実行を撮影したものではない。

| 時間 | 内容 |
|---|---|
| 00:00–00:40 | baselineの件数・長さと双方向UDPからDNS #4→#5・25 msまで根拠をたどる |
| 00:40–01:40 | #6の未応答観測、#7・#8→#9の曖昧さ、捕捉切詰めと破損レコード、pcapng拒否 |
| 01:40–02:30 | 同じbaselineでTSharkと共通項目を照合。25 msの一致と重複時に20 msを採用しない理由 |
| 02:30–03:00 | 対応範囲と限界、独立レビューへ引き継ぐ理由と将来のpcapng設計候補 |

出力はMP4 / H.264 / 1280×720 / 24 fps / 180.000秒。ソースは[字幕と場面構成](demo.ass)。
既存FFmpeg 6.1.1（libass、libx264）とNoto Sans CJK JP / DejaVu Sans Monoでレンダリングした。再生成は`docs/`から実行する。FFmpegやフォントは製品・CIの依存には追加しない。

```bash
ffmpeg -hide_banner -nostdin -f lavfi -i 'color=c=0x101d2c:s=1280x720:r=24:d=180' \
  -vf 'subtitles=demo.ass' -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p \
  -threads 2 -movflags +faststart -an /tmp/pcap-analyzer-demo.mp4
ffprobe -v error -count_frames -show_entries stream=codec_name,nb_read_frames:format=duration \
  /tmp/pcap-analyzer-demo.mp4
ffmpeg -v error -xerror -i /tmp/pcap-analyzer-demo.mp4 -f null -
```

## 独立レビューの引き継ぎ

1. Draft PR #3のheadを別環境で展開し、READMEの再現手順と期待結果を照合する。
2. 動画の根拠 #4/#5、#7/#8/#9 と捕捉切詰めを、CLIのJSON・fixture・比較記録と突き合わせる。
3. IEEE 802.3を混在させた場合の継続、レコード長矛盾での正常prefix保持、DNS観測数上限後の部分結果と最新commitのCIを確認して最終判定する。

修正後のMVP承認は、入力境界の回帰試験、DNS観測数上限の停止結果、最新CIを対象とする再レビューで判定する。
