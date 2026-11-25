# 共同編集アプリの作業指示

ルートの [AGENTS.md](../AGENTS.md) と依頼者の指示を適用する。仕様は [設計](docs/design.md)、達成状況は [作業ログ](work_log.md) で確認する。

- `feat/collaborative-editor` のworktreeで `261125/` を担当する。ルートREADMEへの変更はこの作業の一覧項目に限る。
- 検証した単位で作業ブランチにコミットし、同名のリモートブランチへpushする。レビューにはDraft PRを使う。`main` への直接pushとマージは禁止する。
- 日本語文書には [yomiyasu](../.agents/skills/yomiyasu/SKILL.md) を使う。検査記録は一時領域に置き、仕様や試験結果との照合も行う。 [日本語技術文書の書き方](../docs/engineering/japanese-writing.md)
- Yjsの更新受理、ローカル保存、サーバー保存確認を別の条件で判定する。権限・保存・再送の順序を変更する場合は、対応する受入条件と理由を記録する。 [受入条件](docs/acceptance.md)
- デモには架空のアカウントと文書を使う。認証情報、DB、端末の保存データ、依存の展開先をコミット対象から外す。
