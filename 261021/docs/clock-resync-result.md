# 1回のresync要求後の時刻設定・停止記録

2026-11-09、基点`d285aa9`。ユーザーが案Aを実行した実結果をreviewした。
[証拠索引](reviews/20261109-resync-result-checks.json)、[保守案](clock-host-maintenance-plan.md)、
[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**要求1回のあと、Windowsのログは受信・同期開始・時刻設定・停止を記録した。事後queryは失敗し、全時計資格は未達。**
コマンド応答は「シャットダウン中のため同期しなかった」だったが、応答だけから「時計は変更されなかった」と判断してはいけない。

## 原本と実行範囲

`clock-host-resync-d77eedf4-87ab-4e92-b108-daa760b07f6a`の原本13file/manifest12件とhelper SHAが一致した。
helperはignoredな`data/reports/clock-resync-guide-20261109/Invoke-ClockResyncOnce.ps1`、SHA
`f77404569cc96f957e379e3009c88be2f35aa787d71bc6068f1affe111c5dd3e`。
`/resync`は1回だけで、config/rediscover/service起動・停止の命令は含まない。
host/開始前serviceは前回と同じ報告値。設定原本のSHAも前回と一致した。
ユーザーがWindows時計操作を実施したことと、チャット側の追加操作は読取だけだったことを分ける。

| 記録 | 結果 |
|---|---|
| before configuration/status/peers | すべてnative exit0。既存設定/未同期状態のgateを通過 |
| resync-once | native exit0だが、本文はシャットダウン中のため同期しなかったと報告 |
| after status/peers | 両方`0x80070426`、native exit−2147023834 |
| service-after.json | 0byte。正常なJSONではなく、事後identity取得不能の原本として保持 |
| failure.log | service ProcessId<=0のためhelperが停止。サービスの自動起動/再試行なし |
| 後続の限定CIM/sc読取 | Stopped/Manual/PID0、Win32/service exitとも0 |

actionの`attempted_once/native_exit0`は操作試行の記録であり、成功の判定ではない。
空のJSONを成功のidentityとして補完したり、原本を書き換えたりしない。
13原本を別ignored directoryへsnapshot保存し、実際のquery/失敗/時刻bracket/元bytes/SHAを保持した。

## 同じ要求区間に存在したOperational記録

既に有効だったTime-Service/Operationalの、host表示UTCで02:55〜02:56の最大64件を読み取った。
該当5件はいずれもXMLのProcessID34104で、開始前serviceのPIDと対応する。
event単体には開始時刻/exe SHAが無いため、それだけで無execの連続性を認証するものではない。

| RecordId / EventId | host表示UTC 2026-11-09 | 記録された内容 |
|---|---|---|
| 2051 / 266 | 02:55:31.8289280 | 管理者の明示的resync通知、ReasonCode0 |
| 2052 / 264 | 02:55:31.9460790 | NTP peerからのデータ受信 |
| 2053 / 265 | 02:55:31.9658846 | time.windows.comを参照した同期開始、local stratum5 |
| 2054 / 261 | 02:55:31.9680008 | OldTime `.965Z`→NewTime `.967Z`への時刻設定を記録 |
| 2055 / 258 | 02:55:31.9714235 | 停止を記録。return code0 |

[Microsoftのevent定義](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-for-traceability)で261はsystem time設定、258はservice停止の記録と確認した。
ログのOld/New差は+2msだが、ミリ秒表示のprovider報告値である。外部UTCとの差の実測や継続精度の保証ではない。
System logのRecord26322/37（受信）、26323/35（同期開始）も同じ区間にあった。
native応答とこれらのeventを両方残し、「同期が一度も行われなかった」「同期できて今も安定」のどちらにも単純化しない。
チャット初期説明の「同期は行われていません」は応答本文だけからの推定で、追加eventにより上記へ修正した。

## 停止の管理元について分かった範囲

`sc.exe queryex/qtriggerinfo/qc W32Time`はすべてexit0。
workgroup（PartOfDomain=false）、Manual/LocalServiceを確認した。
現triggerはDOMAIN JOINEDとCUSTOM SYSTEM STATE CHANGEのSTART2件で、STOPは表示されていない。
一般的な[workgroupの停止trigger例](https://learn.microsoft.com/en-us/troubleshoot/windows-client/active-directory/w32time-not-start-on-workgroup)を、そのまま今回原因としない。

既存SynchronizeTime taskのactionは`sc.exe start w32time task_started`、最終実行のhost表示UTCは00:25:21、result0。
既存process開始00:25:22.0395997と近接するが、TaskScheduler/Operationalは無効で起動履歴を取れない。
タスクを起動/変更したりlogを有効化したりしていない。
ForceSynchronizeTimeのExecute/Argumentsがnullなのは未展開action情報で、taskの処理が無いと証明しない。
既存SCMの限定範囲には該当なしで、停止callerやidle/task modeの内部理由は未特定。
code0は今回の終了値であり、意図どおり/恒久安定/停止原因解決を意味しない。

## 次の範囲と停止条件

同じhelperは再実行しない。今回の1回許可を、service起動・追加resync・poll変更・task/log設定変更へ拡張しない。
次はtask起動形態と停止方針の根拠、または新しい自然eventをreviewする。
もしservice起動の1条件比較を提案する場合も、起動で同期/時計stepが生じ得るため、前後証拠・影響・復元方針を固定して別途許可を得る。
「常時稼働でないから故障」とも、単発補正できたから長期資格passedとも扱わない。
独立UTC/elapsed sourceの真正性/不確かさは未達で、実collector/全資格・実fit/採点/T02/T11/T12/T13bは保留。
過去T03/tick11000の動的原因へ今回の停止や+2msを遡及適用しない。
今回の追加作業は原本照合/限定read-onlyのみ。製品source/schema/writer/lock/workflow、実DB/registry/現配信、future拒否は変更していない。
