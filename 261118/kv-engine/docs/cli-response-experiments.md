# CLIのClose・成功応答境界

## 契約と今回の修正

永続CLIの更新順序は`PUT/DELETE → File.Sync → 索引更新 → Close → stdout`。
Closeに失敗した場合は標準出力に成功応答を書かない。標準出力への書き込みが失敗した場合もexit 6となり、同期済みの更新はロールバックしない。
呼び出し元は、応答を最後まで受け取ったことと、プロセスがexit 0で正常終了したことを確認する。
応答がない場合、応答が途中で終わった場合、または応答が完全でもプロセスが異常終了した場合は、更新が残っていないと仮定せず、読み直して確認する。
再試行の重複排除はなく、同じPUT/DELETEを再実行すればsequenceが進む。

今回、`json.Encoder`に短いwriteとnil errorを返す出力先を渡すと、JSONが途中でもexit 0になることを再現した。
修正前はPUT/DELETE/inspectのzero/shortの6ケースが失敗。GETは既に短いwriteを検出していた。
永続CLIのstdoutを検査するWriterで統一し、短いwriteを`io.ErrShortWrite`に変換してexit 6を返す。
短いnil writeはio.Writerの契約に従わない注入だが、成功と誤認しない防御を明示する。
JSON形式、ログ形式v1、公開fixture、同期順序は変更しない。

## ハーネスと故障モデル

- `response_linux_test.go`: 実DBの操作後にstdoutのEIO/EPIPE・0バイト/nil・部分/nil・部分/EIOを注入。
  PUT/DELETE/GET/inspect各6ケース（正常含む）。stdout内から独立inspectできることでもLOCK解放を確認する。
- 実DBのCloseを完了した後にEIOを付加し、PUT/DELETE/GET各々でClose呼出し1回・stdoutなし・exit 6を確認。
  実際のFD閉鎖失敗やLOCK閉鎖失敗を再現したものではない。
- 空値GETは0バイトのstdoutとexit 0。不存在との区別は既存CLI試験も再実行する。
- `response_crash_linux_test.go`: テスト実行ファイル内で製品と同じ`runPersistentWithOpen`を通し、実engine.DBを使用する。
  Openを関数引数として渡せる最小interfaceを設け、Closeとstdoutのラッパーで停止させる。
  製品コマンドに停止flag・環境変数・sleepは追加しない。環境変数の解釈はtestファイルのみ。
- 子が専用FDへ停止点を通知し、親は通知後にSIGKILLして実際の終了signalを確認する。
  stdoutは別pipeで、部分/完全応答は親がkill前に読み取る。無出力点ではkill後にEOFまで読んで0バイトを確認する。
  子の期限15秒、通知/読取り期限10秒、失敗時のkill/Wait/FD cleanupを設ける。
- 正常終了PUT/DELETEと未知停止点の事前拒否を対照にする。
- 別途buildした製品バイナリのstdoutをLinux `/dev/full`へ接続し、PUT/DELETEがexit 6で終了して更新は残ることを確認。
  実データや実ディスク容量を消費する試験ではない。EPIPEはWriter注入であり、実pipeのSIGPIPE試験ではない。

## 停止点と観測

各ケースの前に3件の更新成功・Closeを確認する。target=old、keep=`00 ff 0a 01`、empty=空値。
146バイト・sequence 3の成功済みprefixへ、最後のPUT（target=`00 ff 0a 01`）またはDELETEを1件だけ実行する。

| 停止点（PUT/DELETE各々） | 親が受け取るstdout | 停止中のLOCK | 再Open |
|---|---|---|---|
| before_close | なし | 保持 | sequence 4 |
| after_close | なし | 解放 | sequence 4 |
| before_response | なし | 解放 | sequence 4 |
| partial_response | 先頭5バイト `{"seq` | 解放 | sequence 4 |
| after_response | 完全な `{"sequence":4}` + LF | 解放 | sequence 4 |

10ケースでSIGKILLを10回実施。どの点も更新のSync成功後なので、最後の更新を含む結果を要求する。
PUTは192バイト・live_keys=3、DELETEは188バイト・live_keys=2。全レコード数4とPUT/DELETE件数も照合する。
成功済みprefix全バイト、keep/empty、不在キーを保持し、targetは完全な新値または削除状態となった。
2回の再Openでも辞書・sequenceが一致し、ログ全バイトが不変。未完の値・重複レコードは許容しない。
after_responseでは親が完全な出力を受け取ったが、子はexit 0まで進んでいない。完全な出力の受信と正常終了は別々に確認する。

## 再現と残課題

module内で実行する。

```sh
./scripts/go-env.sh go test -count=1 -v ./cmd/kv -run '^(TestPersistent(Response|CloseFailure|EmptyResponse|CLIOutput)|TestCLIResponse)'
make check
./scripts/go-env.sh go test -race -count=1 -timeout=3m ./...
```

全DBとbuild先は`t.TempDir()`。今回の43ケースに加えて、既存の更新/Open停止・複合I/O・Close競合・fixture試験を再実行する。
プロセス停止後もOSキャッシュが生存する条件であり、ホスト停止・電源断・装置flush・実媒体障害は未検証。
任意スケジュールや全I/O障害の組合せを網羅した主張もしない。
次の1単位は、段階3の受入条件を既存実験・故障モデル・未検証条件へ対応付け、完了可否を判断すること。
この単位だけでは段階3全体の完了とせず、段階4の性能計測・段階5のデモには進まない。
