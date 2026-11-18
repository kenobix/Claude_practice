# PCAP解析仕様 v0.4（Phase 2 フロー・DNS照合実装）

2026-11-17策定。元計画: [261014/plan_pcap_analyzer.md](../../../261014/plan_pcap_analyzer.md)。
2026-11-17にPhase 1を実装。入力検査・L2/L3/L4分類・基本集計・JSON/端末表が実装済み。
同日にPhase 2前半の双方向フロー集計・TCP flags・フロー数上限を実装。
継続単位としてDNS名復号・正規化・単一質問抽出の内部APIを実装済み（下記参照）。
次の継続単位でRR構造と圧縮参照先の検査APIも追加済み（下記参照）。
DNS構造検査をpacket分類/CLI診断へ統合済み。照合キーと窓処理は内部APIで実装済み。
DNS照合のreport/CLI/Schema統合、逆行時の全結果無効化と上限途中結果を実装済み。
JSONの形状/版を更新し、Schemaへ照合イベント・根拠参照・上限停止の契約を追加。

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
- caplen > wirelenはレコードヘッダーの破損。本文読込前にE_RECORD_LENGTHで停止し、当該レコードと後続を集計しない。診断に予定packet_indexと宣言された両方の長さを残す。
- caplen > snaplenは本文が完全ならmalformedとして計数する。
  大きな宣言長を無制限にメモリ確保しない。ファイルサイズ・上限を照合して順次処理する。
- 末尾の部分レコードヘッダー・宣言caplen未満の本文は入力不完全。読めた完全レコードのみ集計し、
  diagnosticsに失敗位置（1始まりの予定packet_index）、実読込長、宣言長を残す。部分本文は通常集計へ混ぜない。
  RawPcapReaderのEOFだけでは正常終端と判定しない。前段で厳密なコンテナ検証を行う。
- 終了コード: 0=完全な入力を処理、2=形式/読込/上限/不完全エラー。Phase 0で使った3は現CLIでは使用しない。
  unsupported/truncated/malformedパケットがあってもコンテナを最後まで処理できれば0。
  入力ヘッダーが無効ならstderr診断のみ。読込開始後の失敗はcomplete=falseの途中JSONとstderr診断。
- 診断コード: E_INPUT、E_FORMAT、E_FORMAT_UNSUPPORTED、E_LINKTYPE、E_RECORD_HEADER、E_RECORD_LENGTH、
  E_RECORD_DATA、E_LIMIT_BYTES、E_LIMIT_RECORDS、E_LIMIT_PACKET_BYTES、E_LIMIT_DIAGNOSTICS、E_INPUT_CHANGED。
  DNSとフローの上限にはE_LIMIT_DNS、E_LIMIT_DNS_OBSERVATIONS、E_LIMIT_FLOWSを使う。
  上限停止で正常完了を装わない。生パケットや機密パスを診断へ埋め込まない。

入力は安定した通常ファイルを用意する。O_NONBLOCKで開いてfstatし、FIFO/デバイスを拒否する。
最大サイズ検査後、1 MiB以下のチャンクでファイル全体をhashし、レコードは逐次処理する。
hash後と読込終端にサイズ/mtime/ctimeの変化を検出して失敗扱いにする。
これは変更検知であり、並行書込みに対する厳密な不変スナップショットの保証ではない。

## 復号範囲と分類

Ethernet II（ヘッダー14 bytes、FCSやpreambleの追加はしない）のIPv4を対象とする。
ヘッダーの長さ／種別フィールドは0〜1500をIEEE 802.3の長さとして扱い、LLC/SNAPの復号範囲外としてunsupportedにする。
1501〜1535は予約値なのでmalformed、1536以上はEtherTypeとして判定する。
IPv4 version/IHL/total length、TCP data offset、UDP lengthを検査する。
IPv4/TCP optionsはヘッダー全体長と捕捉境界を検査しScapyへ渡す。個々のoptionの意味は検証しない。Ethernet paddingは捕捉バイト数に含める。
IPv4のMF=1またはfragment offset>0はIP段階でfragment件数を計数し、TCP/UDP/DNSを解析しない。
TCP再構成・再送推定は行わない。チェックサム不一致のみでmalformedとしない（offload等があり検証対象外）。

完全レコードごとに必ず1分類。競合時は次の優先順。

| 状態 | 条件例 | 後段 |
|---|---|---|
| malformed | caplen>snaplen、L2の予約値、IHL<5、total length<IHL、UDP length<8、矛盾したTCP offset | 理由と番号。DNS照合除外 |
| truncated | caplen<wirelen、対象ヘッダー/宣言IP長に必要な捕捉bytes不足 | 不足を補完しない。DNS照合除外 |
| unsupported | IEEE 802.3、ARP/IPv6/VLAN、IP fragment、非TCP/UDP、DNS質問数≠1等 | 未対応範囲と番号 |
| parsed | 対応ヘッダーと対象DNSの構造を検査できたもの | フローと対象DNSへ |

DNS候補の構造不正/未対応もpacket分類に反映する（適用条件は下記）。
parsedはDNSの全RDATAの意味や名前解決成功、他アプリケーション本文の正常性を保証しない。UDP長はIPv4の
宣言payload長と一致することを要求し、チェックサムは検証しない。

実装済みフローは、非フラグメントのIPv4 TCP/UDPで
両端点とヘッダー長が完全・整合なら分類にかかわらず捕捉長を加算できる。
caplen<wirelenでもL4ヘッダーまで完全な例はフローに加算可能、DNS照合は不可。
フローへ入らなかった捕捉bytesはsummary.unattributed_captured_bytesへ。
IP/TCP/UDPの欠けたポートを推測しない。詳細reasonはpacket_indexとともにdiagnosticsへ。

## 指標と時刻

PacketRecord: packet_index（完全レコードをファイル順に1から）、timestamp_ns、captured_len、original_len、
classification、reason、protocol（下記カテゴリ）、src_ip/dst_ip/src_port/dst_port/ip_protocol（未取得はnull）。
整数timestamp_nsは不正fraction時のみnull。該当レコードはmalformedとして数え、
timestamp_invalid_countを増やして時刻範囲から除外する。件数・bytesは保持する。
flow_eligible（bool）とtcp_flags（TCPのみ9 bit整数）を追加。
フローに必要なヘッダーが完全な場合だけflow_eligible=true。DNS構造検査に成功した質問をdns_questionへ保持する。

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
fragmentはtcp/udpへ重複計数しない。L2の長さ／種別フィールドがIPv4のEtherType以外ならnon_ipv4、
IPv4ヘッダーまで取得/検査できなければunknown。正常性は別のclassification_countsで表す。

input.timestamp_precisionにus/nsを保存。µs入力をns精度で観測したと表現しない。
隣接レコードの時刻が1回でも逆行すればファイル全体のDNS時間解析を無効にし、
dns.time_analysis=disabled_timestamp_regressionとして警告を出す。
eventsは通常配列。逆行検出時は確定済みを含め全件破棄して[]、event_countsはnull。
query_count/response_countは時刻に依存しない対象観測件数として維持する。
ソートや前半の成功値の温存をしない。件数・バイト・フローは維持する。
同一時刻は逆行ではなく、ファイル順に処理し0 msの一意応答を許可する。

## 双方向フロー（Phase 2前半実装済み）

endpoint=(IPv4の4 bytes、port整数)。小さいendpointをa、大きいものをbとし、
キー=(IP protocol番号、a、b)。表示IP文字列の辞書順や最初の観測方向は使わない。
protocolが違えば同じ両端点でも別フロー。IPが同じならportで比較する。
両端点が完全に等しい場合はa→bに一意に加算する。port=0も有効なヘッダー値として扱う。

flows.pyがa_to_b/b_to_a別にpackets、captured_bytes、original_bytes、packet_indicesを保持する。
根拠番号はファイル順。再送・重複捕捉もそのまま加算し、TCP接続単位とは呼ばない。
TCP flagsはF/S/R/P/A/U/E/C/N（NS）の各bitを独立に計数し、SYN+ACKはSとAの両方へ1。
方向を合わせたフロー全体のflag件数。bitがなければ0、UDPのtcp_flag_countsはnull。

| 条件 | フローへの帰属 |
|---|---|
| 非fragmentのIPv4 TCP/UDP、IPv4ヘッダーとTCP/UDPヘッダー全体が完全・整合 | 帰属する |
| caplen<wirelenだが上記ヘッダーが完全、payloadやフレーム末尾が不足 | 帰属する。実捕捉caplenと記録wirelenを別に加算 |
| 不正時刻fractionだけでmalformed、ヘッダーは完全・整合 | 帰属する。時刻範囲からは従来どおり除外 |
| Ethernet/IP/L4ヘッダー不足、TCP options部分不足、ヘッダー長の矛盾 | 帰属しない |
| caplen>snaplen | 記録長に矛盾があるため帰属しない |
| IPv4 fragment（最初のfragmentを含む）、ARP/IPv6/VLAN/非TCP・UDP | 帰属しない |
| caplen>wirelen、ファイル本文が宣言caplenより短い部分レコード | 破損したレコードとして停止し、全体集計にも帰属集計にも入れない |

classificationとは独立したflow_eligibleを使い、欠けたポートやflagsを推測しない。
DNS本文の構造不正/未対応も、検証済みヘッダーによるフロー帰属を無効にしない。

フロー数上限100000。`--max-flows`で1..100000に引下げ可能。
上限件数ちょうどと既存フローへの追加は可。次の新規キーを作る前に停止する。
超過を起こした完全レコードは全体の件数・分類・時刻へ計数し、フローには作成/追加せず
未帰属へ記録する。E_LIMIT_FLOWS、該当packet_index、complete=false、終了2を返し、
次のレコードは読まない。診断上限・読込エラーで停止しても、計数済みのレコードは必ず分配済みにする。

summaryにflow_count、unattributed_packet_count、unattributed_captured_bytes、
unattributed_original_bytesを追加。未帰属番号はunattributed_indicesへ保存する。

```text
Σ方向別packets + unattributed_packet_count = packet_count
Σ方向別captured_bytes + unattributed_captured_bytes = captured_bytes
Σ方向別original_bytes + unattributed_original_bytes = original_bytes
```

各方向のpackets=packet_indicesの長さ。全方向の根拠番号と未帰属番号は互いに重ならず、
全体の1..Nをちょうど1回ずつ覆う。空入力はflows=[]、未帰属件数/長さは0。
flows配列はprotocol番号、aのIP bytes/port、bのIP bytes/portでソートし、決定的に出力する。

## DNS名復号・単一質問抽出（内部API実装済み）

`dns.py` のdecode_nameはDNSメッセージ全体と名前開始offsetを受け、DNSNameを返す。
labelsは元のbytesラベル列に終端root b""を1つ加えたtuple、end_offsetは元の符号化名の直後。
ポインターを辿ってもend_offsetは最初のポインター直後を保つ。
normalized_labelsは各bytesラベルのASCII A-Zのみ小文字化。Unicode/IDNA変換を行わない。
displayは末尾ドット付きで、ドット/バックスラッシュ/空白・制御文字/非ASCIIを\DDD形式にする。
表示名はキーに使わず、非ASCII bytesやラベル境界の差を保存する。

検査: 通常ラベル最大63 bytes、展開後名最大255 bytes（root含む）、
メッセージ最大65507 bytes（IPv4 UDP payloadの上限）、名前復号最大256ステップ。
ポインターは同一メッセージ内のoffset>=12、かつ参照位置より前の対象のみ許可。
範囲外/ヘッダー内/前方参照/循環/不足を拒否する。参照先が過去に解析済みのラベル先頭かの
証明はまだ行わないため、完全な圧縮名検査を実装したとは扱わない。
過剰な参照連鎖はunsupportedとして明示し、再帰や無制限の処理をしない。

parse_questionは12 bytesのDNSヘッダー、opcode=0、QDCOUNT=1、名前、QTYPE/QCLASSだけを検査し、
DNSQuestionを返す。保持値はdns_id/is_response/rcode/tc/name/qtype/qclass/end_offset。
question_key=(normalized_labels, qtype, qclass)。IP/port/IDを組み合わせる完全照合キーは下記の内部API。
opcode/質問数/未対応ラベル形式/処理上限はunsupported、境界不足・矛盾はmalformed。
DNSDecodeErrorにclassification/reason/offsetを保持し、raw bytesをエラー文へ含めない。
完全なUDP payloadに対するAPIなので、DNSフィールド不足を捕捉truncatedとは判定しない。

この単位はanswer/authority/additional件数、RRのRDLENGTH/RDATAを検査しない。
TC/RCODEは情報として保持するだけで、応答照合・成功判定・所要時間は計算しない。
全体構造検査には以下のparse_messageを使用し、packet分類へ接続する。
parse_question単独APIは引き続き質問抽出だけを保証する。

## DNSメッセージのRR構造（内部API実装済み）

parse_message(message)はopcode=0/QDCOUNT=1の完全なUDP payloadを入力とし、
DNSMessage(question, answers, authorities, additionals)を返す。各セクションはwire順のtuple。
各DNSResourceRecordはname/rtype/rclass/ttl/rdata/rdata_offset/end_offsetを持つ。
名前の後の固定10 bytesを読み、RDLENGTHに従って本文範囲を検査する。
宣言した全RRを読み終えてメッセージ末尾と一致することを要求し、余分なbytesはmalformed。
TC=1/NXDOMAINでも構造境界を緩めず、TC/RCODEを保持するだけで成功/照合は判定しない。

parse_messageは名前の解析成功時にラベル先頭・root・圧縮ポインター位置を登録し、
以後の圧縮参照先をその集合に制限する。label内、type/class/TTL等の数値領域、
不透明なRDATA内部をたまたま名前として解釈しない。未登録参照は安全のためunsupported
（dns_pointer_target_unverified）とし、未知RR仕様を不正と断定しない。
単独decode_name/parse_questionの従来APIはこのメッセージ全体の参照先証明を行わない。

以下の名前を含むRDATAのwire形状は検査し、名前の境界を参照先として登録する。

| RR type | 検査するRDATA |
|---|---|
| NS(2)/CNAME(5)/PTR(12) | 名前1つ、符号化長がRDLENGTHと一致 |
| MX(15) | preference 2 bytesと名前1つ |
| SOA(6) | 名前2つと32 bit整数5つの計20 bytes |

名前の符号化bytesはRDLENGTHを跨いで読まない。圧縮参照の展開先だけは以前の検証済み名へ辿れる。
その他のRDATAは宣言長だけを確認してraw bytesで保持し、内部の名前を推測しない。
Aのアドレス長、未知RRの内部構造、EDNSの意味など全RDATA仕様の妥当性は保証しない。

既存のメッセージ65507 bytes・各名256ステップに加え、全3領域合計1024 RR、
メッセージ全体で名前復号16384ステップまで。各ラベル/root/ポインターを1ステップとし、
追跡先の展開も加算する。ちょうど上限は可。上限超過はunsupported
（dns_record_work_limit/dns_message_work_limit）。現在は内部APIの固定上限で、CLI設定は未追加。
境界不足はmalformedとしてDNSDecodeErrorに理由/offsetを残す。

## DNS構造検査のpacket分類/CLI統合（実装済み）

非fragmentのIPv4 UDP、src/dstいずれかのportが53、L2/L3/L4ヘッダーと記録長・時刻が
正常、捕捉不足なしの候補だけにparse_messageを適用する。malformed/truncatedの既存理由は
上書きせず、欠けた本文をDNS不正と推測しない。不正時刻のパケットもDNS検査対象から除外。
UDP宣言長までのpayloadを渡し、Ethernet paddingは含めない。TCP port53は対象外。
packet分類では両向きとも構造を検査し、QR/port方向の照合資格は下記observationで判定する。

DNSDecodeErrorのclassification/reasonを既存のpacket分類・P_MALFORMED/P_UNSUPPORTED診断へ反映。
完全なUDP payload内でのDNSフィールド不足はmalformed。質問数/opcode/未検証参照先や
固定処理上限はunsupported。無制限処理を避けるこれらのDNS上限は入力全体の停止条件ではなく、
そのパケットのunsupported理由となる。通常診断上限に達した場合はE_LIMIT_DIAGNOSTICSで停止する。
空のport53 UDP本文は従来parsedからmalformedへ変更。既存14 fixtureの期待集計は維持。

DNS本文に問題があっても完全なL4ヘッダーのflow_eligibleは維持し、件数/捕捉長/元長と根拠を
従来どおり分配する。通常診断上限で停止したパケットも分配済み。完全レコードのDNS構造不正だけなら
コンテナ処理complete=true/終了0で、診断をJSON/端末へ出力する。
構造検査に成功した質問だけをPacketRecord.dns_questionへ保持する（下記）。
入力上限・整数nsを維持する。窓処理とDNSレポートは下記契約で統合済み。

## DNS照合キー・向き判定（内部API実装済み）

packet.decodeはparse_messageがメッセージ全体の検査を終えた場合だけDNSQuestionを
PacketRecord.dns_questionへ引き継ぐ。RR検査に失敗した場合もNoneのまま。
質問のみを残し、RR/RDATAや生パケットをレコードへ保持しない。既存の逐次処理を維持する。

dns.observation(record)はdecoder生成PacketRecordを受け、以下の全条件を満たせば
不変なDNSObservation、満たさなければNoneを返す。モデルを任意生成する外部入力APIではない。
parsed、udp/protocol17、flow_eligible、整数nsが有効、検査済み質問・両端点が存在することを要求。
QR=0のqueryはdst53、QR=1のresponseはsrc53でなければ除外する。両port53でもQRで決定する。
向き不一致はpacketの構造不正とはせず、既存parsed分類・フロー帰属を変更しない。
不正時刻/truncated/fragment/不正DNS等からは質問を引き継がず、観測値を生成しない。

DNSKeyはclient/serverの各(IPv4 packed bytes, port整数)、dns_id、正規化ラベルtuple、qtype/qclass。
queryでは送信元をclient、応答では宛先をclientとする。フローキーと異なり端点をソートしない。
ASCII以外のbytesとラベル境界を維持し、rootは末尾b""一つ。表示名から復元しない。
RCODE/TC/QRはキーに含めず、DNSObservationのkey/packet_index/timestamp_ns/is_response/rcode/tcへ保持。
不変値として等値比較・hashが可能で、最初の観測方向やファイル順にキーが依存しない。

ここではペアリング・窓・時間差計算を行わない。時刻逆行時のファイル全体の時間解析無効化は
既存reportに維持し、report側が既返却結果を含め破棄する責務を持つ。observation自体は時刻を並べ替えない。
キー抽出はJSON表現を扱わない。report側でイベントとキーをJSON/端末へ出力する（下記レポート契約）。
下記の窓APIとreport/CLI/Schemaは接続済み。全有効時刻の逆行時は結果を無効化する。

## DNS照合（窓内部API・レポート統合済み）

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

### 窓APIの呼出し契約

`dns_windows.DNSWindowCollector(max_keys=100000)`は非減少の整数ns・ファイル順に
DNSObservationをaddする内部API。max_keysは1..100000へ引下げ可能でbool/非整数を拒否する。
readerの最大100万完全レコード以下の入力を前提とし、任意長のイベントストリームAPIにはしない。
addのpacket_indexは正整数・狭義増加。advance(ns)はDNS以外を含む有効パケット時刻を伝える。
不正/逆行時刻と逆順番号は状態更新前にValueError、finish後の操作はRuntimeError。
時刻逆行時は呼出し側が既に返されたイベントも含めファイル全体の時間結果を破棄する。
全有効パケット時刻での逆行検知・DNS結果無効化をreportへ接続済み。
不正fractionは時刻として使わず、逆行の判定や窓進行から除く。

各呼出しは確定済みの不変DNSEvent tupleを返す。collector内に出力履歴を貯めない。
matchedの応答時間はfloatを使わずresponse_time_nsへ整数差を保持し、JSONでは整数nsを正とし、表示用response_time_ms=ns/1000000も付ける。
絶対timestampは引き続き整数nsであり、float timestampによる差分計算は行わない。
イベントはkey/status/query_indices/response_indices/response_time_ns/rcode/tcを保持する。
孤立応答は直ちに出力する。他は閉区間窓終了またはfinishまで確定を遅らせ、
完了後query再利用で暫定時間が成功値として残ることを防ぐ。

主イベントには全query番号と最初のresponse番号を記録し、2つ目以降のresponseは
additional_responseとして別イベントへ記録。各responseのRCODE/TCを保存する。
曖昧化後も追加応答の時間値はnullで、各イベントのquery番号はグループ全queryを参照する。
1呼出し内のイベントは主イベントの最初の根拠番号、追加イベントは応答番号を基準に安定ソート。
呼出しを跨ぐ最終レポートも同じ根拠番号順で全体ソートする。

期限heapにはqueryグループごとに1要素だけを登録。重複query/responseで期限を延長しない。
advanceはdeadline<現在時刻のキーだけを確定/削除し、同時刻応答を受理できるようdeadline==現在を保持。
未完了/完了を合わせた保持キー上限で新規queryを拒否し、InputError(E_LIMIT_DNS, packet_index)を返す。
既存キーと孤立応答は上限時にも扱える。期限切れ削除後に新規キーの容量を判定する。
上限で履歴を勝手に捨てず、失敗した新規queryを登録しない。CLIの停止/途中レポートへ接続済み。

finish(end_ns)は観測した最後の有効パケット時刻（DNS以外も含む）で全保持窓を確定・解放し、終端状態にする。
末尾が窓端ちょうどならno_response_observed、手前ならwindow_incomplete。重複queryは終端でもambiguous。
既知の整数nsと読めた範囲だけの観測結果であり、サーバー障害・損失と断定しない。

## レポート契約

[report.schema.json](report.schema.json) が現行のJSON Schema draft 2020-12契約。
schema_version=0.4.0、phase="2b"、tool.version=0.3.0、config.version=0.4。
DNS照合イベント・状態件数・max_dns_keysを追加する版変更であり、旧出力との互換は保証しない。
入力SHA-256はファイル全体。読込を上限で停止してもhashは入力全体を識別する。

summaryに件数、caplen/wirelen合計、分類/プロトコル別件数、parsed_ratio、時刻範囲、
fragment件数、不正時刻件数を出す。classification_indicesは各分類の1始まりの根拠番号配列。
全完全レコードはちょうど1配列に現れ、分類件数と配列長が一致する。
全生パケットや全PacketRecordは保持せず、最大100万件の根拠番号と上限付き診断だけを残す。

flowsは上記の双方向集計配列。dns.statusはcomplete/partialで、入力処理のcompleteと一致する。
時間解析の有効性は別のdns.time_analysis（enabled/disabled_timestamp_regression）で示す。
DNS構造検査結果はpacket分類/diagnosticsへ反映し、DNS照合状態とは区別する。
query_count/response_countは構造・QR/port方向・時刻が有効な対象観測数（重複を含む）。
無効化後も件数を維持し、それ以降の対象観測を数える。event_countsは6状態のイベント数。
これはpacket分類の内訳でもquery件数でもない。無効化時のnullは「0件」の意味ではない。

JSONの各イベントはkey/status/query_group/query_indices/response_indices/response_time_ns/
response_time_ms/rcode/tcを持つ。keyはclient/serverのIPv4文字列とport、id、qtype/qclass、
正規化labels_hex（末尾rootは空文字）と安全にエスケープした表示用name。
表示名からキーを復元せず、非ASCII・dot・backslash・制御bytesも元ラベルで区別する。
孤立応答のquery_groupはnull。それ以外は主イベントの最初のquery根拠番号。
主イベントだけに全query_indicesを記録し、additional_responseはquery_indices=[]と
query_groupで主イベントへリンクする。応答番号は各イベントに一度だけ記録する。
内部DNSEventが共有するquery tupleをJSONへ繰り返し複製せず、参照出力をレコード数に比例させる。
query_group参照先の存在・キー一致、全イベントの根拠と件数・状態数はSchema以外の意味整合テストでも検証。

--max-dns-keysは1..100000（既定/最大100000）へ引下げ可。完了キーも窓期限まで数える。
E_LIMIT_DNSでは超過queryも全体/フロー/対象query件数へ計上し、DNS未登録の根拠を
unassigned_indicesへ残す。既存履歴は維持しcomplete=false、dns.status=partial、終了2。
時間解析が有効な間は、同一キーや孤立応答も含め、DNS問い合わせと応答の合計10000観測までを照合に取り込む。
この固定上限は保留中の根拠と確定イベントの両方を制限する。10001件目は全体・フロー・対象観測件数に残し、DNSでは
unassigned_indicesへ記録する。E_LIMIT_DNS_OBSERVATIONS、complete=false、dns.status=partial、終了2。
キー上限と観測数上限に同時に達する場合は観測数上限を優先する。フロー数も超過した場合はE_LIMIT_FLOWSを優先し、DNS未登録番号も維持する。
時刻逆行を同じレコードで検出した場合は、時間解析を無効化して既存結果を破棄するため、観測数上限では停止しない。
DNS処理後にフローへ必ず分配してから停止し、診断上限時も計数済みの完全レコードを欠落させない。
有効な時刻は非DNS/unsupported/truncated/malformedでも窓進行に使う。
入力不完全/読込/各上限停止時は読めた最後の有効時刻で終端処理し、未読時間は推測しない。
partialの時間値/状態は処理済みprefixの結果であり、未読末尾を含む全ファイルの時刻整合性を保証しない。
時間解析有効時はquery_indicesとresponse_indicesとunassigned_indicesが対象観測を重複なく分配する。
無効化時はevents=[]/event_counts=null、件数と根拠付き警告を保持する。
通常診断上限やフロー上限が先に停止診断となっても、time_analysisで無効化を明示する。
Phase 0の将来用フロー/DNSレポートは[旧Schema](report.phase0.schema.json)と
[契約例](../expected/baseline.report.json)として保管し、現行出力とは混同しない。
DNSの手定義期待値は[baseline.dns.json](../expected/baseline.dns.json)。
旧フローの全指標は[baseline.flows.json](../expected/baseline.flows.json)を履歴として照合する。
Phase 1の[baseline.phase1.json](../expected/baseline.phase1.json)も履歴として保持し、旧指標が変わっていないことを検証する。

診断はpacket_index/code順、JSONキーはソートする。壁時計時刻、処理時間、絶対パスを
出力へ埋め込まず、同一入力/設定で同一JSONとする。無効グローバルヘッダーと入力サイズ超過は
stdout空・stderr診断・終了2。読込開始後の不完全レコードや上限はcomplete=falseの途中出力とstderr診断・終了2。
有効ヘッダーだけなら正常0件、比率/時刻はnull。malformedレコードがあっても入力処理完了なら終了0。
根拠参照は1..N（失敗診断のみN+1またはnullも可）。Schemaに加えて合計・参照の意味整合をテストする。

## 構成と実装順

```text
classic pcap → reader.py（厳密境界検査 + RawPcapReader）
                    ↓ packet.py（Ethernet種別・境界検査 + IP/TCP/UDP復号）
                    ↓ models.py / PacketRecord
                    ├→ flows.py（方向別集計）
                    ├→ dns.py → dns_windows.py → dns_report.py（構造・窓・根拠・レポート）
                    └→ report.py（分類整合・JSON / 端末表）← cli.py
```

Phase 0でcli.pyの起動足場とfixture生成/検証を実装した。
Phase 1は①コンテナ検証・診断・上限、②時刻/長さ/番号付きRecord、③Ethernet/IPv4/TCP/UDP分類、
④summary/JSONと意味整合テスト、⑤正常/空/破損/未対応/上限のCLI終了規則を実装済み。
Phase 2前半でflows.pyとフローのJSON/端末表を実装済み。
後半でDNS正規化、窓と終端処理、曖昧さ・逆行時撤回とreport/CLI/Schemaを実装済み。
[Phase 2受入記録](phase2_acceptance.md)のCLI横断試験によりPhase 2完了と判定。
Phase 3では専用.venvへTSharkを展開し、8 fixtureの共通フィールドと残る6 fixtureの入力異常/形式差を独立比較済み。
比較条件と除外理由は[tshark_comparison.md](tshark_comparison.md)へ記録する。
DNS共通フィールド/一意な応答時間の独立比較と、独自状態との相違の説明も完了。
[Phase 3の判定と範囲](tshark_dns_comparison.md)で未説明差分0を確認し、人工入力の対応範囲でPhase 3完了と判定。
Phase 4の[固定規模CLI計測](benchmark.md)で人工UDP混在入力の時間・最大RSS・状態割合を実測済み。
計測経路はLinux専用の開発用スクリプトで、製品API/Schema/上限を変更しない。
新規環境でのREADME通し再現と[3分デモ動画](demo.mp4)は完了。[MVP受入記録](mvp_acceptance.md)を独立レビューへ引き継ぐ。
DNS/多数フロー/上限付近の性能や実通信への適用は未検証。

Scapy APIの採用根拠: [公式RawPcapReader API](https://scapy.readthedocs.io/en/latest/api/scapy.utils.html#scapy.utils.RawPcapReader)。
インストール版2.6.1のソースも確認し、メタデータがsec/usec/wirelen/caplen、nanoで精度を判別することを検証する。
