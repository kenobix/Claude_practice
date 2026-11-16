# 耐久性・ロック・復旧 v1（設計確定、未実装）

## 対象環境と保証の条件

Linux / WSL2のLinux ext4ローカルFSを対象とする。観測環境は[README](../../README.md)。
Windowsマウント（/mnt/c等）、ネットワークFS、他OSは今回の保証対象外。
WSL2ではext4の背後に仮想ディスク・Windowsホスト・実ストレージがある。
GoのFile.Sync成功だけで、この全層の電源断耐性を実証したことにはならない。

目標: 成功応答した更新は、OS・仮想化・装置が同期契約を守り、後続の破損や外部変更がない条件で復旧後も残る。
fsyncエラー、ディレクトリ同期未対応、read errorを無視して保証を弱めない。
媒体故障、bit rot、悪意のある変更、メモリ破壊、他プロセスからの直接編集は保証外。
性能と電源断耐性は未計測・未検証。kill -9試験はプロセス障害の検証に限る。

## ファイルとOSロック

DBディレクトリは利用者が事前に作成する（専用領域、同一ユーザー、symlinkや外部操作を避ける）。
`LOCK` をO_CREATE|O_RDWRで開き、Linux `syscall.Flock(fd, LOCK_EX|LOCK_NB)` を使う予定。
ファイル存在では所有判定しない。get/inspectも排他取得し、競合は即時ErrLocked。
LOCKのFDはDBの生存中保持。LOCKはunlink/置換しない。全FDを閉じたときOSがロックを解放する。
flockはadvisoryなので協調しない外部writerを防げない。
Go標準ライブラリのLinux用実装を選び、他OS対応は将来の判断にする。

## 新規作成と更新の順序

1. 引数を検証し、既存DBディレクトリのLOCKを取得する。
2. 新規ならdata.logをO_CREATE|O_EXCLで作成（0600）。完全な16バイトヘッダーをwrite-allしFile.Sync。
3. DBディレクトリを開いてSyncする。LOCKとdata.logのディレクトリエントリを同期する。
   DBディレクトリ自体の永続性にはその親のSyncも必要なので、初回作成時はその親もSyncする。
   さらに上の新規ディレクトリ階層は利用者が事前に作成・永続化済みであることを前提とする。
   起動途中で失敗した既存ファイルは削除せず診断する。
4. 既存ログなら復旧検証を終える。既存の場合もDBディレクトリと親のSyncを完了してから更新を許可する。
5. 各更新をmutex下で検証し、容量・sequenceを確認。レコード全体をメモリで組み立てる。
6. ログ末尾へwrite-all。短いwriteを繰り返し、0バイト/nilはio.ErrShortWriteとして停止。
   一部書けてerrorになっても成功扱いせず、そのプロセスの状態を失敗へ遷移させる。
7. data.logのFile.Syncが成功してからメモリ索引・最新sequenceを更新する。
8. 同期済みsequenceを返し、CLIがstdoutへ成功応答を書く。

ログのサイズを伸ばす通常追記はファイル同期を行う。新しい名前や置換を作らないので、毎操作の親Syncは不要。
Open中はGET・更新・Inspect・Closeを直列化。複数操作のトランザクションは提供しない。

## 失敗状態

write / Sync / 読み取りI/Oエラーを検出したDBは失敗状態となり、Close以外の操作をErrRecoveryRequiredで拒否する。
I/O結果が不明なまま同じプロセスで追記を継続しない。Closeで資源を解放し、Openで再検証する。
入力・上限・ロック競合はI/O失敗状態にしない。初期化・復旧失敗のOpenはDBを返さない。
Sync成功後・応答前の停止やstdout失敗では、エラー/応答なしでも更新が残り得る。
再試行の重複排除は提供しない。Delete不在にも新たなsequenceが発行される。

## 復旧とinspect

ロック取得後に先頭から検証し、検証済みPUT/DELETEだけでメモリ索引を再構築する。
正常EOFはそのまま開く。不完全な最後のレコードだけなら、旧サイズ・最終正常境界・理由を診断出力し、
Truncate(last_valid_offset) → data.log.Syncを成功させてから開く。失敗したら通常起動を止める。
完全レコードのCRC不一致、ヘッダー不正、sequence不正、未知形式、途中破損は変更せず停止。
初回の短いファイルヘッダーも自動修復しない。利用者の既存データを削除しない。
修復ツールは今回対象外。将来必要なら明示的に作ったコピーだけを扱う。
inspectは同じ検証をするが切り戻さない。開くだけで診断対象を失わないようにする。

## 判断の根拠

- [Linux fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html): ファイル同期とディレクトリエントリの同期は別。
- [Linux flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html): advisory lock、非待機取得、FDの生存期間。
- [Go os.File.Sync](https://pkg.go.dev/os#File.Sync): ファイル内容のstable storageへのcommitを要求するAPI。

上記は設計の根拠。今回このエンジンの同期・ロック実装を実測した結果ではない。
