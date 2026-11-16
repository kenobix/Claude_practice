# API・CLI契約 v1（設計確定、未実装）

## データとAPI

キーは正しいUTF-8の1〜256バイト。NULを含むキーはCLIとの共通制約として拒否する。
Unicode正規化をしない。バイト列が同じ場合のみ同一キー（例: 合成文字と結合文字は区別）。
値は任意の0〜1,048,576バイト。不在と空値を区別し、外部依存を使わない。

予定API（`internal/engine`、外部向け公開パッケージ化は後段階）:

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

## 予定CLI

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
正常な復旧切り戻しの診断はstderrへ `event:"tail_truncated"`、旧サイズと境界を出す。
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

この文書のDB操作は今後の仕様。現在の実行ファイルは `-version` / `-h` の骨組みのみ。
