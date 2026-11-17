# 耐久性・ロック・復旧 v1（段階2実装）

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
`LOCK` をO_CREATE|O_RDWRで開き、Linux `syscall.Flock(fd, LOCK_EX|LOCK_NB)` を使う。
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

上記は設計の根拠。同期呼び出し順序とロック競合はテストしたが、装置の電源断保証の実測ではない。

## 段階2の実装上の補足

- 追記は、ロックとmutexの下で検証済み末尾offsetへWriteAtする。O_APPENDは使わない。
  書き込むのは常に末尾だけで、短いwriteを補完し、0バイトwriteや一部成功+errorは失敗状態にする。
- data.log新規作成はO_EXCL。既存ファイルにはTRUNCを使わず、LOCKはunlinkしない。
  data.log/LOCKのsymlinkと非regular fileは拒否。新規ログ作成先はLOCK以外に項目のない既存ディレクトリ。
- ログ走査はStatで得た最大256 MiBのsnapshotに制限する。短いfile headerは起動拒否。
  完全な正常prefixの後でのみ、EOFに接した短いrecord headerまたは検証済みheaderの短いbodyを復旧する。
  snapshotのEOFより前に読み取りが途絶えた場合はI/Oエラーとし、復旧しない。
- 復旧前に `tail_truncate_planned` とold_size/last_valid_offset/reasonを診断出力する。
  診断失敗ではファイルを変更しない。Truncate/Sync失敗ではDBを返さずロックを解放する。
- inspectはログをO_RDONLYで開き、修復・Syncをしない。LOCKは作成する場合がある。
- 起動失敗時のFD、Close時のログ→LOCKの順を保つ。既存データを初期化し直さない。
- Getでread/CRCエラーを検出した場合もDBを失敗状態にし、Close以外を拒否する。

この限定的な末尾復旧は今回の依頼に従い段階3の予定から先に実装した。
任意の外部truncateと追記中断は常に識別できない。協調しない外部編集や保存装置の故障は依然保証外。
段階2ではkillを行わず、段階3の初回単位で既存DB更新の停止点と応答履歴を使うSIGKILL試験を追加した。
[更新停止の実験](crash-experiments.md)に加え、[Open中の停止試験](open-crash-experiments.md)で初回作成9点と復旧4点を検証した。
0/8バイトの既存headerは破損として不変のまま拒否し、完全headerは空DBとして再開。
復旧中断後も成功応答済みの正常prefixを保ち、二度目のOpenで不要な修復を行わないことを確認。
[複数seedの更新停止試験](seeded-crash-experiments.md)では3 seed・48停止ケースと正常終了6ケースで成功応答履歴を照合した。
ホスト停止・電源断、複合I/O障害、Close競合・製品CLI応答周辺は未検証。
