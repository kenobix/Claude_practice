# 自作KVエンジン: 永続KVと復旧実験

作業日は2026-11-17（JST）。ディレクトリ名には指定の `261118` を使用した。
[元計画](../261014/plan_database_engine.md)に基づく学習プロジェクトとして、Linuxで動く単一writerの永続KVエンジンとCLIを実装した。
更新はログに追記して同期する。再起動時にログから索引を再構築し、条件を満たす不完全な末尾だけを修復する。
既存のログ形式v1と公開fixtureとの互換性を保った。段階3の初回に、PUTとDELETEのそれぞれ8停止点でSIGKILL後の復旧を調べる試験を実装した。
Open中の初回作成9点と復旧処理4点で停止させる試験に加え、3 seedでの更新停止48ケースと正常終了6ケースも検証した。電源断耐性は未検証。
`kv memory`も維持するが、そのデータは終了すると消える。

## 進捗と次の作業

- 段階0〜2: 設計、メモリKV・レコード処理、Linux永続KVと通常再起動の検証が完了。
- 段階3: 限定末尾復旧・I/Oエラー注入と、既存DBの更新中に親子ハンドシェイクで止めるSIGKILL試験を実施。
  [停止点・参照モデル・実験結果](kv-engine/docs/crash-experiments.md)に範囲と再現手順を記録。
- 段階3の次の単位として、初回header/file/dir/parent Syncと復旧Truncate/Syncの停止点を検証済み。
  [Open中の停止条件・実測結果](kv-engine/docs/open-crash-experiments.md)に正常再開・破損拒否・復旧の再実行を記録。
- 複数seedの試験では3 seed × PUT/DELETE × 8停止点と正常終了を対象に、55件の成功履歴と最後の1操作を照合した。
  詳細は[操作列・復旧境界・観測結果](kv-engine/docs/seeded-crash-experiments.md)に記録した。
- 複合I/O単位: 更新ENOSPC/Sync失敗と再Openの読取・同期・復旧失敗を組み合わせた36ケースと正常対照が成功。
  [失敗後の拒否・prefix保持・再試行](kv-engine/docs/compound-io-experiments.md)を記録。
- Close競合単位: I/O先行・Close先行・全API同時開始の18ケースを検証済み。
  [閉鎖順序・失敗応答・成功履歴の照合](kv-engine/docs/close-concurrency.md)を記録。
- 開発基盤: [専用GitHub Actions](../.github/workflows/kv-engine.yml)で整形・vet・全テスト・CLI build・race detectorを自動実行。
  [CIの対象・権限・確認方法](kv-engine/docs/ci.md)を参照。リモート実行結果はDraft PR #2のChecksで確認する。
- CLI応答単位: Close/stdout周辺の10 SIGKILLケース、Close・出力失敗と再Openを検証。
  短いJSON出力を成功扱いする問題を修正。[応答境界・実験結果・限界](kv-engine/docs/cli-response-experiments.md)を記録。
- 復旧前診断: 短い出力を見逃して切り戻す問題を修正。診断失敗時のログ不変と再試行を検証。
  [診断の完了条件・回帰試験](kv-engine/docs/recovery-diagnostics.md)を記録。
- 段階3: [受入条件と根拠の対応表](kv-engine/docs/stage3-acceptance.md)を照合し、定義したプロセス障害・I/O注入モデル内で完了と判定。
  電源断・ホスト停止・装置flush・診断出力先の永続化は未検証。MVP全体の完成ではない。
- 段階4: [計測CLI・条件・実測表](kv-engine/docs/measurement.md)を整備。1千/1万/10万キーでwarm再Open・同期PUT・Goヒープを実測し、この範囲の受入条件を満たした。
  生サンプルはJSON出力。RSS・cold start・環境間比較は未計測。
- 段階5: [3本の再現デモ](kv-engine/docs/demo.md)と[実装の読み順・設計判断・振り返り](kv-engine/docs/design-decisions.md)を整備。
  製品CLIの別プロセス操作、既存SIGKILLハーネス、fixtureコピーの復旧/破損拒否を区別して検証した。
- 最終受入: [MVP全条件・根拠・導入再現・状態図](kv-engine/docs/mvp-acceptance.md)で技術的な確認と人による確認を分ける。
  管理対象ソースからの新規Go導入・全試験・3デモ再現を確認。回線タイムアウトとVCS取得失敗の調整も記録。
  初回独立レビューの指摘3件（親ディレクトリ同期、Close失敗の分類、ヘッダー早期EOFの分類）を修正し、回帰を確認した。
  再レビューで見つかった探索権限の回帰も修正した。ロック、ログ操作、親の同期は、Linuxで入力パスを開いて得たディレクトリFDを基準にする。
  修正後の独立再レビューと本人による書面での設計説明を確認し、段階0〜5の受入を完了した。受入時点ではPR #2のマージ承認を別の手順として扱った。

## 作業環境

- リポジトリ: `kenobix/Claude_practice` / `origin`: `git@github.com:kenobix/Claude_practice.git`
- 独立worktree: `/home/kenshin/work/Claude_practice-261118`（ユーザー訂正後のパス）
- ブランチ: `feat/database-engine`。開始時HEAD: `b4c460a`、未コミット変更なし。
- Ubuntu 24.04.4 LTS / WSL2 / x86_64、kernel `6.6.87.2-microsoft-standard-WSL2`
- 保存先: `/dev/sdd` 上の ext4（`rw,relatime,discard,data=ordered`。観測値であり電源断耐性の証明ではない）
- システムのGo/gofmt: 未導入。SHA-256確認済みGo 1.27.4を `261118/.tools/go1.27.4/go` に配置（Git管理外）。
- Git、GNU Make、curl、Python 3、VS Code Remote CLIは利用可能。デモとその統合テストにPython 3（標準ライブラリ）を使用。
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

## 保存・停止・復旧を試す

Goをセットアップしたら、モジュールのディレクトリで `make demo` を実行する。デモにはPython 3が必要。
デモは人工データを入れた専用の一時DBだけを使う。正常に再起動できること、一部を書き込んだ時点で実際にSIGKILLを送った場合の挙動、不完全な末尾の復旧、CRC不一致による起動拒否を確認する。
進め方は[3〜5分の説明順と期待出力](kv-engine/docs/demo.md)を参照。最後の `demo_pass` とexit 0で成功を確認する。
テストハーネスがSIGKILLを送り、製品CLIには停止設定を追加していない。

エンジンはレコードの全バイトを書き込み、File.Syncが成功してから索引を更新し、操作の成功を返す。CLIはCloseが成功してから標準出力に成功応答を書く。
成功応答済みの更新が復旧後も残るには、OS、ファイルシステム、仮想化層、装置が同期契約を守ることが前提となる。電源断耐性は未検証。
[設計判断と既知の制限](kv-engine/docs/design-decisions.md)に、索引・同期・破損時停止の選択をまとめた。

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

各コマンドは別プロセスで実行する。putとdeleteは `{"sequence":1}` のようなJSONを返し、getは値の生バイトを改行なしで返す。
`--value-file FILE` でバイナリファイル、`-` で標準入力から値を読む。空入力も保存できる。
DBディレクトリは事前に作成する。新しいdata.logはLOCK以外にファイルがないディレクトリにだけ作成する。
getとinspectはdata.logを新規作成しない。ロック競合時はexit 4、inspectで破損や不完全な末尾を検出した場合はexit 5になる。
通常のOpenで不完全な最終レコードを検出した場合に限り、診断を出力してからログを切り詰める。**原本を調査する場合は最初にinspectを使う。**
inspectも排他ロックを取得し、LOCKを作る場合がある。data.logは変更しない。
I/Oテストはすべて専用の一時ディレクトリで実行し、普段使うデータは投入しない。

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

stdoutには次の内容が出る。

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
- `kv-engine/cmd/kvbench/`: 専用一時DBでの計測CLI、JSON生サンプル、全キー再読取照合
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
