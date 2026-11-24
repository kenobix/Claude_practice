# 段階3・初回単位: 既存DB更新のSIGKILL復旧試験

2026-11-17 JST。[詳細計画](../../../261014/plan_database_engine.md)の段階3を進める最初の単位。
段階0〜2は完了し、末尾復旧とI/Oエラー注入は利用可能。最優先の未完了だった「実プロセス停止と応答履歴照合」を選択した。
初回作成/復旧中の停止点は本ハーネスを再利用した[後続単位](open-crash-experiments.md)で追加した。複合I/O障害は[後続試験](compound-io-experiments.md)で限定検証した。

## 実験の契約

- [テスト実装](../internal/engine/crash_linux_test.go)はLinuxのテストバイナリだけに含まれる。
  既存diskOpsで実ログFDを包み、製品コードのPUT/DELETE/Syncを通す。製品CLIの追加設定はない。
- 親がt.TempDir内に空DBを初期化し、子がそのDBをOpenする。初期化中の停止は今回の対象外。
- 親→子は1操作ずつJSON送信、子→親はready/ack/stopped/errorを返す。
  値はJSONの[]byte（Base64）。親が次の操作を送るのは前のackを受け取った後だけ。
- 子のackはライブラリのPut/Delete成功後に送るテスト用応答。製品CLIのClose/stdout処理そのものの停止試験ではない。
- 5件の成功応答を親で保持: バイナリkeep、target=old、goneのPUTとDELETE、空値empty。
  最後の1件だけtargetのPUTまたはDELETEを未応答状態にし、停止点で子を待機させる。
- 停止点通知を受信後、親がProcess.KillでSIGKILLを送り、WaitStatusでもSIGKILLを確認する。
  sleepで位置を推測しない。子は各ケース15秒以内に終えるようcontext deadlineとcleanupを設ける。
- after_ackだけは最後の成功応答も受信してから停止させる。他の点は未応答が最大1操作。
- 親が成功応答を受け取った全操作だけを適用した場合と、最後の未応答1操作も適用した場合について、それぞれ辞書とlatest_sequenceを復旧結果と照合する。
  期待する全キーをGetで読み、live_keysも照合する。値の一部だけが残る、想定外のキーが現れる、成功応答済みの操作が失われる場合は失敗とする。
- 不完全な末尾はinspectで変更せず診断後、通常Openで切り戻す。診断・境界・ロック再取得・次の更新も検証。
- 正常EOF終了ではデータを再読取可能、未知停止点は非zero終了かつログ不変であることを確認。

## 停止点と今回の観測

PUTとDELETEの両方で同じ結果になった。停止前のログは229バイト、最終正常sequenceは5。

| 停止点 | 実際に止める位置 | 復旧sequence | 末尾切り戻し |
|---|---|---:|---|
| before_write | 追記WriteAtの前 | 5 | なし |
| partial_header | header先頭10バイトを書いた直後 | 5 | 239→229バイト |
| partial_body | 完全header＋key＋本文1バイトの直後（DELETEはCRCの先頭） | 5 | 268→229バイト |
| after_write | レコード全体write直後、Sync前 | 6 | なし |
| before_sync | 更新File.Syncの呼出し直前 | 6 | なし |
| after_sync | File.Sync成功直後、索引公開前 | 6 | なし |
| before_ack | 索引公開・Put/Delete返却後、応答前 | 6 | なし |
| after_ack | 成功応答送出後（親が受信してからSIGKILL） | 6 | なし |

全長ログはPUTで275バイト、DELETEで271バイト。after_ackの成功応答数は6、その他は5。
Sync前の完全なレコードが残ったのは、今回のプロセス停止試験での観測である。電源断時にも残るという保証ではない。
Sync後・未応答の操作も残り得るという既存設計を確認できた。

## 再現手順

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
make check
./scripts/go-env.sh go test -count=1 -run '^TestCrash' -v ./internal/engine
./scripts/go-env.sh go test -race ./...
```

各ケースはt.Logで操作、停止点、成功応答数、復旧sequence、旧サイズ、正常境界、修復の有無を出力する。
人工データだけを使用し、DB/子プロセスはテスト終了時に清掃する。通常DBファイルをGitへ残さない。
環境はGo 1.27.4 linux/amd64、Ubuntu 24.04/WSL2、ext4（[環境記録](../../README.md)）。

## 未検証事項と次の単位

初回単位の停止点は固定8箇所、操作列も固定。初回header/file/dir/parent Sync中と復旧Truncate/Sync中は
[後続単位](open-crash-experiments.md)で検証。複数seedとI/Oエラーの組合せ、製品CLI応答周辺は未検証。
複数seedの更新停止試験は[後続記録](seeded-crash-experiments.md)で実施済み。複合I/O障害も[後続試験](compound-io-experiments.md)で検証した。[Close競合](close-concurrency.md)も実施済みで、次はCLI応答周辺を扱う。

SIGKILLはOSページキャッシュを消さない。ホストクラッシュ、WSL仮想ディスク、装置flush、電源断耐性は実証していない。
この単位の成功を段階3全体やMVP全体の完了とは扱わない。

後続の[CLI応答境界単位](cli-response-experiments.md)も実施済み。
現在の次の作業は段階3の受入条件・実験対応表と完了判定。上記の「次」の記述は実験当時の順序を示す。
