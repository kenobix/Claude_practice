# 人工fixtureの設計と手計算

期待値は[expected/fixtures.json](../expected/fixtures.json)に記録する。実装の出力から作り直さない。
[scripts/generate_fixtures.py](../scripts/generate_fixtures.py)はsocketを開かず、テスト用の人工入力（fixture）をファイルとして生成する。
IPアドレスは192.0.2.1と198.51.100.53、DNSのA応答は203.0.113.7とする。ローカルMACを使い、DNS名はexample.testとする。
時刻の基準は1700000000秒。IPv4とTCPのオプション、EthernetのパディングとFCSは付けず、短いフレームもfixtureにそのまま保存する。
PCAPコンテナはstructで組み立て、L2/L3/L4のバイト列はScapyで生成する。DNSペイロードには固定のバイト列とstructを使う。

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
フローは4本で、TCPは各方向に2件、108 bytesずつある。
DNSでは一意に照合できる1グループ（#4/#5）の観測時間が25msとなる。未応答の観測は1グループ（#6、5秒の窓を終端まで観測）。
ambiguousは1グループ（#7/#8/#9）で、応答時間はnullとする。

## 境界例

| ファイル | 意図 / 将来の期待 |
|---|---|
| header_only.pcap | 24 bytes、正常0件 |
| empty.pcap | 0 bytes、E_FORMAT |
| capture_truncated.pcap | レコード自体は完全。caplen40/wirelen54でTCPヘッダーの一部が未捕捉のため `truncated` |
| record_cutoff.pcap | 本文は宣言された54に対して40しかない。完全レコード0件、E_RECORD_DATAで入力不完全 |
| header_cutoff.pcap | レコードヘッダーが8/16 bytes、E_RECORD_HEADER |
| malformed_length.pcap | 本文55 bytes、caplen55/wirelen54の矛盾。E_RECORD_LENGTHで終了2、完全レコード0件 |
| unsupported_arp.pcap | 42 bytes、unsupported |
| fragment.pcap | 50 bytes、MF=1、unsupported、L4/DNS除外 |
| nano.pcap | little endian ns、整数時刻1700000000000000123 |
| big_endian.pcap | big endian µs、整数時刻1700000000000123000 |
| backward.pcap | query1秒→response0.9秒、160 bytes、DNS時間解析を全体無効化 |
| unsupported_linktype.pcap | ヘッダーlink type101、E_LINKTYPE |
| unsupported_pcapng.pcapng | section headerのみ、E_FORMAT_UNSUPPORTED |

Phase 0では生成の決定性、コンテナbytes・メタデータ、Scapy復号、手計算との一致を確認した。
Phase 1では上表の入力検査・分類・集計・整数時刻・エラー終了も解析器で確認済み。
現在はDNS照合も実装済み。独立レビュー後の修正では保存fixtureのバイト列を維持した。
malformed_lengthの受入期待値は、レコード破損として扱うよう更新した。IEEE 802.3混在例はテスト内で生成する。
[Phase 1の全体期待値](../expected/baseline.phase1.json)は手計算から定義し、
解析コードや生成スクリプトはこのファイルを上書きしない。

## Phase 1/2で追加するfixture

big endian ns（4 magicの残り）、不正magic/version/snaplen/fraction、caplen>snaplen、
IPv4/TCP/UDP/DNS長さ矛盾、IPv6/VLAN、各上限の境界（小さい設定値で再現）、
DNS窓ちょうど5秒/超過、同ID別名/port/type/class、大小文字/非ASCII/圧縮ループ、
NXDOMAIN/TC、完了後query再利用、additional/unmatched_response、window_incomplete。
現時点でこれらのうちPhase 1の形式・ヘッダー・上限の境界は、追加機能テスト内で
独立したbytes/structから合成して検証済み。保存fixtureは引き続き14ファイルである。
この段階ではDNS照合の挙動と性能は検証していない。

## Phase 2前半の検証

保存fixture14ファイルは変更せず、基本例の4フロー・方向別bytes・根拠番号を
初期手計算期待値と照合した。現行全体期待値は[baseline.flows.json](../expected/baseline.flows.json)。
TCPフラグS=2/A=3、全方向680 captured/original bytes、未帰属0。
追加の一時fixtureはtests/test_flows.py内で固定bytes/structから生成し、
IPバイト順・同一IP/port・各キー成分・UDP・9 flags・truncated・除外・途中停止を確認する。
フロー数の上限で停止することは、設定を1本、2本に引き下げて検証した。既定値であり設定上限でもある100000は別に確認した。
10万本を実際に保持する負荷試験は未実施。この段階ではDNS照合の期待値を検証する実装もなかった。
