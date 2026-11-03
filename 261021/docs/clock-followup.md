# 時計調査の追加読取と残る採取（2026-11-03）

298c6c8以降の[配布build/debugと内部数値の照合](chrony-runtime-control.md)で、完全debugを取得し非飽和時の計算を確認した。
下記の「内部変数未取得」は当時の状態。新しい観測でもtick11000時の値・管理者PHC/socket採取・修復資格は未了。

基点`dcfdd9c1d1bcc5b17194cc7dbd7c333b9ec19416`。
[前回の管理者ログ](clock-admin-log-review.md)、[補正量の説明](chrony-control-analysis.md)、
[今回の試行・hash索引](reviews/20261103-clock-followup-checks.json)を参照する。
**時計は未修復・未資格確認。** 読取と使い捨て試験だけを行い、実DB・registry・現在配信は触っていない。

## 新しく確認できた事実

| 対象 | 観測 | 解釈と限界 |
|---|---|---|
| 稼働範囲 | Windows tokenは非管理者。Ubuntu/docker-desktopは既にRunning。bootは前回の管理者採取と同じ | 新しいdistro起動、UAC昇格、debug-shell拒否の反復はしない。Ubuntu rootは初期PID namespaceの代わりにならない |
| timesyncd | local214→global496を読取scheduler照会と専用traceで再対応。開始tick1505、exe SHA・namespace・親233が前回と一致 | この観測区間のidentity。別boot・過去失敗へPIDだけで遡及しない |
| chronyd setter | global213によるADJ_TICK/FREQUENCY成功8件。tick11000/freq0は3件。残りは10461/10920/10633/10509/10749とfreqの組合せ | 今回は「常に11000」ではない。modeが指定するtick/freqだけを解釈し、traceの他の未初期化fieldを公開summaryへ転記しない |
| 負方向step | 40秒区間で`initd` global12579のsettimeofdayと約0.627秒の負方向差、timesyncd496のADJ_SETOFFSETと約1.297秒の負方向差が対応 | 前回区間のinitdが前進だけだった事実は保持する。今回のcommだけでinitdのexe・起動者・Windowsの機構を断定しない |
| 時計進行 | Windows QPCに対しRAWの比は0.999949〜1.000048、MONOTONICは1.073665〜1.073769。81 bracket、初回1件除外 | 有限の今回区間。過去の時間計測をこの比で補正しない。QPCは絶対UTC精度の証明ではない |
| T13自然probe | 実時計・FakeRegistryだけで68回目にfuture拒否。reviewが検証UTCより1.018032秒先。tag不変、blocked維持 | 単独probeはsetter trace終了後。直近setterへ推測で結び付けない。拒否の緩和・clamp・自動retryはしていない |
| T03回帰 | 9件成功。通常TERMはsignal→handler→HTTP終了→中断台帳保存→child_exit130、約15.011msのMONOTONIC経過 | 人工台帳停止の診断も成功。自然な10秒終了停止は未再発で、歴史的原因は未解決。外部秒として校正済みとはしない |
| T09 | 公式HEADは202609=200（25,848,624 bytes）、202610=404 | 原本取得完了ではない。10月bufferなしで評価窓を縮めず、fitも行わない |

別の採取間にはUTC表示が約5,141.170秒進む一方、MONOTONIC経過が約60.813秒という差も残った。
**その空白区間はsetter traceで覆っていない。** 大きさだけでWindows、WSL init、chronydのどれかを変更元と決めない。
試行順序にはMONOTONICと保存順を併用し、UTCだけで並べ替えない。

専用traceは全CPUのlost/dropped/overrunが0。終了後にinstance/probeを削除し、global trace制御とkprobe定義の前後SHA一致を確認した。
clock/service設定は書いていない。この一時診断制御とOS時計修復を混同しない。

### setterと自然拒否を同時採取した追加attempt

別の`joint-v1`では、専用40秒traceの開始確認後にFakeRegistry probeを起動した。
71回目のreview生成から検証拒否までのMONOTONIC区間内に、時計sampleの負方向差が1区間だけ存在し、
global496の`clock_adjtime → timekeeping_inject_offset`と成功戻り値が対応した。
reviewは検証UTCより1.235856秒先となり、承認tag不変・blockedを確認した。
**今回の自然再現にはtimesyncdのstepが対応した。** 過去T05/T13の全失敗や、
chronydが11000を選ぶ内部理由まで証明したとはしない。
原本の生成/検証時計・MONOTONIC・setter要求・trace/cleanupと機械的な区間照合を`joint-analysis.json`へ保存した。

## tick=11000の内部理由を絞れた範囲

system distroの可視ELFは、前回の稼働exe採取と同じSHA
`bf39d010dfc6177cd864d96ddd0cb32eb796704e8c68fe8bd65088d20d2e64b8`。
ELFのpackage noteはchrony4.1 / moduleVersion4.1.0.0 / Mariner2.0、
Build IDは`5bff56b8a81ac12e2fbf0c249759c6e1271cdac6`だった。
今回この可視fileを読んだことは、稼働213のexeを再読したことの代わりにはしない。

埋込み`.gnu_debugdata`をメモリ上でXZ展開し、`update_slew`の位置と長さを確認した。
しかし`base_freq`、`corr_freq`、`offset_register`等の変数symbolは含まれていない。
**関数位置が分かっても内部変数を読めたことにはならない。**
daemonへのattach、メモリ読出し、signal、動的debug有効化は行っていない。

[上流4.1のlocal.c](https://github.com/mlichvar/chrony/blob/4.1/local.c)では、周波数とoffsetを更新するとき、
driverの周波数設定とoffset累積を順に呼ぶ。
[sys_generic.c](https://github.com/mlichvar/chrony/blob/4.1/sys_generic.c)では双方が`update_slew`を呼び、
基準周波数と残った時刻差の補正を合わせて制限する。
したがって、同じchronydが短い間隔で異なるtick/freqを設定する観測はこの経路と整合する。
**今回その関数呼出し自体や内部数値をtraceしたのではなく、経路の確定ではない。**

Microsoftの[Mariner2.0のpackage仕様](https://github.com/microsoft/azurelinux/blob/2.0/SPECS/chrony/chrony.spec)も取得した。
PHC追従用の稼働設定と、system distroに見える`server time.windows.com`/`cmdport 0`のpackage既定設定を区別する根拠になる。
branch上の仕様とELFのversion表示だけで、正確なbuildの全patchや再現buildを証明しない。
RPMコマンドはsystem distroに存在せず、release/検証情報は未取得。新規installはしない。

次に必要なのは、同じboot/開始時刻/exeを再確認した上でのPHC fdとUDP323のinode所有、
生成設定・include有無の照合である。その後も内部理由が残る場合は、該当Build IDのdebug情報の有無を確認し、
必要な数値だけを読む設計を別途reviewする。推定offsetで稼働メモリを読まない。
同期調整者同士のfeedbackという仮説と、上流計算との整合を確定原因に格上げしない。

## 追加readerと確認範囲

[read_chrony_resources.sh](../scripts/read_chrony_resources.sh)は既存inventoryの補助。
PHCのdevice番号と候補processの開いたfdを照合し、socket inodeを**候補process自身の**`net/udp`/`udp6`へ結ぶ。
今回の対象は、WSL既定argv/default config、PHC `/dev/ptp0`、loopback UDP323に限定する。

- fdは最大256。無関係なfdのtargetや内容、環境変数、認証keyを出力しない。
- deviceは`stat`だけ。deviceやsocketをopenして内容を読まず、ioctlも呼ばない。
- 開始tick/exe SHA/namespace/config SHAの前後比較とfd linkの前後比較で、観測した変更を不完全扱いにする。
- 別argv/config/include graph、候補0件/複数、PHC/socket不足、読取失敗/変更は42。非root77、必要tool不足69。
- 0は限定読取完了であり、所有者の署名認証、全fdの原子的snapshot、内部原因の解決、時計資格の合格ではない。

Ubuntu rootでの実行はchronydが見えず42だった。**初期namespaceでのPHC/socket採取は未実施。**
fixtureのdevice照合には`/dev/null`を利用してdevice番号照合を試し、本物のPHC測定とは明確に区別する。
専用16件が成功。inodeの他namespace/別port、IPv6、include/別argv、fd/実行file/config変更、fd上限の負例を含む。
管理者での追加採取は[手順書](clock-owner-admin-read.md#chrony-resources-admin)を参照。

## 修復案・影響・切戻し

今回修復は行っていない。[既存の修復候補表](chrony-control-analysis.md#clock-repair-options)を維持する。
採取後、同期責任を一つにする保守比較の必要性を判断する。実施するなら事前にOS変更の許可、host精度、
全WSLへの影響、元のservice active/enabled・設定hash、切戻し方法を具体化する。
service停止だけで資格合格にせず、独立した長時間/休止再開後の時計観測と厳密future拒否を確認する。
現在の前提ではT12の実時間計測・T13bの実採否を進めない。

追加のhost読取では、Ubuntu/system distroの可視process内に`initd`は見つからなかった。
これは初期namespaceを含む共有kernel全体で不在という意味ではない。現setterのexe/管理元は未確定のまま。
WindowsのW32Time/vmictimesyncはStopped/Manualだった。
Windowsから`w32tm /stripchart /computer:ntp.ubuntu.com /samples:5 /period:1 /dataonly`を実行し、
返った5offsetは−1.3567374〜−1.3418427秒だった。`/resync`やservice開始は行っていない。
単一NTP先・短区間・通信経路の観測なので絶対UTC精度の校正や原因確定とはしないが、
**host/PHC側が正確だと無条件に仮定してUbuntuの同期だけを止める修復案は採用しない。**
hostの外部UTCとの差と、同期責任の決定も先に確認する。
[追加原本索引](reviews/20261103-clock-extra-checks.json)を参照。

最初のsandboxではWindows interop socketとDNSを拒否されたため、読取範囲のsandbox外実行で確認した。
package照会は外側exit0でも内側rpmが127だったので、package検証成功には数えない。
追加readerの初回13テストはUDP列の数え違いで2失敗。結合列`tr:tm->when`を修正し、別portも拒否して14件成功、
さらにuppercase include/config変更の拒否を加えて16件成功とした。初回logを上書きしていない。
