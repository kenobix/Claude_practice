# CI runner 開発指示

- 対象計画は `../261014/plan_ci_runner.md`。現在は環境整備とP0設計準備。
- 変更は原則この日付ディレクトリ内。ルートの共通CI・AGENTS.md、他worktreeは変更しない。
- 今回の依頼では261117/の変更をfeat/ci-runnerへcommit・pushする。mainへのpush・マージ、PR作成はしない。
- Pythonは `ci_runner/.venv` に分離し、依存変更時はrequirements.lockを更新する。
- 任意コード・fixtureをホストで実行しない。Workerとジョブの隔離境界を守る。
- Docker権限やOS設定を無断変更しない。制限の未検証を成功と書かない。
- テスト結果はunit/APIとPostgreSQL/Docker統合を区別してwork_log.mdへ記録する。
- P0/P1/P2の設計と実装済みの機能を区別する。認証情報はGitに含めない。
