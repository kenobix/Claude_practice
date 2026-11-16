# 永続KVエンジン: 開発基盤と段階0

作業日: 2026-11-17（JST）。ディレクトリ名は指定の `261118` を使用。
[元計画](../261014/plan_database_engine.md)に基づく学習プロジェクト。
現在は仕様とビルド用の骨組みのみ。PUT/GET/DELETE、保存、復旧、ロックは未実装。

## 作業環境

- リポジトリ: `kenobix/Claude_practice` / `origin`: `git@github.com:kenobix/Claude_practice.git`
- 独立worktree: `/home/kenshin/work/Claude_practice-261118`（ユーザー訂正後のパス）
- ブランチ: `feat/database-engine`。開始時HEAD: `b4c460a`、未コミット変更なし。
- Ubuntu 24.04.4 LTS / WSL2 / x86_64、kernel `6.6.87.2-microsoft-standard-WSL2`
- 保存先: `/dev/sdd` 上の ext4（`rw,relatime,discard,data=ordered`。観測値であり電源断耐性の証明ではない）
- システムのGo/gofmt: 未導入。検証用に公式Go 1.27.4 linux/amd64を `/tmp` に展開。
- Git、GNU Make、curl、Python 3、VS Code Remote CLIは利用可能。
- Go module: `github.com/kenobix/Claude_practice/261118/kv-engine`、Go 1.27.4で固定。外部依存なし。

## 開発手順

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
go version                 # go1.27.4 linux/amd64
make check                 # gofmt差分の検査、vet、test、build
make fmt                   # フォーマットを適用
make build                 # bin/kv（Git管理外）
go run ./cmd/kv -version    # 段階0の識別表示
```

`make`を使わない場合:

```bash
gofmt -l cmd internal
go vet ./...
go test ./...
go build -o bin/kv ./cmd/kv
```

Goがない場合は[環境構築手順](kv-engine/docs/development.md)でユーザー領域への導入方法を確認する。
一時導入はセッション限定で、通常のターミナルにはGoを追加しない。
VS Codeから `code ../kv-engine.code-workspace` で専用workspaceを開ける。
タスクはmodule内で実行する。拡張機能の自動導入・設定変更は行わない。
Codexはルートと[作業指示](AGENTS.md)を読む前提で作業する。

## 構成・仕様

- `kv-engine/cmd/kv/`: ビルド可能な識別表示CLI
- `kv-engine/internal/{record,engine,recovery}/`: 責務を宣言した空のパッケージ
- [API・CLI契約](kv-engine/docs/api.md)
- [ファイル形式・正常／末尾欠落／破損の具体例](kv-engine/docs/file-format.md)
- [耐久性・ロック・復旧](kv-engine/docs/durability.md)
- [障害注入・検証計画](kv-engine/docs/testing.md)
- [テストfixture](kv-engine/tests/README.md) / [作業記録と次の作業](work_log.md)

最初の完成範囲は単一writer、追記ログ、復旧、CLI、定義した障害モデル内の永続性検証。
SQL、B+Tree、MVCC、分散、ネットワークAPI、コンパクションは対象外。
今回は公開ページを追加せず、commit・push・PR作成もしない。
