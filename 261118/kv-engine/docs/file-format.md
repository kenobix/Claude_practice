# 追記ログ形式 v1（段階2: 形式互換の保存・再生を実装）

ファイル名は `data.log`。全整数はunsigned little-endian。
上限256 MiB = 268,435,456バイト（ファイルヘッダー込み）。分割・圧縮・paddingなし。
CRCはCRC-32C Castagnoli（Go `crc32.Checksum(data, crc32.MakeTable(crc32.Castagnoli))`）。
CRC格納値もlittle-endian。暗号学的な改ざん検出ではない。

## ファイルヘッダー（16バイト）

| offset | 幅 | 内容 |
|---|---:|---|
| 0 | 8 | magic: ASCII `KVELOG01` |
| 8 | 2 | version = 1 |
| 10 | 2 | header length = 16 |
| 12 | 4 | reserved = 0 |

完全一致を要求。未知versionはunsupported、magic/長さ/reserved不正はcorrupt。
既存ファイル0〜15バイトは不完全な**ファイルヘッダー**として起動を停止し、削除・再初期化しない。
観測サイズが16バイト以上あるのにヘッダーの読み取りが途中でEOFに達した場合はI/Oエラーとして起動を停止する。
ヘッダーのみの16バイトは正常な空DB。ファイルヘッダーにCRCはなく固定値の検査で判定する。

## レコードヘッダー（32バイト）

| 相対offset | 幅 | 内容 |
|---|---:|---|
| 0 | 4 | total_length（ヘッダー、キー、値、末尾CRCを含む） |
| 4 | 8 | sequence（最初1、直前+1。0/欠番/重複/逆行は不正） |
| 12 | 1 | op: 1=PUT、2=DELETE |
| 13 | 3 | reserved = 0 |
| 16 | 4 | key_length（1〜256） |
| 20 | 4 | value_length（0〜1,048,576、DELETEは0） |
| 24 | 4 | reserved = 0 |
| 28 | 4 | header_crc（このヘッダーのbytes [0,28)） |

その後にキー、値、4バイトのrecord_crcを置く。
record_crcの対象はレコード先頭から値末尾まで（header_crcを含む）、末尾CRC自身は除外。
`total_length = 32 + key_length + value_length + 4`。
最小37バイト、最大1,048,868バイト。DELETEのキーも同じUTF-8規則。
sequenceのuint64最大値を使い切った後は新たな更新を拒否する（wrapしない）。
ログ全体のサイズ超過も異常として通常起動を止める。

## 検証と不完全な末尾

1. 固定ヘッダーを固定サイズのバッファで読む。レコード境界で0バイトなら正常EOF。
2. 境界で1〜31バイトだけなら不完全な最終ヘッダーとする。
3. 32バイトあれば**先にheader_crcを検証**。不一致は末尾でもcorrupt。
4. op/reserved/sequence/各長さ/合計長を検証。計算はuint64で行いoverflowを防ぐ。
5. ここまで成功した長さだけを使い、上限内のメモリを確保する。
6. キー・値・末尾CRCの途中でEOFなら不完全な末尾。その他のread errorはI/O失敗。
7. 全長が読めればrecord_crcとキーUTF-8/NULを検証。不正は末尾でもcorrupt。
8. 正常レコードのみ索引へ反映し、次の境界へ進む。

完全なヘッダーに不正な長さがある場合は、EOFに達していても切り戻さない。
途中の不正レコードを飛ばして後続を探すこともしない。
任意の破損・外部切り詰めと正当なwrite途中停止を常に識別できるとは限らない。
特に32バイト未満の末尾はCRC検証不能なので、追記途中停止モデルの下でだけ切り戻しを許可する。
CRC衝突やレコード境界への外部truncateは検出できない。

## 具体例

[fixtures.md](../tests/fixtures.md)に計算済みhexとCRCを記載する。
正常ログはPUT `k`=`v` (sequence 1)、DELETE `k` (sequence 2)で構成する。
正常なら91バイト、2レコード、live_keys=0、latest_sequence=2。
不完全例はDELETEの先頭10バイトだけを付けた64バイトで、直前境界54へ切り戻す。
途中破損例は正常ログのPUT値（絶対offset 49）を `76` から `77` に変え、CRCは元のままとする。
全長があってCRC不一致なのでoffset 16で停止し、DELETEを採用しない。

## 段階1の実装API

- `record.FileHeader() []byte` / `ReadFileHeader(io.Reader) error`: versionを含む固定ヘッダーを検査。
- `record.Encode(Record) ([]byte, error)`: キー・値・op・非zero sequenceを検証してレコードを生成。
- `record.Decode(io.Reader, expectedSequence uint64) (Record, error)`: 正確な次sequenceを呼び出し側が指定する。
- 完全なレコード境界のEOFは `io.EOF`、短い最終レコードは `ErrIncomplete`、完全ヘッダーやCRC不正は `ErrCorrupt`。
- 短いファイルヘッダーは `ErrCorrupt`（自動復旧対象ではない）、未知versionは `ErrUnsupportedVersion`。
- readのI/Oエラーは原因をwrapし、不完全な末尾と区別する。
- 全エラー時にゼロ値Recordを返す。成功時は正確に1レコードを消費する。エラー時も検証のため読んだバイトは消費するが、後続レコードの再探索はしない。
- 固定32バイトのheaderを検証してから本文を最大1,048,836バイトだけ確保する。
- versionはファイルヘッダーの属性。個々のレコードにversion fieldは追加していない。

段階2でdecodeをrecovery.Scanへ接続し、位置索引の再構築と限定的な末尾切り戻し・同期を実装した。
fixtures.mdのhexは段階0のまま変更せず、Goテストでencode/decodeとの一致を検証している。
