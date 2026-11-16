# Go・VS Codeでの開発手順

## 依存条件とユーザー領域のセットアップ

Linux x86_64、Go **1.27.4**、標準ライブラリのみ。外部Go依存とgo.sumはない。
セットアップにsh、curl、tar、sha256sum、mktemp、GNU mvを使う。Makeは任意。
OS全体へのインストール、sudo、シェル起動設定の変更は行わない。

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
sh scripts/setup-go.sh
./scripts/go-env.sh go version
make check
```

[公式配布](https://go.dev/dl/)のGo 1.27.4 linux/amd64をダウンロードし、次のSHA-256と照合する。
`7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`
一致したアーカイブだけを新規stagingディレクトリへ展開し、`261118/.tools/go1.27.4/go`へ配置。
その既存ディレクトリを上書きしない。導入済みならバージョンを検証して終了する。
Go本体・キャッシュ・GOPATHは `261118/.tools/` 以下、Git対象外。
前回の `/tmp` 導入は清掃で消えるため、段階1ではこのworktree内のユーザー所有領域へ切り替えた。
公式サイトへ接続できない環境では取得済みアーカイブを引数に渡せる（同じSHA-256で再照合）。

```bash
sh scripts/setup-go.sh /path/to/go1.27.4.linux-amd64.tar.gz
```

makeは `scripts/go-env.sh` 経由でGo/gofmtを起動し、GOTOOLCHAIN=localと局所キャッシュを設定する。
通常のPATH上のGoには依存せず、Goバージョンの自動ダウンロードも行わない。
`go.mod` のgo行は最小必要版。再現用の具体版はsetupスクリプトで固定する。
[Go公式導入説明](https://go.dev/doc/install)も参照。

## コマンド

```bash
make fmt
make check
./scripts/go-env.sh go test -json -count=1 ./...
./scripts/go-env.sh go test -race ./...  # gcc等のCコンパイラが必要
./scripts/go-env.sh go test ./internal/record -run '^$' -fuzz '^FuzzDecode$' -fuzztime=5s -parallel=2
./scripts/go-env.sh go test ./internal/record -run '^$' -fuzz '^FuzzFileHeader$' -fuzztime=5s -parallel=2
```

Makeなしなら `./scripts/go-env.sh go vet ./...`、`go test ./...`、`go build -o bin/kv ./cmd/kv` を
それぞれラッパー経由で実行する。整形確認は `./scripts/go-env.sh gofmt -l cmd internal tests`。
`make check` はfmt検査、vet、実テスト、CLI buildを実行する。テストの内容は[検証計画](testing.md)を参照。
生成CLIは `bin/kv`、Git対象外。fuzzのキャッシュも.tools内。将来失敗したfuzzの再現入力は精査してfixtureとして保存する。
テストファイルやDBを作る場合は `t.TempDir()`、手動試験のDBは `mktemp -d` 以下。

## VS Code / Codex

```bash
code ~/work/Claude_practice-261118/261118/kv-engine.code-workspace
```

worktreeは `~/work/Claude_practice-261118`、ブランチは `feat/database-engine`。
`kv: check` / `kv: fmt` / `kv: test` タスクは局所toolchainを使うmakeを起動するため恒久PATH変更不要。
rootと261118のAGENTS.mdに従う。VS Code GUI・Codex拡張の有効化状態はCLIから未確認。
エディタ拡張の自動インストールや全体設定は変更していない。
