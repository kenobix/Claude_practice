# 作業記録 — PCAP解析 Phase 0

実施日: 2026-11-17（Asia/Tokyo）。作業ディレクトリ名261119はユーザー指定。

## 環境確認

- cwd: `/home/kenshin/work/Claude_practice-261119`。
  当初指定の `~/projects/Claude_practice-261119` は存在しなかったが、
  ユーザーから `~/work/Claude_practice-261119` への訂正を受けて確定した。
- branch: `feat/pcap-analyzer`、HEAD: `b4c460a`、origin: `git@github.com:kenobix/Claude_practice.git`。
- 初期git statusはclean。既存worktreeを利用し、新しいworktree/branchは作成していない。
- mainは別の `/home/kenshin/work/claude_practice`。他にci-runner、database-engineのworktreeがある。
- Python 3.12.3、ホストユーザーpip 26.1.1、venv利用可。
  開始時にScapy/pytestはimport不可、TSharkはPATH上になし。OSパッケージは導入していない。
- ルートAGENTS.mdと `261014/plan_pcap_analyzer.md` を確認。
  今回のcommit/push/PR禁止・main変更禁止を優先した。

## 実施内容

1. `261119/` にREADME、作業指示、ログを配置。実装以下にsrc/tests/fixtures/expected/docs/scriptsを作成。
2. Python 3.12.3の専用 `.venv` を作成。Scapy 2.6.1、pytest 8.4.2、jsonschema 4.25.1、
   setuptools 80.9.0、wheel 0.45.1を導入し、推移依存とpip 24.0を `requirements.lock` に固定。
   最初のsandbox内インストールは名前解決不能で失敗。ネットワーク許可付きの同じvenvへの導入で成功。
   管理者権限・OS変更は行っていない。
3. src形式のパッケージをeditable install。CLIのhelp/version、入力指定時の未実装エラーを用意。
   VS Codeのプロジェクト用Python/pytest設定を追加した。VS Code拡張の導入・GUI操作は行っていない。
4. classic pcapの形式/長さ/上限、精度、分類優先順位、フローキー、DNS窓/曖昧さ/時刻逆行、
   計算式、JSON Schema、責務分担、Phase 1の順序をdocsに整理。
5. 手計算の期待値を先に保存し、固定IP/MAC/時刻/example.testから人工fixtureを14ファイル生成。
   `baseline.report.json` は将来の出力契約例。入力SHA-256は生成ファイルの識別用に計算した値であり、
   解析器の実行結果ではない。件数・長さ・DNS期待値は手計算に基づく。
6. Scapy import時のインターフェース情報取得がsandboxのnetlink socket制限で失敗したため、
   `offline.py` でプロセス内のインターフェース/経路自動読込を無効化した。
   インストール済みScapyやOSのファイルは変更していない。
   その後、全socket生成を禁止した子プロセスでfixture生成に成功するテストを追加した。
7. ルートREADMEのディレクトリ一覧に261119を1行追記した。
   これはルートAGENTS.mdに従う最小の範囲外変更。公開ページは追加していない。

## 実行結果

実装ディレクトリでの操作:

| 操作 | 結果 |
|---|---|
| `.venv/bin/python scripts/generate_fixtures.py` | 人工fixture 14ファイル生成 |
| `.venv/bin/python -m pytest -q` | **15 passed** |
| `.venv/bin/python -m pip check` | No broken requirements found |
| `.venv/bin/pcap-analyzer --version` | pcap-analyzer 0.0.0、終了0 |
| `python -m pcap_analyzer --help` / 引数なし | help表示、終了0（pytestで確認） |
| CLIにbaseline.pcap指定 | E_NOT_IMPLEMENTED、終了3、stdout空（pytestで確認） |
| Git差分・除外設定・文書リンク・秘密情報パターン | 対象パスを確認。venv/cache/egg-infoは除外 |

テストが検証するもの:

- 再生成したfixtureと保存fixtureのbytes一致、socket不要での生成。
- structでの独立コンテナ読込とScapy RawPcapReaderのメタデータ読込。
- 基本例10件、捕捉/元長680 bytes、コンテナ864 bytes、固定時刻・IP・DNS質問/回答・重複query。
- µs big endian/ns little endianの整数時刻、切り詰め/破損の実bytes、ARP、fragment、逆行の記録順。
- JSON Schema自体の妥当性、手計算レポート例のSchema/件数/バイト/根拠整合。
- CLI起動と未実装時の終了動作。

## 未実施と次段階

解析本体、フロー/DNS照合、異常入力に対するCLI診断、設定上限停止、端末表、TShark比較、性能測定、
実通信データ、VS Code GUIからの実行は未実施。DNS25ms等はPhase 2の期待値であって実測ではない。
IPv6/pcapng/VLAN/TLS復号、ライブ捕捉・スキャン・外部照会は実装していない。

次はPhase 1として、①厳密コンテナ検証と停止コード、②PacketRecord/整数時刻/長さ、
③Ethernet/IPv4/TCP/UDP検査と分類、④summary/JSON/根拠と整合検査、
⑤正常・空・破損・未対応・上限のCLIテストを実装する。
Phase 2でフローとDNS状態機械、Phase 3でTShark比較へ進む。

変更は未ステージ・未commit。push・PR作成を行っていない。


# Phase 1 — 読み込みと件数集計

実施日: 2026-11-17（Asia/Tokyo）。以下は前節のPhase 0作業後の追加記録。

## 既存成果の確認と保存

- 指定worktree/branchを再確認。最初に既存15テストを再実行し成功した。
- 既存仕様、Schema、14人工fixture、期待値、CLI、差分を確認。
- 依頼に従ってルートREADMEへの前回の一覧追加1行だけを取り消し、元の内容へ戻した。
  他のPhase 0成果は保持した。以後の変更は261119/以下のみ。
- Phase 0を `ea61cf2`（chore(261119): initialize PCAP analyzer development environment）として
  commitし、`origin/feat/pcap-analyzer` へpushした。mainは変更していない。
- 今回の指示に合わせ261119/AGENTS.mdのPhase 0時のcommit禁止等を更新した。

## Phase 1実装

- models.py: 上限設定、入力診断、根拠番号・整数ns・長さ・端点・分類を持つPacketRecord。
- reader.py: 通常ファイルの読み取り専用open、classic pcap v2.4の4 magic、link type1、
  snaplen、厳密なレコード境界、caplen実読込長、RawPcapReaderによる逐次読込。
  Scapyの既定MTUによる長いレコードの切り落としを防ぐためsize=caplenを明示。
- packet.py: ヘッダー境界を検査してからScapyでEthernet/IPv4/TCP/UDPヘッダーのみ復号。
  payloadを復号せず、fragmentをL4から除外。分類競合はmalformed > truncated > unsupported > parsed。
- report.py: 件数、捕捉/元長、時刻範囲、分類・排他的protocol別件数、fragment、根拠番号、
  不正時刻、診断の逐次集計とJSON/端末表。
- cli.py: JSONと端末表、引下げ可能な上限、正常0/入力・不完全・上限エラー2。
  無効グローバルヘッダーはstdout空、読込開始後の停止はcomplete=falseの途中出力を返す。
- 入力100 MiB/100万レコードに加え、1レコード1 MiB/通常診断1万件の上限を導入。
  診断上限は当該レコード計数後に停止し、終了診断1件を追加する。
- SHA-256はサイズ検査後に入力全体をチャンク読込して計算。完全レコードを一括保持しない。
  FIFO/デバイス拒否、サイズ/mtime/ctimeの変化検知も追加した。不変snapshot保証ではない。
- 不正timestamp fractionはmalformedに数えて時刻範囲から除外。
  逆行は警告として保持し、JSONを決定的にするため時刻順へ並べ替えない。
- 現行JSON Schemaを0.2.0（Phase 1）へ更新。flows=null、DNSはnot_implemented/events=null。
  旧Schemaはreport.phase0.schema.jsonとして保管し、旧将来レポート例も維持した。
  Phase 1用baseline.phase1.jsonは独立した手計算値から定義した。
- ツール版を0.1.0へ更新し、同じvenvへネットワーク不要でeditable再導入した。
  依存版・requirements.lockの変更はない。

## 検証結果

全100テスト成功。既存15件はCLIの未実装期待と旧Schema参照を更新しつつ維持し、
85件の機能テストを追加した。件数だけでなく以下の仕様を確認した。

- 14種類の保存fixtureをすべて再利用し、正常・空・未対応・破損の期待値を照合。
- baselineは10件/捕捉680 bytes/元長680 bytes/TCP4件/UDP6件/parsed10件、8秒幅。
  全体期待JSONと一致し、入力SHA-256・Schema・分類合計・参照番号の一意性も一致。
- little/big endian × µs/nsの4形式で整数nsと元精度を確認。不正fractionを除外し、
  逆行のmin/max範囲と警告を確認。
- Ethernet/IPv4/TCP/UDPの不足・長さ矛盾、options全体長、fragment、IPv6/VLAN/ARP、
  分類の優先順位、caplen/wirelen/snaplenの矛盾を検証。
- レコードヘッダー不足/本文不足で完全レコードだけを保持し、終了2と不完全JSONを確認。
- 不正magic/version/snaplen、圧縮入力、pcapng、未対応link typeを拒否。
- 入力bytes/レコード/捕捉長/診断の上限と境界、巨大宣言長の割当前拒否、
  次のレコードをScapyで読まずに件数上限で停止することを確認。
- Scapyの既定MTUより長い70054 bytesのレコードが切り落とされないことを確認。
- 同じ入力のJSON一致と1パケット追加時の件数/バイト増分、未実装値の明示を確認。
- socket生成禁止下の解析、読み取り専用ファイルの不変、FIFOの非ブロック拒否、
  存在しない入力、ディレクトリ、読込中変更検出を確認。
- 別途、固定seedの不正ヘッダー500例をdecodeへ入力し、未捕捉例外が出ないことを確認した。
  これは異常入力全体の網羅性を保証する検証ではない。
- pip check成功。READMEのコマンドで端末表/JSONを確認し、文書リンク・差分・秘密情報パターンを点検。

## 制限とPhase 2

DNS本文・名前の正規化・照合、フロー集計、TCP再送推定、チェックサム、optionの意味検証は未実装。
parsedはL4ヘッダーまでの検査成功。UDP長はIPv4の宣言payload長との一致を要求する。
pcapng/圧縮pcap/IPv6/VLAN/cooked/Wi-Fi、IP/TCP再構成、TLS復号は対象外。
TShark比較、性能測定、実通信、他Python版、VS Code GUI実行は未検証。
ライブ捕捉・送信・スキャン・外部照会は行っていない。

Phase 2は①双方向キー/方向別bytes/根拠のフロー集計と上限、②DNS境界・圧縮名・byte正規化、
③5秒窓/重複・ID再利用/追加・孤立応答、④終端/逆行での未確定・時間無効化、
⑤既存手計算期待値と新境界fixtureによる照合の順で進める。
今回の成果はレビュー後にfeat/pcap-analyzerだけへ保存する。mainへのpush・マージとPR作成は行わない。


# Phase 2前半 — 双方向フロー集計

実施日: 2026-11-17（Asia/Tokyo）。DNS照合は今回の範囲外。

## 開始確認

- cwd=`/home/kenshin/work/Claude_practice-261119`、branch=`feat/pcap-analyzer`。
- Git statusはclean、開始コミットはPhase 1の`0b03294`。
- 指定計画・仕様・Schema・AGENTS・既存コードを確認し、既存100テストを最初に再実行して全件成功。
- 以後の変更は261119/内のみ。root README・main・他worktreeは変更しない。

## 実装内容

- flows.pyを追加。キーはIP protocol番号と、IPv4の4 bytes/port整数で順序を固定した両端点。
  初回パケットの向きやIP表示文字列の辞書順に依存せず、protocol/a/b順に出力する。
  同一IPではportで比較し、両端点が同一ならa_to_bへ加算する。
- 方向別packets/captured_bytes/original_bytes/packet_indicesを集計。
  TCP flagsの9 bit（F/S/R/P/A/U/E/C/N）を独立計数し、SYN+ACKは両方へ1。
  UDPのtcp_flag_countsはnull。ポート再利用・重複捕捉を含む端点集計でありTCP接続とは呼ばない。
- PacketRecordへflow_eligibleとtcp_flagsを追加。分類とは独立して必要ヘッダーの完全性を伝える。
  ヘッダー全体が完全・整合するtruncatedは帰属し、payload不足を補完せずcaplen/wirelenを別加算。
  不正時刻fractionのみのmalformedもヘッダーが有効なら帰属する。
  fragment、非TCP/UDP、ヘッダー/option領域不足、長さ矛盾は未帰属へ記録する。
- フロー上限100000、CLI --max-flowsで1..100000に引下げ可。
  新規キーで超過した完全レコードは全体に数えて未帰属へ記録し、次レコードを読まず
  E_LIMIT_FLOWS / complete=false / 終了2で停止する。既存キーへの追加は上限時も許可。
- summaryにflow_countと未帰属件数/捕捉長/元長を追加し、unattributed_indicesで根拠を保持。
  すべての方向と未帰属で全件数/捕捉長/元長を分配し、根拠番号1..Nを重複なく覆う。
  診断上限停止の前にもフロー帰属を済ませ、途中結果の整合性を保持する。
- flows配列と端末の方向別フロー表・flags表示を統合。
  現行Schema=0.3.0、phase="2a"、config版0.3、ツール版0.2.0へ更新した。
  DNSはnot_implemented/events=null、時刻逆行の警告は維持する。
- baseline.flows.jsonを初期の手計算フロー値・Phase 1の手定義基本値から定義。
  解析出力から期待値を生成せず、旧expectedファイルも変更せず保持した。
- README、仕様、fixture設計補足、AGENTSを更新。固定依存版は変更なし。
  同じ専用venvへローカルeditable再導入し、ツール版を更新した。

## テストと確認結果

全153テスト成功（既存100件 + フロー53件）。既存テストは元の指標・分類・入力制限・
時刻の検証を保持し、Schema版/flows型の変更に関係する期待と整合性検査のみ更新した。

- 基本fixtureの全体JSONを手定義期待値と一致確認。4フロー、10件、680 captured/original bytes。
  TCP両方向各2件/108 bytes、flags S=2/A=3、未帰属0。
  全方向680+未帰属0=全体680 bytes、件数10+0=10、元長680+0=680。
- IP数値順（192.0.2.2と192.0.2.10）、逆方向から始まる記録、同一IP/port、
  port/IP/protocol違いの分離、UDP双方向、全9 flagsとSYN+ACKを検証。
- 完全ヘッダーのtruncated（TCP/options/UDP）、wirelenだけが長い記録、不正時刻だけのmalformedの帰属を検証。
  ヘッダー/option領域不足、offset/UDP長矛盾、caplen>wirelen/snaplen、fragment/ARPの除外を確認。
- 混在例で方向別captured108+未帰属82=全体190 bytes、
  original118+96=214 bytes、方向件数2+未帰属2=全体4件、根拠番号の分配を確認。
- max_flowsを2本へ縮小し、3本目で停止すること、2本ちょうど/既存キー追加が成功することを確認。
  超過パケットも含む3件162 bytes=方向108+未帰属54、4件目をScapyで読まないことを確認。
  既定値・設定最大値100000と、それを超える設定の拒否を検証した。
  10万本を実際に保持する負荷試験は未実施。
- 診断上限停止、破損レコード、従来の読込/件数上限時もSchemaと件数・bytes・根拠の整合を確認。
- 同じ入力のJSON再実行一致、flow配列順、端末表、CLI上限/終了コード、Schema必須項目とUDP flags制約を確認。
- 既存の14保存fixtureは変更せず再利用。新規条件はテスト内の固定bytes/structから一時合成。
  既存のsocket生成禁止下解析テストも成功。ライブ捕捉・ネットワーク送信・スキャンは行っていない。

## 残る範囲と次の手順

DNS本文・圧縮名検査、byte列の名前正規化、問い合わせ/応答照合は未実装。
既存のpcapng/IPv6/VLAN等の未対応、チェックサム・option意味検証、TCP接続分割/再送推定の除外も維持。
TShark比較、性能・大規模フロー負荷、実通信、VS Code GUIは未検証。

Phase 2後半は①DNS境界/圧縮名検査とASCIIのみの正規化、②端点・ID・質問の照合キー、
③5秒窓とquery重複/完了後再利用、④追加/孤立応答・記録終端/時刻逆行、
⑤RCODE/TC・根拠・Schemaと手計算期待値の照合、の順で進める。
今回の成果は差分とテストを確認してfeat/pcap-analyzerだけへcommit・pushする。


# 継続単位 — DNS名復号・単一質問抽出

実施日: 2026-11-17（Asia/Tokyo）。開始HEAD=ac1d3ae、担当branch=feat/pcap-analyzer、
worktree=/home/kenshin/work/Claude_practice-261119、初期Git statusはclean。

## 現状整理と選択

ルート/261119のAGENTS、261014計画、README、仕様・Schema、作業記録、既存コード・テストを確認。
GitHub CLIでDraft PR #3を確認した。PR本文はPhase 1/100テスト時点で止まり、
双方向フローを未完了としていた。既存153テストを再実行し全件成功した。

完了済みは入力検査・基本集計・双方向フロー、未完了はDNS本文検査/照合とPhase 3/4。
照合は名前の安全な復号・正規化に依存するため、今回の最優先実装単位を
「DNS名と単一質問抽出の内部API」に限定した。回答領域、packet/CLI統合、窓処理は行わない。
古いAGENTSのPhase 2前半限定を継続作業用ルールへ更新し、既存Draft PR更新を許可する内容に合わせた。

## 実装と結果

- dns.py: DNSName/DNSQuestion/DNSDecodeError、decode_name/parse_questionを追加。
- 生bytesのラベル境界を保ち、終端rootを1つ保持。ASCII A-Zのみ小文字化。
  ドット/バックスラッシュ/制御文字/非ASCIIは表示時に10進エスケープし、キーは表示から再構築しない。
- 後方圧縮ポインター、符号化名の直後offset、ID/QR/RCODE/TC/QTYPE/QCLASSを保持。
  RCODE/TCを成功や応答時間へ変換しない。
- 名前255 bytes、メッセージ65507 bytes、1名256ステップに制限。
  不足/範囲外/ヘッダー内/前方参照/循環を拒否し、未対応形式と構造不正を例外で区別。
- 新規40テスト成功。最大長境界、圧縮/連鎖/循環、各不足、質問数/opcode、
  byte正規化・型/class差、root/エスケープ、処理上限と既存人工DNS payloadを確認。
- 既存153件を含め全193テスト成功。既存テストは変更していない。
- CLI、JSON Schema 0.3.0、ツール版0.2.0、依存版、保存fixture/期待値は変更なし。
  DNS出力は引き続きnot_implemented/events=null。ライブ捕捉・送信・スキャン・外部照会は未実施。
- README/仕様へAPI境界と次段階を追記。差分・文書リンク・秘密情報・生成物を確認する。

## 未完了と次の単位

質問抽出の成功はDNS本文全体の正常性を意味しない。回答/authority/additional件数、
RR境界/RDLENGTH/RDATA、ポインターが既知ラベル境界を指すことの証明は未検証・未実装。
これらを次のレビュー単位として追加し、その後にpacket分類/CLIへ接続する。
完全照合キー、5秒窓/再利用/重複・追加応答、終端/時刻逆行、DNSレポートはその後。
TShark独立比較・性能/負荷計測・デモも未実施。今回の範囲から先へは進めない。

成果は担当branchへcommit・pushし、Draft PR #3の完了/今後の予定とテスト数を実績へ更新する。


# 継続単位 — DNSのRR構造と圧縮参照先検証

実施日: 2026-11-17（Asia/Tokyo）。開始HEAD=82ec2ad、cwd=/home/kenshin/work/Claude_practice-261119、
branch=feat/pcap-analyzer、初期statusはclean。

ルート/担当AGENTS、261014計画、README/work_log、仕様、既存コード・テストとDraft PR #3を確認。
AGENTSは継続利用可能な内容のため変更なし。既存193テストを再実行し全件成功した。
名前・質問抽出まで完了しており、次の依存部分であるRR構造/参照先検証を1単位として選択。
CLI統合・DNS照合は今回実施せず、次の作業へ残した。

## 実装

- dns.pyへparse_messageとDNSMessage/DNSResourceRecordを追加。
  answer/authority/additionalの宣言件数、名前、固定10 bytes、RDLENGTHを検査し、
  raw RDATAと開始/終了offsetを保持する。全セクション終了後の余分なbytesも拒否する。
- メッセージ文脈では検証済みのラベル先頭/root/ポインター位置だけを圧縮参照先として許可。
  ラベル内部・固定数値・不透明RDATAへの未検証参照はunsupported。
  従来のdecode_name/parse_question単独APIの契約は維持する。
- NS/CNAME/PTRの名前、MXのpreference+名前、SOAの名前2つ+20 bytesをwire形状として検査。
  名前の符号化bytesがRDLENGTHを跨がないよう制限し、それらの名前境界も参照先へ登録。
  他RDATAは不透明bytes。Aのアドレス長やEDNS等、全RRの意味的妥当性は保証しない。
- 既存のメッセージ/各名上限に加え、全3領域合計1024 RR・名前復号計16384ステップに制限。
  上限超過はunsupported、構造不足/矛盾はmalformedのDNSDecodeErrorを返す。
  TC/RCODEは境界検査を緩めず保持するだけで、DNS成功/所要時間を判定しない。

## 検証

新規42件を追加し、全235テスト成功（既存193件は変更せず維持）。
3領域、圧縮A例、複数RR、名前を含む5種のRDATA、pointer/rootの再参照、
各領域のヘッダー/RDATA不足、RDLENGTH内境界、未検証参照先、余剰bytes、
TC/NXDOMAIN、処理上限と既存人工DNS記録を検証した。
追加テスト初回に処理上限の期待値を5ステップと数え誤ったため、
質問3 + pointer/追跡先4 = 7ステップへ手計算を訂正し、上限ちょうどと1少ない値で再検証した。
既存CLI/Schema/ツール版/依存/fixture/期待値/packet分類/フロー帰属/時刻精度は変更なし。
READMEと仕様へ実装範囲・固定上限・未検証の意味検査・次の単位を追記した。
ライブ捕捉・送信・スキャン・外部照会は行わず、GitHubは担当PRの確認/更新と保存だけに使用。

## 次の作業と残課題

次の1単位は非fragment・非truncatedのUDP port53候補へのparse_message接続と、
DNS構造不正/未対応のpacket分類への反映（フロー帰属と分類の優先順位を維持）。
その後に完全照合キー、5秒窓・重複/追加/孤立応答、終端/時刻逆行、DNSレポートへ進む。
未知RDATAの意味検査、TShark独立比較、性能/負荷、デモは未実施。
差分・秘密情報・生成物を確認して担当branchへcommit・pushし、PR #3の進捗を更新する。


# 継続単位 — DNS構造検査のpacket分類・CLI診断への統合

実施日: 2026-11-17（Asia/Tokyo）。開始HEAD=26ee33c、
cwd=/home/kenshin/work/Claude_practice-261119、branch=feat/pcap-analyzer、初期statusはclean。
ルート/担当AGENTS、261014計画、README/work_log、仕様/Schema、既存コード・テスト、Draft PR #3を確認。
AGENTSは現状でも継続利用可能で変更なし。既存235テストは実装前に全件成功（11.80秒）。

## 選択と実装

入力・基本集計・フローとDNS内部構造検査は完了済み。照合キー/窓/イベント・Phase 3/4は未完了。
内部検査と照合処理の依存境界となるpacket分類/CLI統合を1つの実装単位として選択した。

- packet.py: 非fragment IPv4 UDPのsrc/dstいずれかport53、既存の長さ/時刻/ヘッダーに問題がなく、
  捕捉不足なしの候補にparse_messageを接続。UDPの宣言範囲を使いEthernet paddingは除く。
- DNS構造不足/矛盾をmalformed、未対応opcode/質問数/参照先・DNS処理上限をunsupportedへ反映。
  生payloadや入力パスを診断へ入れず、既存diagnosticsにreason/根拠番号を出す。
- DNS本文不正でも検証済みのflow_eligibleは維持。既存の分類優先順位・入力上限・整数nsは維持。
  truncated/不正時刻/fragment/記録長矛盾等はDNS構造検査へ渡さない。
- report.py: 端末へDNS構造検査の適用範囲を表示。既存の分類件数/根拠/診断がJSONにも反映される。
  Schemaへ分類範囲とdns.statusが照合状態であることの説明を追加。JSON形状/版番号は維持。
  DNS結果保持、照合キー、応答照合、窓処理、イベント出力はこの単位に追加しない。

## 検証結果

新規36件を追加し、全271テスト成功（24.19秒）。
両方向/両port53、TC/NXDOMAIN、圧縮RRと全3領域、空DNS・名前/RR/RDATA不足、
opcode/質問数/未対応label/参照先/1024 RR上限、非候補の検査除外、paddingの切り離しを検証。
混合3レコードはparsed/malformed/unsupported各1、捕捉/元長167 bytes=61+45+61、
同一フロー帰属167 bytes+未帰属0 bytes=全167 bytes、根拠[1,2,3]で一致。
診断上限停止時も上記の件数/長さ分配を維持し、complete=false/終了2を確認。
通常DNS不正のCLI終了0・安全な診断・同一入力JSON決定性・Schema/参照整合も確認。

実装直後の既存テストで空port53 UDPをparsedとする旧期待1件が失敗したため、
今回の仕様変更どおりmalformedへ更新。その他の既存234件は変更せず成功。
既存14 fixtureと手定義期待値・依存lock・PacketRecordは変更なし。
Python 3.12.3、pip checkはNo broken requirements found（環境のpip cache権限警告のみ、OS変更なし）。
README/仕様を更新。全差分・秘密情報・生成物を確認し、担当branchへcommit・push、Draft PR進捗を更新する。
ライブ捕捉・送信・スキャン・外部解析照会は行わない。

## 残課題と次の単位

次は検証済み質問のラベルbytes/QTYPE/QCLASS・IDとclient/server端点からの照合キー・QR/port向き判定。
その後に5秒窓/重複・再利用/追加・孤立応答、終端/時刻逆行、RCODE/TCと根拠のイベント/Schemaを実装する。
現在dns.status=not_implemented/events=nullは照合未実装を表す。構造検査成功を名前解決成功と呼ばない。
未知RDATA等の全意味検査、TShark独立比較、性能/大規模負荷、デモ、VS Code GUIは未検証。
今回の単位を完了後、これらの先の実装には進まない。
