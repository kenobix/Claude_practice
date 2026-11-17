# Phase 3 前半: TSharkとの共通フィールド比較

Phase 2の人工入力に対する受入完了後、独立した復号器で共通項目を確認する。
今回の単位はコンテナが完全な8 fixture。Phase 3全体の完了判定はまだ行わない。
解析器の対応形式・分類・DNS窓規則・Schemaは変更しない。

## 実行環境と再現

Ubuntu 24.04 / amd64 / Python 3.12.3。TSharkはUbuntu配布の4.2.2-1.1build3。
OSには未導入。Docker daemonへの接続権限もなかったため、使わなかった。
専用`.venv/tshark/`へ配布debのデータだけを展開した。`apt install`、`dpkg -i`、
maintainer script、sudo、setcap、dumpcap、OS設定変更は実行しない。
Wireshark共通の捕捉プログラムを含む`wireshark-common`は取得・展開しない。
これはUbuntu 24.04 amd64用の開発用比較環境で、汎用配布環境ではない。
Python依存lockや通常のCLI実行にTSharkを追加しない。

[パッケージ一覧](tshark-packages.json)はAPTメタデータの版・取得パス・SHA-256を記録する。
OS上の既存共有ライブラリも利用するため、debだけでOS非依存になるとはしない。
再現時は同じOS/architectureを使い、パッケージが取得できなければ停止する。
以下は`261119/pcap_analyzer/`から実行する。ネットワーク利用はセットアップの取得だけ。

```bash
mkdir -p .venv/tshark/debs .venv/tshark/root
.venv/bin/python - <<'PY'
import hashlib, json, pathlib, subprocess, urllib.request
base = pathlib.Path('.venv/tshark')
for package in json.loads(pathlib.Path('docs/tshark-packages.json').read_text()):
    path = base / 'debs' / pathlib.Path(package['Filename']).name
    if not path.exists():
        url = 'https://archive.ubuntu.com/ubuntu/' + package['Filename']
        with urllib.request.urlopen(url, timeout=300) as source, path.open('wb') as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
    if hashlib.sha256(path.read_bytes()).hexdigest() != package['SHA256']:
        raise SystemExit('SHA-256 mismatch: ' + path.name)
    subprocess.run(['dpkg-deb', '--extract', str(path), str(base / 'root')], check=True)
PY
.venv/bin/python scripts/compare_tshark.py > /tmp/pcap-tshark-comparison.json
```

低速・中断で不完全なdebが残った場合は、該当する`.venv/tshark/debs/`内のファイルだけを
再取得してSHA-256を確認する。ミラーを使う場合もSHA-256の一致を必須とする。
取得物と展開物はGit除外済みの`.venv`内のみ。実行結果は文書用の人工fixture情報だけを含む。

## 固定コマンドと比較条件

[比較スクリプト](../scripts/compare_tshark.py)が固定fixtureだけを選び、必ず`-r`を付ける。
任意の通信ファイルやライブinterfaceを入力するオプションは設けない。
[TShark公式マニュアル](https://www.wireshark.org/docs/man-pages/tshark.html)に従い、
名前解決を`-n`で無効化、`-T fields`と`-e`で対象フィールドを抽出する。
capture/display filterはどちらも未指定、1 passで、再構成を行わない。

```text
<root>/usr/bin/tshark -n -r <fixture> -T fields -E occurrence=f
  -o ip.defragment:FALSE -o tcp.desegment_tcp_streams:FALSE
  -o tcp.analyze_sequence_numbers:FALSE
  -e frame.number -e frame.time_epoch -e frame.cap_len -e frame.len
  -e ip.src -e ip.dst -e ip.proto
  -e tcp.srcport -e tcp.dstport -e udp.srcport -e udp.dstport -e tcp.flags
```

`WIRESHARK_CONFIG_DIR`は毎回作る空の一時ディレクトリ、`WIRESHARK_DATA_DIR`は展開した
`usr/share/wireshark`、`LD_LIBRARY_PATH`は展開した`usr/lib/x86_64-linux-gnu`。
plugin/extcapのディレクトリも空の一時ディレクトリへ指定し、継承した`WIRESHARK_*`設定を除く。
個人のWireshark profileは使わない。`LC_ALL=C`、`TZ=UTC`。実際のTShark版全文、固定設定、
各入力SHA-256、抽出値、未比較理由、差分を結果JSONに記録する。
終了0は比較一致、1は差分、2は環境/実行/抽出異常。欠落や実行失敗を成功扱いしない。

## 実測結果（2026-11-17）

[実行結果JSON](tshark_comparison.result.json)へ全抽出値と比較結果を保存した。
8 fixture / 17 records、比較対象の全フィールドに差分なし、全実行stderr空、終了0。
captured合計1080 bytes、original合計1094 bytes。後者との差14 bytesはcapture_truncatedの不足。

| fixture | records | captured | original | L4比較除外 |
|---|---:|---:|---:|---|
| baseline | 10 | 680 | 680 | なし |
| header_only | 0 | 0 | 0 | なし |
| nano | 1 | 54 | 54 | なし |
| big_endian | 1 | 54 | 54 | なし |
| backward | 2 | 160 | 160 | なし |
| capture_truncated | 1 | 40 | 54 | TCPヘッダー不足 |
| fragment | 1 | 50 | 50 | IPv4 fragment |
| unsupported_arp | 1 | 42 | 42 | 非IPv4 |

nanoは1700000000000000123ns、big_endianは1700000000000123000nsで一致。
baselineのSYN+ACKはflags=0x12として両ビットを確認した。
backwardは1700000001000000000→1700000000900000000nsの逆行を保持した。
上記3件のL4除外は仕様に基づく未比較であり、全プロトコル一致の主張ではない。

比較ハーネスは手定義のTShark行で整数精度、異常出力、件数/flags/IP差分検出、
部分TCPの除外範囲、未導入/非0終了/timeoutを20テストで検証。
既存386件を含む全406件成功（28.97秒）、pip check成功。
通常CIはハーネスをテストする。TSharkの取得と実体での8 fixture比較はローカルの明示実行で、
CIが独立比較まで実施したとはしない。CI workflowとPython依存lockは変更なし。

## 比較の定義と限界

- 全8 fixture: 完全レコード数、番号、捕捉長、記録元長、時刻、確認できたIPv4両端とprotocol。
- 時刻: decimal秒を整数演算でns化する。binary floatを経由しない。元µsとnsを混同しない。
- フロー帰属可能なレコード: TCP/UDP portsとTCP flagsのビット値。
- `capture_truncated`: フルTCPヘッダーを確認できずL4比較から除外。IP/長さ/時刻は比較する。
  TSharkが部分ヘッダーからportsを出しても、解析器の集計対象を広げない。
- `fragment`: IPv4情報まで比較。TSharkが先頭fragmentからUDPを解釈しても、解析器はL4対象外。
- `unsupported_arp`: frame情報を比較。IPv4/portsはなし。ARPをparsedへ変更しない。
- `backward`: レコード順の時刻をそのまま比較。TSharkのDNS対応付けを独自窓規則へ流用しない。

8 fixtureはbaseline/header_only/nano/big_endian/backward/capture_truncated/fragment/unsupported_arp。
残る6 fixture（空入力、レコード本文/ヘッダー途切れ、caplen>wirelen、未対応link type、pcapng）は
次の比較単位。独自の4分類とTSharkのexpert情報の一対一一致は要求しない。
DNS ID/質問/RCODE/TC/一意な応答時間の共通部分と、独自状態との相違の確認も残る。
性能・負荷・デモ、実通信、他OS、他Pythonは今回の検証対象ではない。
