# 保存済みPCAP解析ツール — Phase 1

作業日: 2026-11-17。`261119`は指定された作業ID。
計画: [保存済みPCAP解析ツール](../261014/plan_pcap_analyzer.md)。

classic pcapの入力検査、Ethernet/IPv4/TCP・UDPヘッダー検査、件数・長さ・時刻・分類の集計、
JSONと端末表を実装した。これは通信量集計の中間版であり、計画全体のMVP完成ではない。
フロー集計とDNS本文検査・問い合わせ/応答照合はPhase 2に残している。

## 作業環境

- worktree: `~/work/Claude_practice-261119`、branch: `feat/pcap-analyzer`
- origin: `kenobix/Claude_practice`。mainへのpush・マージとPR作成は行わない。
- Python 3.12.3、専用環境: `pcap_analyzer/.venv`、ツール版0.1.0
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

全100テスト成功（既存15件を維持し、旧CLI未実装テストを現動作に更新、機能テスト85件追加）。
生成した14種類の人工fixture、手計算期待値、4種類のmagic/時刻形式、不正なヘッダー、
分類優先順位、caplen/wirelen、破損レコード、安全上限、Schema、決定性を検証する。
インストールのPyPI接続とGitHub保存以外の処理はオフライン。ライブ捕捉・送信・スキャン・逆引き・外部照会なし。
Scapyの経路/インターフェース自動読込をプロセス内で無効化し、socket生成禁止下でも解析をテストする。
`.venv`、cache、egg-info等はGit対象外。人工PCAPと期待値は再現用成果物として保持する。

## CLI

```bash
# 端末向け表示（既定）
.venv/bin/pcap-analyzer fixtures/baseline.pcap

# 決定的なJSON（schema_version=0.2.0）
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json

# 3件で停止し、complete=falseの途中JSONと終了2を確認
.venv/bin/pcap-analyzer fixtures/baseline.pcap --format json --max-records 3

# 破損した本文：完全レコード0件、E_RECORD_DATA、終了2
.venv/bin/python -m pcap_analyzer fixtures/record_cutoff.pcap --format json
```

基本例の結果は10パケット、捕捉680 bytes、記録上の元長680 bytes、TCP4件、UDP6件、
全件parsed、観測時刻1700000000000000000〜1700000008000000000 ns（幅8秒、元精度µs）。
PCAP自体のサイズ864 bytesにはコンテナヘッダーも含む。
`parsed`はL2/L3/L4ヘッダーまでの検査成功を意味し、DNS等の本文の正常性を意味しない。

JSONの`classification_indices`にファイル順・1始まりの根拠番号を保存する。
`flows=null`、`dns.status=not_implemented`、`dns.events=null`で未実装を明示する。
時刻逆行では警告と`disabled_timestamp_regression`を記録し、並べ替えはしない。

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
| 通常診断 | 1万件（ほかに終了診断1件） | `--max-diagnostics` |

すべて正整数。ちょうど上限は許可。診断上限は当該レコードを計数してから明示停止する。
入力全体のSHA-256を上限確認後に計算し、パケットは逐次処理する。FIFO/デバイスは拒否。
並行変更はサイズ/mtime/ctimeの変化を検出するが、不変スナップショットを保証しない。

## 仕様・期待値・制限

- [現行仕様とPhase 2の設計](pcap_analyzer/docs/spec.md)
- [fixture設計と手計算](pcap_analyzer/docs/fixtures.md) / [fixture別期待値](pcap_analyzer/expected/fixtures.json)
- [現行Schema](pcap_analyzer/docs/report.schema.json) / [Phase 1の基本例期待値](pcap_analyzer/expected/baseline.phase1.json)
- [Phase 0の将来レポート例](pcap_analyzer/expected/baseline.report.json) / [対応する旧Schema](pcap_analyzer/docs/report.phase0.schema.json)
- [作業記録](work_log.md)

未対応: pcapng、圧縮pcap、IPv6、VLAN、Linux cooked capture、Wi-Fi、ライブ捕捉、
IP再構成、TCP再構成/再送推定、TLS復号。IPv4 fragmentは独立計数しL4/DNSから除外する。
ヘッダーの長さ・捕捉境界を検査するがチェックサムや個々のoptionの意味は検証しない。
UDP長はIPv4の宣言payload長との一致を要求する。
プロトコル件数は確認できたIPv4 protocol番号等で識別し、L4のtruncatedでもtcp/udpへ数え得る。
分類の件数とは別の指標で、それぞれの合計が全レコード数と一致する。

フロー、DNS正規化/窓処理/曖昧さの解消、端末のフロー表はPhase 2。
DNS25ms・未応答・重複queryの判定は依然として将来の手計算期待値である。
TShark未導入のため独立比較は未実施。性能測定、実通信、他Python版、VS Code GUIは未検証。
捕捉長と記録上の元長を回線上の実転送量と呼ばず、観測不足を障害や攻撃と断定しない。
