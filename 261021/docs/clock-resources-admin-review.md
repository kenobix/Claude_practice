# 管理者PHC・UDP原本の照合とhost同期の残条件

2026-11-09、基点`464df19`。指定されたowner `b9641680-…` とresources `ed1b37fc-…` の2 directoryをofflineでreviewした。
[証拠索引](reviews/20261109-admin-resources-checks.json)、[原本取得手順](clock-owner-admin-read.md)、[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を参照。
**採取区間のchronyd実体とPHC/UDP所有を確認した。時計資格・tick11000の内部原因・実fit開始条件は未達。**

基点`b0589ee`後の追補：[Windows管理者runtime review](clock-host-runtime-review.md)で、下記で不足していたsource/configurationを取得・照合済み。
以下の権限拒否/管理者query未実施は前回の履歴。現在の残件はpeer/last-error等による原因切り分け、独立源/同期責任/全資格である。

## 採取の完了と内容の結合

ownerの`reader_exit_code=0`、resourcesの`inventory_exit=0/resources_exit=0`と、各log末尾の実完了行/errors0を照合した。
保存hash一覧5件/7件は、script1件/2件と出力4件/5件のbytesに全一致した。
一覧の`/proc/1031/root/…`は採取時の保存名であり、現在の`/proc/1031`を辿らず既知のrepo相対pathへ限定して照合した。
raw11file（owner5/resources6）は変更せず、別のignored review directoryへsnapshotとhashを保存した。
console貼付けのechoは乱れているが、fileの完了行・hash照合が成功している。見た目だけからコマンドの再実行を求めない。

同じbootのuptime bracketはowner `[186127.55,186127.77]`、resources `[186422.75,186423.10]`。
間の連続監視や、外部UTCに対する時刻・経過秒の資格を表さない。
従来の`.ps1`方式とは違い、今回は直接保存方式のreader終了値/実完了行/uptime/hashを確認できた。
Windows管理者tokenの別署名証明や`wsl.exe`自身の終了値は含まれない。未採取のmetadataは補わない。
SHAは受領後の内容結合であり、改ざん不能な署名や独立時計源の真正性を証明するものではない。

## 照合できた実体

| 対象 | 採取区間で確認した値・範囲 |
|---|---|
| chronyd | 初期PID namespaceのPID213、開始tick319、親PID1 `/init`。argv `/sbin/chronyd`、exe `/usr/sbin/chronyd`。両inventoryの開始tick/親/exe SHA/namespaceが一致 |
| executable | 320,600bytes、SHA `bf39d010dfc6177cd864d96ddd0cb32eb796704e8c68fe8bd65088d20d2e64b8`。resourcesのdev/inode/sizeは`27:4227:320600`。hash一致は既照合Mariner buildへの対応 |
| namespace | pid4026531836 / mnt4026531841 / time4026531834 / net4026531840（netはresources）。Ubuntu側timesyncdとはpid/mntが異なる |
| 親 | PID1 mini_init、exe `/init`、SHA `f84f9ae9116b972fce6723f920fb8fb1dedff2aeb0baa40ed7c5d2b9f4b73311`。起動瞬間のlauncherは未観測 |
| 設定 | `/etc/chrony.conf` SHA `9315d50044034745ade10c260359b463eb77a61af9330f818f68ed16253f8528`。既照合WSL生成6行と一致し、別config argv/include graphの警告なし。sourceは`refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0` |
| PHC | FD3がchar device rdev `247:0`を開く。`/dev/ptp0`と一致し、sysfs clock_nameは`hyperv`。hostに依存する時刻経路であり独立外部UTCではない |
| UDP | net namespace内のFD5→inode3582→`127.0.0.1:323`、FD6→inode3583→`[::1]:323`。いずれもremote未接続。NTP同期先の123番portや、過去queryの認証証拠とは混同しない |
| 採取時のtimesyncd | PID973/開始tick18257168、親233 systemd。exe SHA `5e87427803daf1dbe974cc62fba335c96ff24e37892f67d9095d6703d2c32009`、systemd親SHA `9a813714ffd4dadb692afe44384c9c5b334fa323a4ec1dd5157aebdcbbb0667a`。service cgroupと共有time namespaceを確認 |

resource readerはPHC fd/UDP対応と同時にexe開始tick/hash/dev/inode/size・namespace・configの前後比較を行う。
保存されたreader SHAが現在の実装と一致し、完了/errors0からその比較が通った範囲を確認した。
after値の別dumpやFD先の装置内容・通信payloadは保存していない。FDが存在することだけで、どの応答が過去のqueryへ返ったかは認証しない。

## 過去のsetterとの対応と限界

旧admin原本のboot・chronyd PID/開始tick/exe SHA/namespace・設定SHAは今回と一致した。
一方、11月3日の内部値probeの実行fileはdev42/inode4227、今回はdev27/inode4227であり、**devを含むfile identityは不一致**。
同じSHA/size/buildのbytesと対応するが、同じinode番号だけで同じ実行file実体や無execの連続性を確定しない。
dev差の発生理由は今回の原本からは未確定。新しいattach/probe/traceは行っていない。

旧timesyncdはPID496/開始tick1505、exeと親systemdのSHAも今回とは異なる。
今回のPID973を、旧traceのsetter496へ読み替えない。変更・再起動・更新の経緯はこの2原本に無く、推定で確定しない。
drift保存値も旧−23906.640630から今回+6820.697933へ変化しているが、現在のbase/corr/offsetやkernelのtick/freqの同時snapshotではない。
tick11000時内部値/kernel書込とT03 phase/stackは自然再発の新資料がある時だけ照合する。

## host側で追加確認した範囲

新しい管理者資料に続き、WindowsのW32Time/vmictimesyncのservice状態と、localな`w32tm /query`3種類だけを読んだ。
通常sandboxはWSL interopの`UtilBindVsockAnyPort`拒否でexit1。許可付き別attemptはWindows token非管理者で実行できた。
W32TimeはRunning/Manual、vmictimesyncはStopped/Manualだった。前回のStoppedという履歴を現在値へ持ち越さない。

`/query /status`はexit0で「閏インジケーター3（同期未実行）」「階層0」「最終正常同期時刻 未指定」「ソース Local CMOS Clock」と表示した。
これはその照会時点のlocal service状態であり、Windowsの絶対UTCずれや、他の全補正経路の不存在を測った結果ではない。
service Runningは同期済みや独立参照の真正性を証明しない。
`/source`と`/configuration`はともに`0x80070005`、native exit−2147024891。wrapperは部分取得をexit42にした。
native出力はCP932、外側PowerShell/CLIXMLはUTF-8だったため、境界内のnative部分だけをCP932でdecodeし、元bytesを保持した。
CLIXMLのmodule準備progressを取得エラーと混同せず、拒否されたqueryは成功扱いにしない。

続いて読取権限のあるHKLM保存値の`Type/NtpServer/Enabled/InputProvider`だけを確認した。
W32TimeのTypeはNTP、peerは`time.windows.com,0x9`、NtpClientとVMICTimeProviderはEnabled/InputProviderとも1。
限定したpolicy key2件はTest-Pathで見つからなかったが、全policy不存在やruntimeの有効設定まで証明しない。
保存された設定値・照会時のservice状態・実際の同期状態を別々に記録し、設定済みだから同期成功とはしない。

既存System logのTime-Serviceだけを最大12件読取し、12件を保存した。
record26111/26136にはNTP peerのDNS解決失敗（event134）、26170/26266にはvalid time data受信（event37）、
26171には当時の同期開始（event35、local stratum5）がある。以前の失敗と後の受信の両方を残し、DNS失敗が恒久的・現在の未同期の原因と断定しない。
event158はWindows側VMICTimeProviderが現hardwareで非対応として停止したとの記録で、非Hyper-V guestでは想定されるとmessageが説明する。
これをWSL guestのPHC停止やHyper-V全体の故障へ読み替えない。
eventのTimeCreatedUTCはhost時計による未資格の記録で、外部UTC証明ではない。最大12件の範囲外の出来事や現在の全同期履歴を網羅したとも主張しない。

| 同期経路 | 今回増えた根拠 | まだ確定しないこと |
|---|---|---|
| Windows側NTP | 保存peer/Enabled、過去のDNS失敗と後の受信/同期開始event、現在のlocal status | 現在のeffective runtime設定、現在未同期となった経緯、絶対UTC誤差・認証/校正 |
| Windows/Hyper-V→WSL PHC→chronyd | 指定採取区間のPHC装置とFD、WSL生成設定/親/実体、UDP command socket所有 | host側値と同時bracket、独立参照、不確かさ、飽和時内部状態 |
| Windows/backend→Docker initd | [過去のruntime setter対応](initd-owner-correlation.md)と[静的取得CALL](backend-time-path-review.md)を継承 | 今回のbackend応答値/動的stack・現在のinitd identityは未採取。PHCと別の独立源として数えない |
| Ubuntu timesyncd | 今回のPID973・service cgroup・親・共有time namespace | 新実体のeffective同期元/時刻書込、旧496から変化した経緯。過去setter証拠を新PIDへ流用しない |

[Microsoft公式のw32tm説明](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings#command-line-parameters-for-w32time)では、local実行の管理者権限と、`/query /source /configuration /status`の照会機能が説明されている。
今回は設定変更・resync・stripchart・service起動/停止を行っていない。管理者の次の限定読取は[host採取手順](clock-owner-admin-read.md#host-w32time-admin)。

## 検証結果と次の判断

offline診断は原本hash・完了・identity・config・PHC/UDP照合と、合成コピー23負例を確認した。
19件はmember hashを付け直した上での意味的な改変、4件はhash不一致/欠落/余剰/重複hash-entryで、全拒否した。
最初のparserは親のPPidも拾って重複判定しexit1。process状態とparent状態を分けた次版は16負例成功。
追加版は旧CRLF原本のboot行を拾えずexit1。元hashを保ってdecode後だけ改行を正規化し、最終23負例が成功した。
診断script/失敗log/旧版/改変生成recipeはignored原本へ保存。製品unit23件を追加したわけではなく、全suiteや新CIの受入も主張しない。

| 判断 | 次の条件・影響・切戻し |
|---|---|
| 今回閉じた項目 | 指定された採取区間のchronyd current identity・PHC fd・UDP inode所有と直接保存経路のreview。新情報なしに同じinventoryを再採取しない |
| 次の読取 | 管理者W32Time source/configuration、host/backendの同期責任、dev差やtimesyncd identity変更の経緯が分かる既存資料。無関係なprocess/環境/認証情報は取得しない |
| 実source・collector | 独立UTC/elapsedのidentity・真正性・不確かさ・共有依存の資料は未提出。PHCやLocal CMOSを代用しない。採用review後に実adapter/host deadline/固定負荷へ進む |
| 修復案 | 複数補正層を前提に、設定前状態と責任分担をreviewして保守比較を設計する。W32TimeがRunningになった事実だけで修復済みとしない。initd全体やchronydを独断で停止しない |
| 影響・切戻し | 共有kernelとDocker配信へ影響し得る。service active/enabled・生成設定・管理元の変更前状態と復元手順を先に固定し、OS/保守許可後のみ実施。履歴のUTC逆行は切戻しで消えず、新規全資格が必要 |
| 実候補 | 時計資格と全前提は未達。T10静的snapshot/protocolは受入済みのまま、fit直前再照合・T11/T02/T12/T13b実candidate証拠は未着手を維持 |

OS/時計設定・実DB・registry・現配信・future拒否は不変。独立した新情報の無い受入済み試験やT09取得/集計は反復していない。
