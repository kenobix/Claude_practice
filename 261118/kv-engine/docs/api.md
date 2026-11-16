# API・CLI契約 v1

## 段階2で利用可能になったAPIとCLI

Linuxで `Open(dir)` / `OpenWithOptions(dir, Options)`、DB.Put/Get/Delete/Inspect/Close、
読み取り専用 `engine.Inspect(dir)` を実装した。Report型は `recovery.Report`。
Openはdata.logの作成を許可。OptionsのCreate=falseなら存在するログだけを開く。
Options.Diagnosticsは末尾切り戻し前のJSON診断の出力先（nilならstderr）。診断失敗時は切り戻さない。
新規DBはLOCK以外の項目がない既存ディレクトリにのみ作る。data.log/LOCKはregular fileを要求し、symlinkを拒否する。
すべてのOpenとengine.InspectがLinuxの排他flockを取り、競合を即時拒否する。
DB.InspectはOpen時走査と成功更新に基づくレポート。engine.Inspectは独立した読み取り専用走査。
DB.Getはメモリの値コピーからではなく、索引に記録されたレコード位置から読み、再びCRCを検証する。
CloseはログFDを閉じてからLOCKのFDを閉じる。Close失敗は返し、二回目以降はnil。

以下の永続CLIは現在利用可能。put/get/delete/inspectを提供する。
キー・引数・値上限を検証してからDBを開く。getも通常Openなので不完全末尾の復旧を行い得る。
inspectだけは切り戻さず、異常でも走査レポートをstdoutへ出す（Open/lock自体の失敗ではレポートなし）。
statusに `io_error` を追加した。原因I/Oエラーはexit 6。構造・CRC破損、未知version、不完全末尾のinspectはexit 5。
CLIは操作後Closeが成功した後にstdoutへ返す。Sync後・Close失敗・stdout失敗の操作がログに残る可能性はある。
復旧事前イベント名は `tail_truncate_planned`。処置前なので復旧完了と誤認しない名前にした。

## 段階1の実装と段階0からの差分

`engine.NewMemory() *Memory`（ゼロ値も利用可）とMemoryのPut/Get/Delete/Closeを実装。
ファイルI/Oは一切せず、Closeまたはプロセス終了でデータを失う。
キー・値・エラー・コピー・sequenceの契約は以下の共通仕様に従う。
不在DELETEもsequenceを進めるが、ログやtombstoneはまだ生成しない。
ログ256 MiB上限は永続版の制限。メモリ版の総メモリ量には今回上限を設けない。

段階0の単発CLIではメモリ状態を後続プロセスへ渡せないため、`kv memory`を追加した。
stdinで1行1JSON objectを受け、stdoutも1行1JSONで返す。最初のエラーで終了し、操作列のrollbackはしない。
起動時にstderrへ `{"event":"memory_only","persistent":false}` を出す。
LF/CRLF、末尾改行のない最終行を受け付ける。空行は不正。1行上限2 MiB（改行込み）。
JSONはUTF-8。未知field、型不正、同一行の複数object、未知opを拒否。
JSONの文字列解釈・重複fieldはGo encoding/jsonに従う（重複は後の値を採用）。
key必須、value_base64のnullは未指定と同じ。putには空文字を含む値指定が必要。

| 入力例 | 成功stdout |
|---|---|
| `{"op":"put","key":"k","value_base64":"AP8="}` | `{"sequence":1}` |
| `{"op":"get","key":"k"}` | `{"found":true,"value_base64":"AP8="}` |
| `{"op":"delete","key":"k"}` | `{"sequence":2}` |

バイナリ値のフレーミングのため、メモリCLIのgetはBase64で返す。空値は `value_base64:""`。
Base64はGo標準のpadded base64、Strictモード。復号後1 MiB上限も確認。
不存在はstdoutなし、stderrにnot_found、exit 3。メモリCLIのinspectと--dbは未対応、exit 2。
入力不正exit 2、I/O失敗exit 6、sequence枯渇exit 7。help/versionはexit 0。
以上は段階1から維持したメモリ専用モード。以下は段階2で実装した永続版の共通仕様。

## 共通のデータとAPI

キーは正しいUTF-8の1〜256バイト。NULを含むキーはCLIとの共通制約として拒否する。
Unicode正規化をしない。バイト列が同じ場合のみ同一キー（例: 合成文字と結合文字は区別）。
値は任意の0〜1,048,576バイト。不在と空値を区別し、外部依存を使わない。

永続API（`internal/engine`、外部向け公開パッケージ化は後段階）:

```go
Open(dir string) (*DB, error)
(*DB).Put(key string, value []byte) (uint64, error)
(*DB).Get(key string) ([]byte, error)
(*DB).Delete(key string) (uint64, error)
(*DB).Inspect() (Report, error)
(*DB).Close() error
```

Getの返却値はコピー。不在は `ErrNotFound`、空値は長さ0の値とnil error。
Putは上書きを許可し成功sequenceを返す。Deleteは不在でもtombstoneを追記しsequenceを進める。
入力不正、容量、sequence枯渇の拒否はログ・索引・sequenceを変えない。
その他: `ErrInvalidKey`, `ErrValueTooLarge`, `ErrLogFull`, `ErrSequenceExhausted`,
`ErrLocked`, `ErrCorrupt`, `ErrUnsupportedVersion`, `ErrRecoveryRequired`, `ErrClosed`。
I/O失敗は原因をラップし `errors.Is` で識別できる設計にする。
同一DBの全メソッドをmutexで直列化し、Closeとの競合も保護する。
Closeは二度目以降nil。失敗状態でもFDを閉じ、ロック解放は最後に行う。

## 永続CLI

```text
kv put     --db DIR --key KEY --value-file FILE
kv get     --db DIR --key KEY
kv delete  --db DIR --key KEY
kv inspect --db DIR
```

`--value-file -` はstdin。値は最大値+1バイトまで読むことで上限超過を検出する。
コマンドやフラグ、キーを検証してからDBを開く。キーは必ず `--key` で渡す。
全操作で `--db` 必須。put/deleteだけ既存の空ディレクトリへの新規DB作成を許可。
DBディレクトリ自体は事前に作成する。get/inspectは存在しないログを作成しない。
ロックファイルだけがある空ディレクトリは初回作成可。既存の空・短いログは初期化せず破損とする。

| 操作 | stdout（成功） | 特殊ケース |
|---|---|---|
| put | 1行JSON `{"sequence":1}` | 上書きも同じ応答 |
| get | 値の生バイト、改行を追加しない | 空値は0バイト、exit 0。不在はexit 3 |
| delete | 1行JSON `{"sequence":2}` | 不在も記録・成功 |
| inspect | 下記の1行JSON | 読み取り専用、切り戻しをしない |

stderrはエラー時に `{"error":"code","message":"..."}` を1行。
破損診断は `offset`（ファイル先頭基準）と `last_valid_offset` を追加する。
正常な復旧切り戻しの診断はstderrへ `event:"tail_truncate_planned"`、旧サイズと境界を出す。
exit: 0=成功、2=引数/入力、3=不在、4=ロック競合、5=破損/未対応形式、
6=I/O/復旧必要/閉鎖状態、7=容量/sequence上限。help/versionはexit 0。
成功JSONの出力失敗はexit 6となり、同期済み更新は残り得る。

inspectは有効なヘッダーとレコードを同じ規則で検証する。
出力項目は `format_version`, `log_bytes`, `valid_bytes`, `record_count`, `put_count`,
`delete_count`, `live_keys`, `latest_sequence`, `status`, `invalid_offset`, `reason`。
件数は検証済みprefixだけ、delete_countはtombstoneの累計、log_bytesは実サイズ。
正常時のstatusは `ok`、offset/reasonはnull。
不完全な末尾は `incomplete_tail`、破損は `corrupt`、未知形式は `unsupported_version`。
異常時も診断JSONをstdoutに出し、stderrとexit 5を併用する。未知形式は件数等をnullにする。
通常Openは復旧後にAPIを公開し、Inspectメソッドは現在の正常状態のReportを返す。
CLI inspectは別の読み取り専用scanを使用し、Openによる自動修復を通さない。
すべてのCLI（inspect含む）は同じ排他ロックを取り、writerとの同時実行を拒否する。

永続版の同期契約はdurability.mdを参照。メモリ版の成功応答に保存保証はない。
