# PCAP解析仕様 v0.2（Phase 1実装 / Phase 2設計）

2026-11-17策定。元計画: [261014/plan_pcap_analyzer.md](../../../261014/plan_pcap_analyzer.md)。
2026-11-17にPhase 1を実装。入力検査・L2/L3/L4分類・基本集計・JSON/端末表が実装済み。
以下のフロー/DNS照合はPhase 2の設計契約であり未実装。DNS本文はPhase 1では検査しない。

## 入力境界と停止

- ローカルの読み取り可能な通常ファイルを読み取り専用で開く。stdin、FIFO、デバイス、圧縮入力は対象外。
- 非圧縮classic pcap v2.4。µs/ns両精度、little/big endianの4 magicを受理。
- link typeは値1のEthernetのみ。pcapng・VLAN・IPv6・cooked capture・Wi-Fiは変換せず対象外を通知。
- 最大100 MiB = 104857600 bytes、完全レコード1000000件。ちょうど上限は可、次のレコードがあれば停止。
- 1レコードのcaplen上限1 MiB、通常診断上限10000件。上限超過時は明示停止する。
  診断上限は当該パケットを計数後に停止し、上限件数に加えて終了診断1件を許可する。
  全上限はCLIから小さくできるが、既定値を超えて引き上げられない。
- snaplenは1以上。グローバルヘッダー24 bytes、レコードヘッダー16 bytesを検査。
  secは符号なし整数、fractionはµsなら0..999999、nsなら0..999999999。
- 空ファイル・不足グローバルヘッダー・不正magic/version/snaplenは失敗。ヘッダーのみは正常0件。
- caplen > snaplen、caplen > wirelenは長さ矛盾。完全に読めたレコードはmalformedとして計数。
  大きな宣言長を無制限にメモリ確保しない。ファイルサイズ・上限を照合して順次処理する。
- 末尾の部分レコードヘッダー・宣言caplen未満の本文は入力不完全。読めた完全レコードのみ集計し、
  diagnosticsに失敗位置（1始まりの予定packet_index）、実読込長、宣言長を残す。部分本文は通常集計へ混ぜない。
  RawPcapReaderのEOFだけでは正常終端と判定しない。前段で厳密なコンテナ検証を行う。
- 終了コード: 0=完全な入力を処理、2=形式/読込/上限/不完全エラー。Phase 0で使った3は現CLIでは使用しない。
  unsupported/truncated/malformedパケットがあってもコンテナを最後まで処理できれば0。
  入力ヘッダーが無効ならstderr診断のみ。読込開始後の失敗はcomplete=falseの途中JSONとstderr診断。
- 診断コード案: E_INPUT、E_FORMAT、E_FORMAT_UNSUPPORTED、E_LINKTYPE、E_RECORD_HEADER、
  E_RECORD_DATA、E_LIMIT_BYTES、E_LIMIT_RECORDS、E_LIMIT_PACKET_BYTES、E_LIMIT_DIAGNOSTICS、E_INPUT_CHANGED。
  E_LIMIT_FLOWS/E_LIMIT_DNSはPhase 2予定。
  上限停止で正常完了を装わない。生パケットや機密パスを診断へ埋め込まない。

入力は安定した通常ファイルを用意する。O_NONBLOCKで開いてfstatし、FIFO/デバイスを拒否する。
最大サイズ検査後、1 MiB以下のチャンクでファイル全体をhashし、レコードは逐次処理する。
hash後と読込終端にサイズ/mtime/ctimeの変化を検出して失敗扱いにする。
これは変更検知であり、並行書込みに対する厳密な不変スナップショットの保証ではない。

## 復号範囲と分類

Ethernet II（ヘッダー14 bytes、FCSやpreambleの追加はしない）のIPv4を対象とする。
IPv4 version/IHL/total length、TCP data offset、UDP lengthを検査する。
IPv4/TCP optionsはヘッダー全体長と捕捉境界を検査しScapyへ渡す。個々のoptionの意味は検証しない。Ethernet paddingは捕捉バイト数に含める。
IPv4のMF=1またはfragment offset>0はIP段階でfragment件数を計数し、TCP/UDP/DNSを解析しない。
TCP再構成・再送推定は行わない。チェックサム不一致のみでmalformedとしない（offload等があり検証対象外）。

完全レコードごとに必ず1分類。競合時は次の優先順。

| 状態 | 条件例 | 後段 |
|---|---|---|
| malformed | caplen>wirelen、IHL<5、total length<IHL、UDP length<8、矛盾したTCP offset | 理由と番号。DNS照合除外 |
| truncated | caplen<wirelen、対象ヘッダー/宣言IP長に必要な捕捉bytes不足 | 不足を補完しない。DNS照合除外 |
| unsupported | ARP/IPv6/VLAN、IP fragment、非TCP/UDP（DNS質問数≠1はPhase 2） | 未対応範囲と番号 |
| parsed | 対応ヘッダーを完全に検査できたもの | フローと対象DNSへ |

Phase 2ではDNS候補の破損もpacket分類に反映する。Phase 1のparsedはTCP/UDPヘッダーまでの
検査成功であり、DNSその他のアプリケーション本文の正常性を意味しない。UDP長はIPv4の
宣言payload長と一致することを要求し、チェックサムは検証しない。

Phase 2のフローは、非フラグメントのIPv4 TCP/UDPで
両端点とヘッダー長が完全・整合なら分類にかかわらず捕捉長を加算できる。
caplen<wirelenでもL4ヘッダーまで完全な例はフローに加算可能、DNS照合は不可。
フローへ入らなかった捕捉bytesはsummary.unattributed_captured_bytesへ。
IP/TCP/UDPの欠けたポートを推測しない。詳細reasonはpacket_indexとともにdiagnosticsへ。

## 指標と時刻

PacketRecord: packet_index（完全レコードをファイル順に1から）、timestamp_ns、captured_len、original_len、
classification、reason、protocol（下記カテゴリ）、src_ip/dst_ip/src_port/dst_port/ip_protocol（未取得はnull）。
整数timestamp_nsは不正fraction時のみnull。該当レコードはmalformedとして数え、
timestamp_invalid_countを増やして時刻範囲から除外する。件数・bytesは保持する。
DNS抽出情報はPhase 2予定。

| 指標 | 定義 |
|---|---|
| packet_count | 完全に読めたレコード数N。重複捕捉を除外しない |
| captured_bytes | Σcaplen。ファイルヘッダー/レコードヘッダーは含めない |
| original_bytes | Σwirelen。記録上の元長であり回線上の実転送量の実測とは呼ばない |
| classification_counts | parsed+unsupported+truncated+malformed=N |
| parsed_ratio | parsed/N、N=0ならnull |
| start_ns/end_ns | 完全レコードの有効時刻のmin/max。有効時刻なしならnull |
| duration_ns | end-start、空ならnull、単一なら0 |
| timestamp_ns | sec×1000000000 + fraction×1000（µs）/ fraction（ns）。float不使用 |

protocol_countsは排他的なtcp/udp/ipv4_fragment/ipv4_other/non_ipv4/unknownで合計=N。
IPv4ヘッダーが検査できればprotocol番号でtcp/udpを識別するため、L4がtruncatedでもtcp/udp件数に入る。
fragmentはtcp/udpへ重複計数しない。EtherTypeがIPv4以外ならnon_ipv4、
IPv4ヘッダーまで取得/検査できなければunknown。正常性は別のclassification_countsで表す。

input.timestamp_precisionにus/nsを保存。µs入力をns精度で観測したと表現しない。
隣接レコードの時刻が1回でも逆行すればファイル全体のDNS時間解析を無効にし、
dns.time_analysis=disabled_timestamp_regressionとして警告を出す。
Phase 1のeventsは未実装を示すnull、Phase 2の時間集計無効時は[]を予定する。
ソートや前半の成功値の温存をしない。件数・バイト・フローは維持する。
同一時刻は逆行ではなく、ファイル順に処理し0 msの一意応答を許可する。

## フロー（Phase 2予定）

endpoint=(IPv4の4 bytes、port整数)。小さいendpointをa、大きいものをbとし、
キー=(IP protocol番号、a、b)。表示IP文字列の辞書順は使わない。
a→bとb→a別にpackets、captured_bytes、original_bytes、packet_indicesを集計。
TCP flagsはセットされた各bitについて計数し、SYN+ACKは両方へ1。接続単位とは呼ばない。
フロー数上限100000。根拠番号も最大レコード数の範囲に制約される。
方向bytesの総和＋unattributed_captured_bytes=captured_bytes。

## DNS照合（Phase 2予定）

非フラグメントかつ非truncatedのUDP、src/dstの一方が53。DNSヘッダー12 bytes以上、
質問/回答の境界、圧縮ポインターの参照先/ループ、RDLENGTHを検査する。初期opcode=0のみ。
QDCOUNT=1のみ対応。質問0の応答と複数質問はunsupported。TCP DNS、暗号化DNSは対象外。
両側port53はQRで向きを決め、queryはdst53、responseはsrc53でなければ対象外。

キー=(client IP/port、server IP/port、ID、展開済みラベル列、QTYPE、QCLASS)。
各ラベルのASCII A-Zだけ小文字化。root終端は1つ、非ASCII bytesは同一視しない。
JSON用のキー表現はラベルごとの小文字hex配列（rootは空文字列を最後に1つ）。
表示名はbytesをエスケープし、表示文字列からキーを再構築しない。

照合窓W=5000000000 ns。最初のquery時刻t0を基準とする閉区間[t0,t0+W]。
窓は重複によって延長しない。窓端と同時刻のresponseは窓内、超えたら期限切れ。
未完了/完了キー履歴の合計上限100000。履歴を捨てて誤照合せず、上限なら明示停止。

| 状態 | 規則 |
|---|---|
| matched | 同じ窓内でquery1件、response1件。response_time_ms=(tr-tq)/1000000 |
| ambiguous | 同じキーのqueryが窓内に複数。完了後のキー再利用も含む。対応グループ全体を曖昧とし時間値を撤回 |
| additional_response | 完了キーへの追加response。最初の一意ペアは維持。ただしquery再利用で曖昧化したら時間値は撤回 |
| unmatched_response | 未確定queryも保持中の完了キーもないresponse。過去の判定は書き換えない |
| no_response_observed | query1件、窓内にresponseなし、記録終端>=t0+W |
| window_incomplete | query1件、responseなし、記録終端<t0+W |

重複queryグループはresponseの有無にかかわらずambiguousとし、全query番号を残す。
窓外のキー再利用は別グループ。窓外responseを過去queryへ遡って成功扱いしない。
responseのRCODEとTCを保存し、NXDOMAINもmatchedになり得る。
DNS状態件数はqueryグループ/孤立・追加responseイベントの件数でありpacket分類の内訳ではない。
全eventにquery_indices、response_indicesを添付。対応なしなら片方は[]。
時間値が算出不可ならnull。障害、損失、攻撃と断定しない。

## レポート契約

[report.schema.json](report.schema.json) が現行のJSON Schema draft 2020-12契約。
schema_version=0.2.0、phase=1、tool.version=0.1.0、config.version=0.2。
入力SHA-256はファイル全体。読込を上限で停止してもhashは入力全体を識別する。

summaryに件数、caplen/wirelen合計、分類/プロトコル別件数、parsed_ratio、時刻範囲、
fragment件数、不正時刻件数を出す。classification_indicesは各分類の1始まりの根拠番号配列。
全完全レコードはちょうど1配列に現れ、分類件数と配列長が一致する。
全生パケットや全PacketRecordは保持せず、最大100万件の根拠番号と上限付き診断だけを残す。

flows=null、dns.status=not_implemented、dns.events=nullで未実装を表す。
dns.time_analysisは通常not_implemented、逆行検出時はdisabled_timestamp_regression。
Phase 0の将来用フロー/DNSレポートは[旧Schema](report.phase0.schema.json)と
[契約例](../expected/baseline.report.json)として保管し、現行出力とは混同しない。
Phase 1の手定義期待値は[baseline.phase1.json](../expected/baseline.phase1.json)。

診断はpacket_index/code順、JSONキーはソートする。壁時計時刻、処理時間、絶対パスを
出力へ埋め込まず、同一入力/設定で同一JSONとする。無効グローバルヘッダーと入力サイズ超過は
stdout空・stderr診断・終了2。読込開始後の不完全レコードや上限はcomplete=falseの途中出力とstderr診断・終了2。
有効ヘッダーだけなら正常0件、比率/時刻はnull。malformedレコードがあっても入力処理完了なら終了0。
根拠参照は1..N（失敗診断のみN+1またはnullも可）。Schemaに加えて合計・参照の意味整合をテストする。

## 構成と実装順

```text
classic pcap → reader.py（厳密境界検査 + RawPcapReader）
                    ↓ packet.py（長さ検査 + Ether/IP/TCP/UDP復号）
                    ↓ models.py / PacketRecord
                    ├→ flows.py（方向別集計）
                    ├→ dns.py（照合窓・根拠・曖昧さ）
                    └→ report.py（分類整合・JSON / 端末表）← cli.py
```

Phase 0でcli.pyの起動足場とfixture生成/検証を実装した。
Phase 1は①コンテナ検証・診断・上限、②時刻/長さ/番号付きRecord、③Ethernet/IPv4/TCP/UDP分類、
④summary/JSONと意味整合テスト、⑤正常/空/破損/未対応/上限のCLI終了規則を実装済み。
Phase 2でflows.py、DNS正規化、窓と終端処理、曖昧さ・逆行時撤回を実装する。
TSharkとの独立比較はPhase 3。性能や実通信への適用は未検証。

Scapy APIの採用根拠: [公式RawPcapReader API](https://scapy.readthedocs.io/en/latest/api/scapy.utils.html#scapy.utils.RawPcapReader)。
インストール版2.6.1のソースも確認し、メタデータがsec/usec/wirelen/caplen、nanoで精度を判別することを検証する。
