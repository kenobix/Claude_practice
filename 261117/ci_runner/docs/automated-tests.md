# GitHub Actionsによる最小自動テスト

対象は[ci-runner-tests.yml](../../../.github/workflows/ci-runner-tests.yml)。
P1全体の完了判定とは分け、既存の通常テストを継続的に実行する。

## 起動条件と実行境界

- pushはfeat/ci-runner・main、pull_requestはブランチ制限なし。いずれも261117/**または担当workflowの変更時だけ起動する。Draft PRも対象。
- Ubuntu 24.04のGitHub-hosted runner、Python 3.12系の最新提供patch、1ジョブ、上限10分。Pythonのpatch版は実行ログで確認する。
- GITHUB_TOKENはcontents: readのみ。checkoutは認証情報を保持せず、公式Actionを40桁commit SHAへ固定する。setup-pythonも同様。
- pushとPRの系列ごとに古い実行を取り消す。秘密情報・Environment・外部サービス設定・サービスコンテナ・特権設定は不要。
- pull_request_targetやself-hosted runnerは使用しない。受理したrepositoryのコードやジョブfixtureをホストで実行する経路は追加しない。
- 固定requirements.lockから導入後、build isolationなし・追加依存なしでeditable install。pip check、通常pytest、compileallを順に実行し、途中の非0終了を失敗にする。

## テストの範囲

```bash
# ci_runner/で、READMEの手順によりvenvと依存を用意してから
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q --strict-markers -m "not integration and not docker"
.venv/bin/python -m compileall -q src tests scripts
```

導入時は82件。ヘルスチェック、HTTP入力・UUID・ページング・body 4096/4097バイト境界、
コンテナ設定の正常照合と権限/mount等の拒否、実行権拒否、結果の取り違え拒否などを検証する。
外部境界は既存のmock/injectionを使用する。DB・Dockerの実動作が成功した証拠にはしない。
integration/dockerの88件はdeselectedであり、実行済みや成功件数へ加算しない。
テスト件数を増やすためのテストや、workflowの文面を複写するテストは追加しない。

専用DB・Dockerの統合/故障/隔離試験は、引き続きREADMEのverify_db.py、verify_execution.py、
verify_preparation.py、verify_isolation.pyで明示的に実行する。新規の外部接続テストには適切なmarkerとopt-inが必要。
このCIはDocker隔離の検証や、Worker・受理commit実行・ログ永続化の実装を代替しない。
必須チェックへの設定変更、既存workflowの変更は今回の対象外。

## 検証記録と参照

ローカルのコマンド結果は[作業記録](../../work_log.md)を参照。GitHub側の実行・結論は
[担当workflowの実行一覧](https://github.com/kenobix/Claude_practice/actions/workflows/ci-runner-tests.yml)で対象SHAと対応させて確認する。
ローカル成功だけでGitHub-hosted runnerの成功を主張しない。

権限・SHA固定の根拠は[GitHub Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)、
path filter・concurrency等は[公式workflow構文](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)を参照。
