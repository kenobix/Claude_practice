# 段階3: 固定seed操作列と更新停止点の照合

2026-11-17 JST。[初回ハーネス](crash-experiments.md)を再利用した、今回の1作業単位。
[テスト実装](../internal/engine/seeded_crash_linux_test.go)のみを追加し、製品コード・形式v1・fixtureは変更しない。

## 実験条件と判定

- 固定seedは42、261118、20261117。各seedで6件の固定操作、48件の疑似乱数操作、targetの最終PUTを生成する。
  55件を1件ずつ子プロセスへ送り、親がackと連続sequenceを受信した操作だけを成功履歴へ追加する。
- 空値、バイナリ値、上書き、DELETE不在・再PUT、UTF-8キー、256バイトキーを含む。
  乱数PUTの値長は0/1/17/4096バイト。値上限1 MiBの停止試験や全seed探索ではない。
- 最後のtarget操作をPUTまたはDELETEとし、8停止点すべてと組み合わせる。
  3 seed × 2操作 × 8点 = 48回のSIGKILL。各seed/操作で正常EOF終了も行うため、合計54ケース。
- 同じ55件の履歴に対して停止点を比較する。任意の操作番号での停止や、1ケース内で複数回の停止は今回扱わない。
- 復旧辞書とsequenceは全ack列、または全ack列＋最後の未応答1操作と照合する。
  before_write/部分writeは未応答操作を許容しない。after_sync以降は操作全体の残存を要求する。
  after_ackと正常終了では56件すべてがack済み。
- 全候補キーと未使用キーをGETし、live_keysも照合して余分なキー・部分値・不存在/空値の混同を検出する。
  PUT/DELETE件数とログ長は履歴から直接計算し、record.Encodeや位置索引を正解モデルに使わない。
- 部分writeはinspectの非変更、incomplete_tail、失敗位置、修復診断の旧サイズ・正常境界、切り戻し後の全バイトを確認する。
  それ以外では診断なし・ログ不変を要求する。
- 2度目のOpenで辞書・全report・ログが不変となること、次sequenceの更新が3度目のOpenでも読めることを確認する。
- 子は既存の15秒期限・cleanup・停止通知ハンドシェイクを使う。全DBはt.TempDir内の人工データ。
  ケース名にseed/操作/停止点を残し、失敗時は生成した全操作列も出力する。

## 観測結果

Go 1.27.4 linux/amd64、Ubuntu 24.04/WSL2、ext4（[環境](../../README.md)）。54ケースすべて成功。

| seed | 55件の正常prefix | 最終PUT全体を含むログ | 最終DELETE全体を含むログ |
|---|---:|---:|---:|
| 42 | 61,241 B | 61,288 B | 61,283 B |
| 261118 | 28,261 B | 28,308 B | 28,303 B |
| 20261117 | 40,514 B | 40,561 B | 40,556 B |

| 停止条件 | 親のack数 | 復旧sequence | 処置 |
|---|---:|---:|---|
| before_write | 55 | 55 | 変更なし |
| partial_header | 55 | 55 | prefix+10 Bをprefixへ切り戻し |
| partial_body | 55 | 55 | prefix+39 Bをprefixへ切り戻し |
| after_write / before_sync | 55 | 56 | 完全な未応答操作を再生 |
| after_sync / before_ack | 55 | 56 | 同期済みの未応答操作を再生 |
| after_ack / normal_exit | 56 | 56 | 全成功応答列を再生 |

復旧sequenceが55なら次の更新は56、56なら57となり、追加の再Openでも全辞書が一致した。
Sync前の全長レコードも今回のSIGKILLでは残ったが、電源断時の残存保証ではない。

## 再現と未検証事項

```bash
cd ~/work/Claude_practice-261118/261118/kv-engine
./scripts/go-env.sh go test -count=1 -run '^TestSeededCrashRecovery$' -v ./internal/engine
make check
./scripts/go-env.sh go test -race ./...
```

OSページキャッシュはSIGKILL後も生存する。ホスト停止、電源断、WSL仮想ディスクと装置flushの保証は未検証。
本ハーネスのackはライブラリ返却後のテスト応答で、製品CLIのClose/stdout周辺の停止試験ではない。
複合I/O障害は[後続単位](compound-io-experiments.md)で検証済み。部分write+ENOSPC後の修復失敗、
Sync失敗後の再Open失敗を組み合わせ、成功の誤報・失敗状態の更新継続・成功済みprefixの変更がないことを確認した。
Closeと各APIの同時競合も[後続試験](close-concurrency.md)で検証済み。
次は製品CLI応答周辺。段階4の計測、段階5のデモも残り、段階3全体の完了とはしない。

後続の[CLI応答境界単位](cli-response-experiments.md)も実施済み。
現在の次の作業は段階3の受入条件・実験対応表と完了判定。上記の「次」の記述は実験当時の順序を示す。
