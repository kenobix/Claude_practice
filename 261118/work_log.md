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
