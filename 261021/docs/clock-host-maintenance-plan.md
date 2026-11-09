# Windows同期の保守比較案（未許可・未実施）

**2026-11-09実施後追補:** 案Aのresync1回はユーザーが実行済み。[実結果](clock-resync-result.md)で、
時刻設定eventの直後に停止/事後query失敗を確認した。以下の未許可・未実施は案作成時点の履歴。
案B/C・service起動・追加resyncは未許可/未実施のまま。案Aをもう一度実行する手順ではない。

2026-11-09、基点`9c32298`。[peer/statusの実review](clock-host-cause-review.md)を受けた次段階のレビュー資料。
ユーザーの「OS/時計設定…を変更せず」に従い、**ここに記した変更コマンドは実行していない。**
現時点で根本原因を確定しておらず、長いpollを短くすれば過去障害が解決すると主張しない。

## 新しく具体化できた条件

前回のruntime原本ではpeer `time.windows.com,0x9`、Type=NTP、SpecialPollInterval16384、MinPoll10、MaxPoll15、HoldPeriod5、LargePhaseOffset50000000。
今回のpeerは保留で残り10301.3131813秒、全体はUnset/no time data。これらは取得時点が違う原本であり、全設定が連続不変と仮定しない。

[Microsoft KB2638243](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/specialpollinterval-polling-interval-time-service-not-correct)は、
special pollと大offsetによりSPIKEへ入る条件で同期が進まない事象を説明する。
資料にある回避条件は`SpecialPollInterval < 2^MinPollInterval * (HoldPeriod+1)`。
保存設定を代入すると右辺は`1024*6=6144`で、16384は満たさない。
ただし今回の実状態はUnsetで、実offset/当該SPIKE遷移を観測していない。**式の不一致だけでKBが今回の原因とは判定しない。**

[Microsoft KB3205089](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/w32time-sync-specialpollInterval-ignored)には、
special pollに関する別の待ち時間問題と、0x9から0x8へ切り替える案がある。
単に設定を既定へ戻す、長い残り時間だけ待つ、無関係なserviceを止める、のいずれも自動的な修復とは扱わない。

## 保守を別途許可された場合の実施順候補

下記は選択肢で、A/Bを一度に実行する手順ではない。どの操作を許可するか、配信への影響と観測期間をreviewしてから1条件ずつ固定する。
通常queryの繰返しで進捗を作らず、既存原本は保持し、新しい操作ごとに別UUIDと結果を残す。

1. 開始前にhost boot/版、W32Timeのprocess/開始時刻/exe hash、runtime設定とpolicy起源、既存peer/status、
   Mariner/Ubuntu/Dockerの管理状態とidentityを限定保存する。今回と違うidentity/設定なら停止して再reviewする。
   OS状態を変えず、実DBやregistryをsnapshot名目で書き換えない。
2. 許可された1条件だけを変更する。失敗しても自動retryや別案への自動移行を行わない。
3. native終了値・操作時点のローカルUTC/QPC・元bytes/SHAと、その後のquery/eventを保存する。
   query exit0、resync exit0、source名の変更、短時間の未再発だけでは全資格passedにしない。
4. 想定外の時計step/identity変更/配信影響が出た場合は次の操作へ進まず、失敗/未確定としてreviewする。
   設定の切戻しも同期を引き起こし得るため、事前許可された範囲と保守内で行う。

| 案 | 変更する1条件 | 開始条件・得られる切り分け | 影響・切戻し |
|---|---|---|---|
| A：現在設定のまま1回のresync | Windows管理者で`w32tm /resync`を1回。rediscoverやconfig変更は混ぜない | 明示許可後のみ。現在peerで時刻を利用できるか、即時の結果を新規証拠として得る。原因の完全確定ではない | 時計step/統計破棄の可能性。設定は変えないが操作自体は取消せない。手動で時刻を逆に戻すことは切戻しとしない |
| B：poll方式の比較 | peerを同じtime.windows.comの0x9から0x8へ変更しupdate。直後resync/service再起動/閾値変更は混ぜない | 新しいruntimeが旧原本と一致し、policy管理の有無・変更責任を確定してから。0x1特殊間隔の寄与を比較する候補。KB原因を確定した対処ではない | 自動同期/時計stepが起き得る。元Type/peer/runtime起源と設定hashを保存し、元0x9/管理元へ復元。履歴・sample統計は戻らない |
| C：guest管理層の比較 | hostの責任を整理後、Ubuntu等の管理対象1条件をreview | hostの同期回復/独立源・管理範囲が明確になってから別案を固定。現段階では開始不可 | shared kernelやDockerへ影響。active/enabled/configの元値と復元操作が未確定なので停止。initd kill/Mariner生成設定直接編集は対象外 |

案Bのレビュー用コマンド候補は次のとおり。**未許可のため実行しない。**

```text
変更候補: w32tm /config /manualpeerlist:"time.windows.com,0x8" /syncfromflags:manual /update
復元候補: w32tm /config /manualpeerlist:"time.windows.com,0x9" /syncfromflags:manual /update
```

これは保存値Type=NTP/peer0x9を前提とする候補で、全policy/全設定の復元コマンドではない。
開始時に起源/値が異なる、企業policyで管理される、操作後のruntimeが期待と違う場合は使用せず管理元のreviewへ戻す。
新peerの選定・防火壁変更・UDP問い合わせ/packet採取・private debug log有効化は、この案に含めない。
接続調査が必要になった場合は、対象/採取範囲/秘密除外/負荷/cleanupを別に具体化する。

## 修復と資格を分ける

この案が成功しても、host/PHC/guest/QPCは共通故障源を持ち得る。
peerのNoAuth表示は独立UTC/elapsedの真正性や不確かさを満たす資料ではない。
[source dossier](clock-source-readiness.md)を先にreviewし、必要な実reader/host deadline/固定負荷を検証してから、
[固定policyの通常60分・許可後の休止再開60分](clock-qualification-plan.md)の全観測で別途資格を判定する。
保守後の良好値で過去のUTC逆行、tick11000/T03、p95、採否を遡及承認しない。
実fit/採点/窓消費は、それぞれの前提を満たすまで保留のままにする。
