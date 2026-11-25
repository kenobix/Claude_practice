# 復旧前診断の出力完了条件

段階3の受入条件を照合した際に、診断出力が途中で終わっても検出できない問題が見つかった。
`json.Encoder.Encode`はWriteが返すerrorを調べるが、書き込みが一部だけでも、Writerがnil errorを返せば拒否しない。
修正前は、診断の出力が0バイトまたは一部だけでも、Openが64バイトのログを54バイトに切り詰めて成功していた。
「診断失敗なら変更しない」という[既存契約](durability.md)に合わないため、段階3の完了判定より先に修正した。

## 判定と処理順序

`tail_truncate_planned`イベント全体をJSONに変換し、LFを付けて診断Writerへ一度だけ書き込む。
errorがなく、返された書き込みバイト数がLFを含む全長と一致した場合に限り、Truncate、File.Syncの順に進む。
書き込みが短くerrorがnilなら`io.ErrShortWrite`とする。Writerがerrorを返した場合は、元の原因を保持したままラップする。
全バイトが受理されても、errorが返れば失敗として扱う。残りの追記やイベントの再送による診断出力の継続は試みない。
診断に失敗した場合、OpenはDB=nilで終了し、ログFDとLOCKを解放する。

イベント名、field、JSONエスケープ規則、末尾LFは既存と同じ。ログ形式v1とfixtureの変更もない。
短いwrite/nilはio.Writer契約違反の注入だが、復旧による変更に先立って明示的に拒否する。
Writerが全バイトを受理しても、診断先の永続化やfsyncを確認したことにはならない。診断の保存保証は追加していない。

## 回帰試験

`internal/engine/diagnostic_linux_test.go`の`TestRecoveryDiagnosticCompletion`で実ファイルを使用する。
PUT k=v（sequence 1）までの正常prefix 54バイトに、次DELETEの先頭10バイトを付けた64バイトを人工的に作る。

| 診断Writerの結果 | Open | Truncate/ログSync | ログ |
|---|---|---|---|
| 0バイト、nil | ErrShortWrite、DB=nil | 0/0回 | 元の64バイト全体を保持 |
| 部分バイト、nil | ErrShortWrite、DB=nil | 0/0回 | 元の64バイト全体を保持 |
| 部分バイト、EIO | 原因EIO、DB=nil | 0/0回 | 元の64バイト全体を保持 |
| 全バイト、EIO | 原因EIO、DB=nil | 0/0回 | 元の64バイト全体を保持 |
| 全バイト、nil | 成功 | 1/1回 | 正常prefixの54バイト |

修正前は最初の2ケースが失敗し、残り3ケースは成功。修正後は5ケースすべて成功。
呼出しは各1回、イベントのold_size=64/last_valid_offset=54/reason/LF、実FD閉鎖1回とLOCK再取得を確認する。
失敗後に完全なWriterへ変更すると同じログを復旧でき、k=v・sequence 1・live_keys=1を保持する。
その次のOpenは診断不要で、再修復せず54バイトの全内容も不変となる。

```sh
./scripts/go-env.sh go test -count=1 -v ./internal/engine -run '^TestRecoveryDiagnosticCompletion$'
make check
./scripts/go-env.sh go test -race -count=1 -timeout=3m ./...
```

すべてのDBは`t.TempDir()`内。新規SIGKILLは追加せず、既存の停止試験を含む回帰テストを再実行した。
EIOは注入で、実媒体障害ではない。ホスト停止・電源断・WSL/装置flush・診断先の永続化は未検証。
本修正時点では段階3の完了判定を次へ残した。後続の[受入条件の照合](stage3-acceptance.md)で、
定義した故障モデル内の完了を判定済み。電源断・診断先の永続化を保証する判定ではない。
