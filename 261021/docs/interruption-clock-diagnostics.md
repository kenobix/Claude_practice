# T03/T13の中断・時計診断

2026-11-01、基点`cfd7ef7`から、T05で残った2件の一過性失敗を調べた。
[検証JSON](reviews/20261101-t03-t13-diagnostics.json)にコマンド・終了値・hashを保存する。

## 分かったことと未確定のこと

| 対象 | 観測 | 判断 |
|---|---|---|
| T03・SIGTERM後10秒の終了待ち | 基点、診断付き試験、独立3反復、全suiteで自然再発なし。人工的に台帳書込みを止めると、signal受信後に停止し、stackと未確定の監査意図を保存できた | 元の停止位置は未特定。今回の人工停止を過去の原因としない |
| T13・直前のレビュー時刻がfuture | 時計をmockしない合成FakeRegistryで2回再現。2回目はレビューUTC `03:07:08.773964`に対し検証UTC `03:07:05.744075`、差3.029889秒で拒否。同じ承認関数のレビュー時刻検証行から発生 | 現在の環境ではUTC逆行による拒否を確認。過去のSQLite失敗時刻は記録がなく、同一原因とは断定しない |
| 実行環境の時計 | 10万sampleでCLOCK_REALTIMEが4回逆行。単調時計・BOOTTIME・MONOTONIC_RAWの逆行は0回 | 経過時間には単調時計を使う。UTCは証拠日時の検証に必要で、単調時計に置換して承認しない |

独立した2万sampleでは逆行0回だった。その後の10万sampleでは約2.956、2.927、3.873、2.921秒の逆行を観測した。
短い期間で0回でも時計の正常性を証明できない。今回の実行環境はWSL2 kernelだが、どのWindows/WSL同期機構が変更を起こしたかは特定していない。
通常のSQLite承認試験の反復は成功しており、FakeRegistryでの再現と区別する。
OSの読取では`NTP=yes`、`NTPSynchronized=yes`、timesyncdはrunning、clocksourceはtscだった。同期flagがyesでも、今回観測した逆行を否定できない。

## 実装した診断

`utc_time`の未来拒否に、入力UTC、実際の比較UTC、先行microseconds、拒否時monotonic_nsを例外noteとして追加した。
未来拒否条件、UTC必須条件、承認の時刻順序、tag書込み順序は維持する。
人工的に1µs先行させた場合も拒否し、UTC同値は通す。時計が戻った場合の承認はtagを変更せずblockedを維持する。
時計を丸める、現在時刻まで待つ、自動retryする、といった処理は追加していない。

T03の使い捨て子processは、signal受信/handler例外、HTTP client終了、台帳書込前後、process終了をUTC/monotonic/PID付きで記録する。
正常SIGTERMはexit 130、SIGKILLは-9を要求し、永続journalの未確定IDと自動再開拒否も確認する。
通常の終了待ちは元の10秒のまま。5秒経過時のwatchdogはstackを書くだけでprocessを終了させない。
人工台帳停止ケースだけは1秒の待機失敗を期待し、0.25秒のstack採取後に試験が子processをkill/reapする。

試験のtraceは`tests/failure_diagnostics.py`で作る。
`BIKE_MLOPS_TEST_DIAGNOSTICS_DIR`を指定すると独立UUIDのdirectoryに保存する。
未指定時は一時directoryを使い、失敗時には削除前にtrace/stack/stderrをunit出力へ出す。
診断I/Oは実行タイミングへ影響するため、診断付き試験の成功だけで元の不具合が解消したとは判断しない。

## 再発時の採取

プロジェクト`261021`から実行する。新しいpathを使い、stdout/stderrと終了値も保存する。
以下の試験とprobeは合成fixture専用で、稼働DBや実MLflowへの接続は行わない。

```bash
cd ~/work/claude_practice/261021
TRACE_LOG="data/reports/clock-interrupt-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$TRACE_LOG"
export BIKE_MLOPS_TEST_DIAGNOSTICS_DIR="$PWD/$TRACE_LOG/traces"
set -o pipefail
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 100 \
  .venv/bin/python -m unittest discover -s tests -p test_capital_request_journal.py -v \
  2>&1 | tee "$TRACE_LOG/journal.log"
TRACE_EXIT=${PIPESTATUS[0]}
printf '\nEXIT_CODE=%s\n' "$TRACE_EXIT" | tee -a "$TRACE_LOG/journal.log"
```

SQLite側は同じ方法で`-p test_capital_deployment_evidence_publisher.py -k report_artifact_review -v`を実行する。
時計を独立採取する場合と、実時計を使って合成承認の拒否を調べる場合は、別の新規directoryを指定する。

```bash
.venv/bin/python scripts/diagnose_runtime_clocks.py \
  --output "$TRACE_LOG/clocks" --samples 20000 --interval 0.001
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python tests/probe_decision_clock.py \
  --output "$TRACE_LOG/natural-decision" --samples 160 --interval 0.2
```

probeの`observed_natural_future_rejection`は「時計をmockせず未来拒否を観測した」という結果で、承認成功ではない。
`no_rejection_in_bounded_observation`も安全性や原因解消の証明ではない。
自然な拒否時には`future-rejection-traceback.txt`、比較時刻、tag不変/blockedを保存する。

## 残る調査

1. T03自然再発時に、parent送信とchild受信、HTTP終了、台帳fsync、interpreter終了のどこで停止したかをtrace/stackで判定する。受信記録がなければsignal配送・schedulingも調べる。
2. 現在の実行環境でUTCを動かした機構を切り分ける。OS同期状態の読取結果だけでは発生源を断定しない。時計設定やWSL再起動は今回実施していない。
3. 独立した評価・実承認・運用測定の前に、当該実行環境でUTCと単調時計を記録する。未来拒否を緩和して実候補をeligibleにしない。
4. T13のHTTP部分書込み/応答喪失/競合decisionと実採否、T07/T17の配信復旧は引き続き別作業。

local全suiteは340件成功。最後の制御時計fixtureの固定後は対象decision 9件を再検証し、最終source全体は実装commitのCIで確認する。
実Capital候補の精度・監視・配信の証拠には流用しない。
