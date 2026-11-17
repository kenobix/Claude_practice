# PCAP Analyzer MVP受入記録

2026-11-18（日本時間）。対象: tool 0.3.0 / JSON Schema 0.4.0。
[元計画](../../../261014/plan_pcap_analyzer.md)のPhase 0〜4を照合し、計画の人工入力・対応範囲で全受入条件を満たすと判定した。
開発時の受入判定であり、別担当者による独立レビューは未実施。既存Draft PR #3でレビューに引き継ぐ。

| Phase | 計画の受入条件 | 検証と証拠 |
|---|---|---|
| 0 | 指標の計算式と解析できない場合の表示を説明できる | [仕様・指標・構成](spec.md)、[14 fixtureの設計と手計算](fixtures.md)、[期待値](../expected/fixtures.json)。新規環境で生成した全14件が保存版とバイト一致。Phase 0試験で固定時刻・長さ・基準DNSを確認 |
| 1 | 正常・空・未対応・破損で規定どおり終了し、読めた件数と集計が一致する | [Phase 1試験](../tests/test_phase1.py)で空ファイルとヘッダーのみ、未対応link type/pcapng、途中ヘッダー/本文、caplen/wirelen、整数ns、安全上限・決定性・加算性を確認。新規環境のCLIでも基準10件・680/680 bytes、捕捉切詰め1件・40/54 bytes、途中本文0件・終了2、pcapng拒否を再現 |
| 2 | 双方向フローとDNS照合を行い、重複ID・逆順・記録終端・フラグメントを成功応答と混同しない | [Phase 2の受入記録](phase2_acceptance.md)と[CLI受入テスト](../tests/test_phase2_acceptance.py)。同一IDの別ポート/名前/type/classを分離、重複は曖昧、孤立・期限外・終端は別状態。時刻逆行で全時間結果を破棄。フラグメント・捕捉不足・不正DNSを照合から除外。方向別/未帰属の分割、TCP flags、根拠、Schema、キー上限も確認 |
| 3 | TSharkと同じfixtureを比較し、対応範囲の違いを説明して未説明差を残さない | [14保存fixtureの比較](tshark_comparison.md)と[DNS比較4ケース](tshark_dns_comparison.md)。TShark 4.2.2実体での版・設定・コマンド・抽出結果を保存。5組の一意なDNS応答時間が一致し、重複/期限外/逆行の相違は説明済み。比較対象で未説明差分0。今回の回帰テストでは保存済み参照行を再生し、TShark実体は再実行していない |
| 4 | 実通信の捕捉なしに、成功例と解析限界を新規環境から再現できる | [READMEの新規環境手順](../../README.md#新規環境からの受入再現)をコミット済みソースの独立コピーと新規Python 3.12.3 venvで実行。固定依存導入、生成、実CLI、全457テスト成功（44.10秒）、pip check成功。正常・曖昧・捕捉切詰め・破損・非対応・時刻逆行を確認 |
| 4 | 固定規模の処理時間・最大メモリ・解析状態割合を記録し、3分動画を添える | [計測条件と結果](benchmark.md)：500/5,000/15,000件各3回。処理時間と最大RSS、40/20/20/20%の分類を記録。[3分MP4](demo.mp4)・[字幕原稿](demo.ass)を収録。全4,320フレームをデコードし、9場面を目視確認 |

通常GitHub Actionsが実施するのは固定依存での回帰テストと小規模ハーネス試験。実TShark、性能の全9試行、動画の生成・目視確認はローカル証拠であり、CIによる実施ではない。
実通信、別OS・Python版、DNSや多数フローの高負荷、入力最大値付近の性能は未検証。これらは当初の人工入力による受入とは別。対象形式、安全上限、観測限界は[仕様](spec.md)を維持する。

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
3. 対応範囲・安全上限・欠測の説明が変わっていないことと、最新commitのCIを確認して最終判定する。

実装・受入成果物の進捗は100%（残り0%、計画の成果物基準）。独立レビューでの承認は別途。発展課題の実装は開始しない。
