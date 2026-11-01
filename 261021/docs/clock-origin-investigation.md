# UTC逆行の呼出元の調査（2026-11-01）

基点は`b5dbdfd2df16da8325b9a422fd2c2729f5991689`。
[後続の管理元照合](clock-process-ownership.md)でUbuntu timesyncdのlocal PIDとkernel PIDの対応・実行file・管理unit・同期元を確認した。以下は当時の調査記録を保持する。chronydの稼働exe/親/生成config照合と環境修復は後続でも未完了。
[前回の診断](interruption-clock-diagnostics.md)から、時計変更の呼出元を追加調査した。
[証拠JSON](reviews/20261101-clock-origin-checks.json)に全試行、失敗、原本のbytes/SHA-256を記録する。
現在環境の機序を絞った節目であり、過去T03/T13の根本原因や環境修復の完了ではない。

## 結論と確度

| 対象 | 今回の直接観測 | 残る限界 |
|---|---|---|
| UTCを戻した呼出し | `systemd-timesyn`（kernel traceのglobal TID 496）が`clock_adjtime`→`do_adjtimex`→`timekeeping_inject_offset`を実行。引数付き採取で−4.473373174秒、−3.128148078秒の要求とsyscall成功を確認し、実時計の逆行区間へ照合 | process名はcommであり認証されたidentityではない。外側PID名前空間の実行file/親/管理unitへの照合は未完了。前回の2件の自然future拒否や歴史的SQLite失敗の瞬間にはsetter traceがなく、同一呼出元とは断定しない |
| 時計の進行率 | Windows QPCに対するWSL MONOTONICの比は、往復遅延を含む範囲でも1.099918〜1.100057。RAWは0.999926〜1.000052。`adjtimex(modes=0)`でtick=11000を観測 | 単調増加でも正しい秒数とは限らない。現在の観測で過去の測定値を一律に割り戻さない |
| tickを書いた呼出し | `chronyd`（global TID 213）が`ADJ_TICK | ADJ_FREQUENCY`（modes=16386）を16回要求し全て成功。うち14回がtick=11000/freq=0、他2回は10441/10614で、その直後に11000へ戻す要求がある | なぜ11000を繰り返し指定するか、どの実行file/設定/起動元かは未特定。Ubuntuのservice一覧にchronydがないことを、共有kernel全体で不在の証拠にしない |
| `initd`とHyper-V | 2区間で計5回の`initd`→`settimeofday`→`do_settimeofday64`成功を観測。今回の5回は**前進**。`hv_set_host_time`は同じ2区間で0回 | Runningのdocker-desktopには`/initd`があり同じtime namespaceだったが、global TIDとそのprocessの対応までは未確定。Docker由来という推定と、今回の負方向の直接原因を分ける。Hyper-V経路が将来/過去も動かないとは言わない |
| Windows | W32Time/vmictimesyncはStopped/Manual、NTP client設定はEnabled=1。Windows UTC/QPCは1,400組の採取で逆行0、現在区間のKernel-General ID 1は検索一致なし | W32Timeが止まっているため`w32tm /query`は0x80070426で失敗。現在のWindows UTCが外部時刻に正確とは確認していない。イベント不在や有限sampleの成功で全ての変更を排除しない |
| T03 | journal 9件成功。通常SIGTERMは受信→handler例外→HTTP終了→台帳書込→exit 130の全phaseを保存。受信からchild_exit記録までは15.918451 MONOTONIC ms | 自然な10秒終了待ち失敗は再発せず、歴史的停止位置は未特定。このms値は正しい外部秒として校正済みではない。人工停止caseを自然再発に数えない |

## 同時採取とカーネル照合

原本はignored `261021/data/reports/20261101-clock-origin/`。
runnerは完全argv、stdout/stderr、開始/終了UTC、MONOTONIC経過、終了値、期限、bytes/SHAを新規pathへ保存する。
UTCは採取中も戻るため、順序はUTC表示の並びだけで判断しない。

1. `collect-paired.py`でWindows UTC/QPCを1,400回、WSL REALTIME/MONOTONIC/RAW/BOOTTIMEを15,359回、既存NTP状態を646回採取した。WSLの逆行6回の後にPacketCount 5761〜5766への更新が各1回、0.5秒以内に観測された。これは相関で、これだけではsetterを断定しなかった。Windows側のstreamingには輸送遅延があるため精密な進行率の根拠には使わない。
2. `bracket-windows.py`で要求送信前/応答受信後のRAW/MONOTONICでWindows読取を囲んだ。81組中、初回serializationの暖機1組を除外。端点の取得幅を残して進行率の上下限を計算した。QPCの端点間隔は36.3632111〜36.3634027秒。MONOTONICは39.996767536〜40.001604267秒、RAWは36.360694071〜36.365098204秒だった。これは性能試験の暖機や合格基準の変更ではない。
3. 専用ftrace instanceで75 MONOTONIC秒ずつ2回観測した。`trace_clock=mono`でclock syscallのenter/exitと3関数を採取し、5ms間隔のREALTIME−MONOTONIC差の変化区間へ対応付けた。負方向の5区間全てで`systemd-timesyn`のoffset注入が一致した。
4. 2回目はUUID付きの一時kprobeで実際の`struct __kernel_timex`のmodesと選択対象fieldを取得した。UAPI headerでsize=208、modes=0、offset=8、freq=16、status=40、time=72、tick=88のoffsetをCで確認した。modes=8476（ADJ_STATUS/NANO/SETOFFSET/MAXERROR/ESTERROR）の負のtime値、modes=16386のtick/freqだけを解釈した。**modesが選択していないfieldの値は意味を持たず、異常値や設定値として解釈しない。**

最初のsetter区間は次の通り。差は隣接sampleの`ΔREALTIME−ΔMONOTONIC`で、syscallそのもののHTTP時間等ではない。

| MONOTONIC秒（関数entry） | 呼出元 | 差の変化（秒） |
|---|---|---:|
| 106101.733657 | initd / do_settimeofday64 | +0.378317092 |
| 106102.021518 | systemd-timesyn / timekeeping_inject_offset | −3.126079512 |
| 106131.741265 | initd / do_settimeofday64 | +0.397592052 |
| 106132.034513 | systemd-timesyn / timekeeping_inject_offset | −3.109425911 |
| 106161.746098 | initd / do_settimeofday64 | +0.382076753 |
| 106162.037398 | systemd-timesyn / timekeeping_inject_offset | −3.118489785 |

引数付きの2区間では、要求−4.473373174秒に対するsample差−4.473371796秒、要求−3.128148078秒に対するsample差−3.128148530秒だった。
差はそれぞれ1.378µs/0.452µs。traceの時刻はµs表示、clock読取は逐次実行なので、この微差をns精度の同時測定と説明しない。
syscall戻り値0と負の引数、負の時計変化が揃っており、この観測区間の直接の逆行原因を特定した。

各CPU bufferのoverrun/commit overrun/dropped eventsは0。専用instance/probeをfinallyで削除した。
既存のglobal tracing_on/current_tracer/trace_clock/set_ftrace_filterと、使用時のkprobe_eventsの前後hashは一致。
時計・同期サービス・実DB・registry・配信を変えていない。観測用trace制御の追加/削除まで「一切書込なし」とは表現しない。

## 仕様との照合

該当releaseの[WSL kernel ntp.c](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/kernel/time/ntp.c)はtick_usecをUSER_TICK_USECで初期化し、ADJ_TICKで更新する。
[jiffies.h](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/include/linux/jiffies.h)ではUSER_TICK_USECはUSER_HZを基準にする。今回`getconf CLK_TCK=100`なので通常の基準値は10000µsで、CONFIG_HZ=250から4000と計算するものではない。
今回の11000と進行比約1.1はこの挙動に整合する。`freq=0`やNTP同期flag=yesだけでは正常な速度を保証しない。
[adjtimex manual](https://man7.org/linux/man-pages/man2/adjtimex.2.html)に従い、状態採取用Cはmodes=0だけを使用した。

[systemd v255のmanager_adjust_clock](https://github.com/systemd/systemd/blob/v255/src/timesync/timesyncd-manager.c)は大きいoffsetにADJ_SETOFFSETを使う。外部の時刻変更を検知すると再問合せする経路もあるため、NTPの最小poll設定32秒でも、他processの変更直後に補正が発生し得る。実環境は255.4-1ubuntu8.17で、upstream tagをUbuntu packageの全patchと同一と保証したわけではない。
[該当WSL kernelのhv_util.c](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/drivers/hv/hv_util.c)には別のhost同期経路がある。今回そのsetterの呼出しは0だったため、implicit=Yだけで負方向の原因と判断しなかった。

[UbuntuのWSL時刻同期説明](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)もhost同期とguest NTPの競合可能性を説明する。ただし、この一般論だけで当該processの所有者や処方を確定しない。
[Docker上の同種報告](https://github.com/docker/desktop-feedback/issues/761)は30秒のinitd setterという仮説を立てる参考にしたが、他人のtraceは今回の実証へ転用していない。今回のinitdは観測した5回とも前進だった。

## 取得失敗と境界

- 通常sandboxではWindows interopのvsockが拒否され、DBusもOperation not permittedだった。許可付きの同じ読取を別attemptへ保存した。最初のwrapperのexit 0を、内側の全query成功と読み替えない。
- `sudo -n`はpassword required。WSLのUbuntu root経由では専用traceを採取できたが、NS_GET_PARENTで外側PID名前空間へ遡れず、global process照合は停止した。予定した専用proc mountは作成されていない。
- docker-desktopの初回process読取はWSL.exeを通したshell引数の展開で失敗した。shell全体のexitは0でもstderrが失敗を示すため不採用。stdinで固定scriptを渡す別試行で`/initd`を確認した。直前にRunning一覧を検査し、停止中なら起動しない。
- `wsl --system`の初回はwslg userでexe/nsを読めなかった。root指定の次回でも外側global PIDの照合はできなかった。
- `wsl --debug-shell`はWindows管理者としての起動を要求し、実行されなかった。最初のUTF-16出力の誤decodeも残し、再取得で正しい拒否文を保存した。Windowsの管理者昇格は行っていない。
- 引数traceの最初の準備はcwdに対し261021を重ねて指定し失敗し、欠落scriptの実行はexit 2だった。修正後の`Path.open('a')`もtracefsのseekでEINVAL。切詰める`w`は使わず、seekしない`os.open(O_WRONLY|O_APPEND)`へ修正した。失敗traceも専用制御を回収し、前後hashを確認した。

## 未実施の設定案と影響

以下は保守時間に実施可否を判断する**提案**で、今回実行していない。単にtimesyncdを止めるだけではtickの進行率問題を解消できず、tickを一度10000へ戻すだけでもchronydが再設定する恐れがある。

1. **所有者を先に特定する（P1/D3/Sol）。** Windows管理者で起動した診断shellから、global PID 213/496とinitd TIDの実行file、親、namespace、起動設定、chronydの引数/clock sourceを限定読取する。今のPIDの再確認が必要。名前だけでUbuntu serviceやDockerを停止しない。公開前にargvやログの秘密情報を確認する。
2. **時計の責任を一本化する案を作る。** host基準ならWindows「時刻と言語→日付と時刻」で自動設定/同期元を確認し、必要なら管理者による`Start-Service W32Time`と`w32tm /resync`を保守時に検討する。[Microsoftのw32tm仕様](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)を参照し、恒久起動方針は端末policyに合わせる。guest側は所有者確認後に、そのchronydを非調整modeへ変更するか正しいtick制御へ直す。[chronyd公式manualの`-x`](https://chrony-project.org/doc/4.7/chronyd.html)は時計調整を無効にするが、所有package/実versionの手順を再確認してから候補とする。Ubuntu timesyncdの停止/無効化は、代替同期の継続動作を確認した後に限って検討する。どのコマンドも今回実行していない。
3. **tickを復元する必要がある場合も所有者修正後。** USER_HZ=100を再確認してADJ_TICK=10000の限定変更案を作る。`adjtimex`コマンドはこの環境にないので、未検証のインストール/変更コマンドを即実行手順にしない。設定者が生きていれば再書込される。freq/status等を一括初期化しない。
4. **影響と切戻しを先に記録する。** Windows同期はhost/全WSLのUTCを動かし得る。guestのtick/調整者変更も共有kernelを使う他distro、Docker、timeout、証明書/認証/証拠日時へ影響する。Docker終了、WSL shutdown、service停止をA/B試験のために勝手に行わない。承認済み保守時間、設定backup、元の起動方式/引数、切戻し、UTCが戻った際の中断条件を具体化する。
5. **変更後に測り直す。** 同じbracket付き時計採取、setter trace、NTP状態、厳密future境界、T03 phase/stackを確認し、長時間/再開後の観測を追加する。有限区間の逆行0だけで永続解決としない。現在のMONOTONICを正しい秒数とみなす実候補の性能・timeout資格は保留し、時計資格確認後に新規attemptで測る。過去の合格/精度指標を現在の時計比で修正しない。MAE/RMSEの計算とHTTP経過時間の問題を区別する。

T13のfuture拒否はそのまま維持する。時刻clamp、待機、自動retryによるeligible化は追加しない。
T03の未再発も解決扱いにしない。HTTP部分書込/応答喪失、T07/T17、実Capitalの採否/監視/配信は別taskとして残る。
