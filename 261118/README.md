# 自作KVエンジン: 永続KVと復旧実験

作業日: 2026-11-17（JST）。ディレクトリ名は指定の `261118` を使用。
[元計画](../261014/plan_database_engine.md)に基づく学習プロジェクト。
Linuxの単一writer、同期追記ログ、再起動時の索引再構築、限定的な末尾復旧、永続版CLIを実装。
既存形式v1と公開fixtureの互換性を維持。段階3の初回として、PUT/DELETE×8停止点のSIGKILL復旧試験を実装。
初回作成9点・復旧処理4点のOpen中停止試験と、3 seedの更新停止48ケース・正常終了6ケースも検証。電源断耐性は未検証。
`kv memory`も維持するが、そのデータは終了すると消える。

## 進捗と次の作業

- 段階0〜2: 設計、メモリKV・レコード処理、Linux永続KVと通常再起動の検証が完了。
- 段階3: 限定末尾復旧・I/Oエラー注入と、既存DBの更新中に親子ハンドシェイクで止めるSIGKILL試験を実施。
  [停止点・参照モデル・実験結果](kv-engine/docs/crash-experiments.md)に範囲と再現手順を記録。
- 段階3の次の単位として、初回header/file/dir/parent Syncと復旧Truncate/Syncの停止点を検証済み。
  [Open中の停止条件・実測結果](kv-engine/docs/open-crash-experiments.md)に正常再開・破損拒否・復旧の再実行を記録。
- 複数seed単位: 3 seed × PUT/DELETE × 8停止点と正常終了で、55件の成功履歴＋最後の1操作を照合済み。
  [操作列・復旧境界・観測結果](kv-engine/docs/seeded-crash-experiments.md)を記録。
- 複合I/O単位: 更新ENOSPC/Sync失敗と再Openの読取・同期・復旧失敗を組み合わせた36ケースと正常対照が成功。
  [失敗後の拒否・prefix保持・再試行](kv-engine/docs/compound-io-experiments.md)を記録。
- 次の優先作業: CloseとPut/Get/Delete/Inspectの同時競合の専用検証。
  CLI応答周辺、段階4の計測、段階5のデモ、電源断検証は残課題。段階3全体は未完了。

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

## 永続版CLIの例

```bash
kv_demo_dir=$(mktemp -d)
printf 'hello' | ./bin/kv put --db "$kv_demo_dir" --key greeting --value-file -
./bin/kv get --db "$kv_demo_dir" --key greeting
printf '\n'
./bin/kv inspect --db "$kv_demo_dir"
./bin/kv delete --db "$kv_demo_dir" --key greeting
./bin/kv get --db "$kv_demo_dir" --key greeting   # 不在: exit 3
```

各コマンドは別プロセス。put/deleteは `{"sequence":1}` のようなJSON、getは改行なしの生バイトを返す。
`--value-file FILE` でバイナリファイル、`-` でstdinを読む。空入力も保存できる。
DBディレクトリは事前作成する。新規data.logはLOCK以外にファイルがないディレクトリだけに作成する。
get/inspectはdata.logを新規作成しない。ロック競合はexit 4、破損/不完全な末尾のinspectはexit 5。
通常Openは不完全な最終レコードだけ診断後に切り戻す。**原本を調査する場合は最初にinspectを使う。**
inspectも排他ロックを取り、LOCKファイルを作る場合があるが、data.logは変更しない。
全I/Oテストは専用一時ディレクトリを使用し、普段使うデータは投入しない。

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
このメモリsessionはプロセスをまたいで共有しない。永続版とは別の動作確認用モード。

## 構成・仕様

- `kv-engine/cmd/kv/`: 永続版CLI、メモリ版CLI、実プロセスの保存・終了コードテスト
- `kv-engine/internal/engine/`: メモリKV、Linux永続DB、OSロック、I/O注入、再起動・参照モデルテスト
- `kv-engine/internal/record/`: ファイルヘッダー・レコードのencode/decode、境界値・破損・fuzzテスト
- `kv-engine/internal/recovery/`: 読み取り専用ログ走査、位置索引、破損位置・正常境界の診断
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
ルートREADMEの一覧更新は統合時に行う。最新の検証件数と未検証事項は[作業ログ](work_log.md)を参照。
