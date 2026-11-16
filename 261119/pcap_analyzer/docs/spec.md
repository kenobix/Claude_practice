# Phase 0 仕様 v0.1

2026-11-17策定。元計画: [261014/plan_pcap_analyzer.md](../../../261014/plan_pcap_analyzer.md)。
ここに書かれた解析動作はPhase 1/2の実装契約。現CLIは起動用で解析未実装。

## 入力境界と停止

- ローカルの読み取り可能な通常ファイルを読み取り専用で開く。stdin、FIFO、デバイス、圧縮入力は対象外。
- 非圧縮classic pcap v2.4。µs/ns両精度、little/big endianの4 magicを受理。
- link typeは値1のEthernetのみ。pcapng・VLAN・IPv6・cooked capture・Wi-Fiは変換せず対象外を通知。
- 最大100 MiB = 104857600 bytes、完全レコード1000000件。ちょうど上限は可、次のレコードがあれば停止。
- snaplenは1以上。グローバルヘッダー24 bytes、レコードヘッダー16 bytesを検査。
  secは符号なし整数、fractionはµsなら0..999999、nsなら0..999999999。
- 空ファイル・不足グローバルヘッダー・不正magic/version/snaplenは失敗。ヘッダーのみは正常0件。
- caplen > snaplen、caplen > wirelenは長さ矛盾。完全に読めたレコードはmalformedとして計数。
  大きな宣言長を無制限にメモリ確保しない。ファイルサイズ・上限を照合して順次処理する。
- 末尾の部分レコードヘッダー・宣言caplen未満の本文は入力不完全。読めた完全レコードのみ集計し、
  diagnosticsに失敗位置（1始まりの予定packet_index）、実読込長、宣言長を残す。部分本文は通常集計へ混ぜない。
  RawPcapReaderのEOFだけでは正常終端と判定しない。前段で厳密なコンテナ検証を行う。
- 終了コード: 0=完全な入力を処理、2=形式/読込/上限/不完全エラー、3=未実装機能。
  unsupported/truncated/malformedパケットがあってもコンテナを最後まで処理できれば0。
  入力ヘッダーが無効ならstderr診断のみ。読込開始後の失敗はcomplete=falseの途中JSONとstderr診断。
- 診断コード案: E_INPUT、E_FORMAT、E_FORMAT_UNSUPPORTED、E_LINKTYPE、E_RECORD_HEADER、
  E_RECORD_DATA、E_LIMIT_BYTES、E_LIMIT_RECORDS、E_LIMIT_FLOWS、E_LIMIT_DNS。
  上限停止で正常完了を装わない。生パケットや機密パスを診断へ埋め込まない。

## 復号範囲と分類

Ethernet II（ヘッダー14 bytes、FCSやpreambleの追加はしない）のIPv4を対象とする。
IPv4 version/IHL/total length、TCP data offset、UDP lengthを検査する。
IPv4 optionsとTCP optionsは長さを検査して読み飛ばす。Ethernet paddingは捕捉バイト数に含める。
IPv4のMF=1またはfragment offset>0はIP段階でfragment件数を計数し、TCP/UDP/DNSを解析しない。
TCP再構成・再送推定は行わない。チェックサム不一致のみでmalformedとしない（offload等があり検証対象外）。

完全レコードごとに必ず1分類。競合時は次の優先順。

| 状態 | 条件例 | 後段 |
|---|---|---|
| malformed | caplen>wirelen、IHL<5、total length<IHL、UDP length<8、矛盾したTCP offset | 理由と番号。DNS照合除外 |
| truncated | caplen<wirelen、対象ヘッダー/宣言IP長に必要な捕捉bytes不足 | 不足を補完しない。DNS照合除外 |
| unsupported | ARP/IPv6/VLAN、IP fragment、非TCP/UDP、DNS質問数≠1 | 未対応範囲と番号 |
| parsed | 対応ヘッダーを完全に検査できたもの | フローと対象DNSへ |

DNS候補の破損もpacket分類に反映する。ただしフローは、非フラグメントのIPv4 TCP/UDPで
両端点とヘッダー長が完全・整合なら分類にかかわらず捕捉長を加算できる。
caplen<wirelenでもL4ヘッダーまで完全な例はフローに加算可能、DNS照合は不可。
フローへ入らなかった捕捉bytesはsummary.unattributed_captured_bytesへ。
IP/TCP/UDPの欠けたポートを推測しない。詳細reasonはpacket_indexとともにdiagnosticsへ。

## 指標と時刻

PacketRecord: packet_index（完全レコードをファイル順に1から）、timestamp_ns、captured_len、original_len、
classification、reason、IP/port/protocol（未取得はnull）。DNS抽出情報は任意。

| 指標 | 定義 |
|---|---|
| packet_count | 完全に読めたレコード数N。重複捕捉を除外しない |
| captured_bytes | Σcaplen。ファイルヘッダー/レコードヘッダーは含めない |
| original_bytes | Σwirelen。記録上の元長であり回線上の実転送量の実測とは呼ばない |
| classification_counts | parsed+unsupported+truncated+malformed=N |
| parsed_ratio | parsed/N、N=0ならnull |
| start_ns/end_ns | 全完全レコード時刻のmin/max。N=0ならnull |
| duration_ns | end-start、空ならnull、単一なら0 |
| timestamp_ns | sec×1000000000 + fraction×1000（µs）/ fraction（ns）。float不使用 |

input.timestamp_precisionにus/nsを保存。µs入力をns精度で観測したと表現しない。
隣接レコードの時刻が1回でも逆行すればファイル全体のDNS時間解析を無効にし、
dns.time_analysis=disabled_timestamp_regression、events=[]、警告を出す。
ソートや前半の成功値の温存をしない。件数・バイト・フローは維持する。
同一時刻は逆行ではなく、ファイル順に処理し0 msの一意応答を許可する。

## フロー

endpoint=(IPv4の4 bytes、port整数)。小さいendpointをa、大きいものをbとし、
キー=(IP protocol番号、a、b)。表示IP文字列の辞書順は使わない。
a→bとb→a別にpackets、captured_bytes、original_bytes、packet_indicesを集計。
TCP flagsはセットされた各bitについて計数し、SYN+ACKは両方へ1。接続単位とは呼ばない。
フロー数上限100000。根拠番号も最大レコード数の範囲に制約される。
方向bytesの総和＋unattributed_captured_bytes=captured_bytes。

## DNS照合

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

[report.schema.json](report.schema.json) がJSON Schema draft 2020-12の初期契約。
schema_version=0.1.0。入力SHA-256はファイル全体、完全レコード数とは別。
ツール版、設定版・各上限、入力精度、完了可否、分類、未帰属量、警告、根拠番号を保存する。
flowsはprotocol/a/bの順、eventsは最初の根拠番号順、diagnosticsは根拠番号/code順。
壁時計時刻・実行時間・絶対入力パスを集計へ埋め込まず、同一入力/設定で決定的JSONとする。
空入力の有効pcapは0件、時刻と比率はnull。未実装値を0に偽装しない。
根拠参照は1..N（失敗診断のみN+1可）。配列合計・参照範囲・比率等はSchemaに加えて意味検証する。
JSONの後に端末表を別レンダラーで実装。stdoutのJSONに診断文を混ぜない。

## 構成と実装順

```text
classic pcap → reader.py（厳密境界検査 + RawPcapReader + Ether/IP復号）
                    ↓ models.py / PacketRecord
                    ├→ flows.py（方向別集計）
                    ├→ dns.py（照合窓・根拠・曖昧さ）
                    └→ report.py（分類整合・JSON / 端末表）← cli.py
```

Phase 0で実装するのはcli.pyの起動足場とfixture生成/検証のみ。
Phase 1は①コンテナ検証・診断・上限、②時刻/長さ/番号付きRecord、③Ethernet/IPv4/TCP/UDP分類、
④summary/JSONと意味整合テスト、⑤正常/空/破損/未対応/上限のCLI終了規則の順。
Phase 2でflows.py、DNS正規化、窓と終端処理、曖昧さ・逆行時撤回を実装する。
TSharkとの独立比較はPhase 3。性能や実通信への適用は未検証。

Scapy APIの採用根拠: [公式RawPcapReader API](https://scapy.readthedocs.io/en/latest/api/scapy.utils.html#scapy.utils.RawPcapReader)。
インストール版2.6.1のソースも確認し、メタデータがsec/usec/wirelen/caplen、nanoで精度を判別することを検証する。
