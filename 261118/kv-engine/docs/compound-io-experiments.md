# 段階3: 更新失敗と再Open失敗を組み合わせるI/O試験

2026-11-17 JST。[詳細計画](../../../261014/plan_database_engine.md)の段階3で残っていた複合I/O障害の1単位。
[実装](../internal/engine/compound_io_linux_test.go)はテスト専用。製品コード・形式v1・fixtureは変更しない。

## 故障モデルとケース

実ファイルのFDを既存のdiskOpsで包む。まず次の4操作を成功させ、sequenceを受け取る。
PUT target=acknowledged、PUT keep=00 ff 0a、PUT empty=空値、DELETE missing。
この正常prefixは197バイト、sequence 4、live_keys 3。
最後にtargetをPUT（ff 00 01 0a）またはDELETEし、更新失敗と再Open失敗を順番に注入する。

| 更新失敗 | 実際に行うI/O | 再Openの注入点 | ケース数（PUT/DELETE込み） |
|---|---|---|---:|
| partial_header | 先頭10バイトを実WriteAt後、ENOSPCを返す | なし / read / DB dir Sync / parent Sync / 診断出力 / Truncate / 復旧Sync | 14 |
| partial_body | 完全header+key+1バイトを実WriteAt後、ENOSPCを返す | 同上 | 14 |
| sync | 全長を実WriteAt後、File.Syncは実行せずEIOを返す | なし / read / DB dir Sync / parent Sync | 8 |

合計36ケース。うち再Openも失敗する複合障害30ケース、追加障害なしの再Open対照6ケース。
別に同じFDラッパーで障害なしのPUT/DELETE/空値保存・Close・再Openを1ケース検証する。
完全レコードには末尾復旧が走らないため、更新Sync失敗とTruncate/復旧Sync失敗の組合せは生成しない。
各FDの注入設定を分離し、初期化や成功済み操作が意図せず失敗することを避ける。
ENOSPC/EIOは注入値であり、実ディスクの空き容量を枯渇させない。

## 判定と観測

- 失敗更新はsequence 0と元のENOSPC/EIOを返す。メモリ索引とreportは成功済み状態のまま。
  Put/Delete/Get/InspectはErrRecoveryRequiredとなり、I/O呼出し記録もログ全バイトも増えない。
  Closeまでは別のinspectもErrLockedとなる。
- 更新失敗後のファイルは正常prefixと完全一致する先頭197バイトを保つ。
  partial_headerは207バイト、partial_bodyは236バイト。inspectは不完全末尾とsequence 4を報告する。
  Sync失敗時はPUTで243バイト、DELETEで239バイトとなり、完全なsequence 5を読める。
  inspectではいずれもファイルを変更しない。
- 再Openのread/dir/parent/診断/Truncate失敗はファイル全体を変更せず、DB=nilとEIOを返す。
  診断失敗はbufferへJSONを記録後にEIOを返すwriterで再現し、Truncateを呼ばないことを確認する。
- 復旧Sync失敗は先に実Truncateが成功しているため、ファイルは197バイトになるが、Openは失敗する。
  診断のold_size/last_valid_offset、Truncate/Sync/Writeの実行有無も検査する。
  Syncが失敗しても、元の末尾が必ず残るとは扱わない。
- Close後・Open失敗後にログFDが実際に閉じられ、Closeが1回であることを確認する。
  flockを再取得し、LOCKのinodeが変わらないことも確認する。
- 注入を除いた再試行は同じファイルを使う。部分ログなら正常境界だけを復旧してsequence 4を再生する。
  すでにTruncate済みなら追加修復なし。全長のSync失敗レコードは未成功操作としてsequence 5に現れる。
- 全候補キーの値・不存在・空値とlive_keysを独立した辞書に照合する。
  再Openで修復が繰り返されないこと、次sequence 5/6の更新をさらに再Openして読めることを確認する。

36ケースと正常対照1ケースはすべて成功。環境はGo 1.27.4 linux/amd64、WSL2/ext4（[環境](../../README.md)）。

## 再現・限界・次の単位

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
./scripts/go-env.sh go test -count=1 -run '^TestCompoundIO' -v ./internal/engine
make check
./scripts/go-env.sh go test -race ./...
```

全DBはt.TempDir内の人工データ。実デバイスのEIO、永続的な故障、任意の多重障害を網羅していない。
プロセスは通常Closeで終了し、この単位で新しいSIGKILLは行わない。既存の停止試験は全体テストで再実行する。
Sync失敗後に残ったバイトやTruncate後の状態は、OSのキャッシュが残っている条件で観測した。電源断耐性の証明ではない。
失敗後の再Openが通ることだけを、失敗したSyncの電源断時保証や装置flush成功と扱わない。
CloseとPut/Get/Delete/Inspectの同時競合は[後続単位](close-concurrency.md)で検証済み。
次の優先単位は製品CLIのClose/stdout応答周辺の停止・出力失敗試験。
製品CLIのClose/stdout応答周辺、段階4の計測、段階5のデモ、ホスト停止/電源断の検証は残課題。
段階3全体の完了とはしない。

後続の[CLI応答境界単位](cli-response-experiments.md)も実施済み。
現在の次の作業は段階3の受入条件・実験対応表と完了判定。上記の「次」の記述は実験当時の順序を示す。
