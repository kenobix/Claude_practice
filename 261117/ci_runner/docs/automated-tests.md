# GitHub Actionsによる最小自動テスト

対象は[ci-runner-tests.yml](../../../.github/workflows/ci-runner-tests.yml)。
P1全体の完了判定とは分け、既存の通常テストを継続的に実行する。

## 起動条件と実行境界

- pushはfeat/ci-runner・main、pull_requestはブランチ制限なし。いずれも261117/**または担当workflowの変更時だけ起動する。Draft PRも対象。
- Ubuntu 24.04のGitHub-hosted runnerで、提供されているPython 3.12系の最新パッチバージョンを使う。1ジョブの上限は10分。実際のPythonバージョンは実行ログで確認する。
- GITHUB_TOKENはcontents: readのみ。checkoutは認証情報を保持せず、公式Actionを40桁commit SHAへ固定する。setup-pythonも同様。
- pushとPRの系列ごとに古い実行を取り消す。秘密情報・Environment・外部サービス設定・サービスコンテナ・特権設定は不要。
- pull_request_targetやself-hosted runnerは使用しない。受理したrepositoryのコードやジョブfixtureをホストで実行する経路は追加しない。
- requirements.lockで固定した依存関係を導入し、ビルドの隔離や依存関係の追加を行わず、editable installする。続いてpip check、通常のpytest、compileallを順に実行する。途中で終了コードが0以外なら失敗とする。

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
必須チェックの設定はリポジトリ側で管理する。

## P5のクリーン環境検証

[別のworkflow](../../../.github/workflows/ci-runner-clean-fixture.yml)は `feat/ci-runner` へのpushで、`261117/ci_runner/` または同workflowが変わったときに動く。GitHub-hosted Ubuntu 24.04、`contents: read`、10分上限で実行する。
新規venvで固定依存を導入し、専用PostgreSQL・実Dockerで[READMEから辿れる初回検証](p5-validation.md#初回のローカル検証)を1件実行する。checkoutのSHAと証拠を照合し、skipでは成功しない。証拠はrunner内で照合する。
対象は固定fixtureの受付、別Workerでの実行と回収、正式結果の保持。P0の全隔離試験、P2の障害試験、P3の実配送、性能測定は個別に検証する。

## 検証記録と参照

ローカルのコマンド結果は[作業記録](../../work_log.md)を参照。GitHub側の実行・結論は
[担当workflowの実行一覧](https://github.com/kenobix/Claude_practice/actions/workflows/ci-runner-tests.yml)で対象SHAと対応させて確認する。
ローカル成功だけでGitHub-hosted runnerの成功を主張しない。

権限・SHA固定の根拠は[GitHub Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)、
path filter・concurrency等は[公式workflow構文](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)を参照。
