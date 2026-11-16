# 保存済みPCAP解析ツール — Phase 0

作業日: 2026-11-17。`261119`は指定された作業IDであり実施日ではない。
計画: [保存済みPCAP解析ツール](../261014/plan_pcap_analyzer.md)。

Phase 0の仕様、手計算期待値、人工fixture、専用Python環境を準備した。
解析エンジンは未実装。CLIはhelp/versionのみ実行でき、入力指定時は明示的に終了コード3を返す。

## 独立した作業環境

- worktree: `~/work/Claude_practice-261119`（指定パスの訂正を反映）
- branch: `feat/pcap-analyzer`、origin: `kenobix/Claude_practice`
- Python: 3.12.3、プロジェクト専用環境: `pcap_analyzer/.venv`
- Scapy 2.6.1 / pytest 8.4.2 / jsonschema 4.25.1
- 他のworktree/main/OS環境は変更しない。commit/push/PR作成なし。

VS Codeではこのworktreeを開いてCodexの作業対象を確認する。
PythonのテストUIを使う場合は `pcap_analyzer/` をフォルダーとして開くと
[設定](pcap_analyzer/.vscode/settings.json)が適用される。
既存のインタープリター選択が優先される場合は `.venv/bin/python` を手動選択する。
VS Code/Codex拡張のGUI操作・テストUI自体は今回の実行環境から検証していない。

## 再現手順

```bash
cd ~/work/Claude_practice-261119
git branch --show-current
git worktree list
git status --short
cd 261119/pcap_analyzer

# 初回のみ。既存.venvを使う場合は作り直す必要なし
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.lock
.venv/bin/python -m pip install --no-cache-dir --no-deps --no-build-isolation -e .

.venv/bin/python -m pip check
.venv/bin/python scripts/generate_fixtures.py
.venv/bin/pcap-analyzer --version
.venv/bin/python -m pcap_analyzer --help
.venv/bin/python -m pytest -q

# 現在は解析未実装の診断。終了コード3が正しい
.venv/bin/pcap-analyzer fixtures/baseline.pcap
```

`requirements.lock`はpipを含め直接・推移依存を固定した開発環境一覧。
ローカルパッケージは別のeditable installで導入するため、絶対パスをlockに含めない。
Python 3.12を対象に確認済み。他のPython版は未検証。
PyPIへの接続が必要なインストール工程以外はネットワーク不要。
`.venv`、キャッシュ、egg-info等はローカル.gitignore対象。
人工PCAPと期待値は再現・レビュー対象の成果物としてGit管理対象に残す。

## 仕様と検証の範囲

- [仕様・計算式・コンポーネント分担・Phase 1順序](pcap_analyzer/docs/spec.md)
- [fixture設計と手計算](pcap_analyzer/docs/fixtures.md)
- [fixture別の期待値](pcap_analyzer/expected/fixtures.json)
- [JSON Schema](pcap_analyzer/docs/report.schema.json)
- [基本例の将来レポート期待値](pcap_analyzer/expected/baseline.report.json)
- [実施記録](work_log.md)

基本fixtureは10パケット、捕捉680 bytes、元長合計680 bytes、PCAP全体864 bytes。
これらはコンテナの独立読込とScapy読込の両方で確認する。
DNS成功25ms、未応答、重複queryの曖昧判定は手計算したPhase 2の期待であり、
解析器による結果ではない。`baseline.report.json`も将来出力の契約例である。
Scapyで生成・読込した共通の誤りは残り得る。TShark未導入のため独立比較は未実施。
時刻・バイト・件数以外の解析、上限停止、異常入力のCLI診断、性能測定は今後実装・検証する。
