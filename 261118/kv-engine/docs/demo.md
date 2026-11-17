# 第三者向けデモ: 保存・停止・復旧・破損拒否

## 準備と実行

Linuxのローカルext4、Go 1.27.4、Python 3（標準ライブラリのみ）が必要。
WSL2はLinux側の保存先を使う。[Go導入手順](development.md)を済ませ、module内で実行する。

```sh
sh scripts/setup-go.sh
make build
# Goのテストハーネスも動くため、最初はコンパイルに時間がかかる場合がある
make demo
```

デモは既存DBを引数として受け取らない。Pythonの専用TemporaryDirectory、Goテストのt.TempDirに人工データを作る。
明示した一時親で実行し、記録を残す場合は次のようにする。

```sh
demo_parent=$(mktemp -d)
TMPDIR="$demo_parent" PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/demo.py --kv ./bin/kv > /tmp/kv-demo.jsonl
# exit 0と最後のdemo_passを確認してから、空になった親だけを除く
rmdir "$demo_parent"
```

生成DBや実行記録はGitへ追加しない。デモは自分で作った専用領域だけを清掃する。
強制終了時は一時領域が残り得る。利用者の既存DBは投入しない。
各CLIに15秒、停止テストに90秒の期限があり、期待したexit/値/境界が違えばdemo_failed・exit 1で止まる。
実装は[demo.py](../scripts/demo.py)、統合検証は[demo_linux_test.go](../tests/demo_linux_test.go)。
Python 3がない場合はデモ/統合テストを未実行として報告し、成功やskip扱いにしない。

## 3〜5分の説明順

以下は説明の目安であり、スクリプト実行時間を実測して3〜5分と保証するものではない。
JSON Linesの各eventを説明しながら、CLIのexit・応答・復旧結果を追う。
`stdout_base64`は生バイナリ/空値/JSONを同じ形式で記録するための表示用。製品CLIの出力形式は変更しない。

| 目安 | 見せること | 期待結果 |
|---|---|---|
| 0:00〜1:00 | normal: PUT old→空値PUT→バイナリ上書き→別プロセスGET→DELETE→GET/inspect | sequence 1〜4。バイナリ`00 ff 0a`を保持、空値exit 0・0 B、不在exit 3。record_count=4/live_keys=1 |
| 1:00〜2:00 | sigkill after_ack: 親が成功応答を受信したwriterを実際にSIGKILL | ack=6/recovered_sequence=6、成功済みprefixを保持。SIGKILLはテストハーネスがWaitStatusまで確認 |
| 2:00〜3:00 | sigkill partial_body、続いてtail fixtureのCLI inspect→GET→再GET | 実停止試験はack=5/recovered_sequence=5、268→229 B。別の公開fixtureはinspect exit 5・64 B不変→診断後54 Bへ復旧、k=v、再GETは診断なし |
| 3:00〜4:00 | corrupt: CRC破損fixtureのinspect→GETを2回 | offset=16/valid_bytes=16、record_count=0、exit 5、91 B全体不変。後続DELETEを黙って採用しない |
| 4:00〜5:00 | 最後のdemo_passと保証条件を説明 | scenarios=3/cleanup=true/power_loss_verified=false。成功応答と電源断保証を区別 |

停止ハーネスとfixtureのDBは別物。partial_bodyで停止させたDBを製品CLIへ渡したようには説明しない。
[既存ハーネス](../internal/engine/crash_linux_test.go)は製品engineの実FDを包むテスト実行ファイルで、
親が停止通知を受けてSIGKILLする。製品CLIへの障害flag追加や、sleepで停止位置を推測する処理はない。
公開fixtureは[固定hex](../tests/fixtures.md)のコピーであり、その場でプロセスを止めて生成したログではない。

## 実験の照合表と出力

| event | 何を検証したか |
|---|---|
| cli | 製品バイナリを別プロセスで起動。operation/key/exit/stdout_base64/stderrを表示。JSON応答・生値・diagnosticをスクリプトで照合 |
| sigkill | 指定した既存Testのpassとack/sequence/bytesのログを確認。テスト関数が実SIGKILL・辞書・prefix・次更新をassertする |
| fixture | 公開人工データのコピーであることとサイズを明示。原本は編集しない |
| scenario_pass | 当該シナリオの値・不存在・境界・不変条件をすべて確認 |
| demo_pass | 3シナリオとTemporaryDirectory清掃が完了。電源断検証ではない |

このデモは小さな説明用ケース。[段階3の受入表](stage3-acceptance.md)の全停止点・複合I/O・Close競合を置き換えない。
`make check`のデモ統合テストはこのスクリプトと実製品プロセスを起動するため、Pythonの構文チェックだけではない。
既存CIも同じ統合テストを実行する。Docker・外部DBの検証は追加していない。

## 実施済みと残る受入確認

2026-11-17、Go 1.27.4 linux/amd64、Python 3、Ubuntu 24.04.4 LTS / WSL2 / ext4で、
対象統合テストと上記の明示した一時親での手動デモが成功した。
実行記録は`/tmp/kv-stage5-demo.jsonl`（Git外）。normal/tail/corruptと最後のdemo_passを確認。
不在バイナリはexit 1・demo_failed、既存sentinel保持・DB未生成も統合テストで確認した。

このローカル試験は既存worktreeのGo・キャッシュを使う。新規runnerのGo取得/照合とデモ統合試験の結果は
当該commitのCI/PRで別に確認する。クリーン環境の導入からMVP全体を照合する最終受入は未実施。
本人が書込み順序を説明できることも、この自動実行から判定しない。
段階5とMVP全体の最終完了判定は保留し、次の1単位でクリーン環境の再現・受入確認を行う。
全受入条件を満たした後は独立レビューを提案し、自動で発展機能へ進まない。
