# PCAP Analyzer MVP受入記録

最終整理は2026-11-24（日本時間）。対象はツール0.3.0 / JSON Schema 0.4.0。
[元計画](../../../261014/plan_pcap_analyzer.md)のPhase 0〜4はすべて合格し、MVPは完成した。
独立再レビュー対象は `f0c6d4cb344ba73cd2a315be6645e449cdf41ce2`。
初回の指摘3件はすべて解消した。指摘はIEEE 802.3での例外、長さが矛盾するレコードを受け入れる問題、孤立DNS応答のイベントによるメモリ使用量の増大だった。再レビューではCritical / High / Medium / Lowは各0件だった。
[PR #3](https://github.com/kenobix/Claude_practice/pull/3)は `1ad2c778d086ba0ec757c2c217f905fa447f3605` としてmainにマージ済み。
マージコミットと整理時点のmainの `261119/` および専用CI設定は、独立再レビュー対象と一致した。

| Phase | 計画の受入条件 | 検証と証拠 |
|---|---|---|
| 0 | 指標の計算式と解析できない場合の表示を説明できる | [仕様・指標・構成](spec.md)、[14 fixtureの設計と手計算](fixtures.md)、[期待値](../expected/fixtures.json)。新規環境で生成した全14件が保存版とバイト一致。Phase 0試験で固定時刻・長さ・基準DNSを確認 |
| 1 | 正常・空・未対応・破損で規定どおり終了し、読めた件数と集計が一致する | [Phase 1試験](../tests/test_phase1.py)で、IEEE 802.3が混在しても処理を続け、件数と根拠番号を出すことを確認した。caplenがwirelenを超えた場合は、それまでの正常レコードを保持して終了2を返した。空ファイル、pcapng、部分レコード、整数ns、上限も回帰検証した |
| 2 | 双方向フローとDNS照合を行い、重複ID・逆順・記録終端・フラグメントを成功応答と混同しない | [Phase 2の受入記録](phase2_acceptance.md)と[CLI受入テスト](../tests/test_phase2_acceptance.py)。同一IDの別ポート/名前/type/classを分離、重複は曖昧、孤立・期限外・終端は別状態。時刻逆行で全時間結果を破棄。フラグメント・捕捉不足・不正DNSを照合から除外。方向別/未帰属の分割、TCP flags、根拠、Schema、キー上限と観測数上限も確認 |
| 3 | TSharkと同じfixtureを比較し、対応範囲の違いを説明して未説明差を残さない | [14保存fixtureの比較](tshark_comparison.md)と[DNS比較4ケース](tshark_dns_comparison.md)をTShark本体（4.2.2）で再実行した。malformed_lengthを解析器は拒否し、TSharkは1件を抽出する。この差は期待値に明記した。DNSの抽出結果は保存版と同一で、未説明差分は0件だった |
| 4 | 実通信の捕捉なしに、成功例と解析限界を新規環境から再現できる | 独立再レビューで[READMEの新規環境手順](../../README.md#新規環境からの受入再現)を再現。固定依存での導入、pip check、fixture 14件のバイト一致、成功例と解析限界のCLI出力、全465テストを確認 |
| 4 | 固定規模の処理時間・最大メモリ・解析状態割合を記録し、3分動画を添える | [計測条件と結果](benchmark.md)：500/5,000/15,000件各3回。処理時間と最大RSS、40/20/20/20%の分類を記録。独立再レビューの9試行で出力ハッシュが保存記録と一致。[3分MP4](demo.mp4)の全4,320フレームをデコードし、9場面をCLI出力と比較記録に照合 |

レビュー対象SHAのGitHub Actionsはpush・PRともに成功した。CIでは依存関係のバージョンを固定して回帰テストと小規模な検証用スクリプトの試験を実行した。TShark本体での比較、性能計測9試行、動画の照合はローカルで実施した。 [push CI](https://github.com/kenobix/Claude_practice/actions/runs/44198603807)・[PR CI](https://github.com/kenobix/Claude_practice/actions/runs/44198613928)

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

## 独立再レビューの追加検証

- 新規の人工PCAPで、IEEE 802.3が混在しても正常フレームを含めて全件を集計することと、`caplen > wirelen` で先行する正常レコードだけを保持し終了2になることを確認した。分類・フロー・DNSの根拠番号、件数、バイト数、Schemaを照合した。
- 展開後の長さが上限の255バイトになるDNS名を、孤立応答に含めて検証した。10,000観測まで照合し、10,001件目で `E_LIMIT_DNS_OBSERVATIONS`、終了2、`complete=false` と未割当番号を返した。
  入力を10,001件と80,000件にした両試行で、出力は10,000イベント・約23.64 MiB、CLIの最大RSSは約161 MiBだった。短い名前の孤立応答を使った修正前後の測定条件は[計測記録](dns_observation_limit.md)を参照。
- 重複問い合わせと追加応答も上限に含まれ、時刻が逆行した入力では件数を保持し、時間依存のDNSイベントを全件破棄した。

多数フローを含む入力全体の上限（100 MiB・100万レコード）付近の性能、別OS・Python版、実通信、VS Code GUIは未検証。対応範囲と観測限界は[仕様](spec.md)を参照。
