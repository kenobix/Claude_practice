# 保存済みPCAP解析ツール — MVP完成

作業日: 2026-11-17。`261119`は指定された作業ID。
計画: [保存済みPCAP解析ツール](../261014/plan_pcap_analyzer.md)。

classic pcapの入力検査、Ethernet/IPv4/TCP・UDPヘッダー検査、件数・長さ・時刻・分類の集計、
JSONと端末表を実装した。独立再レビューでPhase 0〜4の全受入条件に合格し、MVPは完成した。
実装は[PR #3](https://github.com/kenobix/Claude_practice/pull/3)でmainに取り込まれた。
初めて使う場合は[3分デモ動画](pcap_analyzer/docs/demo.mp4)と[新規環境からの受入再現](#新規環境からの受入再現)を参照。
双方向フロー集計、方向別件数/長さ/根拠、TCP flags、フロー数上限を追加した。
DNS名の復号・正規化と単一質問抽出を内部APIとして追加した。
DNSメッセージのRR構造検査を完全なUDP port 53候補のパケット分類とCLI診断へ統合済み。
検証済み質問の引継ぎ、端点/ID/質問の照合キーとQR/portによる方向判定も内部APIで実装済み。
5秒窓による問い合わせ/応答照合をJSON/端末表へ統合済み。
時刻逆行時は全DNS結果を破棄し、キー数上限では途中結果と根拠を返す。
DNSの問い合わせ・応答は孤立応答も含めて10000観測まで照合する。超過時はE_LIMIT_DNS_OBSERVATIONSと途中結果を返す。
孤立応答の最大RSSとJSONサイズは[修正前後の計測記録](pcap_analyzer/docs/dns_observation_limit.md)を参照。
IEEE 802.3のフレームは未対応として集計を継続する。caplenがwirelenを超えるPCAPレコードはE_RECORD_LENGTHで停止し、先行レコードだけを集計する。

## 作業環境

- 開発時のworktreeは `~/work/Claude_practice-261119`、実装ブランチは `feat/pcap-analyzer`。
- originは `kenobix/Claude_practice`。完成したMVPはmainから取得できる。
- Python 3.12.3、専用環境: `pcap_analyzer/.venv`、ツール版0.3.0
- Scapy 2.6.1 / pytest 8.4.2 / jsonschema 4.25.1
- 依存は推移依存・pipを含め[requirements.lock](pcap_analyzer/requirements.lock)に固定。
  ローカルパッケージはeditable installで別途導入する。他プロジェクト・OSは変更しない。

VS Codeでこのworktreeを開き、Codexのブランチと作業対象を確認する。
`pcap_analyzer/` をフォルダーとして開けば[Python/pytest設定](pcap_analyzer/.vscode/settings.json)が適用される。
既存選択が優先される場合は `.venv/bin/python` を手動指定する。GUI操作自体は未検証。

## セットアップとテスト

```bash
cd ~/work/Claude_practice-261119
git branch --show-current
cd 261119/pcap_analyzer

# 初回のみ。既存.venvを使う場合は作り直さない
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.lock
.venv/bin/python -m pip install --no-cache-dir --no-deps --no-build-isolation -e .

.venv/bin/python -m pip check
.venv/bin/python scripts/generate_fixtures.py
.venv/bin/python -m pytest -q
```

独立再レビューの新規環境で全465テスト成功。IEEE 802.3混在・予約値境界、レコード長矛盾での正常prefix保持と本文読込前の停止、DNS観測数上限を検証した。
DNS名/質問抽出40件とフロー・入力処理等の既存テストも再実行した。
DNS候補の空payloadの分類変更に合わせ、既存1件の期待をparsedからmalformedへ更新した。
既存100件は分類・入力制限・時刻等の検査を維持し、版変更に伴う期待を更新。
フローの正常系・方向・キー・flags・truncated帰属・除外・上限・決定性・Schemaの53件を追加した。
生成した14種類の人工fixture、手計算期待値、4種類のmagic/時刻形式、不正なヘッダー、
分類優先順位、caplen/wirelen、破損レコード、安全上限、Schema、決定性を検証する。
依存取得（PyPI/Ubuntu配布）とGitHub保存以外の処理はオフライン。ライブ捕捉・送信・スキャン・逆引き・外部照会なし。
Scapyの経路/インターフェース自動読込をプロセス内で無効化し、socket生成禁止下でも解析をテストする。
`.venv`、cache、egg-info等はGit対象外。人工PCAPと期待値は再現用成果物として保持する。

### 新規環境からの受入再現

対象ブランチをチェックアウトしたリポジトリのルートで実行する。既存venvは使わず、
コミット済みの内容を一時ディレクトリへ展開する。依存取得後の生成・解析・照合はオフラインで実行できる。

```bash
set -e
repro_dir=$(mktemp -d /tmp/pcap-repro.XXXXXXXX)
git archive HEAD 261119/pcap_analyzer | tar -x -C "$repro_dir"
cd "$repro_dir/261119/pcap_analyzer"
python3.12 -m venv .venv
.venv/bin/python -m pip --isolated install --no-cache-dir -r requirements.lock
.venv/bin/python -m pip --isolated install --no-cache-dir --no-deps --no-build-isolation -e .
.venv/bin/python -m pip check
.venv/bin/python scripts/generate_fixtures.py --output regenerated
diff -qr fixtures regenerated

for sample in baseline capture_truncated record_cutoff malformed_length unsupported_pcapng backward; do
  input=$sample.pcap
  expected=0
  case $sample in
    record_cutoff|malformed_length) expected=2 ;;
    unsupported_pcapng) input=$sample.pcapng; expected=2 ;;
  esac
  result=0
  .venv/bin/pcap-analyzer "regenerated/$input" || result=$?
  test "$result" -eq "$expected"
done
.venv/bin/python -m pytest -q
```

`diff`は出力なしが一致。pytestは保存版14ファイルのJSON CLI出力を既存の手定義期待値とSchemaで照合する。
生成版と保存版はバイト単位で比較しており、既存fixtureと期待値は上書きしない。

| 入力 | 表示で確認する結果 |
|---|---|
| `baseline.pcap` | 10件・680/680 bytes。DNS #4→#5 は25 ms、#6 は窓内で応答観測なし、#7・#8→#9 は曖昧で時間なし |
| `capture_truncated.pcap` | 終了0・完全レコード1件。捕捉40/元長54 bytes、truncated 1。足りない14 bytesは補完しない |
| `record_cutoff.pcap` | 終了2・`INCOMPLETE`・`E_RECORD_DATA`・完全レコード0件。途中の40 bytesは集計外 |
| `malformed_length.pcap` | 終了2・`INCOMPLETE`・`E_RECORD_LENGTH`・完全レコード0件。caplen55とwirelen54が矛盾 |
| `unsupported_pcapng.pcapng` | 終了2・`E_FORMAT_UNSUPPORTED`。勝手に変換せずレポートなし |
| `backward.pcap` | 終了0・2件。時刻逆行のためDNS時間結果を全体で破棄 |

## 自動テスト（GitHub Actions）

[PCAP analyzer tests](../.github/workflows/pcap-analyzer-tests.yml)で上記の固定依存導入と全テストを実行する。
`261119/**`またはこのworkflow自身の変更が対象。`feat/pcap-analyzer`と`main`へのpush、
およびpull requestで起動する。
Ubuntu 24.04 / Python 3.12系列の1ジョブ、10分上限。依存導入はジョブ内の専用`.venv`に限定する。
ActionsはコミットSHA固定、権限は`contents: read`のみ、checkoutの認証情報は保持しない。
同じrefの古い実行は中止する。キャッシュ・成果物アップロード・外部サービス・追加secretは使わない。

fixtureの再現性、正常/不正/境界入力、Schema、件数・バイト数・根拠、DNS内部API、
socket生成禁止下でのfixture生成/解析を含む全テストを使用する。
fixtureを生成し直して期待値を上書きする手順は追加しない。
セットアップ時の依存取得以外に、テストでライブ通信の捕捉・送信・スキャンを行わない。
導入時は343件がローカル/ホスト双方で成功し、actionlint 1.7.12で静的検証済み。
DNS統合後は370件、Phase 2受入追加後は386件、比較ハーネス追加後は406件、入力異常比較追加後は421件、DNS比較追加後はローカル442件成功。
TShark実体の14保存fixture比較とDNS4ケース比較はローカル明示実行。
通常CIは保存した参照行の再生とハーネスを検証し、TShark実体を導入・起動しない。
ホスト結果は各commitの実行一覧で確認する。
GitHubホスト上の実行結果は[Actions実行一覧](https://github.com/kenobix/Claude_practice/actions/workflows/pcap-analyzer-tests.yml)で確認する。
CIの成功だけでTShark比較や性能測定の完了とはしない。Phase 2の判定根拠は下記の受入記録を参照。

## CLI

```bash
# 端末向け表示（既定）
.venv/bin/pcap-analyzer fixtures/baseline.pcap

# 決定的なJSON（schema_version=0.4.0）
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json

# 3件で停止し、complete=falseの途中JSONと終了2を確認
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json --max-records 3

# 新規2本目のフローで停止。超過パケットは未帰属に入れ、終了2
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json --max-flows 1

# DNS新規2キー目で停止。超過queryも全体/フローへ計数、DNS未登録番号を出し終了2
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json --max-dns-keys 1

# 破損した本文：完全レコード0件、E_RECORD_DATA、終了2
.venv/bin/python -m pcap_analyzer fixtures/record_cutoff.pcap --format json
```

基本例の結果は10パケット、捕捉680 bytes、記録上の元長680 bytes、TCP4件、UDP6件、
全件parsed、観測時刻1700000000000000000〜1700000008000000000 ns（幅8秒、元精度µs）。
PCAP自体のサイズ864 bytesにはコンテナヘッダーも含む。
`parsed`は対応ヘッダーと、対象UDP port 53ならDNS構造の検査成功を意味する。
全RDATAの意味、名前解決の成功や問い合わせ/応答の対応は保証しない。

JSONの`classification_indices`にファイル順・1始まりの根拠番号を保存する。
`flows`に双方向の集計を出力する。DNS構造エラーは既存の分類/diagnosticsに番号付きで出力する。
`dns.status`は入力処理に合わせcomplete/partial、`time_analysis`はenabled/disabled_timestamp_regression。
`events`へmatched/ambiguous/additional_response/unmatched_response/no_response_observed/window_incompleteを出す。
baselineはquery4→response5の25ms、query6の未応答観測、query7,8/response9の曖昧判定となる。
時刻逆行では確定済みを含め全DNSイベントを破棄し、状態件数をnullにする。query/response件数は維持する。
入力途中で停止した場合は処理済み範囲の結果だけであり、未読末尾の時刻整合性を保証しない。

| 終了コード | 意味 / 出力 |
|---|---|
| 0 | 有効コンテナを終端まで処理。unsupported/truncated/malformedパケットを含み得る |
| 2 | 入力/形式/読込/上限エラー。不正グローバルヘッダー・サイズ超過はstdout空、stderr診断 |
| 2 | 読込開始後の不完全レコード・上限はcomplete=falseの途中出力とstderr診断 |

有効ヘッダーのみのpcapは正常0件、空ファイルは形式エラー。
捕捉された完全レコードのcaplen<wirelenはtruncated、ファイル本文が宣言caplenに届かない場合は入力不完全。
無効なfractionはmalformedとし時刻範囲から除外、timestamp_invalid_countに数える。

| 安全上限 | 既定値・引上げ不可の最大値 | 小さくするオプション |
|---|---|---|
| 入力ファイル | 100 MiB | `--max-input-bytes` |
| 完全レコード | 100万件 | `--max-records` |
| 1レコードの捕捉長 | 1 MiB | `--max-packet-bytes` |
| DNS保持キー（完了履歴込み） | 10万件 | `--max-dns-keys` |
| 通常診断 | 1万件（ほかに終了診断1件） | `--max-diagnostics` |
| 双方向フロー数 | 10万件 | `--max-flows` |

すべて正整数。ちょうど上限は許可。診断上限は当該レコードを計数してから明示停止する。
入力全体のSHA-256を上限確認後に計算し、パケットは逐次処理する。FIFO/デバイスは拒否。
並行変更はサイズ/mtime/ctimeの変化を検出するが、不変スナップショットを保証しない。

## フローの意味と整合性

キーは(protocol番号、IPv4 bytes/portで昇順に固定した端点a/b)。最初の観測方向に依存しない。
同じIP同士はportで比較し、両端点が完全に同じ場合はa_to_bへ加算する。
方向ごとにpackets/captured_bytes/original_bytes/packet_indicesを保持する。
TCP flagsはF/S/R/P/A/U/E/C/Nを独立に計数し、SYN+ACKはSとAへ各1。UDPのflagsはnull。
同じ両端点の集計であり、単一TCP接続や一意のアプリケーション通信量とは呼ばない。

基本例は4フロー。TCPは各方向2件/108 bytes、UDPはport53000で72/88 bytes、
53001で72/0 bytes、53002で144/88 bytes。方向別合計680 bytes + 未帰属0 bytes = 全捕捉680 bytes。
TCP flagsはS=2、A=3。件数も10+未帰属0=10、元長も680+0=680で一致する。

非fragmentのIPv4 TCP/UDPでヘッダー全体が完全・整合していれば、payload不足のtruncatedも帰属する。
不正時刻だけでmalformedとなったものも帰属可能。ヘッダー不足・長さ矛盾・fragment・未対応形式は未帰属。
全分類は従来どおり維持する。summaryの未帰属件数/捕捉長/元長とunattributed_indicesに根拠を残す。

上限を超える新規フローのパケットは全体に計数し、未帰属へ記録してE_LIMIT_FLOWSで停止する。
上限ちょうどのフロー数と既存キーへの追加は許可する。途中結果でも次の等式を検証する。

```text
方向別件数の総和 + 未帰属件数 = 全件数
方向別captured bytesの総和 + 未帰属captured bytes = 全captured bytes
方向別original bytesの総和 + 未帰属original bytes = 全original bytes
```

## DNS内部API: 名前・単一質問・RR構造検査

`src/pcap_analyzer/dns.py` に `decode_name(message, offset)` と `parse_question(message)` を追加。
DNS照合に先行する依存部分だけを実装した。UDP payloadのbytesを入力し、
ASCIIだけを小文字化したラベル列（終端root付き）、QTYPE/QCLASS、ID/QR/RCODE/TCを取り出す。
ドット・バックスラッシュ・制御文字・非ASCII bytesは表示時に10進エスケープし、
表示名からキーを復元しない。圧縮名の後の元メッセージ位置も保持する。

ラベル/名前長、ポインターの範囲・循環、ヘッダー/質問の不足を検査する。
後方ポインターのみ受理し、1名256ステップ、メッセージ65507 bytesまでに制限。
opcode!=0、質問数!=1、未対応ラベル形式・過剰な参照連鎖はunsupported、
境界矛盾はmalformedとしてDNSDecodeErrorへ分類する。
これは完全なUDP payload上のDNS構造の検査であり、捕捉truncatedの分類は既存packet層で行う。

`parse_message(message)` は質問とanswer/authority/additionalを宣言件数どおり走査し、
RRの名前・固定10 bytes・RDLENGTHを検査する。未消費の末尾bytesも拒否する。
戻り値はDNSMessage（question/answers/authorities/additionals）で、各RRは名前・type/class/TTL、
raw RDATAとその開始/終了位置を保持する。

圧縮ポインターは解析済みのラベル先頭・root・ポインター位置だけを参照可能。
NS/CNAME/PTR、MX、SOAの名前フィールドもRDLENGTH内で検査・登録する。
未知のRDATAを名前と推測せず、不透明bytesへの参照はunsupportedとする。
1メッセージ1024 RR・名前復号計16384ステップまでに制限する。
循環・不足・長さ矛盾はmalformed、参照先の未検証や処理上限はunsupported。

**parse_messageはpacket分類とCLIへ統合済み。** parse_questionは引き続き質問だけの抽出APIであり、
全体の構造検査にはparse_messageを使う。単独decode_nameは参照先の由来を証明しない。
A等の内容を含む全RDATAの意味、EDNS等の追加仕様は検証しない。
構造検査成功を名前解決の成功やサーバー正常と扱わない。
非fragmentのIPv4 UDPでsrc/dstのいずれかが53、既存ヘッダー/長さ/時刻検査に問題がなく、
truncatedでない候補だけにparse_messageを適用する。完全UDP内のDNS不足はmalformed、
対象外opcode/質問数/参照先・DNS処理上限はunsupported。既存の優先順位を維持する。
DNS本文が不正でも検証済みのL4ヘッダーによるフロー帰属は維持する。
DNS候補の空payloadは従来のparsedからmalformedへ変更。port 53以外/TCPは本文を検査しない。
Ethernet paddingをDNS本文へ含めない。診断上限では従来どおり途中結果と終了2を返す。
JSONの形状/版番号は維持し、Schemaに分類範囲と照合statusの説明を追加した。

## DNS照合キーと向き判定（内部API）

`PacketRecord.dns_question`へ全体構造検査に成功したDNSQuestionだけを引き継ぐ。
質問のbytesラベル・ID・QTYPE/QCLASS・QR/RCODE/TCを保持し、RR/RDATAや生パケットを残さない。
`dns.observation(record)`は照合対象ならDNSObservation、対象外ならNoneを返す。
queryはdst port53、responseはsrc port53を要求し、両port53でもQRで役割を決める。
向きが合わないDNSも構造上のparsed分類とフロー帰属は維持する。

DNSKeyは(clientのIPv4 packed bytes/port、serverのIPv4 packed bytes/port、ID、
ASCIIのみ小文字化したラベルtuple、QTYPE、QCLASS)。フローと違い端点をソートしない。
DNSObservationはkey・根拠番号・整数ns・QR/RCODE/TCを保持する不変値で、応答照合や時間計算は行わない。
不正時刻/truncated/fragment/不正DNS等は除外し、同じ入力から同じキーを得る。
API入力は既存decoderが生成したPacketRecordとし、任意モデルの外部入力検証APIとはしない。
DNSイベントは下記窓APIを経由してCLI/Schemaへ統合済み。

## DNS窓処理（内部API）

`dns_windows.DNSWindowCollector`へ時刻順のDNSObservationを`add`し、
DNS以外の有効パケット時刻も`advance(ns)`で伝え、最後の有効時刻で`finish(end_ns)`する。
各呼出しは確定済みDNSEventのtupleを返す。イベント履歴は呼出し側が受け取り、collectorは保持しない。
最初のqueryから閉区間5秒（端を含む）で、重複queryでも延長しない。
matched/ambiguous/additional_response/unmatched_response/no_response_observed/window_incompleteを区別する。
query再利用で時間値を撤回できるよう、孤立応答以外は窓閉鎖/finishまで確定出力しない。

完了キーも期限まで保持し、未完了と合わせ100000キーを上限とする（内部APIで引下げ可）。
上限超過はE_LIMIT_DNS。参照数は呼出し元readerの100万レコード上限に従う。
期限管理はheapで行い、全キーを各パケットで走査しない。期限の同時刻は登録順で処理する。
イベントの根拠はtuple、応答時間は整数ns。応答ごとにRCODE/TCを保持する。
追加応答には独立イベントを作り、最初の応答のみ主イベントに記録する。
曖昧なグループは全query番号を保持し、追加応答のイベントにも同じquery番号を付け、時間値を出さない。

時刻逆行・不正時刻・逆順の根拠番号は例外で拒否する。ファイル全体の逆行を検出した呼出し側は、
既に返された結果を含め全DNS時間結果を破棄する。dns_report.pyでこの責務を実装済み。
finish後は再利用不可。既存の分類・フロー・時刻精度は維持する。

## DNS出力と観測限界

全有効パケット時刻で窓を進め、根拠順にイベントを出力する。
一意な対応だけに整数`response_time_ns`と表示用`response_time_ms`を付け、それ以外はnull。
RCODE/TCは各応答の観測値であり、NXDOMAINも応答として数える。
keyはclient/serverのIPv4/port、ID、正規化ラベルのhex配列、QTYPE/QCLASSを保持する。
`name`はエスケープ済み表示専用。入力の制御文字等をそのまま端末へ出さない。

主イベントの`query_indices`は全query番号を一度だけ保持する。
追加応答は`query_indices=[]`とし、`query_group`（主イベントの最初のquery番号）で参照する。
これにより重複query×追加response数の組合せで根拠リストが増大するのを避ける。
各応答番号は一度だけ出力し、孤立応答のquery_groupはnull。
`event_counts`は6状態のイベント件数であり、パケット分類やquery件数の内訳とは異なる。
DNSキー上限の超過queryは件数へ含め、`unassigned_indices`へ根拠を記録する。
同時にフロー数も超過すればE_LIMIT_FLOWSを優先するが、DNS未登録番号は失わない。

未応答・曖昧・記録終端は観測状態であり、障害・損失・攻撃と断定しない。
[Phase 2受入記録](pcap_analyzer/docs/phase2_acceptance.md)で計画の完了条件を確認し、Phase 2完了と判定した。
既存14 fixtureのCLI横断確認に加え、同一DNS IDのport/名前/type/class分離と逆順応答、
fragment・切詰め・不正DNS・孤立/遅い応答・期限後再利用が混在する例を検証した。
Phase 3では専用`.venv`に展開したTSharkと、8 fixtureの共通項目・残る6 fixtureの入力異常/形式差を比較済み。
[独立比較の環境・コマンド・結果・未比較範囲](pcap_analyzer/docs/tshark_comparison.md)を参照。
空入力/未対応形式を扱う範囲、途切れたレコードの拒否、長さ矛盾の分類を説明し、未説明差分0。
[DNS独立比較とPhase 3完了判定](pcap_analyzer/docs/tshark_dns_comparison.md)で、
baseline/backwardと追加の人工2レシピ（計32パケット）のDNS共通項目を照合した。
一意な5ペアの応答時間（25ms、123ns、5秒、1ns等）が一致し、重複/期限外/逆行の相違も説明済み。
Phase 3は指定した人工入力の対応範囲で完了。Phase 4の[固定規模CLI計測](pcap_analyzer/docs/benchmark.md)を実施した。
500/5,000/15,000パケット各3回でSchema・手計算・バイト数を照合し、出力ハッシュは独立再レビューでも保存記録と一致した。
新規環境での導入・fixture生成・正常例と解析限界の再現、[3分デモ動画](pcap_analyzer/docs/demo.mp4)とCLI出力の整合も確認した。
独立再レビューで最大長255バイトのDNS名を含む孤立応答を検証し、10,001件目で照合を停止した。
検証の詳細と未検証範囲は[MVP受入記録](pcap_analyzer/docs/mvp_acceptance.md)を参照。

## 仕様・期待値・制限

- [現行仕様とPhase 2の設計](pcap_analyzer/docs/spec.md)
- [fixture設計と手計算](pcap_analyzer/docs/fixtures.md) / [fixture別期待値](pcap_analyzer/expected/fixtures.json)
- [現行Schema](pcap_analyzer/docs/report.schema.json) / [Phase 2前半のフロー期待値](pcap_analyzer/expected/baseline.flows.json) / [Phase 1の旧期待値](pcap_analyzer/expected/baseline.phase1.json)
- [Phase 0の将来レポート例](pcap_analyzer/expected/baseline.report.json) / [対応する旧Schema](pcap_analyzer/docs/report.phase0.schema.json)
- [DNSの手定義期待値](pcap_analyzer/expected/baseline.dns.json)
- [Phase 2 CLI横断期待値](pcap_analyzer/expected/phase2.fixtures.json) / [受入確認と完了判定](pcap_analyzer/docs/phase2_acceptance.md)
- [DNS独立比較・Phase 3判定](pcap_analyzer/docs/tshark_dns_comparison.md) / [TShark DNS参照行](pcap_analyzer/docs/tshark_dns.result.json)
- [作業記録](work_log.md)

未対応: pcapng、圧縮pcap、IEEE 802.3 LLC/SNAP、IPv6、VLAN、Linux cooked capture、Wi-Fi、ライブ捕捉、
IP再構成、TCP再構成/再送推定、TLS復号。IPv4 fragmentは独立計数しL4/DNSから除外する。
ヘッダーの長さ・捕捉境界を検査するがチェックサムや個々のoptionの意味は検証しない。
UDP長はIPv4の宣言payload長との一致を要求する。
プロトコル件数は確認できたIPv4 protocol番号等で識別し、L4のtruncatedでもtcp/udpへ数え得る。
分類の件数とは別の指標で、それぞれの合計が全レコード数と一致する。

DNS名/質問抽出・RR構造検査とpacket分類/CLI診断への統合は実装済み。
照合キー/向き判定・窓処理・DNSイベントのCLI/Schema統合は実装済み。
フロー集計と端末フロー表は実装済み。
人工baselineの25ms・未応答観測・重複queryはCLI出力も手計算一致を確認。
TShark比較は14保存fixtureの共通項目/入力異常とDNS4ケースまで。DNS名表示は通常ASCIIラベルの共通部分に限定。
多数フローを含む入力全体の上限付近の性能、実通信、別OS・Python 3.12以外、VS Code GUIは未検証。
捕捉長と記録上の元長を回線上の実転送量と呼ばず、観測不足を障害や攻撃と断定しない。

## 固定規模CLI計測（Phase 4の一部）

```bash
cd ~/work/Claude_practice-261119/261119/pcap_analyzer
.venv/bin/python scripts/benchmark.py > /tmp/pcap-benchmark.json
```

Linux専用。生成PCAP/CLI出力は一時ファイルのみ。各試行60秒上限、子プロセスを終了・回収する。
[計測境界・手計算・条件・限界](pcap_analyzer/docs/benchmark.md)と
[9試行の生データ](pcap_analyzer/docs/benchmark.result.json)を参照。
CIでは小規模の実CLIと失敗/timeout等の経路を検査し、性能値の一致や速度閾値を要求しない。
