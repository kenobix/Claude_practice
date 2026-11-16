# 人工fixtureの設計と手計算

期待値は[expected/fixtures.json](../expected/fixtures.json)。実装の出力から作り直さない。
生成は[scripts/generate_fixtures.py](../scripts/generate_fixtures.py)。socketを開かずファイルだけを合成する。
IPは192.0.2.1 / 198.51.100.53、A回答203.0.113.7、ローカルMAC、DNS名example.test。
基準秒1700000000、IPv4/TCP optionsなし、Ethernet padding/FCSなし。短いフレームもfixture上は保持する。
PCAPコンテナはstructで組み、L2/L3/L4 bytesはScapy。DNS payloadは固定bytes/struct。

## 基本例 baseline.pcap

Ethernet14 + IPv4 20 + TCP20 = 54 bytes。
DNS questionはheader12 + name14 + type/class4 = 30 bytes。
UDP queryは14+20+8+30=72、responseは圧縮A回答16を加え88 bytes。

| # | offset秒 | 内容 | caplen/wirelen |
|---|---|---|---|
| 1 | 0 | client:40000→server:443 TCP SYN | 54/54 |
| 2 | .01 | 逆方向 TCP SYN+ACK | 54/54 |
| 3 | .02 | 順方向 TCP ACK | 54/54 |
| 4 | 1 | client:53000→server:53 DNS ID1 | 72/72 |
| 5 | 1.025 | #4応答 RCODE0 TC0 A=203.0.113.7 | 88/88 |
| 6 | 2 | client:53001→server:53 DNS ID2 応答なし | 72/72 |
| 7 | 3 | client:53002→server:53 DNS ID3 | 72/72 |
| 8 | 3.01 | #7と同一キーの重複query | 72/72 |
| 9 | 3.02 | #7/#8のキーに対する応答 | 88/88 |
| 10 | 8 | 逆方向 TCP ACK、終端時刻を確定 | 54/54 |

10件、54×4+72×4+88×2=680 captured/original bytes。
コンテナ全体は24+16×10+680=864 bytes。時刻範囲8秒、全件parsedがPhase 1目標。
フロー4本、TCP方向別2件108 bytes。DNS成功1グループ(#4/#5)=25ms、
未応答1(#6、5秒窓を終端まで観測)、ambiguous1(#7/#8/#9)=時間null。

## 境界例

| ファイル | 意図 / 将来の期待 |
|---|---|
| header_only.pcap | 24 bytes、正常0件 |
| empty.pcap | 0 bytes、E_FORMAT |
| capture_truncated.pcap | 完全レコードだがcaplen40/wirelen54、truncated、TCPヘッダー不足 |
| record_cutoff.pcap | 宣言54に実本文40、完全レコード0件、E_RECORD_DATAで不完全 |
| header_cutoff.pcap | レコードヘッダーが8/16 bytes、E_RECORD_HEADER |
| malformed_length.pcap | caplen55/wirelen54、malformed |
| unsupported_arp.pcap | 42 bytes、unsupported |
| fragment.pcap | 50 bytes、MF=1、unsupported、L4/DNS除外 |
| nano.pcap | little endian ns、整数時刻1700000000000000123 |
| big_endian.pcap | big endian µs、整数時刻1700000000000123000 |
| backward.pcap | query1秒→response0.9秒、160 bytes、DNS時間解析を全体無効化 |
| unsupported_linktype.pcap | ヘッダーlink type101、E_LINKTYPE |
| unsupported_pcapng.pcapng | section headerのみ、E_FORMAT_UNSUPPORTED |

今回のテストは生成の決定性、コンテナbytes・メタデータ、Scapy復号、手計算との一致を確認する。
分類/DNS照合/エラー終了の上表は未実装解析器に対する将来の期待であり、成功結果ではない。

## Phase 1/2で追加するfixture

big endian ns（4 magicの残り）、不正magic/version/snaplen/fraction、caplen>snaplen、
IPv4/TCP/UDP/DNS長さ矛盾、IPv6/VLAN、各上限の境界（小さい設定値で再現）、
DNS窓ちょうど5秒/超過、同ID別名/port/type/class、大小文字/非ASCII/圧縮ループ、
NXDOMAIN/TC、完了後query再利用、additional/unmatched_response、window_incomplete。
現時点でこれらの解析挙動・性能は検証していない。
