# KVエンジン専用CI

[workflow](../../../.github/workflows/kv-engine.yml)は、担当ブランチとmainへのpush、main向けpull_requestで、
`261118/**`またはworkflow自身が変更された場合に実行する。Draft PRも対象。
他プロジェクトだけの変更では起動しない。pushとPRの両イベントがある場合は各々実行する。
この設定はmainへのpushやマージを許可するものではない。

## 構成と安全条件

- GitHub提供の`ubuntu-24.04` runner、1 job、15分上限。同じworkflow/refの旧実行はキャンセルする。
- 権限は`contents: read`のみ。checkoutは公式v7.0.1のcommit SHA固定、`persist-credentials: false`。
- `pull_request_target`、自前runner、sudo、追加secret、サービス、成果物アップロードは使用しない。
- 既存`setup-go.sh`を使い、公式Go 1.27.4 linux/amd64をSHA-256照合後workspace内へ導入する。
  OSのGoは変更せず、アーカイブとバイナリをGitへ含めない。新規runnerでは公式配布への接続が必要。
- 標準ライブラリだけを使うため`GOPROXY=off`、`GOSUMDB=off`、`GOTOOLCHAIN=local`。
  外部Go依存やtoolchain自動取得を必要とする変更は、そのまま通過させない。
- `CGO_ENABLED=1`とgccの存在を確認してrace detectorを実行する。複数OS/Go版のmatrixとcacheは設けない。
- テストDBは既存テストどおり`t.TempDir()`。実データ、他worktreeを使用しない。

## 実行内容と結果の確認

moduleの作業ディレクトリは`261118/kv-engine`。

```sh
sh scripts/setup-go.sh
./scripts/go-env.sh go version
command -v gcc
make check
./scripts/go-env.sh go test -race -count=1 -timeout=3m ./...
```

`make check`は整形検査、vet、全テスト、CLI buildを順に実行する。
いずれかが失敗すればjobは失敗し、後続stepは実行しない。
raceテストはキャッシュを使わず、各パッケージに3分の期限を設定する。
新しいfuzz探索は行わず、通常テスト内の既存seedを実行する。

ローカルでは同じコマンドの成功に加え、actionlint 1.7.12でworkflowを検証した。
一時コピーの未知runnerが拒否されること、未整形Goコードでfmt-checkが失敗し、整形後に成功することも確認。
ツールと負例は`/tmp`だけに配置し、検証専用の依存やテストを製品moduleへ追加していない。

リモート実行はローカル検証と区別し、push後に担当commitのrun/checkを確認する。

```sh
gh run list --workflow kv-engine.yml --branch feat/database-engine
gh run view RUN_ID
gh pr checks 2
```

コミット作成時点ではリモートrunは未実行。push後の実結果はDraft PR #2の進捗・Checksと完了報告で示す。
workflow追加だけではブランチ保護の必須checkにはならず、リポジトリ設定は変更しない。
GitHub上での成功も定義済みプロセス障害モデル内の結果であり、電源断や装置flushの証明ではない。
段階3の製品CLI応答周辺試験、段階4の性能計測、段階5のデモは引き続き未完了。
