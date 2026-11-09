# Windows peer・最終エラー・service identityの照合

2026-11-09、基点`9c32298`。提出された`clock-host-cause-a52e422e-45db-44d1-b1a8-1c3579e47e0b`をoffline reviewした。
[今回索引](reviews/20261109-host-cause-checks.json)、[前回runtime review](clock-host-runtime-review.md)、
[最新計画](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**採取したWindows全体の同期状態は未同期・時刻データなし。peerは保留中で、現在の有効サンプルは0だった。**
peer/status/identityの読取不足は今回解消したが、「なぜこの状態になったか」の根本原因や実時計資格は未確定である。

## 採取の受入範囲

原本9file、manifest8memberのSHA、添付consoleの保存先、query2件のnative exit0を照合した。
UTF8 BOM/CRLFを保存したままsnapshotとhashを作り、解析文字列だけを正規化した。
UNCは既知parent/basenameに限定し、manifestが指す任意pathを開かない。
収集modeは`read-only-local-cause-diagnostics`、clock値は`local-unqualified`と明示されている。
管理者flagは署名でないため、それだけで真正性を認定せず、実行手順/console/実出力との整合を受入範囲とする。

| 項目 | 今回の原本で確認した値 | 限界 |
|---|---|---|
| host | Version10.0.26200、Build26200、LastBootUpTimeUTC=`2026-10-27T14:23:16.1235780Z` | hostが報告した値。暗号的boot IDや独立UTCではない |
| service identity | W32Time Running/Manual、PID34104、process StartUTC=`2026-11-09T00:25:22.0395997Z` | service自身の厳密なstart eventではなく、serviceをホストするprocessの開始時刻 |
| exe | `C:\WINDOWS\system32\svchost.exe`、SHA `1222a44a5fdb4efde4dfcb41093648627950e7ec02d8667f1c26ccae31d922e2` | 前後identityは一致。読み込んだw32time.dll全体/実行中の無改変/過去processの認証ではない |
| query時点 | statusのUTC bracket `02:06:44.7640694Z`〜`.8043916Z`、peersは`.8291682Z`〜`.8594510Z` | 同日host UTCの表示。独立校正されず、queryは逐次・非atomic |
| QPC | StopwatchFrequency10,000,000、status差307,197tick、peers差261,560tick | 名目秒数は0.0307197/0.026156。実elapsedの独立校正や時計資格ではない |
| event | RecordId>26321、Time-Service/System、最大32件、返却0件 | 「該当なし」のerror_recordsを保持。アクセス成功の全履歴や異常なしの証明にしない |

UTC bracketはQPC bracketの外側で取得する設計なので、両差の違いをそのまま進行率違反へ変換しない。
前後のprocess identity一致は採取の対応を支えるが、query間の連続監視や過去のWSL setterとの同時観測ではない。
前回host rawには同じidentityが無く、今回値を前回や歴史的chronyd/timesyncd/initdへ持ち越さない。

## statusとpeerを分けて読む

| 対象 | 実際の値 | 判断 |
|---|---|---|
| status | LI3、階層0、Local CMOS Clock、最終正常同期未指定 | 全体は未同期と報告している |
| status最終エラー | 1「時刻データが利用できなかったため…同期をとり直しませんでした」 | CLI query自体のexit0とは別の、時刻同期処理のエラー |
| status内部状態 | State Machine0（設定解除）、source flag0/role0 | SPIKE状態の実測ではない。phase offset0をUTC差0秒と解釈しない |
| peer | time.windows.com,0x9、保留中、mode0/階層0、残り10301.3131813秒 | 待ち状態の報告。次の成功や永続故障の証明ではない |
| peer最終エラー | error/message IDとも0（成功） | peer単位の欄。全体statusエラー1/未同期を上書きしない |
| peerサンプル | 有効データcounter0、reachability0 | 現在保持している有効sampleがない。過去に一度も受信できなかったとは言わない |
| peer解決試行回数 | 0 | 連続解決失敗counterが0。現在DNS障害が継続中とする根拠ではないが、今後のDNS成功やUDP受信を保証しない |
| peer認証 | `0x0000005A (NoAuth )` | この出力は認証された独立参照の根拠にならない |
| peer時刻欄 | 最終正常同期`2026/11/09 9:25:22` | timezone/formatterの対応を採っていないため勝手にUTCへ変換しない。全体の最終正常同期未指定と併記する |

[Microsoftのpeer要素定義](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-w32t/28e3c644-cb9d-4925-8c80-288f6339d9e0)は、
解決失敗回数、有効sampleの現在保持数、peer単位の最終エラーを区別している。
CLIの個別欄の成功や過去時刻だけを、今回の時計全体の同期・UTC精度・真正性・QPC校正へ昇格させない。
statusの「最終正常同期時刻から4395.0509461秒」も、元の時刻が未指定のため、成功eventの日時の逆算へ使わない。

## 歴史的イベントとの関係

前回保存済みの12イベント原本は5,170bytes/SHAを検算して読んだ。hostへの同じイベント照会は再実行していない。
Record26266/ID37は`2026-11-09T00:25:22.2254368Z`にvalid time dataの受信を報告しており、
今回processのStartUTCよりhost wall値で0.1858371秒後に当たる。近接は分かるが、受信payload・当時PID/開始tickはeventに無い。
新PID34104がそのeventを発行した、当時のsampleが時計全体へ採用された、以後継続同期したとは確定しない。
古いDNS失敗134、後の受信37/同期開始35の両方を保持し、現在のvalid counter0と矛盾するからと履歴を捨てない。
今回の「新イベント0件」は、前回最大RecordIdより後という限定範囲の結果。log巻戻り/保存範囲/記録されない状態変化は別である。

## 原因の候補と未確定事項

| 候補 | 新資料から言えること | 足りない根拠 |
|---|---|---|
| DNS/UDPの恒久遮断 | 現在の全体はno time dataだが、peer解決失敗counter0、過去のvalid受信もある | 当該応答/経路・packetの採否・同時エラー。pingやTCP123でUDP123成功とはしない |
| 長いspecial poll/保留 | 前回runtime SpecialPollInterval16384、今回残り10301.3131813秒 | 保留へ入った理由、poll更新/受信/拒否の履歴。待機だけで修復したとしない |
| SPIKEとspecial pollの既知問題 | 0x9を使い、既知問題のpoll条件を調べる価値がある | 実offsetとSPIKE遷移が未採取。今回はUnsetであり、KBの条件一致は未確定 |
| guest/backendとの競合 | host→PHC、guest補正、静的backend時刻書込経路の既存根拠はある | 今回host queryと同時のsetter/内部値/応答なし。過去tick11000/T03を解決しない |

[条件付きの保守比較案](clock-host-maintenance-plan.md)へ、確認済み設定に対する候補、影響、切戻し、停止条件を具体化した。
これはOS変更の許可・修復済みという意味ではない。同じbasic/peer/PHC queryを直後に繰り返す新しい依頼はしない。
新しい状態変化・自然event・source資料、または別途許可された保守比較が次の開始条件である。

## 後続タスクと検証

標準の`clock-source-input-*`に新dossierは無く、未提示の別pathの不存在は断定しない。
今回NoAuth/Local CMOS/名目QPCのmetadataだけでは独立UTC/elapsedを採用できず、全資格は`incomplete`。
実source/collector/host deadline/固定負荷、許可後の修復・休止再開・全資格、T10実fit直前再照合→T02/T11/T12/T13bは前提待ち。
T09/T10静的受入は完了のまま。未消費窓を使うfit/採点、OS/時計設定、実DB/registry/配信、future拒否は変更しない。

今回専用のignored offline診断で9原本/8memberと14負例（再hash12件）を照合した。
identity差、QPC/UTC逆転、query失敗、peer/global誤読、counter/認証差、event件数/診断欠落も拒否した。
固定captureの意味照合の検証で、製品unit追加/一般reader/実時計資格の受入ではない。
診断の初回はexit0。準備の旧索引読取でkey名を誤ったKeyErrorは別に保存し、製品の失敗と区別した。
公開索引にhash・実コマンド・終了値・未資格UTC/MONOTONIC・限界を結び、raw/個人情報はignored directoryへ保持する。
