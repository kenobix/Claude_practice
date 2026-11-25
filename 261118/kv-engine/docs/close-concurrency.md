# 段階3: Closeと各APIの同時競合

2026-11-17 JST。[実装](../internal/engine/close_linux_test.go)はテスト専用。
既存のDB mutexとdiskOpsを使い、製品コード・レコード形式v1・fixtureを変えずに18ケースを追加した。

## Closeの契約

- Put/Get/Delete/InspectとCloseは同じDB mutexで直列化する。Closeは実行中の操作を中断しない。
- Closeが先にmutexを取得した場合、後続APIはErrClosedとなり、値・sequence・reportを返さずログI/Oもしない。
- Closeはdata.logを閉じてからLOCKを閉じる。data.logのClose待機中も他のOpen/inspectはErrLockedとなる。
- 最初のCloseはそのI/Oエラーを返す。閉鎖済みフラグを保持し、同時または繰返しのCloseはnilを返す。
  同じFDを再Closeしてエラーを取り直す仕様ではない。Closeは追加のFile.Syncを行わない。
- 同時に開始した操作が成功する順番は保証しない。成功した更新のsequenceは連続し、その操作列が再Open後に残る。
  拒否された操作はログへ現れない。Inspect成功時は、ある正常prefixの一貫した件数を返す。

これは既存実装の動作を明文化したもので、同時実行の機能拡張ではない。

## 試験と観測

全DBはt.TempDir内の人工データ。基本状態はtarget=old、keep=00 ff 01、empty=空値の3操作。
実FDのSync、ReadAt、Closeの内部でチャネルを使って待機させる。待ち合わせには5秒の期限を設け、cleanupで待機を解除する。
経過時間から順序を推測しない。入出力とCloseの回数はアトミックなカウンターで記録する。

| 試験 | ケース数 | 判定 |
|---|---:|---|
| Put/Delete/GetがI/Oを先に実行 | 3 API × 成功/EIO = 6 | mutex保持中はfile Close未実行・LOCK保持。操作の結果後にClose完了 |
| Closeが先に実行 | 4 API × Close成功/EIO = 8 | APIはErrClosed、ログI/O/バイト不変。同時の追加Close 7件と繰返しCloseもnil、FD Closeは1回 |
| 共通barrierから全APIとCloseを開始 | 4ラウンド | 各ラウンド64 API＋8 Close。成功更新履歴と再Open辞書を照合 |

I/O先行ではPut/DeleteはSync直前、Getは最初のReadAtで停止する。
成功時は期待する値/sequenceを返し、EIO時はエラーとゼロのsequence/値を返す。
更新SyncのEIOは実Syncを行わず返すが、全WriteAtは完了しているため、今回の再Openではsequence 4として再生された。
GetのEIOは元のsequence 3を保持する。keep/emptyも再Openで保持された。

Close先行ではdata.log Closeの実行直前で停止し、LOCKがまだ保持されていることを確認する。
CloseのEIOは実File.Close成功後に付加して返す注入であり、FDは閉じた状態にする。
最初の呼出しだけがEIOを受け、他のCloseと後続APIが閉鎖状態を守ることを検証する。
LOCK自体のCloseエラーや、FDが閉じられない仮想的な失敗の模擬ではない。

同時開始では基本状態に16キーのPUTを追加し、19操作を成功済みにする。
16 PUT、16 DELETE、16 GET、16 Inspect、8 Closeを同時開始し、成功またはErrClosedだけを許容する。
成功した更新を返されたsequenceの順に並べ、19の次から欠番も重複もなく続くことを確認する。
テスト側の独立した辞書へ成功更新だけを適用し、全候補キー・不存在・live_keys・最新sequenceを再Open結果と照合する。
Inspectの成功結果は、そのsequenceまでの成功履歴から算出したPUT/DELETE件数とlive_keysに一致する。
Getは他の操作が変えないkeepの全バイトを返す。すべてのClose終了後は実FD閉鎖とflock再取得を確認する。
成功件数とスケジュールは実行ごとに異なり、0件の更新成功も許容する。成功先行の経路は上の固定順序ケースで別途検証する。

18ケースすべて成功。Go 1.27.4 linux/amd64、WSL2/ext4（[環境](../../README.md)）。

## 再現と限界

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
./scripts/go-env.sh go test -count=1 -run '^TestDisk(Close|ConcurrentClose)' -v ./internal/engine
make check
./scripts/go-env.sh go test -race ./...
```

全スケジュールの網羅やデッドロック不存在の証明ではない。InspectはI/Oを行わないため、I/O先行ケースには含めず、
Close先行とbarrier同時開始で検証する。race detectorの成功も任意の並行実行の安全性を証明しない。
この単位では新たなプロセス停止を注入せず、既存のSIGKILL試験は全体テストで再実行する。
OSキャッシュは生存し、電源断・ホスト停止・WSL/装置flushの保証は未検証。
次の優先単位は製品CLIのClose/stdout成功応答周辺の停止・出力失敗試験。
段階4の計測と段階5のデモも残り、段階3全体の完了とはしない。

後続の[CLI応答境界単位](cli-response-experiments.md)も実施済み。
現在の次の作業は段階3の受入条件・実験対応表と完了判定。上記の「次」の記述は実験当時の順序を示す。
