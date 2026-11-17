# 復旧前診断の出力完了条件

段階3の受入条件照合中に、診断出力の短いwriteを見逃す問題を検出した。
`json.Encoder.Encode`はWriteのerrorを調べるが、nil errorと短いバイト数を返すWriterを拒否しない。
修正前は診断が0バイト/一部だけでもOpenが成功し、64バイトのログを54バイトへ切り戻していた。
これは「診断失敗なら変更しない」という[既存契約](durability.md)に合わないため、完了判定より先に修正した。

## 判定と処理順序

`tail_truncate_planned`イベント全体をJSONへ変換し、LFを付けて診断Writerへ一度だけ渡す。
errorがなく、返却バイト数がイベント全長と等しい場合にのみ、Truncate→File.Syncへ進む。
短いwrite/nilは`io.ErrShortWrite`、Writerが返したerrorは元の原因を保持してラップする。
全バイト受理後でもerrorが返れば失敗とし、追記や再送で診断の重複・継続を試みない。
失敗時はDB=nilでOpenを終了し、ログFDとLOCKを解放する。

イベント名、field、JSONエスケープ規則、末尾LFは既存と同じ。ログ形式v1とfixtureの変更もない。
短いwrite/nilはio.Writer契約違反の注入だが、復旧による変更に先立って明示的に拒否する。
Writerの全長受理は診断先の永続化・fsyncを意味せず、診断の保存保証を追加したものではない。

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
段階3は引き続き完了判定前。次に受入条件と各実験・故障モデル・未検証条件の対応表を整理する。
