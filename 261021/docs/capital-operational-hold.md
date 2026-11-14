# 実候補の運用測定保留と部分復元（2026-11-14）

**実API・監視と対応復元は部分受入。運用測定はUTC逆行のため保留、T13b採否はblocked。**
[先行runtime受入](capital-runtime-acceptance.md)、[最終証拠索引](reviews/20261114-capital-operational-restore-checks.json)、
[作業別policy](product-completion-policy.md)を参照する。時計研究は再開していない。

## 1回の実測と、その後の停止判断

同じcandidate `e2df579…`、専用registry version1、登録artifact SHA `13dbe8447e816b29ecf76c1cf4e173f679a50204ab8d9d682c113ed405810f8c`。
04-01〜04-08未満の訓練内168時間を巡回する固定planを使い、attemptは`918ccaf6-d342-41aa-8ce1-e3b52f31f213`の1件だけ。

| 保存された数値・整合検査 | 結果 |
|---|---|
| 暖機 / 測定 / error / retry | 50 / 1,000 / 0 / 0 |
| nearest-rank p95 | 84.049708ms（数値上は250ms以下） |
| DB監査 | 全1,050行、入力出典・candidate/artifact・保存予測を照合 |
| journal | 2,101 eventのhash連鎖・要求順・latencyが一致 |
| 製品のlinked recheck / stdlib別process | 要求数・p95・hash・監査一致。ただし後者がUTC順序違反を検出してexit1 |

専用hostはRyzen 7 7735HS、WSL2、8 logical CPU、約29.43GBメモリ。
APIはOMP/OPENBLAS各2 thread、container CPU/メモリ上限なし。host/container依存版・実image・負荷・資源原本を保存した。
p95はHTTP要求（transport/JSON decode）を対象にし、observer/要求journalのfsyncは別の全体経過へ含める固定仕様。
PC上の未精密校正の実測で、本番SLOでも精密秒の保証でもない。

測定前は通常同期表示がyes、約3秒31点でUTC/monotonic/perf_counterの順序が正常だった。
しかし全journalを後から検算すると次の2対が逆行していた。短区間の正常を測定区間全体へ一般化しない。

| sequence / request | 前→後のUTC | 逆行量 |
|---|---|---:|
| 940→941、measured0419結果→0420送信 | 11:45:21.888190→11:45:21.846610 | 41.580ms |
| 1723→1724、measured0811送信→結果 | 11:45:51.801170→11:45:51.799001 | 2.169ms |

原本のrunner出力は`completed / measurement_status=passed`で、実行完了と数値条件の状態を表す。
**今回の受入判断は`measurement_acceptance_on_hold_clock_order_violation`**。
`operational-acceptance-hold.json`を別に保存し、report/journal/運用台帳の過去bytesを書き換えていない。
registryの`operational_gate_status=not_evaluated`、`deployment_permission=blocked`を維持し、
この試行の`capital_operational_*`証拠tagも記録していない。再測定・reopen・clamp・resyncをしない。
調整process/同期源を調べず、原因は未特定のまま。過去T03/tick11000の原因へ遡及適用しない。

## 時計に依存する主張をしない、隔離した部分復元

所有確認済みの専用API/MLflowだけを静止してwriterを止め、同じPostgreSQL内のregistry/audit両DBをdumpしartifact全体と組にした。
収集中にsourceのtable/sequence/schema manifestが変わっていないことも確認。
別project `capital-restore-20261114`、別の2 volume、空きlocalhost portへ復元した。

- registry/auditの全table内容・sequence・column manifestが復元前後で一致。
  auditは観測32,849、予測2,512、monitor1,464、deployments0行。
- artifact全8ファイルのbytes/hashが一致。復元先のskopsだけを故意に破損し、実loaderがhash不一致でdeserialize前に拒否した。
  計画された負例として復元先のcopyだけをbackup bytesへ戻し、元artifactは変更しない。
- 復元先APIのready200、同candidate/version/artifact/profileを確認。保存済みrequestの再送と、新IDの予測700.7861957728056・DB監査が一致。
- 復元先container/volume/networkはcleanup済み。元の専用stackは再開しready200、blocked維持を確認。
  既存UCIの5コンテナID/image/起動時刻は試験前後で不変。

これは同versionの対応復元・保存結果維持の部分証拠である。二版切替、Caddy切替、旧REAL移行、承認operationの成立とは呼ばない。
別のAPI停止観測probeは、すでにsource再開後でrunning=true/connection resetだったため、停止検知caseの合格には使わなかった。
raw dumpとartifactのprivate backupはignored directory（0700）で保持し、公開索引にはhashだけを載せる。

## 残課題と再測定を勝手に行えない理由

全22 caseの一覧を`final-gap-inventory.json`へ整理した。
実観測のある8領域はcontract review待ち、5領域は部分的、9領域は未実証。全categoryの正式attestation/bundleは未作成。

| 優先度 / 難易度 | 次の作業 |
|---|---|
| P1 / D3・Sol | 証拠consumerがhashの正しいjournalでもUTC逆行をfail closedで拒否することを、負例と回帰/CIで製品側へ結合する。今回の手動検算による保留を迂回できないようにする。future拒否や時刻を緩めない |
| P1 / D3・Sol | completedだが事後に不適格となった試行の理由付き無効化/reviewを、原本と履歴を保持する形で設計する。現retry reviewはfailed/started/interruptedだけを許可しcompletedを拒否する。台帳削除/状態書換え/別台帳作成/自動reopenで回避しない |
| P1 / D3・Sol | 実candidate結合を保つ監視の不足負例、audit書込503、破損時HTTP readiness、二版/停止/Caddy/中断復旧、旧REAL移行、承認operation/timeout metadataを隔離して補う。合成CI成功を実証へ転用しない |
| P1 / D3・Sol | 分布警告1,336・誤差警告234の理由付きreview。閾値や評価窓を変えず、実producer原本との対応を説明する |
| P1・証拠後 / D3・Sol＋別承認 | validな運用試行と全producer証拠を揃えてT13b採否。今は未達 |

別PCへ移せば正常とは認めず、候補・正式消費台帳・今回の運用台帳/保留原本も合わせて移す。
git pullだけでignored成果物は移らない。**別PCでの再測定も、上記の事後不適格review契約が未整備の現時点では開始しない。**
ユーザーに今すぐ必要なコマンドはない。独立した製品の拒否検証・不足case整理は、時計研究を再開せず進められる。

先行文書commit9a1f433の[自動CI検算](reviews/20261114-capital-runtime-docs-ci-checks.json)は590unit/全job成功、
16 artifact memberとarchive-only再計算/cleanupを確認。既存合成CIの結果であり、今回の実運用保留を解除しない。
