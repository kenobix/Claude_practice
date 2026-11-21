# 261118 の開発指示

上位の `../AGENTS.md` と `../261014/plan_database_engine.md` を読む。

- 作業ブランチは `feat/database-engine`。mainや他worktreeを変更しない。
- 各依頼では計画・README・work_log・既存Draft PRを確認し、最優先の未完了項目からレビュー可能な実装単位を1つ選ぶ。
- 一度に全計画を進めず、その単位の実装・正常系/異常系・既存テスト・記録更新までを完了する。未検証事項を成功扱いしない。
- ユーザーの最新指示に従い、確認済みの節目でcommitし、feat/database-engineへpushする。mainへのpush・マージ、force pushは禁止。
- 対応する既存Draft PRの進捗が古ければGitHub CLIで実績に合わせて更新し、Draft状態を維持する。
- 変更範囲はこのディレクトリ以下。前回のルートREADME追加行は取り消し、一覧更新は統合時に行う。
- 標準ライブラリを優先。SQLite等をエンジンの代わりに使用しない。
- 形式と復旧規則を変えるときは先にdocsを更新し、既存fixtureへの影響を示す。
- テストDBは必ず `t.TempDir()` 以下。CLI手動試験は `mktemp -d` 以下。既存データを流用・削除しない。
- バイナリ、通常DB、キャッシュ、計測生成物はGitに残さない。小さな公開用fixtureだけ残す。
- OS全体へのインストール、秘密情報の保存、実測のない性能・耐久性の断定をしない。
- 検証はmodule内で `make check`。Goが使えなければ未実行を明記する。
- 完了時にREADMEとwork_logへ観測結果・未検証事項を記録する。
