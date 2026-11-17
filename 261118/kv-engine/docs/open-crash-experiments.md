# 段階3・Open単位: 初回作成と末尾復旧の停止試験

2026-11-17 JST。前回の[更新停止ハーネス](crash-experiments.md)を再利用する1単位。
段階0〜2と更新中16ケースは完了済み。最優先の未完了だったOpen中停止を選択した。
複数seed・複合障害、Close/CLI応答周辺、段階4〜5には今回着手しない。

## 対象と停止方法

[open_crash_linux_test.go](../internal/engine/open_crash_linux_test.go)はテストバイナリだけに含まれる。
既存diskOpsで実ファイルのWriteAt/Sync/Truncateと実ディレクトリSyncを包み、Open開始前に停止点を設定する。
子の最初の通知が指定したstoppedであることを確認し、readyや操作ackが先に来れば失敗とする。
親のSIGKILL、WaitStatusによるシグナル確認、15秒の子期限とcleanupは既存ハーネスを再利用する。
製品のOpen/CLIに設定を追加せず、ログ形式v1とfixtureも変えていない。

## 初回作成: 9点の観測

事前作成した空のt.TempDirを使用。停止時に成功応答済み操作は0件。

| 停止点 | 停止位置 | kill後のdata.log | 再Open結果 |
|---|---|---:|---|
| init_before_header | data.log作成後、header WriteAt前 | 0バイト | corrupt、変更せず拒否 |
| init_partial_header | header先頭8バイトwrite直後 | 8バイト | corrupt、変更せず拒否 |
| init_after_header | header全16バイトwrite直後 | 16バイト | 正常な空DB |
| init_before_file_sync | header File.Sync直前 | 16バイト | 正常な空DB |
| init_after_file_sync | header File.Sync成功直後 | 16バイト | 正常な空DB |
| init_before_dir_sync | DBディレクトリSync直前 | 16バイト | 正常な空DB |
| init_after_dir_sync | DBディレクトリSync成功直後 | 16バイト | 正常な空DB |
| init_before_parent_sync | 親ディレクトリSync直前 | 16バイト | 正常な空DB |
| init_after_parent_sync | 親ディレクトリSync成功直後 | 16バイト | 正常な空DB |

短いheaderはinspectと2回のOpenで拒否・不変を確認し、LOCKの再取得も確認する。
完全headerは形式v1と一致、record_count/latest_sequence/live_keys=0を確認し、
新しいPUTがsequence=1で成功し、Close/Open後にもバイナリ値00 ffを読み出せることを検証する。
短い既存ファイルを自動削除・再初期化する救済処理は追加しない。

## 復旧中: 4点の観測

親が3件の成功応答を保持: keep=00 ff、target=committed、empty=空値。
writerへ未応答DELETE(target)を1件だけ送り、完全header+key+CRC先頭1バイトの位置でSIGKILLする。
成功応答済みprefixは150バイト、部分末尾を含む全長は189バイト、正常sequenceは3。
そのログを開く別プロセスを次の各点で再びSIGKILLする。

| 停止点 | kill後のログ長 | 次のOpen |
|---|---:|---|
| recovery_before_truncate | 189 | 部分末尾を診断し150へ修復 |
| recovery_after_truncate | 150 | 追加修復なしで正常Open |
| recovery_before_sync | 150 | 追加修復なしで正常Open |
| recovery_after_sync | 150 | 追加修復なしで正常Open |

全ケースで成功応答列の辞書とlatest_sequence=3を照合する。未応答DELETEは見えず、targetの値を保持。
診断の有無・最終正常境界を検証し、Close後の2回目Openでもログが正常prefixのままで診断を繰り返さないことを確認する。
LOCKを再取得したDBから次のPUTがsequence=4で成功することも確認。
1ケースにつきwriterと復旧プロセスを各1回killするので、初回作成9回と合わせて本単位で17回SIGKILLする。

## 正常系と異常系の対照

同じ実ファイルラッパー・ディレクトリ同期経路で停止だけを無効化し、初回作成と末尾復旧が正常終了する対照試験を置く。
未知のOpen停止点はI/O開始前にエラーにし、data.logが生成されないことを確認する。
既存の更新中16ケース・正常終了・不正要求拒否も全テストで再実行する。

## 再現手順

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
make check
./scripts/go-env.sh go test -count=1 -run '^TestCrash(Initialization|RecoveryOpen|OpenNormal)' -v ./internal/engine
./scripts/go-env.sh go test -race ./...
```

Go 1.27.4 linux/amd64、Ubuntu 24.04/WSL2、ext4。[環境記録](../../README.md)を参照。
専用一時ディレクトリ内の人工データだけを使用。各点のbytes、sequence、ack数はt.Logに出力する。

## 限界と次の作業

SIGKILLはOSページキャッシュを消さない。Sync前の完全headerやTruncate結果が残ったのは今回のプロセス障害の観測であり、
電源断時・ホスト停止時の残存、ディレクトリ名の永続化、WSL/保存装置のflush保証を証明していない。
今回の停止点は固定のAPI境界とheader8バイト地点だけ。全バイト中断・同時Closeは未検証。
複数seedと複合I/O故障は後続の別単位で扱った（下記）。

後続の[複数seed試験](seeded-crash-experiments.md)と[複合I/O試験](compound-io-experiments.md)は実施済み。
次はClose競合。CLI応答周辺、性能計測、デモは残る。段階3全体の完了とは扱わない。
