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

## 段階2: 永続KVの実装・検証（2026-11-17 JST）

### 開始状態と方針

worktreeは~/work/Claude_practice-261118、branchはfeat/database-engine、HEADはb669c7a。
開始時の未コミット変更なし。ルートAGENTS、計画書、形式・耐久性仕様、既存コードとfixture/testを確認。
今回も変更範囲は261118/のみ。形式v1とfixtureのhex原本は変更していない。
Goは既存のworktree内Go 1.27.4 linux/amd64を使用し、OS全体のインストールなし。

### 実装した内容

- Linux flockによるLOCKの非待機排他取得。すべてのOpen/inspectが協調し、競合はErrLocked。
  LOCKはunlinkせず、ログFDを閉じてからロックFDを閉じる。
- 新規data.logはO_EXCLで作成、ヘッダーwrite-allとFile.Sync、DBディレクトリと親のSync。
  既存ログは再初期化しない。新規作成はLOCK以外がない事前作成ディレクトリに限定。
  LOCK/data.logのsymlink・非regular fileは拒否する。
- 同期追記は検証済み末尾へのWriteAt。短いwriteを補完し、0バイトwrite、一部成功+error、Sync失敗を検出。
  検証・256 MiB上限・sequence上限を先に確認し、File.Sync成功後だけ索引とsequenceを公開。
- recovery.Scanで先頭から形式・CRC・sequenceを検証し、キー→最新PUTのoffset/sequence索引を再構築。
  GETは索引位置から読み出してCRCを再確認する。値全体を索引には保持しない。
- write/Sync/Get I/O失敗後はClose以外をErrRecoveryRequiredで拒否。
- 正常EOF、完全ヘッダー/レコード破損、不完全末尾を区別。
  正常prefix後のEOFに接した未完レコードのみ、診断→Truncate→Syncで復旧する。
  短いfile header、CRC不一致、不正header、未知version、snapshot末尾前のread途絶は復旧しない。
- 永続CLIのput/get/delete/inspectを追加。putはファイルまたはstdin、getは生バイト。
  全引数と値を検証してからOpenし、get/inspectはログを作成しない。
  inspectは不完全末尾も変更せず診断。エラー時の位置・最終正常境界をstderrへ出す。

### 設計との差分

限定的な末尾復旧とI/O注入テストは今回の依頼により段階3から前倒しした。
処置前の診断名はtail_truncate_plannedとした。処置の成功を事前に示さないための変更。
失敗したTruncate/SyncはOpen失敗とし、DBを公開しない。
単発CLIは操作とCloseが成功してからstdoutへ返す。Close/stdout失敗でも同期済み更新は残り得る。
DB.Inspectは現在の検証済み状態の集計、engine.Inspect/CLI inspectはログの独立した再走査。
詳細はdocs/api.mdとdocs/durability.mdへ反映。

### 検証結果

- make check: gofmt差分なし、go vet ./...成功、go test ./...成功、CLI build成功。
- go test -json -count=1 ./...: 51個のTest関数と2個のFuzz関数が成功。
  サブテストと既存fuzz seedを含むpassイベント203件、fail 0、skip 0。5パッケージすべて成功。
- go test -race ./...: 全5パッケージ成功、race報告なし。
  今回は新しいfuzz探索をせず、既存fuzz seedを通常テストで再実行した。
- 保存の実証: CLIをそれぞれ別プロセスで起動し、00 ff 0aの値、空値、上書き、ファイル入力、
  DELETE、不在DELETE、inspectのsequence=5/live_keys=0、競合時exit 4、破損時exit 5を確認。
- 128件の固定seed操作と8回のClose/Openを、辞書の全8キーと各操作後に照合。
- 別プロセスのLOCK保持中はOpen/inspectを拒否し、通常終了後に同じLOCK inodeで再取得できた。
- 実ファイル上のDELETEレコードの全36不完全位置で、inspectが不変、Openが正常境界へ復旧、
  復旧後の次のDELETEがsequence=2になることを確認。
- 初回ヘッダー→file Sync→DB dir Sync→parent Syncと、更新Sync前に索引を公開しないことを注入層で確認。
- 短いwrite、0バイトwrite、部分write+ENOSPC、Sync EIO、Get EIO、初期化/dir Sync/Truncate/診断失敗を注入。
  成功の誤報、失敗後の更新継続、ロックの残留がないことを確認。
- 容量の一致/超過と上限超過ログを疎ファイルで検証。256 MiBの実データ負荷試験や実ディスク枯渇ではない。
- 短いfile header、未知version、完全な末尾CRC不一致は原本を変えず拒否。
- 段階0fixtureの一時コピーで、正常/不完全/破損のOpen結果と切り戻し後バイト列を照合。
- README、作業ログ、仕様、ローカルリンク、workspace JSON、Gitの全差分と生成物除外を確認。

### 未検証の耐久性条件

今回の別プロセス試験は通常終了と再起動。kill -9、OS/WSLホストの停止、実機電源断は未実施。
OS・WSL仮想ディスク・ホスト・装置がflush契約を守ることを前提とし、その全層を実測で証明していない。
媒体故障、CRC衝突、協調しない外部編集/切り詰め、故意の改ざんは保証外。
短い末尾と外部破損の識別不能性は引き続きある。性能・総メモリ量・全容量書込み時間は未計測。
SQL、MVCC、複数writer、コンパクションは未実装。

### 段階3の実験計画

1. write前後、各部分write、Sync前後、索引更新前後、成功応答直前に親子ハンドシェイクの停止点を追加。
2. 親プロセスに成功応答列を保持し、未応答は常に最大1操作としてkill/reopenするハーネスを作る。
3. 復旧結果を成功応答列、または成功応答列+最後の未応答1操作の参照モデルと照合。
   同期後・応答前の操作が全体として残る場合を許容し、部分値は許容しない。
4. 初回作成・dir Sync・復旧Truncate/Sync中の停止、一部write+error、CRC/header途中破損を組み合わせる。
5. seed、停止点、応答列、旧サイズ、正常境界、復旧結果、OS/FS/Goを再現可能な人工データで保存。
6. プロセス停止試験と、別途計画するホスト停止/電源断/装置flush検証を分けて報告する。

## 段階3の初回単位: 更新停止点とSIGKILLハーネス（2026-11-17 JST）

### 現状・優先順位・選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: cfe1e05。
  開始時はclean。root/担当AGENTS、261014のDB計画、README/work_log、既存コードとテストを確認。
- Draft PR: [#2](https://github.com/kenobix/Claude_practice/pull/2)。段階1時点の進捗で段階2が未完了のまま。
  ghは利用可能。sandbox内のAPI接続は失敗し、ネットワーク許可付きghで読取に成功した。
- 段階0〜2は完了。段階3の限定末尾復旧・I/O注入は実装済みで、実プロセス停止と親側応答履歴の照合が未完了。
- 最優先の1単位として「既存DBの更新停止点とSIGKILL復旧ハーネス」を選択した。
  初回作成/復旧中の停止と複合障害はこのハーネスに依存するため次へ残し、段階4〜5を先に進めない。
- 担当AGENTSの古い段階2限定指示を、1単位ずつ選び検証・記録・担当ブランチへ保存する継続運用へ更新。

### 実装と既存設計との整合

- 新規crash_linux_test.goで既存diskOpsの実ログFDを包み、製品コードを変更せず停止点を注入。
  製品CLIに環境変数や停止コマンドを追加せず、レコード形式とfixtureを維持した。
- 親子JSONプロトコルで1操作ずつ送信。親が受信したack列を保存し、停止時の未応答を最大1操作に制限。
- PUT/DELETEそれぞれbefore_write、partial_header、partial_body、after_write、before_sync、
  after_sync、before_ack、after_ackの8点、計16ケースを実装。
- sleepで位置を推測せず、子の停止通知後にSIGKILLし、WaitStatusで実際のシグナルも確認。
  contextの15秒期限とcleanupで、異常終了時にも子プロセスとLOCKを残さない。
- 正常終了の再Open、未知停止点の非zero終了とログ不変もテスト。
- 復旧後は成功応答列、またはそれ+最後の未応答1操作の辞書とsequenceを照合。
  バイナリ/空値/不在、余分なキー、部分値を検査し、ロック再取得後の次の更新も確認。
- ackはライブラリAPI成功を返すテスト用応答。製品CLIのClose/stdout周辺の停止検証とは区別する。

### 実行結果

- 開始時の既存make checkは成功。
- 追加したTestCrash系: 16 SIGKILLケース、正常EOF終了、未知停止点拒否が成功。
- 最終make check: gofmt差分なし、go vet ./...、go test ./...、buildが全成功。
- go test -json -count=1 ./...: 55個のTest関数（子用helperを含む）と2個のFuzz関数が成功。
  サブテストと既存fuzz seedを含むpassイベント223件、fail 0、skip 0。5パッケージすべて成功。
- go test -race ./...: 全5パッケージ成功。新しいfuzz探索は実施していない。
- 観測: 部分header/bodyはsequence 5に復旧し、239/268バイトを正常境界229へ切り戻した。
  Sync後・応答前は未応答でもsequence 6が残り、応答後の操作もsequence 6として再生できた。
  Sync前の全長レコードも今回のSIGKILLでは残った。これは電源断時の保証とはしない。
- 実験条件・再現コマンド・各停止点の観測表はdocs/crash-experiments.mdに記録。
- テスト起動コマンドを一度module外で実行してgo.mod未検出となったため、module内で実行し直して検証した。
  テスト関数の失敗や未実行の成功扱いはない。
- 差分、秘密情報パターン、リンクとGit除外を確認。変更は261118/のみ、DB/Go本体/CLI生成物は追加しない。

### PRと今回の終了範囲

既存Draft PR #2の古い進捗を、段階2完了・段階3のこの単位のみ完了へ更新する。
Draft維持。mainへのpush/merge、force pushは行わない。PR反映の実結果は最終報告で示す。
今回は選択した更新停止点ハーネスまでで終了し、段階3全体の完了とはしない。

### 残課題・次の1単位

次は初回header/file/dir/parent Syncおよび復旧Truncate/Syncの停止点を追加し、本ハーネスを再利用する。
残りは複数seed・複合I/O障害、Close同時競合、製品CLI応答周辺、段階4の計測、段階5のデモ。
ホスト停止・WSL仮想ディスク・実機電源断・装置flushの検証、性能計測は未実施。
SIGKILL中もOSページキャッシュは生存するため、電源断耐性を証明したとは主張しない。

## 段階3・Open単位: 初回作成と復旧中のSIGKILL（2026-11-17 JST）

### 現状確認と選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 4f04044。開始時clean。
- root/担当AGENTS、261014のDB計画、README/work_log、既存Open・更新停止ハーネス・形式/耐久性仕様・テストを確認。
- Draft PR #2は正しい担当ブランチ・Draft状態、レビュー/コメントなし。前回の更新停止16ケースまで反映済み。
- 段階0〜2、更新中停止と限定末尾復旧/I/O注入が完了。初回作成・復旧中の停止が最優先の未完了。
  既存の親子ハーネスに依存する「Open中の停止試験」を今回の1単位に選び、複数seed等には進めない。
- AGENTSはすでに継続開発の1単位運用へ更新済みで、今回は変更不要。
- Goは既存の局所toolchain 1.27.4 linux/amd64、OS設定と他worktreeの変更なし。

### 実装

- テスト専用のopenCrashLogで実ファイルWriteAt/Sync/Truncateと実ディレクトリSyncを包む。
- 既存子プロセス起動をOpen前の停止点指定に対応させ、ready前に停止したことを通知で確認する。
  通常更新ハーネスのプロトコル・15秒期限・cleanup・SIGKILL確認を再利用。
- 初回作成9点: header write前/先頭8バイト後/全体write後、file/DB dir/parent Sync前後。
- 復旧4点: Truncate前後、復旧Sync前後。別writerで3件のackと部分DELETEを作りwriterをkillしてから、
  そのログを開く復旧プロセスもkillする（各ケース2回）。
- 正常系対照は同じFDラッパー/ディレクトリ同期経路で停止だけを無効化し、作成/復旧完了を確認。
  未知のOpen停止点をI/O開始前に拒否する異常系も追加。
- 製品コード、ログ形式v1、fixtureは変更していない。製品CLIに停止環境変数は追加しない。

### 結果

- 開始時の既存make check: 成功。
- 新規13停止ケース（9初期化+4復旧）: 成功。本単位で17回SIGKILL。正常対照2件・未知停止点拒否1件も成功。
- make check: fmt検査、go vet ./...、go test ./...、CLI buildが全成功。
- go test -json -count=1 ./...: 58個のTest関数（子helperを含む）と2個のFuzz関数が成功。
  サブテストと既存fuzz seedを含むpassイベント242件、fail 0、skip 0、全5パッケージ成功。
- go test -race ./...: 全5パッケージ成功。新しいfuzz探索は実施せず既存seedを再実行。
- 初期化0/8バイトheaderはinspectと2回のOpenでもcorrupt・不変。ロックを再取得できた。
  完全16バイトheaderは正常な空DBとなり、PUT sequence=1と再Open後の00 ff読出しが成功。
- 復旧前の部分ログ189バイト、正常prefix150バイト、ack=3。Truncate前killは189のまま再修復、
  Truncate後/Sync前後killは150のまま正常Open。全ケースでackの辞書とsequence=3を保持。
  2度目のOpenは追加修復せずログ不変、次の更新sequence=4が成功。
- 詳細表と再現手順はdocs/open-crash-experiments.mdへ追加。README・耐久性・検証計画・初回実験記録を更新。
- リンク、差分、秘密情報パターン、生成物除外を確認し、261118/内の対象だけを保存する。

### 残課題・次の単位・PR

次は複数seedの更新操作列と停止点を組み合わせ、成功応答履歴との照合を拡張する。
その後に複合I/O故障、Close競合、製品CLI応答周辺、段階4の性能計測、段階5のデモを残す。
初回作成の全バイト位置とAPI内部の任意位置を網羅した試験ではなく、段階3全体の完了とはしない。
SIGKILL中もOSページキャッシュは生存するため、ホスト停止/電源断、ディレクトリ名の永続性、
WSL仮想ディスク/保存装置のflush保証は未検証。Sync前の残存を電源断保証としない。
commit・担当ブランチへの通常push後、Draft PR #2の進捗を本単位完了と次の作業へ更新する。
Draft維持、main変更・force pushなし。PR反映の実結果は最終報告で確認する。

## 段階3・複数seed単位: 操作履歴と更新停止点（2026-11-17 JST）

### 状態確認と選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 512c031。開始時clean。
- root/担当AGENTS、261014の計画、README/work_log、既存コード・テスト・形式/耐久性仕様・fixtureを確認。
- Draft PR #2はDraftのまま、更新中16点とOpen中13点が反映済み。次の優先項目は複数seedの更新停止試験。
- 段階0〜2と既存停止ハーネスは完了済み。そのハーネスに依存する「複数seed×更新停止点」の1単位を選択。
  複合I/O障害、Close競合、製品CLI応答周辺、段階4〜5には進めない。
- 担当AGENTSは現在の継続運用に適合しており変更不要。Goは局所導入1.27.4 linux/amd64。
- 読取コマンドの一部にmodule cwdとルート相対パスを混在させ、ファイル未検出となったため、正しいmodule相対パスで読み直した。
  開始時のmake checkは成功。OS設定・他worktreeは変更していない。

### 実装と設計整合

- seeded_crash_linux_test.goを追加し、実ファイル・子プロセス・SIGKILLハンドシェイクを再利用。
  製品コード・レコード形式v1・公開fixtureは変更なし。
- seed 42/261118/20261117ごとに6固定操作+48乱数操作+target最終PUTを生成する。
  親が受信した55件のackを保存し、最後のPUT/DELETEを8停止点で止める。48 SIGKILLと正常終了6ケース。
- 空値/バイナリ、上書き/削除/不在、UTF-8/256バイトキー、0/1/17/4096バイト値を含む。
- 全ack、または全ack+最後の未応答1操作の辞書・sequenceだけを許容。
  同期後の操作の残存、不完全操作の非公開、全キー/余分なキー/未使用キーを照合。
- 形式の算術からログ長・PUT/DELETE件数を算出し、encodeや位置索引を正解にしない。
  inspect非変更、切り戻し診断・正常prefixの全バイト、二度目Openの辞書/report/ログ不変も検査。
  次sequenceのPUT後も再Openし、全辞書とsequenceを再確認。
- 成功ログはseed・停止点・ack数・復旧sequence・ログ長・修復有無を表示。失敗時は全生成操作列を追加表示。

### 検証結果

- 追加54ケースすべて成功。3 seed×2操作×8停止点=48 SIGKILL、正常終了6。
- make check: gofmt検査、go vet ./...、go test ./...、CLI buildすべて成功。
- go test -json -count=1 ./...: Test関数59個（子helperを含む）、Fuzz関数2個の既存seedが成功。
  サブテスト等を含むpassイベント297件、fail 0、skip 0、全5パッケージ成功。
- go test -race ./...: 全5パッケージ成功。新しいfuzz探索は実施せず既存seedを実行。
- before_write/部分writeはsequence55、全長write後〜応答前は56、応答後/正常終了は56へ復旧。
  正常prefixはseed順に61,241 / 28,261 / 40,514バイト。
  部分header/bodyはprefix+10/+39バイトから正しい境界へ切り戻された。
  次の更新sequence56または57も追加再Open後に保持。
- README、testing/durability、既存実験記録を更新し、seeded-crash-experiments.mdに観測表を記録。

### 残課題・次の単位

次は複合I/O障害の検証。部分write+ENOSPC後のTruncate/Sync失敗や、更新Sync失敗後の再Open失敗を組み合わせ、
成功の誤報・失敗後の操作継続・ack済みprefixの変更がないかを検証する。
Close競合、製品CLIのClose/stdout応答周辺、段階4の計測、段階5のデモは残る。
今回も任意の操作番号・任意バイト位置・全seedを網羅せず、段階3全体の完了とはしない。
SIGKILLはOSキャッシュを失わせない。ホスト停止・電源断・WSL仮想ディスク/装置flushの耐久性は未検証。
差分・秘密情報・生成物を確認し、対象ファイルのみcommitして担当ブランチへ通常pushする。
既存Draft PR #2の進捗を本単位の実績と次の作業へ更新し、Draftを維持する。反映結果は最終報告で確認する。

## 段階3・複合I/O単位: 更新失敗と再Open失敗（2026-11-17 JST）

### 状態と選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 0c88924、開始時clean。
- root/担当AGENTS、261014の計画、README/work_log、既存I/O抽象層・失敗状態・復旧・テスト・fixture仕様を確認。
- Draft PR #2はDraft、担当ブランチとHEADが一致し、複数seed試験まで反映済み。
- 段階0〜2、更新/Open停止試験、複数seed試験が完了。次の最優先だった複合I/O障害を1単位に選択。
  既存diskOpsと辞書モデルに依存し、Close競合・製品CLI応答周辺・性能計測は次へ残す。
- AGENTSは継続開発に適合しており更新不要。局所Go 1.27.4 linux/amd64を使用。開始時make checkは成功。

### 実装と判定

- compound_io_linux_test.goに実FDを包むテスト専用注入層と37ケースを追加。製品コード・形式v1・fixtureは変更なし。
- PUT/DELETE各々で、更新のpartial_header/partial_body+ENOSPC、全write後のSync EIOを注入。
  再Openではread、DB dir Sync、parent Sync、診断出力、Truncate、復旧SyncのEIOを適用可能な経路で注入。
- 複合障害30ケース、再Openの追加障害なし6ケース、同じラッパーでの障害なし正常対照1ケース。
- 4件の更新成功後、最後の1件がsequence=0と原因エラーを返すことを検査。
  メモリreport/索引非公開、以後全APIのErrRecoveryRequiredとI/O停止、Closeまでの排他保持を確認。
- 成功済みprefix全バイトと失敗後サイズ、inspect非変更、再Open失敗のDB=nil/EIOを検査。
  診断JSONの境界、修復のTruncate/Sync/Write呼出し有無、FDのClose回数/実閉鎖、同じLOCK inodeでの再取得も確認。
- 注入を除いて同じログを再試行し、辞書・live_keys・sequence、追加Openの修復不要、次の更新と再Openを確認。

### 検証結果

- 対象37ケースが成功。既存テストを含むmake check（fmt/vet/test/build）もすべて成功。
- go test -json -count=1 ./...: Test関数61個（子helper含む）とFuzz関数2個の既存seedが成功。
  サブテスト等を含むpassイベント335件、fail 0、skip 0、全5パッケージ成功。
- go test -race ./...: 全5パッケージ成功。新しいfuzz探索は実施せず既存seedを再実行。
- 正常prefixは197バイト・sequence4。部分header/body失敗後は207/236バイト。
  read/dir/parent/診断/Truncate失敗では全バイト不変。復旧Sync失敗ではTruncate済み197バイトとなるがOpenは失敗。
- 再試行では部分ログをsequence4へ復旧。更新Sync失敗で全長が残るPUT/DELETEは243/239バイト・sequence5で再生。
  エラーになった操作の不在を仮定せず、辞書と正常prefixを確認。次sequence5/6の更新も再Open後に保持。
- README・耐久性・検証計画・既存実験記録を更新し、compound-io-experiments.mdに故障モデルと観測表を記録。

### 限界・次の1単位・保存

次はCloseとPut/Get/Delete/Inspectの同時競合の専用検証。その後に製品CLIのClose/stdout応答周辺を扱う。
段階4の計測、段階5のデモ、実デバイス障害・ホスト停止・電源断・WSL/装置flushの検証は未実施。
今回のEIO/ENOSPCは注入で、実容量を枯渇させていない。全DBはt.TempDir内の人工データ。
この単位では通常Closeを使い、新規SIGKILLケースは追加せず既存試験を再実行した。
OSキャッシュが生存する条件の再試行成功を、失敗したSyncの電源断時保証と扱わない。段階3全体は未完了。
差分・秘密情報・生成物を確認した後、261118/内の対象だけcommitし、担当ブランチへ通常pushする。
Draft PR #2の進捗を更新してDraftを維持する。反映の実結果は最終報告で確認する。

## 段階3・Close競合単位（2026-11-17 JST）

### 状態確認と選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 7b8a1ee、開始時clean。
- root/担当AGENTS、261014の計画、README/work_log、既存のmutex/Close/I/O抽象層・並列テスト・fixture仕様を確認。
- Draft PR #2はDraft、担当ブランチ/HEADが一致し、複合I/O試験まで反映済み。
- 段階0〜2と更新/Open停止・複数seed・複合I/O試験が完了。次の最優先だったClose競合を1単位に選択。
  既存diskOpsと辞書モデルを再利用し、製品CLI応答周辺と段階4〜5は次へ残す。
- AGENTSは継続運用に適合しており変更不要。Goは局所導入1.27.4 linux/amd64。OS・他worktreeは変更なし。

### 実装と既存契約の確認

- close_linux_test.goに実FDを包むchannel待合せとatomic counterを追加。製品コード・形式v1・fixtureは変更しない。
- I/O先行6ケース: Put/DeleteのSync、GetのReadAt内部で停止し、Closeを競合させる。各々成功/EIOを検証。
  停止中にmutex保持・file Close未実行・LOCK保持を確認し、解除後の結果とClose後の再Openを照合。
- Close先行8ケース: Close内部で停止して各APIと追加7件のCloseを開始。Close成功/EIOの双方を検証。
  EIOは実File.Close後に付加する注入であり、最初のCloseのみエラー、他のCloseと繰返しCloseはnil。
  APIはErrClosedとゼロ結果、追加I/Oなし、ログ不変。LOCKはdata.log Close待機中も保持される。
- 同時開始4ケース: 各64 API+8 Closeを共通barrierから開始。成功かErrClosedのみ許容する。
  成功更新をsequence順に並べ、辞書と再Open結果を照合。Inspect成功時も同じprefixの件数/live_keysを要求。
- Close回数1、実FD閉鎖、flock再取得を全ケースで確認。タイムアウトとcleanupを設け、sleepで順序を推測しない。

### 検証結果

- 追加18ケースは全成功。make check（gofmt検査、go vet ./...、go test ./...、CLI build）も成功。
- go test -json -count=1 ./...: Test関数64個（子helper含む）とFuzz関数2個の既存seedが成功。
  サブテスト等を含むpassイベント356件、fail 0、skip 0、全5パッケージ成功。
- go test -race ./...: 全5パッケージ成功。新しいfuzz探索は行わず既存seedを再実行。
- 同時開始の最終通常テストで成功更新19/21/0/5件、成功Inspect 10/12/0/1件を観測。
  件数は固定せず、Closeが先なら全操作拒否も許容。両順序は固定I/O待合せケースで別途確認。
- I/O先行のPut/Deleteは全長write済みのため、Sync EIOでも今回の再Openではsequence4を再生。
  Get EIOは元のsequence3を保持。Close先行では全APIを拒否し元のsequence3とログを維持。
- README・耐久性・検証計画・既存実験記録を更新し、close-concurrency.mdに契約・観測と限界を記録。

### 残課題・次の単位・保存

次は製品CLIのClose/stdout成功応答周辺の停止・出力失敗試験。
段階4の性能計測、段階5のデモ、ホスト停止・電源断・WSL/装置flushの検証は残る。段階3全体は未完了。
今回のEIOは注入値で、LOCKのCloseエラーや実デバイス障害・全スケジュールを網羅していない。
InspectはI/Oを行わないためI/O先行の注入点を持たず、Close先行と同時開始で確認した。
全DBはt.TempDir内の人工データ。新規SIGKILLは追加せず、既存試験を全体テストで再実行。
OSキャッシュの生存下での再Open成功やrace detector成功を、電源断耐性・全並行実行の証明としない。
差分・秘密情報・生成物を確認して261118/内の対象だけcommit・担当ブランチへ通常pushする。
Draft PR #2の進捗を実績へ更新し、Draftを維持する。反映結果は最終報告で確認する。

## 開発基盤単位: KV専用GitHub Actions（2026-11-17 JST）

### 確認と選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: b28c223、開始時clean。
- root/担当AGENTS、261014の担当計画、README/work_log、Goコード・実テスト・fixture仕様・Makefile・setupを確認。
- Draft PR #2はDraftを維持し、担当ブランチとHEADが一致。既存workflowは他プロジェクト専用のみ。
- 開始時make checkは成功し、既知の未解決不具合・テスト失敗は確認されなかった。
  ユーザーの新しい優先順位に従い、CLI応答周辺より先に未整備の自動テスト導入を1単位として選択した。
- 既存セットアップ・実テストを利用し、製品コード・形式v1・fixtureは変更しない。AGENTSの恒久ルールも変更不要。
  ユーザーが今回許可した.github/workflows/の例外を利用し、既存workflowと他プロジェクトは変更しない。

### 実装

- .github/workflows/kv-engine.ymlを追加。main/担当ブランチのpush、main向けPRを担当パスで絞る。
- ubuntu-24.04の1 job、15分上限、同一workflow/refの旧runをキャンセル。
- contents: readのみ。公式checkout v7.0.1をSHA固定し、認証情報を永続化しない。
- 既存setup-go.shのGo 1.27.4とSHA-256検証を再利用し、workspace内だけへ導入。
- Go/gccの存在確認、make check、キャッシュ無効のraceテストを実行。raceはパッケージごと3分上限。
- 外部Go依存なしの条件に合わせGOPROXY/GOSUMDBをoff、GOTOOLCHAIN=local、CGO_ENABLED=1。
  secret・サービス・artifact・cache・追加OS導入は使わない。
- README・検証/開発手順を更新し、docs/ci.mdにトリガー・権限・再現方法・限界を記録。

### 検証

- 局所Go 1.27.4 linux/amd64、gccあり。既存導入を再利用するsetupとCIと同じコマンド群が成功。
- make check: fmt検査、vet、既存テスト、CLI buildが成功。
- go test -json -count=1 -timeout=3m ./...: 全5パッケージ、passイベント356件、fail 0、skip 0。
  既存のTest関数64個（子helper含む）とFuzz関数2個のseedを再実行。新規テスト関数やfuzz探索は追加しない。
- go test -race -count=1 -timeout=3m ./...: 全5パッケージ成功。
- 公式actionlint 1.7.12をチェックサム照合後/tmpへ展開し、新規workflowの静的検証が成功。
  一時コピーの未知runnerを拒否する異常系と、未整形Goをfmt-checkが拒否し整形後に通す正常対照を確認。
  負例は専用一時領域で自動除去し、製品コードやDBへ触れていない。

### 未検証事項と次の作業

この記録のcommit時点でGitHub runnerは未実行。push後に担当commitのrunを確認し、
実結果とcommit SHAをDraft PR #2・完了報告へ記録する。ローカル成功をリモート成功と扱わない。
ブランチ保護などのリポジトリ設定は変更しない。CIはホスト停止・電源断・装置flushを検証しない。
段階0〜2は完了、段階3は引き続き未完了。次の最優先は製品CLIのClose/stdout成功応答周辺の停止・出力失敗試験。
段階4の性能計測と段階5のデモも残る。今回の単位で次の機能実装には進まない。
差分・秘密情報・生成物を確認後、担当ファイルだけcommit・担当ブランチへ通常pushする。

## 段階3・CLI応答境界単位（2026-11-17 JST）

### 状態確認・選択

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 3560a9f、開始時clean。
- root/担当AGENTS、261014担当計画、README/work_log、CLI・engine・既存テスト・fixture仕様・耐久性契約を確認。
- Draft PR #2のHEADと担当ブランチが一致。前回導入した専用CIのpush/PR両runはsuccess。
- 既知の未解決不具合は記録されておらず、CIも整備済み。次の優先単位だったCLIのClose/stdout周辺を選択。
  段階0〜2、更新/Open停止・複数seed・複合I/O・Close競合に依存し、受入条件整理・段階4〜5は次へ残す。
- AGENTSの恒久ルールに変更はなく更新不要。他worktree・既存workflow・OS設定は変更しない。

### 実装と発見した問題

- PUT/DELETE/inspectで、JSON出力先が短いwriteとnil errorを返すと、不完全な応答にもexit 0を返していた。
  修正前のzero/shortの6ケースで再現。GETは既に検出していた。
- 永続CLIのstdoutをcompleteResponseWriterで検査し、短いwriteをio.ErrShortWrite→exit 6へ統一。
  通常のJSONとGET生バイトは維持。短いnil writeはWriter契約違反の注入で、実ディスク障害とは区別する。
- runPersistentWithOpenと最小persistentDB interfaceを追加し、引数で実DBのCloseラッパーを渡せるようにした。
  製品CLIの環境変数・flagへ停止設定を追加せず、制御はテスト実行ファイル内だけで行う。
- response_linux_test.go: 4操作×6出力モード、Close EIOの3操作、空値GETを検証。
- response_crash_linux_test.go: PUT/DELETE×5停止点（Close前後・stdout前/部分/全出力後）、
  正常終了2ケース、未知停止点拒否、製品バイナリの/dev/full失敗2ケースを検証。追加の実ケースは計43。
- すべて実DB・専用t.TempDir。親子ハンドシェイク、期限、SIGKILL/Wait確認とFD cleanupを使用。
  成功済みprefix・辞書・sequence・件数・バイト長・LOCKと、2回の再Open後の全バイト不変を検査。
- README/API/耐久性/検証計画と実験記録への参照を更新し、cli-response-experiments.mdに故障モデルと観測表を記録。
  レコード形式v1・fixture・engine本体・依存関係は変更しない。

### 実行結果

- make check（fmt検査・go vet ./...・全テスト・CLI build）が成功。
- go test -json -count=1 -timeout=3m ./...: 全5パッケージ成功。Test関数71個（子helper含む）、Fuzz関数2個の既存seed。
  passイベント405件、fail 0、skip 0。新しいfuzz探索は実施していない。
- go test -race -count=1 -timeout=3m ./...: 全5パッケージ成功。Go 1.27.4 linux/amd64を局所導入から使用。
- 追加10 SIGKILLケースはすべてsequence 4を再生。成功済み3操作の146バイトprefixを保持。
  PUT後192バイト・live_keys=3、DELETE後188バイト・live_keys=2。keep/empty/不在も一致。
- before_closeはLOCK保持、それ以降は解放済み。stdoutは0/部分5/完全15バイト。
  部分/完全の応答は親がkill前に受信。完全なJSON受信後でも、SIGKILLをexit 0と取り違えない。
- Close EIOはstdoutなし・exit 6・Close呼出し1回、stdoutの各失敗もexit 6。
  別製品プロセスの/dev/full失敗でも同期済みPUT/DELETEが残り、再読取りで確認できた。

### 未検証・次の1単位・保存

停止ケースは実engineと製品共通のCLI処理をテスト実行ファイルで動かす。製品バイナリ自体への停止制御は追加していない。
Close EIOは実Close後の付加エラー、EPIPEはWriter注入。LOCK閉鎖失敗・実pipeのSIGPIPE・実媒体障害は未検証。
OSキャッシュの生存下でのプロセス停止であり、ホスト停止・電源断・WSL/装置flush耐性を証明しない。
次は段階3の受入条件を各実験・故障モデル・未検証条件へ対応付け、完了可否を判断する単位。
この作業だけで段階3全体を完了扱いせず、性能計測・デモへは進まない。
差分・秘密情報・生成物を確認後、261118/内の対象だけcommitして担当ブランチへ通常pushする。
push後のCIはcommit時点で未実行。実結果とSHAを既存Draft PR #2と最終報告へ記録し、Draftを維持する。

## 段階3・復旧前診断の完了検査（2026-11-17 JST）

### 状態確認と優先順位

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 5a9bc58、開始時clean。
- root/担当AGENTS、261014の担当計画、README/work_log、engine/recovery/record/CLIと既存テスト・fixture仕様を確認。
- Draft PR #2のHEADが担当ブランチと一致し、前回のpush/PR両CIはsuccess。CI導入済み。
- 次の候補だった段階3の受入条件照合中に、復旧前診断の短いwrite/nilを見逃す問題を発見した。
  安全性・データ整合性の問題を最優先とするユーザー指示に従い、この修正を今回の1単位へ選び直した。
  受入条件の対応表・完了判定と段階4〜5は今回進めない。恒久ルールは変わらずAGENTSの更新不要。

### 再現・修正と回帰試験

- 既存のjson.EncoderはWriterのerrorを検査するが、短いwrite/nilを検出しない。
  診断が0バイトまたは一部だけでもOpenが成功して64→54バイトへ切り戻す2ケースを再現した。
- disk_linux.goで診断イベントをJSON+LFへ変換し、1回のWriteで全長受理・errorなしを確認してからTruncate/Syncへ進む。
  短いwrite/nilはio.ErrShortWrite、部分/全長write+EIOは原因を保持した失敗とし、DB=nilでログを変更せず終了する。
- diagnostic_linux_test.goに5ケースを追加：0/nil、部分/nil、部分/EIO、全長/EIO、全長/nil。
  正常系ではTruncate/ログSync各1回。失敗系では各0回と元の64バイト全体の不変を確認。
- 各ケースで診断呼出し1回、イベントfieldとLF、実FD閉鎖1回、LOCK再取得を確認。
  完全なWriterで再試行すれば54バイトの正常prefixへ復旧でき、k=v・sequence 1・live_keys=1を保持。
  次のOpenは診断不要で、全バイトが不変。全DBは専用t.TempDir内の人工データ。
- README/API/耐久性/検証計画を更新し、docs/recovery-diagnostics.mdに故障モデルと観測表を記録。
  耐久性文書末尾の古い「CLI応答周辺は未検証」も、実施済みの後続実験への参照へ訂正。
- ログ形式v1・fixture・CLIの応答形式・依存関係・workflow・他worktree・OS設定は変更しない。

### 検証結果

- 修正前はzero/partialの2ケースが失敗、他3ケースが成功。修正後は5ケースすべて成功。
- make check（fmt検査・go vet ./...・全テスト・CLI build）が成功。
- go test -json -count=1 -timeout=3m ./...: 全5パッケージ成功。Test関数72個（子helper含む）、Fuzz関数2個の既存seed。
  passイベント411件、fail 0、skip 0。件数増加のためのケースや新しいfuzz探索は追加しない。
- go test -race -count=1 -timeout=3m ./...: 全5パッケージ成功。Goは局所導入1.27.4 linux/amd64。
- 今回の製品コード変更に対する回帰確認として既存SIGKILL・CLI・複合I/O・fixture試験も再実行した。
  新たな停止点は追加していない。診断先のEIOは注入で、実媒体障害を起こしていない。

### 未検証事項・次の1単位・保存

診断Writerの全長受理は診断先のfsyncや永続化を意味しない。ホスト停止・電源断・WSL/装置flushも未検証。
Writerの契約違反を防御する変更であり、任意のWriter内部故障を検出できる主張はしない。
段階0〜2は完了、段階3は完了判定前。次の最優先は受入条件と実験・故障モデル・未検証事項の対応表と完了判定。
差分・秘密情報・生成物を確認後、261118/内の対象だけcommit・担当ブランチへ通常pushする。
commit時点で今回のGitHub CIは未実行。push後の実結果とSHAをDraft PR #2と最終報告へ記録し、Draftを維持する。

## 段階3・受入条件の照合と完了判定（2026-11-17 JST）

### 選択した1単位

- cwd: /home/kenshin/work/Claude_practice-261118、branch: feat/database-engine、開始HEAD: 6957897、未コミット変更なし。
- root/担当AGENTS、261014の計画、README/work_log、実装・テスト・fixture、Draft PR #2を確認。
  PRのHEADは担当ブランチと一致、前回のpush/PR両CIはsuccess。KV専用CIは整備済み。
- 既知の未解決不具合・失敗テストは確認されず、次に残っていた段階3の受入判定を選択。
  段階4の計測や段階5のデモを同時に実装しない。恒久ルールは変わらずAGENTS更新不要。

### 成果と判定

- docs/stage3-acceptance.mdを追加し、元計画の条件をS3-1〜10へ整理。
  更新/Open/CLI停止点、応答履歴、EOF分類、末尾復旧、破損拒否、長さ検証、I/O失敗、Close競合を
  既存ソース・実テスト関数・実験観測表へ対応付け、各行の限界も記録した。
- 親が保持する成功列Aと最後の未応答操作P（最大1件）を正解にし、write前/不完全ならA、
  同期後ならA+P、ack受信後は最後も含むAを要求することを確認。
  全長write後/Sync前の許容結果と今回の観測、製品CLIの完全応答とexit 0も区別した。
- 実装とassertionの照合・受入ゲートに基づき、段階3を定義したプロセス障害/I/O注入モデル内で完了と判定。
  未検証条件を成功扱いせず、MVP全体は段階4〜5が未完了と明記。
- README、検証計画、耐久性文書に判定書を参照させ、現在の次の作業を更新。
  製品コード、テスト、形式v1、fixture、依存関係、workflowは変更していない。

### 実行した受入ゲート

- Go 1.27.4 linux/amd64（worktree内の局所導入）を使用。
- make check: fmt検査・go vet ./...・既存全テスト・CLI buildが成功。
- go test -json -count=1 -timeout=3m ./...: 全5パッケージ成功。
  Test関数72個（子helper含む）とFuzz関数2個の既存seed。passイベント411件、fail 0、skip 0。
- go test -race -count=1 -timeout=3m ./...: 全5パッケージ成功。
- 文書だけの変更であり、新規テストやfuzz探索は追加しない。受入判定の根拠を現HEADで確かめる目的で
  既存の正常/異常/境界・SIGKILL・複合I/O・CLI・fixture回帰をキャッシュ無効で再実行した。
- Git差分・範囲・秘密情報・Markdownのローカル参照を確認し、生成物は追加しない。

### 未検証事項・次の1単位・保存

電源断、ホスト/カーネル停止、WSL仮想ディスク/装置flush、実媒体障害、診断先のfsyncは未検証。
全バイト位置でのSIGKILL、全seed/全並行スケジュール、実pipeのSIGPIPE、LOCK Close失敗も未検証。
これらをモデル外/未検証として保持したまま段階3を判定し、新しい保証を追加しない。
次の最優先は段階4の計測ハーネスと機械可読な結果。固定条件・容量内の1千/1万/10万キーについて
起動時間・更新待ち時間・メモリを測り、CPU/Go/OS/FS/同期/値長/キャッシュ/測定区間を記録する。
OS全体のキャッシュ設定は変更せず、ページキャッシュを残す再Openをcold startと呼ばない。
担当ディレクトリの対象文書だけcommit・担当ブランチへ通常pushする。
commit時点で今回のリモートCIは未実行。実結果・SHAと段階3完了判定を既存Draft PR #2へ反映し、Draftを維持する。
