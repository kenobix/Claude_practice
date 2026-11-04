# initdの時計書込とDocker Desktop管理層の照合

基点891a420以降の[backend取得interface・時計書込CALLの静的照合](backend-time-path-review.md)を追加した。
保存済みELFを対象とし、新しいsetter traceやbackend応答の証拠ではない。以下の動的観測は前回区間のまま保持する。

2026-11-04、基点`d8ec16412cc833e4b9eb9582921a4309265d1166`。
[前回の内部制御調査](chrony-runtime-control.md)からの追加診断。
原本・全試行・失敗・hashは[今回の索引](reviews/20261104-clock-readiness-checks.json)を参照する。

**今回の負方向settimeofdayは、Docker Desktopの`/initd services`のスレッドに対応した。**
現在の実行fileと管理層の照合が進んだ。chronydのtick11000の内部原因、時計修復、資格確認は未完了。
以前の全setterやT03/T13の失敗へ、この対応を遡及適用しない。

## 採取条件と照合方法

新しいPHC/socket管理者directoryはなかった。旧管理者ログ1件は以前と同じbytes/SHA。
Windows管理者shellの再試行や、別namespaceへのmount/nsenterは行っていない。
既にRunningであるUbuntuとdocker-desktopを確認し、Docker Desktop内のinitd候補2processだけを読んだ。
最初の単発`adjtimex(modes=0)`はtick10262/freq2792973。11000の再現として扱わない。
chronyc trackingはPHC0・frequency約73579ppm slowと返したが、daemonのPHC fd所有や外部UTC精度を証明しない。
初回system読取にはWSLのcwd翻訳警告があった。stdoutの読取結果・exit0と警告を両方保存した。
後続の呼出しは`--cd /`を明示した。

kernel BTF SHAを前回検証済みの値へ照合してから、一意名の専用trace instance/probeを作った。
`chrt -p <tid>`だけでschedulerを照会し、`security_task_getscheduler`の対象global TID/TGID/開始nsを記録した。
**`chrt -p`は今回の照会用の引数で、優先度変更の引数は使っていない。**
2processの全35スレッドを前後で照会し、各照会のMONOTONIC範囲・順序・`/proc`の開始tickとkernel開始nsを照合した。
候補のexe SHA/dev/inode/size、開始tick、namespaceも前後一致した。commだけで対応付けていない。

traceは事前に固定した45 MONOTONIC秒で終了した。ユーザー空間へのuprobeは今回使っていない。
時計syscallを書いたのは既存processであり、診断script自身は時計設定やserviceを操作していない。
一時的なkernel診断probeの負荷はあり、無観測時の性能や時計資格の試験ではない。

## 今回対応した実体

| 対象 | 読取結果 |
|---|---|
| 時計を書いたスレッド | global TID12582 → Docker Desktopの可視TID75。kernel開始ns1830441609058、`/proc`開始tick183044 |
| 所属process | global TGID12571 → 可視PID64、argvは`/initd services`、開始tick183037 |
| 実行file | `/initd`、69,586,424 bytes、SHA `37ddac517530bcdf87c40c2b1df37188d093a6ee6204df43e64edbfba3c27b43`、dev118/inode1266 |
| processのnamespace | pid4026532393 / mnt4026532392 / time4026531834 / net4026532251 |
| 親 | global12550 → 可視PID43、同じ`/initd`、argvは`/initd`、開始tick183008 |
| さらに上の管理層 | 可視PID42の`unshare -mpf … wsl-bootstrap jump` → PID11の`wsl-bootstrap run` → WSLの`/init` relay/session |
| bootstrap | `/usr/local/bin/wsl-bootstrap`、SHA `3c02aea445bab3e9ffbada3b2aa615e99a0a47f4bcabed935c9d9301b8006527`。引数はDocker Desktopのbase ISO/CLI ISO/data diskを指定 |

公開summaryではWindows個人directoryやdata-disk識別値を省いた。元の限定parent-chain読取はignored reportsへ保持する。
現在の親子関係と引数が分かったという意味で、起動瞬間をtraceしたものではない。
boot、開始tick、exe SHA/inodeを伴わない将来のPID12582/64を、この表と同じprocessと扱わない。

## 実際の時計変更

MONOTONIC154322.417563秒で上記スレッドが`do_settimeofday64`へ入り、要求UTC nsは1793795939033317000。
同じTIDの`sys_settimeofday`は0を返した。
対応する時刻sampleの前後で、MONOTONICは10,369,756ns進み、REALTIMEは733,087,722ns戻った。
setter event・要求値・成功戻り値が、この逆行sample区間へ対応した。

約0.271秒後にはtimesyncd496の別要求もあり、`ADJ_SETOFFSET | ADJ_NANO`を選んだtime値は
−1秒+362242222ns、すなわち−0.637757778秒だった。両processの経路を一つの要求へ混ぜない。
このtraceではtick11000の要求は0件。chronyd内部値の再採取や、11000が出るまでの延長はしていない。

全CPUのoverrun/commit overrun/dropped eventsは0。専用instance/probeを削除し、global trace制御と
kprobe/uprobe定義の前後hash一致を確認した。

## binaryから分かった範囲

実exeと同じSHA/sizeのコピーを静的解析した。実行・installはしていない。
`.go.buildinfo`はGo公式の[build情報読取実装](https://go.dev/src/debug/buildinfo/buildinfo.go)の形式に従って読み、
Go1.27.3、main path `github.com/docker/pinata/linuxkit/pkg/desktop/rootfs/cmd/initd`を得た。
module版は`(devel)`で、この情報から正確なGit revisionや署名済みprovenanceは取得できない。
symbol文字列には`desktop.runBackendTimeSync`・そのfunc1・`syscall.Settimeofday`が存在した。
backend時刻同期という仮説と整合するが、**当該goroutineの動的call stackやbackendの時刻源を観測した証拠ではない。**

## 検証と残件

照合parserは今回の2×35照会・exe・開始時刻・namespace・setter成功・時刻sample・cleanupを検査した。
別の合成コピー7ケースで、成功戻り値欠落、未照合TID、照会identity変更、exe SHA不一致、cleanup未完了、
buffer overrun、namespace変更をいずれも拒否した。原本は変更していない。
これは診断parserの検証で、製品unit suite・CIの新しい受入や、実Capital運用性能の証拠ではない。

| 区分 | 次の作業・境界 |
|---|---|
| 確認済み | 今回のinitd setterと実exe・thread group・Docker Desktop bootstrap管理層の対応 |
| 未確定 | backendから受けた時計値/プロトコルとhost同期責任、chronydの飽和時内部値、PHC/UDPの管理者採取、T03歴史的停止 |
| 修復案 | [既存の修復表](chrony-control-analysis.md#clock-repair-options)へ今回の管理層を反映。initdはservices全体の管理processなので、時計だけを止めるつもりでkill/停止しない。時刻同期専用のサポートされた設定・保守比較案を先に確認する |
| 影響 | Docker Desktop停止/再起動は既存containerや実配信も止め得る。共有kernelの時計調整は他distro・timeout・証跡へも影響する |
| 切戻し | 変更案を採用する前に管理層の元設定と復元操作を固定し、別途OS変更許可を得る。時計履歴自体は切戻しで消せず、保守後に新しい資格試験が必要 |

future拒否の緩和、時刻clamp、自動retry、OS/時計設定変更、実DB/registry/現在配信の操作はしていない。
新しい管理者ログや状態変化なしに同じ採取を繰り返す必要はない。
