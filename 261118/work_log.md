# 作業ログ: 開発環境・段階0

作業日: 2026-11-17 JST。依頼に従い `261118/` を使用。

## 確認した初期状態

- worktree: `/home/kenshin/work/Claude_practice-261118`
- branch: `feat/database-engine` / HEAD: `b4c460a`
- remote: `git@github.com:kenobix/Claude_practice.git`（fetch/push）
- 開始時未コミット変更なし、`261118/` なし。
- main、feat/ci-runner、feat/pcap-analyzerは別worktree。移動・切替・更新なし。
- 最初の指定 `~/projects/...` と実体が異なったため既存worktreeで継続。その後ユーザーが `~/work/...` へ訂正。
- ルートAGENTS.mdと261014/plan_database_engine.mdを読了。
- OS: Ubuntu 24.04.4 LTS、WSL2、x86_64、kernel 6.6.87.2-microsoft-standard-WSL2。
- FS: `/dev/sdd` のext4。Windowsマウントではない。
- Go/gofmtはPATHと既知の導入先で未検出。git/make/curl/python3/codeは検出。

## 変更と構成判断

1. 261118にREADME、AGENTS、作業ログ、専用VS Code workspaceを追加。
2. kv-engineにgo.mod、Makefile、cmd/kv、internal/record・engine・recoveryのdoc.go、docs、testsを作成。
3. 空ディレクトリだけでなくdoc.goで責務を残し、全パッケージのコンパイル確認を可能にした。
4. API、CLI、ログ形式、同期、復旧、ロック、障害注入を設計。製品機能は未実装。
5. 正常・不完全末尾・途中破損のhex fixtureをMarkdownとして追加。通常DBファイルは生成していない。
6. 上位AGENTSの一覧更新規則に従い、原則261118内という制約の例外としてルートREADMEに1行のみ追加。
   公開ページ・ホスティングの変更はない。

## Goの取得と検証環境

OS全体にはインストールしていない。通常のシェルPATHや起動設定も変更なし。
公式 `https://go.dev/dl/?mode=json` のメタデータでGo 1.27.4 linux/amd64を選び、
公式アーカイブのSHA-256を照合して一時領域へ展開した。

- archive SHA-256: `7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`
- 一時toolchain: `/tmp/kv-engine-toolchain-0s95bsss/go`
- GOCACHE/GOPATHも同じ一時領域、GOTOOLCHAIN=local。
- 実行版: `go version go1.27.4 linux/amd64`
- go.modはGo 1.27.4。標準ライブラリのみ、外部依存なし。
- sandbox内curlはDNS失敗したため、ネットワーク許可付き実行で公式配布元から取得した。
- 恒久導入は未実施。[導入手順](kv-engine/docs/development.md)を用意。一時領域の削除後は再取得が必要。
- VS Codeの専用workspace・タスクを用意したが、GUIとCodex拡張の有効化状態はこのCLIから未確認。

## 確定した設計

- UTF-8キー1〜256バイト、NUL拒否、正規化なし。値0〜1 MiB。不在と空値を区別。
- putは上書き、delete不在もtombstone、getは生バイト、inspectは変更しない診断。
- ログ256 MiB上限、16バイトfile header、32バイトrecord header、little-endian、CRC-32C。
- sequenceは1から連続増加、最大値到達後は拒否。長さを信用する前にheader CRCを検証。
- write-all → file Sync → 索引更新 → 成功応答。初回ファイル/DBディレクトリ/親のSync条件を定義。
- LOCKのflockを全操作で排他取得し、同一DBの操作も直列化。
- 不完全な最後のレコードだけ切り戻し+Sync。完全header/record破損は末尾でも停止。
- I/O失敗後はClose以外拒否。未応答の最大1操作が復旧後に残ることを許容。
- OS・仮想ディスク・装置の同期契約が前提。kill -9で電源断耐性を証明したと扱わない。

## 検証結果

- `make check`: 成功（gofmt差分なし、go vet ./...、go test ./...、CLI build）。
- `go test ./...`: 4パッケージとも `[no test files]`。コンパイル確認のみ。
- `bin/kv -version`: `kv stage0 (no database operations)`、exit 0。
- `bin/kv -h`: 段階0のhelpを表示、exit 0。
- Pythonで作成したfixtureのCRCを別のGo一時プログラムで照合: 正常header/record、切断位置、破損検出を確認。
- workspace JSON構文、ローカルMarkdownリンク、git diffの空白と全変更内容を確認。
- ビルド確認のbin/kvは削除。bin/と将来の局所キャッシュは.gitignore対象。
- commit、push、PR作成、main変更は実施なし。

## 未検証事項

DB操作・ロック・同期・切り戻しは未実装であり、復旧テストと永続性試験は未実施。
プロセス停止、電源断、WSLホスト障害、装置flush、容量不足の実機試験、性能計測も未実施。
仕様上のCRCの限界、短い末尾と外部破損の識別不能性をfile-format.mdに記録。

## 次の段階1

1. 入力検証とメモリ版Put/Get/Delete、参照モデルの辞書を実装。
2. CLIの引数、バイナリstdin/ファイル入力、生バイト出力、エラー/exit契約を実装。
3. レコードencode/decodeを分離し、fixture・境界値・全切断位置・不正長さ・CRC・sequenceをテスト。
4. decoderのfuzzを追加し、panic/過大確保を検証。
5. メモリ版だけでは保存できないことをCLI上で明示する。永続化・OSロックは段階2、障害注入は段階3へ進める。

## 段階1開始時の保存（2026-11-17 JST）

最新の依頼により、前回のcommit/push禁止を解除し、作業ブランチへの保存を行う。
前回のルートREADMEの追加1行だけを取り消した。他のルート変更なし。
段階0の仕様・fixture・骨組みを確認し、まだ実テストがないことを再確認。
261118/のみを最初のcommitへ含める。段階1の実装は次のcommitに分ける。

## 段階1の実装と検証（2026-11-17 JST）

### 保存と変更範囲

段階0の保存commitは `dafe953`（chore(261118): initialize KV engine development environment）。
`origin/feat/database-engine`へのpushが完了した。ルートREADMEには差分なし。
今回も対象は261118/だけ。mainへのpush・merge、PR作成は行わない。
Gitの共有管理領域がsandbox外のため、許可付きgit実行を使用した。

### Go環境

前回の一時toolchainと照合済みアーカイブは残っていた。
再現スクリプト `kv-engine/scripts/setup-go.sh` でSHA-256を再確認し、
ユーザー所有worktree内の `261118/.tools/go1.27.4/go` に新規導入した。
再実行は既存版を確認して終了し、上書きしないことを確認した。
Go本体・キャッシュ・GOPATHはGit管理外。OS全体やシェル起動設定は変更していない。
makeとVS Codeタスクはgo-env.shを使うため、前回の/tmpと通常PATHに依存しない。
実行版は `go version go1.27.4 linux/amd64`。標準ライブラリのみ。

### 実装

- Memory: Put/Get/Delete、上書き、空値と不在、UTF-8/NUL/サイズ検証、コピー、sequence、Close、mutex直列化。
- record: file header、version、32バイトheader、sequence/op/長さ、CRC-32C、encode/decode。
- decoder: header CRCと各上限を検証してから本文を最大1,048,836バイト確保する。
  正常EOF、不完全なレコード、完全header/recordの破損、I/O失敗を区別する。
- CLI: `kv memory`のJSON Lines session、Base64のバイナリ値、入力検証、エラーと終了コード。
  終了時にデータが消えることをhelp/versionとstderrのmemory_onlyイベントで表示。
- テスト: record/engine/cmd/kvの実テスト、公開hexと仕様の一致テスト、2種類のfuzz。

### 段階0からの差分と理由

- メモリ版の状態を同一プロセス内で確認するためJSON Linesの `kv memory` を追加。
  getの出力はsessionのフレーミングのためBase64。永続版の単発CLIと生バイト出力は後段階の仕様として保持。
- APIは `NewMemory` とMemory型。ディレクトリを開くOpen/Inspectはまだ追加していない。
- 不在DELETEはsequenceを進めるが、追記ログもtombstoneも未生成。ログ256 MiB制限は永続版で適用予定。
- CLI統合テストはrun/mainの配線を一緒に検証するためcmd/kvへ配置。testsには固定hexとの照合テストを置く。
- バイト配置とfixtureは段階0から変更なし。設計差分はdocs/api.mdとfile-format.mdへ記録。

### 検証結果

- `make check`: fmt検査、go vet ./...、go test ./...、CLI buildがすべて成功。
- 最終 `go test -json -count=1 ./...`: **32個のTest関数＋2個のFuzz関数**が成功。
  サブテスト・fuzz seedを含むpassイベント116件、fail 0、テストskip 0。
  4パッケージでテスト成功、internal/recoveryは予約のみで `[no test files]`。
- `go test -race ./...`: 成功。メモリKVの並列呼び出しを含めrace報告なし。
- `FuzzDecode`: 5秒、2 workers、62,819回、失敗なし。
- `FuzzFileHeader`: 5秒、2 workers、59,550回、失敗なし。
- キー1/256/257バイト、UTF-8/NUL、不在/空値、値0/1 MiB/+1を確認。
- レコードの全切断位置、各バイトの1bit改変、CRCを付け直した不正長/op/sequence/reservedを検証。
  不正な完全headerが不完全末尾扱いされず、本文read前に拒否されることを確認。
- 未知version、短いfile header、I/Oエラー、複数レコード境界を検証。
- 辞書の参照モデルを3 seed×1000操作で照合。各操作後に全16キーの値と不在を検証。
- CLI実行プロセスのexit 0/2/3、入力I/O失敗、JSON/Base64不正、2 MiB行上限を検証。
- READMEの利用例を実行し、Base64のバイナリ値と空値・sequenceの出力が説明と一致。
- セットアップのshell構文・再実行、ローカルリンク、workspace JSON、全差分、秘密情報パターンを確認。
- Go本体と生成CLIがgit check-ignoreで除外されることを確認。fixture原本は変更なし。

### 未実装・未検証

永続化、fsync、OSロック、ログ上限、起動時索引再構築、inspect、切り戻し、クラッシュ復旧は未実装。
電源断・WSLホスト障害・装置flush・実ディスク容量不足・性能の検証はしていない。
fuzzの短時間実行は、全入力や任意の障害への安全性を証明しない。
メモリ版の総容量制限とメモリ使用量の実測も対象外。
VS Code GUI・Codex拡張の有効化状態はCLIから未確認。

### 段階2の実装順序

1. Linux用LOCK管理とDBのOpen/Closeを作り、2プロセス競合と終了後再取得をt.TempDirでテスト。
2. file I/Oの小さな注入層を設け、新規header作成・file/dir/parent Syncの順序を実装する。
3. record encoderを追記へ接続し、write-all・256 MiB/sequence上限・Sync失敗時の停止状態を実装。
4. Sync成功後だけ索引を更新し、成功sequenceを返す。GETは検証済み位置から読み取る。
5. 通常起動のログ走査と索引再構築を接続し、PUT/DELETE列を再起動後の参照モデルと照合。
6. 単発の永続版CLIと--dbを接続し、正常再起動とロック競合を実プロセスで検証。
   不完全末尾の切り戻しとクラッシュ注入の本格実装・検証は計画の段階3で行う。
