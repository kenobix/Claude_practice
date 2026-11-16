# 261118 の開発指示

上位の `../AGENTS.md` と `../261014/plan_database_engine.md` を読む。

- 作業ブランチは `feat/database-engine`。mainや他worktreeを変更しない。
- 現在の依頼は段階1（メモリKV・レコード処理・CLIと実テスト）。永続化・復旧は段階2以降。
- ユーザーの最新指示に従い、段階0保存と段階1完了を別commitにし、feat/database-engineへpushする。mainへのpush・マージは禁止。PR作成は依頼なし。
- 変更範囲はこのディレクトリ以下。前回のルートREADME追加行は取り消し、一覧更新は統合時に行う。
- 標準ライブラリを優先。SQLite等をエンジンの代わりに使用しない。
- 形式と復旧規則を変えるときは先にdocsを更新し、既存fixtureへの影響を示す。
- テストDBは必ず `t.TempDir()` 以下。CLI手動試験は `mktemp -d` 以下。既存データを流用・削除しない。
- バイナリ、通常DB、キャッシュ、計測生成物はGitに残さない。小さな公開用fixtureだけ残す。
- OS全体へのインストール、秘密情報の保存、実測のない性能・耐久性の断定をしない。
- 検証はmodule内で `make check`。Goが使えなければ未実行を明記する。
- 完了時にREADMEとwork_logへ観測結果・未検証事項を記録する。
