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
