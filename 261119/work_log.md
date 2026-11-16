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
