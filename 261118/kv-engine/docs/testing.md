# 段階1の実テストと段階2以降の検証計画

段階0のgo testはテスト関数なしのコンパイル確認だった。段階1では以下の実テストを追加した。
永続化・復旧・fsync・OSロックの動作はまだ検証していない。実行結果は[作業ログ](../../work_log.md)に記録。

## 実装済み: 入力・レコード・CLI（段階1）

- UTF-8の1/256バイト、0/257バイト、無効UTF-8、NUL、正規化違い、バイナリ値、空値、1 MiBと+1。
- PUT上書き、DELETE不在、再PUTと参照モデルの辞書を照合。不在と空値を区別。
- fixtures.mdの期待バイト列をencode/decode両方向で照合。CRC計算の自己一致だけを正解にしない。
- 全切断位置、header CRC、record CRC、op、reserved、sequence、長さoverflow、未知versionを検査。
- 不正な巨大長さを本文read前に拒否し、fuzzでpanic/範囲外アクセスを調べる。確保上限はコードと境界テストで確認し、メモリ使用量の実測は未実施。

- engine: 単純な辞書を正解モデルに、3つの固定seed×1000操作で全16キーを操作ごとに照合。
- engine: Put/Getのコピー、Close、sequence枯渇、並列呼び出しを検証。
- record: 実fixtureの読取・生成が固定hexと一致。完全なレコードの各バイト1bit改変を検出。
- record: 正しいCRCを付け直した巨大長さ・未知op等も本文read前に拒否。
- record: ファイルヘッダーの全切断位置、未知version、I/Oエラーを区別。
- CLI: 同一sessionの操作列、プロセス間で状態が残らないこと、2 MiB行上限、値上限、JSON・Base64不正、I/Oエラー。
- CLI: t.TempDirに実行ファイルをbuildし、別プロセスのexit 0/2/3を検証。
- race detectorとFuzzDecode/FuzzFileHeaderの短時間探索も実行する。fuzzの完了は全入力安全性の証明ではない。

## 未実施: 永続化・ロック（段階2）

- `t.TempDir()` にDBを作り、混在列→Close→Openで辞書と照合。
- 初回作成のfile/dir/parent Sync順序をI/O注入層で記録。
- 2つの独立プロセスでロック競合を確認。通常終了・kill後の再取得、LOCKファイル残存も確認。
- 同一DBの並列呼び出しとCloseを `go test -race ./...` で確認（導入後）。
- 256 MiBの直前/一致/超過を容量検査し、拒否時にログ・sequence・索引が変化しないことを確認。

## 未実施: 障害注入（段階3）

| 注入点・障害 | 期待する判断 |
|---|---|
| write前/部分write/全write後、Sync前 | 応答なし。検証済みprefixまたは完全な未応答1操作を許容 |
| Sync後、索引更新前/応答直前 | 未応答操作が全体として残り得る |
| 成功応答後にプロセス停止 | 全応答済み更新が復旧結果に反映される |
| 初回header write/File.Sync/dir.Sync/parent.Sync前後 | 成功応答なし。短い既存headerは停止、完全headerは再検証 |
| 部分write+error/0バイトwrite/ENOSPC/EIO/Sync失敗 | 成功を返さず、後続操作を拒否。再Openで検証 |
| 完全headerのCRC/長さ破損、途中値のbit改変 | 位置付き停止。勝手なtruncateや後続レコードの採用なし |
| 不完全な末尾とTruncate/Sync失敗 | 正常境界のみ復旧対象。修復I/O失敗ならOpen失敗 |
| inspectで破損/不完全ログを走査 | 診断後も全バイト不変 |

ハーネスは子プロセスを動かし、親が成功応答sequence・操作・値を保持する。
常に未応答操作は最大1件。応答を受け取る前に次の操作を送らない。
障害注入は環境変数任せのsleepではなく、明示的な注入点/親子ハンドシェイクで止める。
復旧後は「全成功応答列」または「全成功応答列＋最後の未応答1操作」を辞書へ再生した結果と比較する。
最後の操作はPUT/DELETEとも全体だけを許容し、部分値は許容しない。
seed、注入点、応答列、ログ長、復旧境界、OS/FS/Go版を人工データで記録する。
応答ログまで同じ障害対象のプロセス内だけに置かない。

全テスト用DBは `t.TempDir()`。CLI手動試験は `mktemp -d` の専用DBのみを使う。
異常系はfixtureのコピーを壊し、原本や利用者DBを変更しない。
kill -9ではOSページキャッシュは残る。電源断、ホストクラッシュ、デバイスflushの真偽は別の検証課題。
