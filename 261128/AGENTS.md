# ファイル同期ソフトの作業指示

- ルートの [AGENTS.md](../AGENTS.md) と [元計画](../261014/plan_file_sync.md) を参照する。実装と試験は `261128/file-sync/` に置く。Phase 0の成果物は設計、固定期待値、人工データと依存の固定まで。
- `feat/file-sync` で作業し、開始点 `e8c730ea392ac77bbe9e88d649c6a90d1e7ef8aa` からの差分を確認する。この案件は担当ブランチへのpushとDraft PRを使う。mainへの直接pushとマージを禁止する。
- Phaseを開始する前に、[データ契約](file-sync/docs/protocol.md)、[状態遷移・復旧](file-sync/docs/design.md)、[受入条件](file-sync/docs/acceptance.md) を読む。固定期待値の見直しには契約変更の理由が要る。実装の出力を期待値として採用しない。
- 検証は人工データと一時ディレクトリで行う。実フォルダ、SQLite DB、blob、実行ログ、認証情報はコミット対象から外す。停止点は同期やコミットの前後を特定して記録する。
- 日本語文書の変更は [yomiyasu](../.agents/skills/yomiyasu/SKILL.md) で検査する。技術上の条件、例外、順序、保証範囲は仕様や試験と照合する。
