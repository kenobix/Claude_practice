# Go・VS Codeでの開発手順

## 依存条件

Linux amd64、Go **1.27.4**、標準ライブラリのみ。GNU Makeは任意。
Goのダウンロード以外はネットワーク不要。`go.sum` は外部依存がないため不要。
`go.mod` は手書きで初期化し、Goによるmodule認識を検証する。
バージョンの自動取得を避ける場合は `GOTOOLCHAIN=local` を設定する。
他プロジェクトのgo.mod、依存関係、システムPATHは変更しない。

## Go未導入時の手順（ユーザーが実行する場合）

OS全体のインストールは不要。[公式配布](https://go.dev/dl/)の対象アーカイブとSHA-256を確認する。
今回の固定対象は `go1.27.4.linux-amd64.tar.gz`。
SHA-256: `7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`。

以下は毎回新しい一時領域に展開する例。既存のGoディレクトリへ上書きしない。

```bash
kv_tools=$(mktemp -d /tmp/kv-engine-tools.XXXXXX)
curl --fail --location https://go.dev/dl/go1.27.4.linux-amd64.tar.gz -o "$kv_tools/go.tar.gz"
printf '%s  %s\n' 7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840 "$kv_tools/go.tar.gz" | sha256sum --check -
# SHA-256が一致した場合だけ次へ進む。
tar -C "$kv_tools" -xzf "$kv_tools/go.tar.gz"
export PATH="$kv_tools/go/bin:$PATH"
export GOTOOLCHAIN=local
export GOCACHE="$kv_tools/cache"
export GOPATH="$kv_tools/gopath"
go version
cd ~/work/Claude_practice-261118/261118/kv-engine
make check
```

失敗した取得物・未照合アーカイブは実行しない。一時領域は環境により再起動や清掃で失われる。
恒久利用では利用者が専用のユーザー領域を選び、同様にチェックして導入する。
sudo/apt、シェル起動ファイルの変更は今回実施しない。
[Go公式導入説明](https://go.dev/doc/install)も参照する。

## VS Code / Codex

既存の独立worktreeは `~/work/Claude_practice-261118`、ブランチは `feat/database-engine`。
追加worktreeの作成や既存worktreeの移動は不要。
ルートのAGENTS.mdと261118/AGENTS.mdに従い、作業をこのディレクトリに限定する。

```bash
code ~/work/Claude_practice-261118/261118/kv-engine.code-workspace
```

`kv: check` / `kv: fmt` / `kv: test` タスクを用意した。
GoのPATHはタスクを起動するVS Codeプロセスにも必要。
一時PATHをexportしたシェルから専用windowを開くか、ターミナル内でmakeを実行する。
`code` コマンドの存在は確認したが、GUI操作・Codex拡張の有効化状態は未確認。
エディタ拡張のインストールや全体設定は変更していない。

## 検証の意味と生成物

`make check` はfmt-check、vet、test、CLI buildを実行する。
現時点ではtest関数がなく、go test成功はコンパイルできたことだけを意味する。
段階1からdocs/testing.mdに従う動作テストを追加する。
テスト用DBは `t.TempDir()`、手動試験は `mktemp -d`。
`bin/` はGit対象外。任意のDBをソースディレクトリ直下へ作らない。
通常DBを除外するための広い `*.log` 指定は、必要なfixtureを隠すため使用しない。
