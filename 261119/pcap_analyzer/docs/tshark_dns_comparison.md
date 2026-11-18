# DNS独立比較とPhase 3完了判定

2026-11-17、Ubuntu 24.04 amd64 / Python 3.12.3 / TShark 4.2.2（4.2.2-1.1build3）。
環境は[既存の専用.venv展開手順](tshark_comparison.md)を使用し、追加インストール・OS変更なし。
今回の単位はPhase 3に残るDNS共通項目・一意な応答時間と、独自窓規則との相違の検証。
解析本体・Schema/ツール版・入力制限・対応形式・保存fixture14件は変更しない。

## コマンド・条件

`261119/pcap_analyzer/`から次を実行する。人工データだけを処理し、通信を送受信しない。

```bash
.venv/bin/python scripts/compare_dns_tshark.py > /tmp/pcap-dns-comparison.json
.venv/bin/python -m pytest -q
```

[スクリプト](../scripts/compare_dns_tshark.py)は既存baseline/backwardと、固定レシピの2ケースだけを処理。
追加PCAPは一時ディレクトリへ生成して終了時に削除し、実通信や任意の外部PCAPを入力しない。
版・設定・入力hash・全抽出行・一致するペア・除外理由・説明済み相違を
[結果JSON](tshark_dns.result.json)へ記録する。期待値は[手定義JSON](../expected/tshark.dns_cases.json)。
イベント配列の順序は `[status, query_indices, response_indices, response_time_ns, rcode, tc]`。
解析結果から期待値を自動生成/上書きしない。

```text
<root>/usr/bin/tshark -n -2 -r <fixed-fixture> -T fields -E occurrence=f
  -o ip.defragment:FALSE -o tcp.desegment_tcp_streams:FALSE
  -o tcp.analyze_sequence_numbers:FALSE
  -e frame.number -e frame.time_epoch -e ip.src -e ip.dst
  -e udp.srcport -e udp.dstport -e dns.id -e dns.flags.response
  -e dns.qry.name -e dns.qry.type -e dns.qry.class
  -e dns.flags.rcode -e dns.flags.truncated
  -e dns.response_to -e dns.response_in -e dns.time
```

フィールドは[公式DNS reference](https://www.wireshark.org/docs/dfref/d/dns.html)を確認し、
4.2.2実体の出力で検証した。DNSは将来フレームへの参照`response_in`も確認するため2 pass。
従来のフレーム/入力異常比較は1 passのまま。filterは未指定、名前解決は`-n`で無効化。
空のconfig/plugin/extcap、一式展開したdata/library、`LC_ALL=C`と`TZ=UTC`を使う。
継承した`WIRESHARK_*`設定を除く。スクリプト終了は0=期待どおり、1=未説明差分、2=実行/抽出/Schema異常。
非0終了、stderrの警告、timeoutを成功扱いしない。

## 人工データと手計算

追加2ケースはstructだけでEthernet/IPv4/UDP/DNSを組み、Scapyや解析器のDNS encoderを使わない。
基準秒1700000000、文書用IP192.0.2.1と198.51.100.53、固定MAC、portはclient=53000+ID/server=53。
通常の質問はalpha.test、QTYPE=1/QCLASS=1、1質問・RRなし。IPv4/UDP checksumは0（検証対象外）。
名前wire12 + type/class4 + DNS header12 + UDP8 + IPv4 20 + Ethernet14 = 70 bytes。
応答もRRなしで70 bytes。捕捉不足以外はcaplen=wirelen。整数ns pcapを手組みする。

| metadataの番号 | 時刻offset(ns) | 内容 |
|---|---:|---|
| 1 / 2 | 0 / 25000000 | ID10 query Alpha.test / 大文字名のNXDOMAIN応答、25ms |
| 3 | 26000000 | ID10追加応答、元の一意ペアは維持 |
| 4 / 5 | 1000000000 / 1000000123 | ID11 QTYPE28 / TC=1応答、123ns |
| 6 / 7 | 2000000000 / 7000000000 | ID12 QCLASS3 / 照合窓端ちょうどの応答、5秒 |
| 8 | 7000000001 | ID13応答のみ |
| 9 | 8000000000 | ID14 query、観測終了まで5秒に足りない |
| 10 | 9000000000 | ID15 IPv4 MF付き応答、DNS照合対象外 |
| 11 | 9000000001 | ID16応答の末尾1 byte不足、cap69/wire70、照合対象外 |
| 12 | 9000000002 | ID17を示す2-byteだけの不完全DNS、cap44/wire44、malformed |

metadataは70×10+69+44=813 captured、70×11+44=814 original bytes。
parsed9/unsupported1/truncated1/malformed1、DNS query4/response5。
TC=1はDNS応答の打切りflagであり、pcapの捕捉不足と別。完全なTC付きDNSはparsedとして照合する。

| late_reuseの番号 | 時刻offset(ns) | 内容 |
|---|---:|---|
| 1 / 2 | 0 / 5000000001 | ID20 query / 5秒+1nsの期限外応答 |
| 3 / 4 | 6000000000 / 6000000001 | 同じID/端点の新窓query / 1nsの応答 |
| 5 / 6 / 7 | 7000000000 / 8000000000 / 8500000000 | ID21 query / 応答 / 同じ窓でのキー再利用 |
| 8 | 13000000000 | DNS以外のUDP port9、窓を閉じる観測時刻 |

late_reuseは70×7+42=532 captured/original bytes、全8件parsed、DNS query4/response3。
既存baseline=10件680 bytes、backward=2件160 bytesと合わせ32パケット、captured2185/original2186 bytes。

## 一致と説明した相違

DNS対象24観測（query13/response11）について、IP/port、ID、QR、質問名/type/class、TC、応答RCODEを照合。
各frame番号と整数ns時刻も照合した。質問名の比較はこの固定レシピの通常ASCIIラベルへ限定し、
大小文字を正規化。TSharkの表示文字列から製品のbytesラベルキーを再構成しない。
非ASCII・ラベル内dot/backslash・root-only等の表示形式をTSharkと同一視したとはしない。

一意にmatchedな5ペアの`response_to`/`response_in`と時間が一致した。
baseline 4→5=25000000ns、metadata 1→2=25000000ns/4→5=123ns/6→7=5000000000ns、late_reuse 3→4=1ns。
decimal秒は符号付き整数nsへ変換し、floatを経由しない。生成と再実行の結果JSONも一致。

| 観測 | TShark 4.2.2 | 解析器の仕様と理由 |
|---|---|---|
| baseline 7,8→9 | 7→9、20ms | 同じ窓のquery2件をambiguousとし、時間なし |
| backward 1→2 | -100ms | 全DNS時間結果を撤回し、件数だけを維持 |
| metadata追加応答3 | response_to/timeなし | query1の窓へadditional_responseとして根拠を保持 |
| metadata孤立8・終端query9 | 対応参照なし | unmatched_response/window_incompleteを観測窓から明示 |
| metadata fragment10・捕捉不足11 | DNS ID15/16など部分情報を抽出 | 除外を維持し、欠測からDNS照合しない |
| metadata不正DNS12 | 対応参照なし | 完全UDP内の不完全DNSをmalformedとして除外 |
| late_reuse 1→2 | 5000000001nsの対応あり | 窓外なのでno_response_observedとunmatched_response、過去を成功へ変更しない |
| late_reuse 5,7→6 | 5→6、1秒 | 応答後も窓内query再利用を記憶し、ambiguousへ変更、時間撤回 |

RCODE3のNXDOMAINも25msの応答であり、名前解決成功と混同しない。
6状態すべての根拠・時間null条件は手定義期待値と一致。独自状態をTShark分類へ無理に対応させない。
報告JSONは現行Schemaで検証。CIは保存したTShark行の再生とハーネス試験で、実体の検証はローカル明示実行。
全442テスト成功（30.39秒、既存421+DNS比較21）。誤ったDNS field/時間/参照、状態の取り違え、
欠けた出力、出力形式異常、限定外の表示名、実行失敗/警告/timeout、CLI終了0/1/2も検証。pip check成功。
他のTShark版・全RDATA意味・全DNS名表示・実通信の正しさを保証する結果ではない。

## Phase 3完了判定

| 計画上の受入 | 根拠 |
|---|---|
| 同じfixtureのframe/長さ/IP/port等の共通定義を比較 | [保存14 fixtureの結果](tshark_comparison.result.json)、[条件と6入力異常の理由](tshark_comparison.md) |
| 版・filter・設定・commandを記録 | 両比較スクリプト/文書/結果JSONに記録 |
| 異常系の差と独自DNS状態の相違を説明 | 入力異常6種と上表、4 DNSケースの結果JSON・手定義期待値 |
| 未説明差を残さない | 両スクリプトの比較対象で未説明差分0 |

指定した人工fixtureと対応範囲でPhase 3完了と判定する。MVP全体は未完成。
次はPhase 4の固定規模人工入力による処理時間・最大メモリ・解析状態割合の実測と、再現手順・3分デモ/動画。
この単位では性能測定やデモを実施しない。MVP全受入後は独立レビューと完成判定を提案する。
