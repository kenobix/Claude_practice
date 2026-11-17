# CI runner 開発指示

- 対象計画は `../261014/plan_ci_runner.md`。現在はP0・受付API・Worker初回取得・実行前DB preflightまで実装済み。次の単位は進捗と依存関係を確認して選ぶ。ジョブ実行・Webhook・復旧等は完了証拠と区別する。
- 変更は原則この日付ディレクトリ内。ルートの共通CI・AGENTS.md、他worktreeは変更しない。
- 今回の依頼では261117/の変更をfeat/ci-runnerへcommit・pushする。mainへのpush・マージ、force pushはしない。対応する既存Draft PRの進捗はユーザー指示に従って更新し、Draftを維持する。
- Pythonは `ci_runner/.venv` に分離し、依存変更時はrequirements.lockを更新する。
- 任意コード・fixtureをホストで実行しない。Workerとジョブの隔離境界を守る。
- Docker権限やOS設定を無断変更しない。制限の未検証を成功と書かない。
- テスト結果はunit/APIとPostgreSQL/Docker統合を区別してwork_log.mdへ記録する。
- P0/P1/P2の設計と実装済みの機能を区別する。認証情報はGitに含めない。
- 隔離検証は `ci_runner/scripts/verify_isolation.py` の固定fixtureだけを使用し、通常pytestではDocker試験をopt-inにする。証拠JSONのソースSHA-256と実行内容を対応させる。
- DBの故障試験は `ci_runner/scripts/verify_db.py` の使い捨て専用DBだけで実施する。CI_TEST_DISPOSABLEを共有DBへ設定しない。001 migrationは変更せず後続版を追加する。
- 一度の継続作業ではレビュー可能な未完了単位を1つ実装・検証して区切る。Workerのclaim-onceは初回取得だけで、実行や自動復旧を行わない点を維持する。
