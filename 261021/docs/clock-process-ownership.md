# 時計調整プロセスの管理元照合（2026-11-01）

基点は`0477e8ccb0d9ebb1b7eb6a51befc8435f279ec57`。
[前回の呼出元調査](clock-origin-investigation.md)と[今回の証拠JSON](reviews/20261101-clock-owner-checks.json)を参照する。
**Ubuntu timesyncdの管理元は特定した。chronydの稼働実体への最終照合と環境修復は未完了。**
続く基点2181bfeからの[補正量・tick計算の調査](chrony-control-analysis.md)では、現在のsetterとtrackingを重ね、上流計算で補正限界との一致を確認した。管理者ログは未採取で、稼働実体と内部理由の確定は引き続き未了。
OS時計・同期設定・実DB・registry・配信は変更していない。一時的な診断probeだけを追加して回収した。

## 確認できたこと

| 対象 | 観測と照合 | 確度・残る境界 |
|---|---|---|
| 負方向補正の実体 | Ubuntu local PID 214 = kernel/global PID 496。実行fileは`/usr/lib/systemd/systemd-timesyncd`、SHA-256は`cbf92a41ffb29efd75daebf8a1a1dd4d0c078d2f3c50f0fc7e9e35715eefb7cd` | 制御した読取照会でPIDを対応付けた。同名commだけによる推定ではない。現在の観測区間のidentityであり、過去T13失敗時のsetterまで遡って証明しない |
| 親・管理unit | local PPid 1の`/usr/lib/systemd/systemd`、cgroupは`/system.slice/systemd-timesyncd.service`。`MainPID=214`、active/running/enabled、ExecStartとexeが一致 | kernel側で読んだ親PIDは233。Ubuntuの親関係と、serviceのMainPID・argv・cgroup・binary hashを合わせた |
| 名前空間 | pid=`4026532219`、mnt=`4026532222`、time=`4026531834`。system distroからはNSpidが`254 214`と見える | 数字は今回のbootの値。次回固定値で検索せず再採取する |
| Ubuntuの起動設定 | unit本体は`/usr/lib/systemd/system/systemd-timesyncd.service`。vendor drop-in `systemd-timesyncd.service.d/wsl.conf`がWSLでの起動条件を有効にする。`/etc/wsl.conf`はsystemd=true | ユーザーが今回独自に作ったunitではない。package更新時の挙動や管理端末policyは別確認 |
| Ubuntuの同期元 | System/Runtime/LinkNTPServersは空、FallbackNTPServersとServerNameは`ntp.ubuntu.com`、ServerAddress=`185.125.190.56`。poll 32秒、採取時offset −3.164061秒 | 名前の設定だけでなく実際の選択先を読んだ。同期flagだけで正しい進行率とはしない |
| chronydの現在のsetter | kernel/global TID 213が今回もtick=11000/freq=0を書込。45秒の初回に10回、35秒の再試行に8回、全てsyscall return 0 | 実行file・親・起動argv・実際に読んだ設定fileは外側PID namespaceにあり未読。Ubuntu/Docker内に同名processがないことを、共有kernel全体の不在と解釈しない |
| chronyの読取応答 | system distroのchronycで、明示的に`127.0.0.1:323`へtracking/sourcesを照会し、選択先`#* PHC0`を確認。共有net namespaceのUDP socket inodeは3582 | Unix socketを読めたという意味ではない。既定chronycの成功だけでは接続経路を決められず、明示queryで補った。UDP応答だけでPID 213のexe identityが認証されたとも言わない |
| PHCの装置 | `/dev/ptp0`、`/dev/ptp_hyperv -> ptp0`、sysfs clock_name=`hyperv`、dev=`247:0` | 装置の存在と応答のPHC0は整合するが、PID 213の実fd/config照合は未実施 |
| Windows | 現tokenは非管理者。W32Time/vmictimesyncはStopped/Manualのまま。WSL 2.6.3.0 | debug shellは管理者起動を要求して拒否。UAC、サービス開始、再同期、policy変更は実施していない |

## PIDを名前に頼らず対応付けた方法

Linuxの[該当kernelのsched_getscheduler実装](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/kernel/sched/core.c)は、呼出側のPIDで対象taskを引き、`security_task_getscheduler(task)`へ渡す。
この**読取専用の照会**を`os.sched_getscheduler(214)`で実行し、UUID専用のkprobeで引数taskのpid/tgid/親pid/開始時刻を採取した。
自processへの照会を対照に置き、begin/end markerの間に各1件だけ一致することを確認した。signal送信や対象processへのコード注入はしていない。

offsetは当該bootの`/sys/kernel/btf/vmlinux`を[公式BTF定義](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/include/uapi/linux/btf.h)に沿って解析した。taskのpid=2416、tgid=2420、real_parent=2432、start_time=2720、comm=2920 bytesだった。別kernelへ固定値を流用する手順ではない。
35秒の成功区間でlocal 214→global 496、親233、task_start=15059948976nsを確認。前後のexe/sha/argv/cgroup/namespaceと`/proc/PID/stat`の開始tickは一致した。
その同じ区間でPID 496が−3.197128772秒のADJ_SETOFFSETを要求して成功し、実時計の負方向変化にも対応した。

初回45秒はcontext-switch回数を含むstatus全体を同一性判定へ使って失敗した。差は`voluntary_ctxt_switches`だけであり、process置換ではなかったと原本を比較した。失敗を成功へ上書きせず、35秒の別attemptで安定fieldだけの比較を確認した。
両区間とも専用instance/probeを削除し、global trace制御/kprobe定義の前後hash一致と全CPUのlost events 0を確認した。modesが選択していないtimex fieldは解釈せず、公開summaryへ転記しない。

## chronydの管理元について分かった範囲

[WSL 2.6.3のinitソース](https://github.com/microsoft/WSL/blob/2.6.3/src/linux/init/main.cpp)は、hostとの同期用に`StartTimeSyncAgent`を呼び、PHC用のchrony設定を生成して`/sbin/chronyd`を起動する。装置は`/dev/ptp0`を指定する。これは**該当版の設計上の管理元**を示す一次資料であり、稼働PID 213のexe/親を読んだ代わりにはしない。
今回のPHC0応答と、通常distroからPIDが見えない状況は、このWSL組込agentの構成と整合する。DockerやUbuntuに新しいchronydを導入する必要があると判断していない。

一方、`wsl --system`で見えた`/etc/chrony.conf`は`server time.windows.com`で、読取応答はPHC0だった。さらにそのmountから`/var/run/chrony/chronyd.sock`は見えず、明示socket指定は失敗した。
したがって、そのpackage既定fileを**稼働daemonの有効設定と見なして編集しない**。system distroのchronyd/chronycはversion 4.1だが、可視binaryのhashと稼働PID 213のbinary hashの一致は未確認。

[chrony 4.1のLinux実装](https://github.com/mlichvar/chrony/blob/4.1/sys_linux.c)は周波数補正をtickとfreqへ分解し、tickのbias幅を通常値の10%とする。この実装では、11000は設定fileに同じ数字が書かれている必要のない計算結果である。
複数調整者の競合で補正量が大きくなっている仮説はあるが、**内部の補正要求とPID 213のbinary/configを同時に照合しておらず、11000になる内部理由の確定ではない**。maxslewrate等を推測だけで変更しない。

## 原因・未確定事項・修復候補

**直接確認した原因:** 現在の負方向stepはUbuntuのsystemd-timesyncdによるNTP補正。別processのchronydによるtick=11000の反復書込も継続している。共有time namespace上の複数調整経路が存在する。
**未確定:** chronyd実体/親/生成config/同期fd、11000を選ぶ内部制御の理由、Windowsの外部UTC精度、過去T03/T13失敗の正確な呼出元。

| 未実施の案 | 実施前の条件・確認対象 | 影響と切戻し |
|---|---|---|
| 管理者で外側processを読取 | [限定読取手順](clock-owner-admin-read.md)で現在PID、exe/hash、親、namespace、process root内の生成configを採取。PID 213という固定値だけを再使用しない | 証拠fileだけ作成。時計変更・service操作なし。全processの環境変数や認証keyは採取しない |
| Ubuntu timesyncdを一時停止する保守試験 | 管理元/host精度を確認し、別途OS変更が許可された保守時間で検討する。事前にactive/enabled、unit/drop-in/hashを保存。今回`systemctl stop/disable`は実行していない | 全WSLの共有時計とtimeoutに影響。chronydのtick/offsetが収束するかを新規attemptで観測し、停止だけで成功としない。元がactiveならstart、enabled状態も保存時に戻す。恒久disableは一時試験とは別判断 |
| Windows同期責任の整理 | W32Timeが停止している理由と端末policyを確認し、外部UTCとの差・選択sourceを読取。サービス開始やresyncは別途保守提案 | host/全WSLのUTCが動き、認証/証跡に影響。元のservice起動方式を保存して切戻す。時計履歴そのものは巻き戻して消せない |
| chronyd側の制御変更 | 実体/config/起動者を特定してから判断。生成configを直接書いてもWSL起動時に置換される可能性がある。Ubuntuから見える別fileは変更しない | 共有kernel全体に影響。元の生成元・argv・configを保存し、その管理層で復元する。単発tick変更や周波数初期化は解決条件にならない |

保守後はWindows QPCで囲む採取、RAW/MONOTONICの進行率、setter、厳密future拒否、長時間/再開後の観測を新しいattemptで確認する。今回の時計比による過去p95の補正、future拒否の緩和、clamp、自動retryは導入しない。

## 検証・失敗の保全

全原本はignored `data/reports/20261101-clock-owner/`。runnerはargv、UTC/monotonic、終了値、deadline、stdout/stderrとhashを保存する。UTCが戻るため順序をUTC表示だけで決めない。
`awk`がないsystem distroでの初回親読取、status全体の同一性比較、shellのglob無効化による空列挙、analysis準備時のcwd指定ミス、Windowsの実行policy/管理者拒否も保持した。外側wrapperが0でも内側query失敗は成功にしない。
管理者用shell readerは修正版をUbuntu rootで実行しtimesyncd 1件/chronyd 0件を検出、終了42（不完全）。非rootは77。PowerShellの実parserは成功したが、`.ps1`ファイル実行はpolicy拒否で、管理者経路のend-to-endは未検証。

T03 journal 9件/11.120 test秒、decision 9件/1.711 test秒が成功。通常SIGTERMの全phaseとexit130を保存し、signal→child_exit記録は15,417,693 MONOTONIC nsだった。外部秒として校正済みではない。自然な10秒停止は再発せず、歴史的原因は未解決。
production source/test/workflow/lock未変更のため全suite・Docker/PG・実配信は再実行していない。今回の合成回帰を実Capitalの採否/監視証拠へ流用しない。前回135原本のhashも再照合した。
