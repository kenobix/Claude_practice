# 自作KVエンジン: 段階1（メモリ版とレコード処理）

作業日: 2026-11-17（JST）。ディレクトリ名は指定の `261118` を使用。
[元計画](../261014/plan_database_engine.md)に基づく学習プロジェクト。
メモリKV、JSON Lines CLI、バイナリレコード処理と実テストを実装済み。
**メモリ版は終了すると全データを失う。永続DBは未完成。** 保存、fsync、復旧、OSロックは未実装。

## 作業環境

- リポジトリ: `kenobix/Claude_practice` / `origin`: `git@github.com:kenobix/Claude_practice.git`
- 独立worktree: `/home/kenshin/work/Claude_practice-261118`（ユーザー訂正後のパス）
- ブランチ: `feat/database-engine`。開始時HEAD: `b4c460a`、未コミット変更なし。
- Ubuntu 24.04.4 LTS / WSL2 / x86_64、kernel `6.6.87.2-microsoft-standard-WSL2`
- 保存先: `/dev/sdd` 上の ext4（`rw,relatime,discard,data=ordered`。観測値であり電源断耐性の証明ではない）
- システムのGo/gofmt: 未導入。SHA-256確認済みGo 1.27.4を `261118/.tools/go1.27.4/go` に配置（Git管理外）。
- Git、GNU Make、curl、Python 3、VS Code Remote CLIは利用可能。
- Go module: `github.com/kenobix/Claude_practice/261118/kv-engine`、Go 1.27.4で固定。外部依存なし。

## 開発手順

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
sh scripts/setup-go.sh          # 公式Goをworktree内へ導入。既存導入は上書きしない
./scripts/go-env.sh go version  # go1.27.4 linux/amd64
make check                     # gofmt検査、vet、実テスト、build
make fmt                       # フォーマット適用
./bin/kv -version
```

標準ライブラリのみ。sudo、システムPATH変更、Go本体のGit追加は不要。
[ユーザー領域のセットアップと検証コマンド](kv-engine/docs/development.md)を参照。
VS Codeから `code ../kv-engine.code-workspace` で専用workspaceを開く。
Codexはルートと[作業指示](AGENTS.md)を読む前提で作業する。

## メモリCLIの例

```bash
./bin/kv memory <<'EOF'
{"op":"put","key":"k","value_base64":"AP8="}
{"op":"get","key":"k"}
{"op":"put","key":"empty","value_base64":""}
{"op":"get","key":"empty"}
{"op":"delete","key":"k"}
EOF
```

stdout:

```json
{"sequence":1}
{"found":true,"value_base64":"AP8="}
{"sequence":2}
{"found":true,"value_base64":""}
{"sequence":3}
```

`AP8=` はバイナリ `00 ff`。stderrに `memory_only` / `persistent:false` を表示する。
各行を順に処理し、最初のエラーで終了する。存在しないキーのgetはexit 3、空値のgetはexit 0。
入力不正はexit 2、I/O失敗はexit 6、sequence枯渇はexit 7。
プロセスをまたぐ共有はできないため、`kv put --db ...` 等の永続版コマンドはまだ提供しない。

## 構成・仕様

- `kv-engine/cmd/kv/`: メモリ版JSON Lines CLIと終了コードのテスト
- `kv-engine/internal/engine/`: コピー境界・mutex・sequenceを持つメモリKVと参照モデルテスト
- `kv-engine/internal/record/`: ファイルヘッダー・レコードのencode/decode、境界値・破損・fuzzテスト
- `kv-engine/internal/recovery/`: 段階2以降の予約パッケージ
- `kv-engine/tests/`: 公開hex fixtureとバイナリ仕様の一致テスト
- `kv-engine/scripts/`: Goの再現セットアップと局所環境ラッパー
- [API・CLI契約](kv-engine/docs/api.md)
- [ファイル形式・正常／末尾欠落／破損の具体例](kv-engine/docs/file-format.md)
- [耐久性・ロック・復旧](kv-engine/docs/durability.md)
- [障害注入・検証計画](kv-engine/docs/testing.md)
- [テストfixture](kv-engine/tests/README.md) / [作業記録と次の作業](work_log.md)

最初の完成範囲は単一writer、追記ログ、復旧、CLI、定義した障害モデル内の永続性検証。
SQL、B+Tree、MVCC、分散、ネットワークAPI、コンパクションは対象外。
作業ブランチ `feat/database-engine` にcommit・pushする。mainへのpush・マージ、PR作成、公開ページ追加は行わない。
ルートREADMEの一覧更新は統合時に行う。段階1の検証件数と未検証事項は[作業ログ](work_log.md)を参照。
