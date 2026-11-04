# 修復後の時計資格：事前に固定する受入手順 v1

基点`24c30c0`、2026-11-04。これは残課題「環境修復の具体化と時計資格」の**計測設計**である。
修復、時計資格の合格、実候補の運用測定を実施した記録ではない。
[調査結果](backend-time-path-review.md)、[管理者採取](clock-owner-admin-read.md#chrony-resources-admin)、
[計画の残件](../../261014/plan_mlops.md#remaining-work-latest)を前提とする。

以前の手順は「長時間・休止再開後に確認する」までで、期間・判定式・欠測時の扱いが未固定だった。
その不足をここで補う。数値はこの学習環境の工学上の受入方針で、OSの保証値、時刻精度の標準、本番SLOではない。
採取器・自動判定器の実装とその負例検証は後続作業。既存の短い診断をこの方針で遡及採点しない。

## 実行開始前の条件

1. 新しいPHC/UDP所有・process identityを照合し、host/backend/guestの同期責任をreviewする。
   未確定のtick11000の過去原因を、この設計の存在だけで解決扱いにしない。
2. 修復案ごとに対象管理元・変更前設定・影響・切戻しを固定し、OS変更と保守時間の許可を得る。
   休止再開も現Docker配信へ影響し得る操作なので、この文書で実行許可を得たとは扱わない。
3. 採取policy本文/SHA、採取器のcommit/hash、OS/WSL/Docker版、boot ID、選択clock、
   processの開始時刻・exe/hash/dev/inode・namespace、同期設定hash、試行UUIDを保存する。
   credential、全環境変数、個人argvを公開証拠へ含めない。
4. Windows QPC周波数を読み、hostを基準にした経過時間比較と、独立した外部UTCとの比較を両方計画する。
   hostのUTC表示、HTTP Date、同期flagだけでは外部UTCとの比較は成立しない。
   外部時刻源と読取方法・応答遅延の上限・時刻源自身の不確かさを先に固定する。
   読取方法が未実装、必要な接続が使えない、または不確かさを評価できない場合は開始条件未充足とする。

## 固定する観測区間

試行はWindows QPCによる連続60分。前半30分は通常の待機、後半30分は使い捨てfixtureの
事前固定した負荷とする。負荷生成器の仕様・hash・資源上限を採取前に固定し、実DBや実registryへ要求を送らない。
QPC/UTCとWSL REALTIME/MONOTONIC/RAW/BOOTTIMEを、要求前後のbracket付きで1秒ごとに採る。
各phaseの最初と最後を必須sampleとし、単なるsample数やWSL sleep回数で60分経過と判定しない。

別途許可された保守内でhost休止再開を1回行い、再開後に同じ60分を新試行として採取する。
休止をまたぐQPC/RAW/BOOTTIMEの差を通常稼働の進行率へ混ぜない。再開前後のUTC順序とboot/identityも保存する。
再開時のUTCを読めなかった場合は、その範囲の逆行なしを主張しない。
休止再開の未実施・採取不能を、通常稼働の成功で代用しない。

sample欠落、QPC上の隣接間隔が2秒を超える箇所、通信失敗、順序逆転、identity/設定変更、
instrumentationのlost/overrun、採取process中断は原本に残す。必要な区間が欠けた試行は`incomplete`とする。
原因調査用のprobeを付けた区間を、probeのない実運用性能測定へ流用しない。
再試行が必要なら理由をreviewし、新UUIDへ全試行を保存する。合格するまでの自動retryや窓の延長は行わない。

## 経過時間の比較式と受入基準

同じWindows QPC sampleの読取時点を、WSL clockで要求前`b`と応答後`a`に挟む。
各clockのraw値とQPC tick・frequencyを保存し、丸め前の有理数で計算する。
sample0/1について`q=(QPC1-QPC0)/frequency > 0`なら、そのclockの進行率を次の区間で評価する。

```text
lower = (b1 - a0) / q
upper = (a1 - b0) / q
```

WSL値は秒単位へ換算する。bracketの非重複・各`b <= a`・QPC順序を先に検証する。
Windows readがそのbracket内で行われたことを保証する取得protocolと、QPC自体の安定性確認が前提である。
外部UTCの絶対offsetが範囲内という結果だけでQPCの秒数校正を済ませない。
QPC参照側の進行率/不確かさを確認できなければ、WSL/QPC比が範囲内でも資格全体は`incomplete`とする。
UTC逆行のあるREALTIMEへこの式を適用して経過時間を合格させない。

MONOTONICとRAWについて、最初のsampleを起点とする60秒ごとの非重複区間と全phase区間をすべて判定する。
終点はQPCで境界に最初に到達したsampleを使い、都合のよいsampleへ差し替えない。
各比の**区間全体が`[0.999, 1.001]`に含まれる**ことを要求する。点推定や単なる区間の重なりで合格させない。
区間が広すぎる場合は`incomplete`、許容範囲と完全に離れる場合は`failed`とする。
BOOTTIMEは順序/休止境界の診断にも残すが、未観測の休止区間の正しさをRAWとの一致で代用しない。

許容比±0.1%は、250msの経過時間に対し尺度誤差0.25ms相当を上限の目安にした設計値である。
これはQPCを基準にした寄与であり、QPC参照側の誤差と要求別の計測誤差は別に評価する。
60秒平均の比が合っても個々のHTTP要求の誤差を保証しない。
後続のT12測定にも前後のbracket・同boot/設定確認を結び、境界付近の結果は計測不確かさを含めreviewする。
この比で過去のp95やtimeout秒数を割り戻さない。

## UTCと承認時刻の受入基準

- 採取されたWindows UTC/WSL REALTIMEの逆行は0件。採取間の未観測変化まで不存在と宣言しない。
- 固定した外部時刻源とのoffsetを、輸送遅延・外部源精度を含む区間として各phase開始/終了と60秒ごとに記録する。
  Windows、WSLの両方について、区間全体が±1秒に入ることを要求する。
  同じhostに依存するPHC・backend・guestを、互いに独立した外部源として数えない。
  区間が境界をまたぐ場合や外部源が未確認なら`incomplete`。明確な逸脱は`failed`。
  ±1秒はこの実験のUTCずれ予算であり、future拒否の許容値ではない。
- 同期元・設定と観測したsetterを照合し、未説明の負方向stepや急な進行率変更があればreviewへ戻す。
  setter名の存在だけ、またはNTP同期flagだけで合否を決めない。
- 厳密future境界の既存fixtureを維持し、同値許可・1µs future拒否・拒否時のblocked/tag不変を検証する。
  実時計を使う合成承認probeでも生成/検証UTCとMONOTONICを保存する。自然future拒否は不合格として記録する。
  clamp、待機、自動retry、future許容幅の追加は行わない。
- T03が自然再発した場合だけphase/stackを照合する。未再発は過去原因の解決ではなく、
  人工停止の成功も過去の停止原因の証明にはならない。

## 判定・保存・失効

明確な違反を1件でも観測したら`failed`、違反未観測でも必須情報/区間が不足なら`incomplete`とする。
両方ある場合は`failed`を主状態とし、不足も併記する。全受入条件を満たしreviewした場合だけ`passed`とする。
有限期間・特定環境の資格であり、原因の全解明や永続安定性の保証ではない。

policy、全sample/journal、command/cwd/終了値、生成/終了UTC・QPC/monotonic、各原本bytes/SHA、
区間別計算と除外理由、setter採取の範囲/負荷/cleanup、review理由を保存する。
切戻し・失敗試行も残す。公開するのは秘密情報を除いた要約とhash。試行後のpolicy変更は別versionで再reviewする。

再起動、休止再開、同期設定/process実体、OS/WSL/Docker版、clocksourceの変更後は以前の資格を再利用しない。
今回予定した休止再開後試行も、再開前資格を持ち越す方式ではない。
実運用測定の前後に同boot/identity/設定と時計を照合し、違いまたは途中の逆行を見つけた測定は未採用とする。
過去の採否や性能を新しい資格で遡及承認しない。

次の実装は、保存済みbracket/外部UTC区間を読むoffline判定器と合成負例から始められる。
採取器・負荷生成器・外部源readerの実装/検証、管理者PHC/UDP採取、同期責任review、修復許可、
修復そのものとこの全受入は残件。T09/T10の入力待ちや実producerの前提を省略しない。
