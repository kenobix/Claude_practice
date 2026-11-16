# CI runner 開発指示

- 対象計画は `../261014/plan_ci_runner.md`。現在は管理下fixtureのP0検証とP1前半の受付APIを実装済み。Worker/Webhook/ジョブ実行は未実装。
- 変更は原則この日付ディレクトリ内。ルートの共通CI・AGENTS.md、他worktreeは変更しない。
- 今回の依頼では261117/の変更をfeat/ci-runnerへcommit・pushする。mainへのpush・マージ、PR作成はしない。
- Pythonは `ci_runner/.venv` に分離し、依存変更時はrequirements.lockを更新する。
- 任意コード・fixtureをホストで実行しない。Workerとジョブの隔離境界を守る。
- Docker権限やOS設定を無断変更しない。制限の未検証を成功と書かない。
- テスト結果はunit/APIとPostgreSQL/Docker統合を区別してwork_log.mdへ記録する。
- P0/P1/P2の設計と実装済みの機能を区別する。認証情報はGitに含めない。
- 隔離検証は `ci_runner/scripts/verify_isolation.py` の固定fixtureだけを使用し、通常pytestではDocker試験をopt-inにする。証拠JSONのソースSHA-256と実行内容を対応させる。
- DBの故障試験は `ci_runner/scripts/verify_db.py` の使い捨て専用DBだけで実施する。CI_TEST_DISPOSABLEを共有DBへ設定しない。001 migrationは変更せず後続版を追加する。
