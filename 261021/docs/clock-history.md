# 時計資料：原因調査の履歴（保留）

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

**時計研究・追加採取・OS操作は引き続き保留。ここにある過去手順は実行許可ではない。**

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [backend時刻取得経路の静的照合と保守判断](#backend-time-path-review)
- [chronydの補正量とtick=11000の追加調査](#chrony-control-analysis)
- [管理者採取ログの照合と入力待ちの整理](#clock-admin-log-review)
- [時計調査の追加読取と残る採取（2026-11-03）](#clock-followup)
- [Windows peer・最終エラー・service identityの照合](#clock-host-cause-review)
- [UTC逆行の呼出元の調査（2026-11-01）](#clock-origin-investigation)
- [時計調整プロセスの管理元照合（2026-11-01）](#clock-process-ownership)
- [管理者PHC・UDP原本の照合とhost同期の残条件](#clock-resources-admin-review)
- [initdの時計書込とDocker Desktop管理層の照合](#initd-owner-correlation)
- [T03/T13の中断・時計診断](#interruption-clock-diagnostics)

<a id="backend-time-path-review"></a>

## backend-time-path-review

<a id="backend-time-path-review--backend時刻取得経路の静的照合と保守判断"></a>

### backend時刻取得経路の静的照合と保守判断

2026-11-04、基点`891a420`。[前回のinitd管理層照合](clock-history.md#initd-owner-correlation)の続き。
原本・失敗・hashは[今回の検証索引](reviews/20261104-backend-boundary-checks.json)を参照する。

**保存済みの同じinitd binaryに、backendの時刻を取得し、Settimeofdayを呼ぶ経路が存在した。**
今回は実行fileの静的解析であり、新しい時計書込・backend応答・goroutine stackの観測ではない。
現在のPHC/UDP所有、tick11000の内部理由、hostの外部UTC精度、時計資格は未解決。

<a id="backend-time-path-review--方法と不採用の試行"></a>

#### 方法と不採用の試行

前回保存した公開ELFのSHA
`37ddac517530bcdf87c40c2b1df37188d093a6ee6204df43e64edbfba3c27b43`を先に照合した。
稼働processへのattach・memory読取・uprobe・追加traceやbackend HTTP要求は行っていない。
今回の結果はこの保存済みbinaryだけに結び付き、現在も同じPID/開始時刻/inodeとは主張しない。

[Go公式pclntab reader](https://go.dev/src/debug/gosym/pclntab.go)と
[runtime moduledata](https://go.dev/src/runtime/symtab.go)を参照した。
ELFはPIE形式で、disk上のpcHeader.textStartは0だった。
最初の解析はこれを基準にしたため、CALLの多くが未解決で、関数途中を逆アセンブルする結果となった。
scriptのexit0でも正しい解析とは扱わず、log・不正なassembly/summaryを`static-v1-invalid/`へ保存して不採用にした。

次の試行は`.go.module`のR_X86_64_RELATIVEを使ったが、最終functab境界をetextと同一視してassertで失敗した。
maxpcとetextには末尾paddingがある。失敗を残し、minpc/text・maxpc/functab末尾・etextとの範囲関係を別々に検証した。
最終試行はtext=`0x832640`、maxpc=`0x2176ec1`、etext=`0x2176ed0`、75,982関数の単調な境界を照合した。
section先頭へ推測で一律補正したものではない。

<a id="backend-time-path-review--コードとして対応した範囲"></a>

#### コードとして対応した範囲

アドレスはこのELF内のvirtual address。ASLR後のruntime addressやfile offsetと混ぜない。

| 対象 | 静的に照合した内容 |
|---|---|
| `desktop.runBackendTimeSync` | `0x2095280`〜`0x2095880`。`backendclient.New`、`time.Now`、`time.Since`、`syscall.Settimeofday`等への直接CALL |
| backend呼出し | `backendclient.New`が返すinterface table=`0x40a8458`。呼出し側のoffset=`0x140`とrelocationを結び、`(*backendClient).GetCurrentTime`=`0x109a6c0`へ対応 |
| 時計書込 | CALL site=`0x20955d8`の相対命令bytesからtarget=`0x8e4f20`を再計算し、`syscall.Settimeofday`の関数境界へ照合 |
| 時刻要求の識別 | 同経路のliteralに`backend /time request timed out: %v`、`backend time: %w`、`settimeofday: %w`。単に全binaryを文字列検索した結果ではなく、対象関数から参照するbytesを確認 |

これにより、backend時刻同期という説明は「symbol名が存在する」段階から、同じコード内の取得interfaceと時計書込CALLの対応まで進んだ。
ただしbackendの応答値、接続先process、backend内のWindows時計取得関数は未観測。
前回の負方向setterがこの関数を実行したとする動的stackも今回取得していない。
過去の全T03/T13失敗や、chronyd飽和の原因をこの経路へ断定しない。

検算では同じELFのCALL opcode/相対target、interface/relocationとsummaryを比較した。
合成コピー5件（基準アドレス0、Settimeofday欠落、CALL target改変、別ELF SHA、別interface method）を全拒否した。
診断summaryの検証であり、製品に新しく5 unit testを追加したという意味ではない。

<a id="backend-time-path-review--公開仕様と修復案への影響"></a>

#### 公開仕様と修復案への影響

[CanonicalのWSL時計説明](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)は、Hyper-V側とguest NTP側が異なる時刻源を使う場合の競合を説明している。
今回の複数setter観測と整合する一般的な説明だが、本環境の飽和原因を確定するものではない。
同資料にはUbuntu releaseごとの既定動作の違いがある。Ubuntu用の説明をMariner側chronydの生成設定と取り違えない。

Dockerの[利用者設定](https://docs.docker.com/desktop/settings-and-maintenance/settings/)と
[管理者設定の公開一覧](https://docs.docker.com/desktop/enterprise/hardened-desktop/settings-management/settings-reference/)も保存・確認した。
確認した2資料からは、今回のbackend時刻同期だけを停止する設定を特定できなかった。
全実装・全releaseに設定が存在しないとする証明ではない。推測したJSON keyや非公開endpointで切り替えない。

| 段階 | 次の条件・影響・切戻し |
|---|---|
| 読取を継続 | 新しい[管理者PHC/socket採取](clock-operations.md#clock-owner-admin-read--chrony-resources-admin)で現在identity/所有/設定を先に確認。今回は旧log1件だけで、新しい採取はなかった |
| 同期責任を設計 | host/backendの時刻源と外部UTC差、Hyper-V・Mariner chronyd・Ubuntu timesyncd・Docker initdの役割を分ける。Ubuntuの調整者1つを止めても、残りが消えたとは扱わない |
| 保守比較案 | 別途OS変更許可と保守時間を得てから、管理元で1条件ずつ比較する案をreview。現配信はDockerに依存するため、Docker停止を無影響の診断として実施しない。initd全体のkillも行わない |
| 影響と切戻し | 元のservice active/enabled、生成設定・exe/hash、Docker管理状態を保存し、同じ管理元で元へ戻す手順を先に固定。共有kernel全体への影響と、時計履歴は消せない点を残す |
| 資格確認 | 修復許可後に事前固定した長時間/休止再開、QPC/RAW/MONOTONIC、UTC順序、厳密future拒否を新規採取。短い未再発を合格にしない。過去p95を後から補正しない |

上記は保守案の前提整理で、実施許可や修復済みを意味しない。
OS/時計設定、実DB/registry/現配信は不変。future拒否の緩和・clamp・自動retryは行っていない。
T03の自然停止は今回観測しておらず、新しいphase/stackはない。

<a id="chrony-control-analysis"></a>

## chrony-control-analysis

<a id="chrony-control-analysis--chronydの補正量とtick11000の追加調査"></a>

### chronydの補正量とtick=11000の追加調査

2026-11-03の[追加読取と同時自然再現](clock-history.md#clock-followup)で、今回のfuture拒否とtimesyncdのstepを対応した。
chronydは今回常に11000ではなく、内部変数・PHC/socket本採取は未了。以下の過去区間の数値/限界は当時の記録として保持する。

続く[管理者ログ照合](clock-history.md#clock-admin-log-review)で稼働exe/hashと生成設定を確認した。以下の未採取という記述は基点からの当時の状態で、内部制御原因の未確定は継続する。

基点: `2181bfe453bf9c87ec9374a5d74013bcdddb86b0`。
[管理元調査](clock-history.md#clock-process-ownership)、[限定した管理者採取](clock-operations.md#clock-owner-admin-read)、
[今回の証拠JSON](reviews/20261101-chrony-control-checks.json)を参照する。

**tick/freqの数値の意味は絞れた。稼働chronydの実行file・管理元と、限界値を要求し続ける内部原因は未確定。**
管理者採取ログは指定のreports内に存在せず、ユーザーからも「まだ採取していない」と回答を得た。
現在のWindows tokenは非管理者。管理者への自動昇格や同じ拒否の再試行はしていない。

<a id="chrony-control-analysis--今回の観測と照合"></a>

#### 今回の観測と照合

| 対象 | 確認結果 | 限界 |
|---|---|---|
| Ubuntu timesyncd | local PID 214→kernel PID 496、親233、開始tick1505・task start 15059948976ns、exe hash・cgroup・namespaceが前回と一致 | 当該bootの読取照会/kprobeによる対応。将来はPID固定で使用しない |
| 負方向補正 | 40 MONOTONIC秒のtraceで同じPID496が−4.197671651秒を要求し、syscallは0。実時計の差変化も一致 | 過去T03/T13失敗の呼出元の遡及証明ではない |
| tick書込 | kernel TID213/comm chronydが10回、modes16386（ADJ_TICKとADJ_FREQUENCY）・tick11000・freq0、全てsyscall成功 | kernel番号とcommは観測値。稼働exe/hash・親・開始tick・namespace・生成設定はまだ読めていない |
| 同時chronyc読取 | 明示したUDP127.0.0.1:323から21応答、全てPHC0。Frequencyは−23906.641ppm、System time補正は1.857597709〜2.478570938秒 | query前後のMONOTONICで囲った別時点の応答。kernel TID213へ認証された接続でも、内部変数の同時snapshotでもない |
| 時計の進行 | 同区間のMONOTONIC/RAW経過比1.100000036、6,802 clock sample | 今回Windows QPCとのbracketは再採取しておらず、外部UTC精度や過去計測の校正には使わない |
| 観測の回収 | 専用probe/instance削除、既存trace制御とkprobe定義の前後hash一致、lost events 0 | 一時的な観測制御は追加/削除した。OS時計/同期設定は書いていない |

同時queryはWSLのcwd引継ぎ警告を21回出した。応答CSVと終了0は保存したが、stderrなしの成功とは記載しない。
別attemptで`--cd /`を明示した同じ読取queryがstderrなしで成功した。warningを消すために最初のログを上書きしていない。
Windows token queryのstderrにはPowerShellのmodule準備progressがあり、errorと混同しない。

<a id="chrony-control-analysis--数値の意味をソースと計算で確認した範囲"></a>

#### 数値の意味をソースと計算で確認した範囲

system distroで見えるchronydは4.1、binary hashも前回と一致した。
**そのfileが稼働kernel TID213のexeだという直接証拠はまだない。** 以下は[upstream chrony 4.1 Linux実装](https://github.com/mlichvar/chrony/blob/4.1/sys_linux.c)に対する条件付きの説明である。

USER_HZ=100ではnominal tickは10000。Linux driverから復元できる補正量は次の式になる。

```text
driver correction ppm = 100 × (10000 − tick) − freq / 65536
tick=11000, freq=0 → −100000 ppm
```

負の値は時計を速める側。通常値に対してtickだけで約10%速める指定と整合する。
これは「tickという数字だけが11000なら必ず同じ補正」という意味ではない。
隣接tickを維持するhysteresisがあり、freqとの組合せを確認する必要がある。
同じ−100000ppmでもtick10999/freq6553600が可能で、tick11000/freq−3342336なら−99949ppmになる。

[generic driver](https://github.com/mlichvar/chrony/blob/4.1/sys_generic.c)では、推定した基準周波数と、残った時刻差を徐々に直すslew補正を合わせ、Linux driverの範囲へ制限する。
同じ版の[tracking生成](https://github.com/mlichvar/chrony/blob/4.1/reference.c)と[local frequency](https://github.com/mlichvar/chrony/blob/4.1/local.c)を追うと、chronycのFrequencyは基準側の値であり、kernelへ渡したslew込みの総量ではない。温度補償の変換もあり、一般には内部base_freqそのものの直接読取とはしない。
したがって、読取Frequency−23906.641ppmとkernel側−100000ppmは、その違いだけで矛盾ではない。

確認用の条件付き計算例:

```text
base_freq = −23906.641 ppm と仮定
corr_freq = −1/12 と仮定（upstream既定maxslewrateの速める側）
total_before_limit = base_freq + corr_freq × (1000000 − base_freq)
                   = −109232.194416667 ppm
Linux driver範囲へ制限 → −100000 ppm → tick11000/freq0の組合せが可能
```

上流の`our_round`、Linux `set_frequency`、generic `clamp_freq`をそのまま抽出し、timex書込を**メモリ上のstub**に置いたCプログラムで8出力ケースと1つの制限条件を検証した。実時計のsyscallを呼ぶプログラムではない。符号、反対側の限界、隣接tick保持、freqを含む逆算を確認した。
原本source、生成C、binary、コンパイルコマンド、出力とhashはignored reportsに保存した。

この計算で分かったのは**観測値が補正限界と一致すること、およびその値になる一つの内部経路**までである。
実際のmaxslewrate、base_freq、corr_freq、offset_register、制限前の総量を読んでいないため、稼働daemonがこの例で飽和したと確定しない。
基準推定値自体が限界付近の場合や、別の実装・制御状態も除外できていない。

<a id="chrony-control-analysis--原因・仮説・未確定事項"></a>

#### 原因・仮説・未確定事項

- **確認済みの現在の原因:** Ubuntu timesyncdが負方向stepを実行する。別のkernel taskはtick/freqを書き、共有時計への複数の調整経路が続いている。
- **有力だが未確定の説明:** PHC追従側が、他の調整者によるstepや周波数変更を外乱として補正し、総量がdriver限界付近になるfeedback。PHC0応答・WSL組込起動設計・今回の数値計算は整合するが、内部状態または管理元を固定した保守比較が必要。
- **未確定:** chronydの実exe/hash・親/開始時刻・namespace・有効な生成設定・PHC fdとUDP socketの所有、限界前の制御量、Windowsの絶対UTC精度、過去のT03停止/SQLite future失敗との同一原因。

[WSL 2.6.3のStartTimeSyncAgent](https://github.com/microsoft/WSL/blob/2.6.3/src/linux/init/main.cpp)はPHC用configを生成してchronydを起動する設計だが、runtimeの所有証明にはしない。
system distroから見えるtime.windows.comのpackage既定fileを稼働設定とみなして修正しない。

<a id="chrony-control-analysis--clock-repair-options"></a>

<a id="chrony-control-analysis--次の採取と修復案・影響・切戻し"></a>

#### 次の採取と修復案・影響・切戻し

次は[管理者採取手順](clock-operations.md#clock-owner-admin-read)を実施し、表示された保存directoryをチャットに伝える。
既存のreaderは稼働候補を列挙し、PIDを自動的に再確認する。
まずexe/hash・親・namespace・開始tick・process root内の選択configを照合する。
読取範囲の不足やinclude指定が見つかれば追加採取を限定して設計する。
daemonのPHC fd/socket inodeと、同じboot/identityを保持するsetter traceの対応も残る。
別bootや別processなら今回の213/496の対応を再使用しない。

| 提案段階 | 具体案・先に必要な根拠 | 影響 | 切戻し・合格条件 |
|---|---|---|---|
| 管理元照合 | 既存の限定readerを管理者で実施。通常namespaceから読めない実体を取得 | 採取ファイルを作成。service/時計操作は不要 | rawをignoredで保持。終了42や読取失敗を完全採取にしない |
| 同期責任の決定 | runtime identity、実config、hostの外部UTCとの差を確認してから、WSL内の調整者を一つにする保守案を選ぶ | Windows/全WSLの時計・timeout・認証/監査時刻へ波及する可能性 | 現在のactive/enabled、unit/drop-in/config/hashを先に保存し、元の管理層で復元。時計履歴は消去できない |
| 隔離した保守比較 | 別途OS変更の許可がある保守時間にUbuntu timesyncd一時停止等を検討。tick/offset/各時計の進行率を前後採取 | 比較中の時計の動きは実運用計測の証拠に使えない | 元がactiveなら再開、enabled状態を復元。停止だけを修復完了としない。失敗attemptも保存 |
| chronyd側変更 | 実起動元と生成設定を確定した場合だけ検討。maxslewrate/maxdriftを推測で変更しない | 再起動時の設定再生成、全WSLの時刻進行へ影響 | 生成元・argv・設定を記録し、その管理元で復元。単発tick/freq変更は避ける |
| 時計資格の確認 | 許可された修復後、QPC bracket、RAW/MONOTONIC、setter、厳密future境界、長時間/休止再開後を新規attemptで確認 | 計測期間を確保する必要がある | 事前に観測期間・許容差・失敗条件を固定。未合格ならT12実運用時間の測定を進めない |

今回修復操作は未実施。future拒否の緩和・clamp・自動retryは追加していない。
過去のp95を現在の進行比で割り戻さない。T03未再発も解決扱いにしない。
source/tests/CI/依存lock/実DB/registry/配信は変更せず、全suiteやDocker/PG受入を繰り返していない。
今回の確認は読取・専用trace・上流計算検証と証拠/文書照合であり、実Capitalの合格証拠ではない。

証拠生成の準備ではcwdに`261021`を重ねたためfile作成/起動が失敗した（exit2）。端末出力を別logへ保存し、runner開始前の失敗なので開始時刻やMONOTONIC経過を補っていない。作成先を修正して検証へ進んだ。探索時のweb cache missも残し、取得に成功した原本だけをソース根拠にした。

<a id="clock-admin-log-review"></a>

## clock-admin-log-review

<a id="clock-admin-log-review--管理者採取ログの照合と入力待ちの整理"></a>

### 管理者採取ログの照合と入力待ちの整理

基点`f70cb37380ba4846451cf662807f208033b7a35c`。ユーザー添付と、
`data/reports/clock-owner-admin-053cb32639e54e04aea39f299e82711d/debug-shell.log`を照合した。
[証拠JSON](reviews/20261101-admin-owner-checks.json)と[採取手順](clock-operations.md#clock-owner-admin-read)を参照する。

<a id="clock-admin-log-review--実行結果"></a>

#### 実行結果

PowerShellの管理者判定はTrue。`.ps1`直接実行はexecution policyで拒否された。
これはreader起動前の失敗であり、管理者権限だけではfile実行のpolicyは変わらない。
その後の対話入力では実際にWSL初期PID namespaceでreaderが動き、**chronyd1件・timesyncd1件・errors0と実行された完了行**を記録していた。
ターミナルのechoには同じ文字列のshellソースも含まれるので、prompt付きechoと行頭からの実出力を分けて解析した。

一方、collection.json/hashes.jsonは存在しない。readerの完了と、外側PowerShell/native clientを含む採取全体の完了は区別する。
native終了値や未採取のQPC値を後から補っていない。元fileを変更せず、別のreview directoryへコピー・SHA保存した。
`>>`は複数行入力でも出る表示で、添付だけで括弧や引用符の閉じ忘れとは判断できない。
実出力があるため「ifのブロックが全く実行されなかった」という仮説は否定できる。
対話consoleをpipelineへつなぐ旧手順は、外側処理の終了まで保証できておらず、今回の案内が十分でなかった。
今後はdebug shellで読取を実行し、fileを直接保存する方式を案内する。

<a id="clock-admin-log-review--実体と管理層の照合"></a>

#### 実体と管理層の照合

| 項目 | 確認結果 |
|---|---|
| chronyd | 初期namespaceのPID/Tgid213、開始tick319、argv `/sbin/chronyd`、exe `/usr/sbin/chronyd` |
| 実exe SHA-256 | `bf39d010dfc6177cd864d96ddd0cb32eb796704e8c68fe8bd65088d20d2e64b8`。前回system distroの可視4.1 binaryと一致 |
| 採取時の親 | PID1、comm mini_init、exe `/init`、SHA `f84f9ae9116b972fce6723f920fb8fb1dedff2aeb0baa40ed7c5d2b9f4b73311` |
| 名前空間 | pid4026531836、mnt4026531841。Ubuntu timesyncdのpid/mntとは異なる。time4026531834は両方で共通 |
| 有効設定の候補 | process root内 `/etc/chrony.conf`、SHA `9315d50044034745ade10c260359b463eb77a61af9330f818f68ed16253f8528`。argvに別config指定なし |
| 設定の生成元との一致 | WSL2.6.3 StartTimeSyncAgentの文字列から復元した全file bytesのSHAと一致。`refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0`等6行 |
| 保存drift | −23906.640630ppm、skew603.665835ppm。前回trackingの−23906.641ppmと表示丸めの範囲で一致 |
| timesyncd | global496/親233、exe/hash・開始tick1505・namespace/cgroupが前回のlocal214対応と一致 |

両processは読取前後の開始tickが一致した。同じboot_idを前回traceと照合した。
chronydの開始は前回のtick書込traceより前で、初期PID namespaceの213は以前のsetter TID213と対応する。
実exeのhashを確認できたのは今回の採取時点で、過去全区間にexecがなかったことまで証明しない。

**稼働実体と管理層はWSL初期namespace側まで確認できた。** Ubuntuへ別途導入したchronydやDocker内daemonと決めつける必要はない。
WSL initの生成設定と実fileの全bytes一致も確認したが、daemonのreparent前の起動瞬間はtraceしていない。
採取時の親mini_initだけから過去の直接launcherを断定せず、WSL組込agentの設計との一致と分けて記録する。

<a id="clock-admin-log-review--原因と残る範囲"></a>

#### 原因と残る範囲

今回の実体hash一致により、[前回の上流4.1計算](clock-history.md#chrony-control-analysis)との対応は強まった。
ただし配布binaryの全patch/build identityまで検証したわけではない。
生成configにはmaxslewrate/maxdrift/includeの指定がなく、上流既定値を使う構成と整合する。
**tick11000の数値が補正限界と一致しても、制限前のbase/corr/offsetを読んでおらず、持続する飽和の内部原因は未確定。**

残る限定読取は、同じdaemonのPHC fdとUDP inode所有、必要ならbuild/package情報と既存logの制御量。
今回のreaderはfd所有まで採っていないため、既存PHC0応答をPID認証と読み替えない。
Windowsの絶対UTC精度、T03歴史的停止、過去T13との同一原因、修復後の時計資格も未了。
修復案・影響・切戻しは[前回の表](clock-history.md#chrony-control-analysis)を維持する。
OS設定変更や同期service停止、future条件の緩和は行っていない。

この管理者ログの基本inventoryを再採取する必要はない。追加読取が必要になった時だけ、新手順で別directoryへ保存する。
独立したT07/T17の隔離検証は先行する。合成成功を実Capital採否の証拠へ流用しない。

<a id="clock-followup"></a>

## clock-followup

<a id="clock-followup--時計調査の追加読取と残る採取（2026-11-03）"></a>

### 時計調査の追加読取と残る採取（2026-11-03）

298c6c8以降の[配布build/debugと内部数値の照合](clock-implementation.md#chrony-runtime-control)で、完全debugを取得し非飽和時の計算を確認した。
下記の「内部変数未取得」は当時の状態。新しい観測でもtick11000時の値・管理者PHC/socket採取・修復資格は未了。

基点`dcfdd9c1d1bcc5b17194cc7dbd7c333b9ec19416`。
[前回の管理者ログ](clock-history.md#clock-admin-log-review)、[補正量の説明](clock-history.md#chrony-control-analysis)、
[今回の試行・hash索引](reviews/20261103-clock-followup-checks.json)を参照する。
**時計は未修復・未資格確認。** 読取と使い捨て試験だけを行い、実DB・registry・現在配信は触っていない。

<a id="clock-followup--新しく確認できた事実"></a>

#### 新しく確認できた事実

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

<a id="clock-followup--setterと自然拒否を同時採取した追加attempt"></a>

##### setterと自然拒否を同時採取した追加attempt

別の`joint-v1`では、専用40秒traceの開始確認後にFakeRegistry probeを起動した。
71回目のreview生成から検証拒否までのMONOTONIC区間内に、時計sampleの負方向差が1区間だけ存在し、
global496の`clock_adjtime → timekeeping_inject_offset`と成功戻り値が対応した。
reviewは検証UTCより1.235856秒先となり、承認tag不変・blockedを確認した。
**今回の自然再現にはtimesyncdのstepが対応した。** 過去T05/T13の全失敗や、
chronydが11000を選ぶ内部理由まで証明したとはしない。
原本の生成/検証時計・MONOTONIC・setter要求・trace/cleanupと機械的な区間照合を`joint-analysis.json`へ保存した。

<a id="clock-followup--tick11000の内部理由を絞れた範囲"></a>

#### tick=11000の内部理由を絞れた範囲

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

<a id="clock-followup--追加readerと確認範囲"></a>

#### 追加readerと確認範囲

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
管理者での追加採取は[手順書](clock-operations.md#clock-owner-admin-read--chrony-resources-admin)を参照。

<a id="clock-followup--修復案・影響・切戻し"></a>

#### 修復案・影響・切戻し

今回修復は行っていない。[既存の修復候補表](clock-history.md#chrony-control-analysis--clock-repair-options)を維持する。
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

<a id="clock-host-cause-review"></a>

## clock-host-cause-review

<a id="clock-host-cause-review--windows-peer・最終エラー・service-identityの照合"></a>

### Windows peer・最終エラー・service identityの照合

2026-11-09、基点`9c32298`。提出された`clock-host-cause-a52e422e-45db-44d1-b1a8-1c3579e47e0b`をoffline reviewした。
[今回索引](reviews/20261109-host-cause-checks.json)、[前回runtime review](clock-operations.md#clock-host-runtime-review)、
[最新計画](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**採取したWindows全体の同期状態は未同期・時刻データなし。peerは保留中で、現在の有効サンプルは0だった。**
peer/status/identityの読取不足は今回解消したが、「なぜこの状態になったか」の根本原因や実時計資格は未確定である。

<a id="clock-host-cause-review--採取の受入範囲"></a>

#### 採取の受入範囲

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

<a id="clock-host-cause-review--statusとpeerを分けて読む"></a>

#### statusとpeerを分けて読む

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

<a id="clock-host-cause-review--歴史的イベントとの関係"></a>

#### 歴史的イベントとの関係

前回保存済みの12イベント原本は5,170bytes/SHAを検算して読んだ。hostへの同じイベント照会は再実行していない。
Record26266/ID37は`2026-11-09T00:25:22.2254368Z`にvalid time dataの受信を報告しており、
今回processのStartUTCよりhost wall値で0.1858371秒後に当たる。近接は分かるが、受信payload・当時PID/開始tickはeventに無い。
新PID34104がそのeventを発行した、当時のsampleが時計全体へ採用された、以後継続同期したとは確定しない。
古いDNS失敗134、後の受信37/同期開始35の両方を保持し、現在のvalid counter0と矛盾するからと履歴を捨てない。
今回の「新イベント0件」は、前回最大RecordIdより後という限定範囲の結果。log巻戻り/保存範囲/記録されない状態変化は別である。

<a id="clock-host-cause-review--原因の候補と未確定事項"></a>

#### 原因の候補と未確定事項

| 候補 | 新資料から言えること | 足りない根拠 |
|---|---|---|
| DNS/UDPの恒久遮断 | 現在の全体はno time dataだが、peer解決失敗counter0、過去のvalid受信もある | 当該応答/経路・packetの採否・同時エラー。pingやTCP123でUDP123成功とはしない |
| 長いspecial poll/保留 | 前回runtime SpecialPollInterval16384、今回残り10301.3131813秒 | 保留へ入った理由、poll更新/受信/拒否の履歴。待機だけで修復したとしない |
| SPIKEとspecial pollの既知問題 | 0x9を使い、既知問題のpoll条件を調べる価値がある | 実offsetとSPIKE遷移が未採取。今回はUnsetであり、KBの条件一致は未確定 |
| guest/backendとの競合 | host→PHC、guest補正、静的backend時刻書込経路の既存根拠はある | 今回host queryと同時のsetter/内部値/応答なし。過去tick11000/T03を解決しない |

[条件付きの保守比較案](clock-operations.md#clock-host-maintenance-plan)へ、確認済み設定に対する候補、影響、切戻し、停止条件を具体化した。
これはOS変更の許可・修復済みという意味ではない。同じbasic/peer/PHC queryを直後に繰り返す新しい依頼はしない。
新しい状態変化・自然event・source資料、または別途許可された保守比較が次の開始条件である。

<a id="clock-host-cause-review--後続タスクと検証"></a>

#### 後続タスクと検証

標準の`clock-source-input-*`に新dossierは無く、未提示の別pathの不存在は断定しない。
今回NoAuth/Local CMOS/名目QPCのmetadataだけでは独立UTC/elapsedを採用できず、全資格は`incomplete`。
実source/collector/host deadline/固定負荷、許可後の修復・休止再開・全資格、T10実fit直前再照合→T02/T11/T12/T13bは前提待ち。
T09/T10静的受入は完了のまま。未消費窓を使うfit/採点、OS/時計設定、実DB/registry/配信、future拒否は変更しない。

今回専用のignored offline診断で9原本/8memberと14負例（再hash12件）を照合した。
identity差、QPC/UTC逆転、query失敗、peer/global誤読、counter/認証差、event件数/診断欠落も拒否した。
固定captureの意味照合の検証で、製品unit追加/一般reader/実時計資格の受入ではない。
診断の初回はexit0。準備の旧索引読取でkey名を誤ったKeyErrorは別に保存し、製品の失敗と区別した。
公開索引にhash・実コマンド・終了値・未資格UTC/MONOTONIC・限界を結び、raw/個人情報はignored directoryへ保持する。

<a id="clock-origin-investigation"></a>

## clock-origin-investigation

<a id="clock-origin-investigation--utc逆行の呼出元の調査（2026-11-01）"></a>

### UTC逆行の呼出元の調査（2026-11-01）

基点は`b5dbdfd2df16da8325b9a422fd2c2729f5991689`。
[後続の管理元照合](clock-history.md#clock-process-ownership)でUbuntu timesyncdのlocal PIDとkernel PIDの対応・実行file・管理unit・同期元を確認した。以下は当時の調査記録を保持する。chronydの稼働exe/親/生成config照合と環境修復は後続でも未完了。
[前回の診断](clock-history.md#interruption-clock-diagnostics)から、時計変更の呼出元を追加調査した。
[証拠JSON](reviews/20261101-clock-origin-checks.json)に全試行、失敗、原本のbytes/SHA-256を記録する。
現在環境の機序を絞った節目であり、過去T03/T13の根本原因や環境修復の完了ではない。

<a id="clock-origin-investigation--結論と確度"></a>

#### 結論と確度

| 対象 | 今回の直接観測 | 残る限界 |
|---|---|---|
| UTCを戻した呼出し | `systemd-timesyn`（kernel traceのglobal TID 496）が`clock_adjtime`→`do_adjtimex`→`timekeeping_inject_offset`を実行。引数付き採取で−4.473373174秒、−3.128148078秒の要求とsyscall成功を確認し、実時計の逆行区間へ照合 | process名はcommであり認証されたidentityではない。外側PID名前空間の実行file/親/管理unitへの照合は未完了。前回の2件の自然future拒否や歴史的SQLite失敗の瞬間にはsetter traceがなく、同一呼出元とは断定しない |
| 時計の進行率 | Windows QPCに対するWSL MONOTONICの比は、往復遅延を含む範囲でも1.099918〜1.100057。RAWは0.999926〜1.000052。`adjtimex(modes=0)`でtick=11000を観測 | 単調増加でも正しい秒数とは限らない。現在の観測で過去の測定値を一律に割り戻さない |
| tickを書いた呼出し | `chronyd`（global TID 213）が`ADJ_TICK | ADJ_FREQUENCY`（modes=16386）を16回要求し全て成功。うち14回がtick=11000/freq=0、他2回は10441/10614で、その直後に11000へ戻す要求がある | なぜ11000を繰り返し指定するか、どの実行file/設定/起動元かは未特定。Ubuntuのservice一覧にchronydがないことを、共有kernel全体で不在の証拠にしない |
| `initd`とHyper-V | 2区間で計5回の`initd`→`settimeofday`→`do_settimeofday64`成功を観測。今回の5回は**前進**。`hv_set_host_time`は同じ2区間で0回 | Runningのdocker-desktopには`/initd`があり同じtime namespaceだったが、global TIDとそのprocessの対応までは未確定。Docker由来という推定と、今回の負方向の直接原因を分ける。Hyper-V経路が将来/過去も動かないとは言わない |
| Windows | W32Time/vmictimesyncはStopped/Manual、NTP client設定はEnabled=1。Windows UTC/QPCは1,400組の採取で逆行0、現在区間のKernel-General ID 1は検索一致なし | W32Timeが止まっているため`w32tm /query`は0x80070426で失敗。現在のWindows UTCが外部時刻に正確とは確認していない。イベント不在や有限sampleの成功で全ての変更を排除しない |
| T03 | journal 9件成功。通常SIGTERMは受信→handler例外→HTTP終了→台帳書込→exit 130の全phaseを保存。受信からchild_exit記録までは15.918451 MONOTONIC ms | 自然な10秒終了待ち失敗は再発せず、歴史的停止位置は未特定。このms値は正しい外部秒として校正済みではない。人工停止caseを自然再発に数えない |

<a id="clock-origin-investigation--同時採取とカーネル照合"></a>

#### 同時採取とカーネル照合

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

<a id="clock-origin-investigation--仕様との照合"></a>

#### 仕様との照合

該当releaseの[WSL kernel ntp.c](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/kernel/time/ntp.c)はtick_usecをUSER_TICK_USECで初期化し、ADJ_TICKで更新する。
[jiffies.h](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/include/linux/jiffies.h)ではUSER_TICK_USECはUSER_HZを基準にする。今回`getconf CLK_TCK=100`なので通常の基準値は10000µsで、CONFIG_HZ=250から4000と計算するものではない。
今回の11000と進行比約1.1はこの挙動に整合する。`freq=0`やNTP同期flag=yesだけでは正常な速度を保証しない。
[adjtimex manual](https://man7.org/linux/man-pages/man2/adjtimex.2.html)に従い、状態採取用Cはmodes=0だけを使用した。

[systemd v255のmanager_adjust_clock](https://github.com/systemd/systemd/blob/v255/src/timesync/timesyncd-manager.c)は大きいoffsetにADJ_SETOFFSETを使う。外部の時刻変更を検知すると再問合せする経路もあるため、NTPの最小poll設定32秒でも、他processの変更直後に補正が発生し得る。実環境は255.4-1ubuntu8.17で、upstream tagをUbuntu packageの全patchと同一と保証したわけではない。
[該当WSL kernelのhv_util.c](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/drivers/hv/hv_util.c)には別のhost同期経路がある。今回そのsetterの呼出しは0だったため、implicit=Yだけで負方向の原因と判断しなかった。

[UbuntuのWSL時刻同期説明](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)もhost同期とguest NTPの競合可能性を説明する。ただし、この一般論だけで当該processの所有者や処方を確定しない。
[Docker上の同種報告](https://github.com/docker/desktop-feedback/issues/761)は30秒のinitd setterという仮説を立てる参考にしたが、他人のtraceは今回の実証へ転用していない。今回のinitdは観測した5回とも前進だった。

<a id="clock-origin-investigation--取得失敗と境界"></a>

#### 取得失敗と境界

- 通常sandboxではWindows interopのvsockが拒否され、DBusもOperation not permittedだった。許可付きの同じ読取を別attemptへ保存した。最初のwrapperのexit 0を、内側の全query成功と読み替えない。
- `sudo -n`はpassword required。WSLのUbuntu root経由では専用traceを採取できたが、NS_GET_PARENTで外側PID名前空間へ遡れず、global process照合は停止した。予定した専用proc mountは作成されていない。
- docker-desktopの初回process読取はWSL.exeを通したshell引数の展開で失敗した。shell全体のexitは0でもstderrが失敗を示すため不採用。stdinで固定scriptを渡す別試行で`/initd`を確認した。直前にRunning一覧を検査し、停止中なら起動しない。
- `wsl --system`の初回はwslg userでexe/nsを読めなかった。root指定の次回でも外側global PIDの照合はできなかった。
- `wsl --debug-shell`はWindows管理者としての起動を要求し、実行されなかった。最初のUTF-16出力の誤decodeも残し、再取得で正しい拒否文を保存した。Windowsの管理者昇格は行っていない。
- 引数traceの最初の準備はcwdに対し261021を重ねて指定し失敗し、欠落scriptの実行はexit 2だった。修正後の`Path.open('a')`もtracefsのseekでEINVAL。切詰める`w`は使わず、seekしない`os.open(O_WRONLY|O_APPEND)`へ修正した。失敗traceも専用制御を回収し、前後hashを確認した。

<a id="clock-origin-investigation--未実施の設定案と影響"></a>

#### 未実施の設定案と影響

以下は保守時間に実施可否を判断する**提案**で、今回実行していない。単にtimesyncdを止めるだけではtickの進行率問題を解消できず、tickを一度10000へ戻すだけでもchronydが再設定する恐れがある。

1. **所有者を先に特定する（P1/D3/Sol）。** Windows管理者で起動した診断shellから、global PID 213/496とinitd TIDの実行file、親、namespace、起動設定、chronydの引数/clock sourceを限定読取する。今のPIDの再確認が必要。名前だけでUbuntu serviceやDockerを停止しない。公開前にargvやログの秘密情報を確認する。
2. **時計の責任を一本化する案を作る。** host基準ならWindows「時刻と言語→日付と時刻」で自動設定/同期元を確認し、必要なら管理者による`Start-Service W32Time`と`w32tm /resync`を保守時に検討する。[Microsoftのw32tm仕様](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)を参照し、恒久起動方針は端末policyに合わせる。guest側は所有者確認後に、そのchronydを非調整modeへ変更するか正しいtick制御へ直す。[chronyd公式manualの`-x`](https://chrony-project.org/doc/4.7/chronyd.html)は時計調整を無効にするが、所有package/実versionの手順を再確認してから候補とする。Ubuntu timesyncdの停止/無効化は、代替同期の継続動作を確認した後に限って検討する。どのコマンドも今回実行していない。
3. **tickを復元する必要がある場合も所有者修正後。** USER_HZ=100を再確認してADJ_TICK=10000の限定変更案を作る。`adjtimex`コマンドはこの環境にないので、未検証のインストール/変更コマンドを即実行手順にしない。設定者が生きていれば再書込される。freq/status等を一括初期化しない。
4. **影響と切戻しを先に記録する。** Windows同期はhost/全WSLのUTCを動かし得る。guestのtick/調整者変更も共有kernelを使う他distro、Docker、timeout、証明書/認証/証拠日時へ影響する。Docker終了、WSL shutdown、service停止をA/B試験のために勝手に行わない。承認済み保守時間、設定backup、元の起動方式/引数、切戻し、UTCが戻った際の中断条件を具体化する。
5. **変更後に測り直す。** 同じbracket付き時計採取、setter trace、NTP状態、厳密future境界、T03 phase/stackを確認し、長時間/再開後の観測を追加する。有限区間の逆行0だけで永続解決としない。現在のMONOTONICを正しい秒数とみなす実候補の性能・timeout資格は保留し、時計資格確認後に新規attemptで測る。過去の合格/精度指標を現在の時計比で修正しない。MAE/RMSEの計算とHTTP経過時間の問題を区別する。

T13のfuture拒否はそのまま維持する。時刻clamp、待機、自動retryによるeligible化は追加しない。
T03の未再発も解決扱いにしない。HTTP部分書込/応答喪失、T07/T17、実Capitalの採否/監視/配信は別taskとして残る。

<a id="clock-process-ownership"></a>

## clock-process-ownership

<a id="clock-process-ownership--時計調整プロセスの管理元照合（2026-11-01）"></a>

### 時計調整プロセスの管理元照合（2026-11-01）

最新の[管理者ログ照合](clock-history.md#clock-admin-log-review)でchronyd実exe・初期namespace・親と生成configの一致を確認した。以下は管理者採取前の記録として保持する。

基点は`0477e8ccb0d9ebb1b7eb6a51befc8435f279ec57`。
[前回の呼出元調査](clock-history.md#clock-origin-investigation)と[今回の証拠JSON](reviews/20261101-clock-owner-checks.json)を参照する。
**Ubuntu timesyncdの管理元は特定した。chronydの稼働実体への最終照合と環境修復は未完了。**
続く基点2181bfeからの[補正量・tick計算の調査](clock-history.md#chrony-control-analysis)では、現在のsetterとtrackingを重ね、上流計算で補正限界との一致を確認した。管理者ログは未採取で、稼働実体と内部理由の確定は引き続き未了。
OS時計・同期設定・実DB・registry・配信は変更していない。一時的な診断probeだけを追加して回収した。

<a id="clock-process-ownership--確認できたこと"></a>

#### 確認できたこと

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

<a id="clock-process-ownership--pidを名前に頼らず対応付けた方法"></a>

#### PIDを名前に頼らず対応付けた方法

Linuxの[該当kernelのsched_getscheduler実装](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/kernel/sched/core.c)は、呼出側のPIDで対象taskを引き、`security_task_getscheduler(task)`へ渡す。
この**読取専用の照会**を`os.sched_getscheduler(214)`で実行し、UUID専用のkprobeで引数taskのpid/tgid/親pid/開始時刻を採取した。
自processへの照会を対照に置き、begin/end markerの間に各1件だけ一致することを確認した。signal送信や対象processへのコード注入はしていない。

offsetは当該bootの`/sys/kernel/btf/vmlinux`を[公式BTF定義](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/include/uapi/linux/btf.h)に沿って解析した。taskのpid=2416、tgid=2420、real_parent=2432、start_time=2720、comm=2920 bytesだった。別kernelへ固定値を流用する手順ではない。
35秒の成功区間でlocal 214→global 496、親233、task_start=15059948976nsを確認。前後のexe/sha/argv/cgroup/namespaceと`/proc/PID/stat`の開始tickは一致した。
その同じ区間でPID 496が−3.197128772秒のADJ_SETOFFSETを要求して成功し、実時計の負方向変化にも対応した。

初回45秒はcontext-switch回数を含むstatus全体を同一性判定へ使って失敗した。差は`voluntary_ctxt_switches`だけであり、process置換ではなかったと原本を比較した。失敗を成功へ上書きせず、35秒の別attemptで安定fieldだけの比較を確認した。
両区間とも専用instance/probeを削除し、global trace制御/kprobe定義の前後hash一致と全CPUのlost events 0を確認した。modesが選択していないtimex fieldは解釈せず、公開summaryへ転記しない。

<a id="clock-process-ownership--chronydの管理元について分かった範囲"></a>

#### chronydの管理元について分かった範囲

[WSL 2.6.3のinitソース](https://github.com/microsoft/WSL/blob/2.6.3/src/linux/init/main.cpp)は、hostとの同期用に`StartTimeSyncAgent`を呼び、PHC用のchrony設定を生成して`/sbin/chronyd`を起動する。装置は`/dev/ptp0`を指定する。これは**該当版の設計上の管理元**を示す一次資料であり、稼働PID 213のexe/親を読んだ代わりにはしない。
今回のPHC0応答と、通常distroからPIDが見えない状況は、このWSL組込agentの構成と整合する。DockerやUbuntuに新しいchronydを導入する必要があると判断していない。

一方、`wsl --system`で見えた`/etc/chrony.conf`は`server time.windows.com`で、読取応答はPHC0だった。さらにそのmountから`/var/run/chrony/chronyd.sock`は見えず、明示socket指定は失敗した。
したがって、そのpackage既定fileを**稼働daemonの有効設定と見なして編集しない**。system distroのchronyd/chronycはversion 4.1だが、可視binaryのhashと稼働PID 213のbinary hashの一致は未確認。

[chrony 4.1のLinux実装](https://github.com/mlichvar/chrony/blob/4.1/sys_linux.c)は周波数補正をtickとfreqへ分解し、tickのbias幅を通常値の10%とする。この実装では、11000は設定fileに同じ数字が書かれている必要のない計算結果である。
複数調整者の競合で補正量が大きくなっている仮説はあるが、**内部の補正要求とPID 213のbinary/configを同時に照合しておらず、11000になる内部理由の確定ではない**。maxslewrate等を推測だけで変更しない。

<a id="clock-process-ownership--原因・未確定事項・修復候補"></a>

#### 原因・未確定事項・修復候補

**直接確認した原因:** 現在の負方向stepはUbuntuのsystemd-timesyncdによるNTP補正。別processのchronydによるtick=11000の反復書込も継続している。共有time namespace上の複数調整経路が存在する。
**未確定:** chronyd実体/親/生成config/同期fd、11000を選ぶ内部制御の理由、Windowsの外部UTC精度、過去T03/T13失敗の正確な呼出元。

| 未実施の案 | 実施前の条件・確認対象 | 影響と切戻し |
|---|---|---|
| 管理者で外側processを読取 | [限定読取手順](clock-operations.md#clock-owner-admin-read)で現在PID、exe/hash、親、namespace、process root内の生成configを採取。PID 213という固定値だけを再使用しない | 証拠fileだけ作成。時計変更・service操作なし。全processの環境変数や認証keyは採取しない |
| Ubuntu timesyncdを一時停止する保守試験 | 管理元/host精度を確認し、別途OS変更が許可された保守時間で検討する。事前にactive/enabled、unit/drop-in/hashを保存。今回`systemctl stop/disable`は実行していない | 全WSLの共有時計とtimeoutに影響。chronydのtick/offsetが収束するかを新規attemptで観測し、停止だけで成功としない。元がactiveならstart、enabled状態も保存時に戻す。恒久disableは一時試験とは別判断 |
| Windows同期責任の整理 | W32Timeが停止している理由と端末policyを確認し、外部UTCとの差・選択sourceを読取。サービス開始やresyncは別途保守提案 | host/全WSLのUTCが動き、認証/証跡に影響。元のservice起動方式を保存して切戻す。時計履歴そのものは巻き戻して消せない |
| chronyd側の制御変更 | 実体/config/起動者を特定してから判断。生成configを直接書いてもWSL起動時に置換される可能性がある。Ubuntuから見える別fileは変更しない | 共有kernel全体に影響。元の生成元・argv・configを保存し、その管理層で復元する。単発tick変更や周波数初期化は解決条件にならない |

保守後はWindows QPCで囲む採取、RAW/MONOTONICの進行率、setter、厳密future拒否、長時間/再開後の観測を新しいattemptで確認する。今回の時計比による過去p95の補正、future拒否の緩和、clamp、自動retryは導入しない。

<a id="clock-process-ownership--検証・失敗の保全"></a>

#### 検証・失敗の保全

全原本はignored `data/reports/20261101-clock-owner/`。runnerはargv、UTC/monotonic、終了値、deadline、stdout/stderrとhashを保存する。UTCが戻るため順序をUTC表示だけで決めない。
`awk`がないsystem distroでの初回親読取、status全体の同一性比較、shellのglob無効化による空列挙、analysis準備時のcwd指定ミス、Windowsの実行policy/管理者拒否も保持した。外側wrapperが0でも内側query失敗は成功にしない。
管理者用shell readerは修正版をUbuntu rootで実行しtimesyncd 1件/chronyd 0件を検出、終了42（不完全）。非rootは77。PowerShellの実parserは成功したが、`.ps1`ファイル実行はpolicy拒否で、管理者経路のend-to-endは未検証。

T03 journal 9件/11.120 test秒、decision 9件/1.711 test秒が成功。通常SIGTERMの全phaseとexit130を保存し、signal→child_exit記録は15,417,693 MONOTONIC nsだった。外部秒として校正済みではない。自然な10秒停止は再発せず、歴史的原因は未解決。
production source/test/workflow/lock未変更のため全suite・Docker/PG・実配信は再実行していない。今回の合成回帰を実Capitalの採否/監視証拠へ流用しない。前回135原本のhashも再照合した。

<a id="clock-resources-admin-review"></a>

## clock-resources-admin-review

<a id="clock-resources-admin-review--管理者phc・udp原本の照合とhost同期の残条件"></a>

### 管理者PHC・UDP原本の照合とhost同期の残条件

2026-11-09、基点`464df19`。指定されたowner `b9641680-…` とresources `ed1b37fc-…` の2 directoryをofflineでreviewした。
[証拠索引](reviews/20261109-admin-resources-checks.json)、[原本取得手順](clock-operations.md#clock-owner-admin-read)、[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を参照。
**採取区間のchronyd実体とPHC/UDP所有を確認した。時計資格・tick11000の内部原因・実fit開始条件は未達。**

基点`b0589ee`後の追補：[Windows管理者runtime review](clock-operations.md#clock-host-runtime-review)で、下記で不足していたsource/configurationを取得・照合済み。
以下の権限拒否/管理者query未実施は前回の履歴。現在の残件はpeer/last-error等による原因切り分け、独立源/同期責任/全資格である。

<a id="clock-resources-admin-review--採取の完了と内容の結合"></a>

#### 採取の完了と内容の結合

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

<a id="clock-resources-admin-review--照合できた実体"></a>

#### 照合できた実体

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

<a id="clock-resources-admin-review--過去のsetterとの対応と限界"></a>

#### 過去のsetterとの対応と限界

旧admin原本のboot・chronyd PID/開始tick/exe SHA/namespace・設定SHAは今回と一致した。
一方、11月3日の内部値probeの実行fileはdev42/inode4227、今回はdev27/inode4227であり、**devを含むfile identityは不一致**。
同じSHA/size/buildのbytesと対応するが、同じinode番号だけで同じ実行file実体や無execの連続性を確定しない。
dev差の発生理由は今回の原本からは未確定。新しいattach/probe/traceは行っていない。

旧timesyncdはPID496/開始tick1505、exeと親systemdのSHAも今回とは異なる。
今回のPID973を、旧traceのsetter496へ読み替えない。変更・再起動・更新の経緯はこの2原本に無く、推定で確定しない。
drift保存値も旧−23906.640630から今回+6820.697933へ変化しているが、現在のbase/corr/offsetやkernelのtick/freqの同時snapshotではない。
tick11000時内部値/kernel書込とT03 phase/stackは自然再発の新資料がある時だけ照合する。

<a id="clock-resources-admin-review--host側で追加確認した範囲"></a>

#### host側で追加確認した範囲

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
| Windows/backend→Docker initd | [過去のruntime setter対応](clock-history.md#initd-owner-correlation)と[静的取得CALL](clock-history.md#backend-time-path-review)を継承 | 今回のbackend応答値/動的stack・現在のinitd identityは未採取。PHCと別の独立源として数えない |
| Ubuntu timesyncd | 今回のPID973・service cgroup・親・共有time namespace | 新実体のeffective同期元/時刻書込、旧496から変化した経緯。過去setter証拠を新PIDへ流用しない |

[Microsoft公式のw32tm説明](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings#command-line-parameters-for-w32time)では、local実行の管理者権限と、`/query /source /configuration /status`の照会機能が説明されている。
今回は設定変更・resync・stripchart・service起動/停止を行っていない。管理者の次の限定読取は[host採取手順](clock-operations.md#clock-owner-admin-read--host-w32time-admin)。

<a id="clock-resources-admin-review--検証結果と次の判断"></a>

#### 検証結果と次の判断

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

<a id="initd-owner-correlation"></a>

## initd-owner-correlation

<a id="initd-owner-correlation--initdの時計書込とdocker-desktop管理層の照合"></a>

### initdの時計書込とDocker Desktop管理層の照合

基点891a420以降の[backend取得interface・時計書込CALLの静的照合](clock-history.md#backend-time-path-review)を追加した。
保存済みELFを対象とし、新しいsetter traceやbackend応答の証拠ではない。以下の動的観測は前回区間のまま保持する。

2026-11-04、基点`d8ec16412cc833e4b9eb9582921a4309265d1166`。
[前回の内部制御調査](clock-implementation.md#chrony-runtime-control)からの追加診断。
原本・全試行・失敗・hashは[今回の索引](reviews/20261104-clock-readiness-checks.json)を参照する。

**今回の負方向settimeofdayは、Docker Desktopの`/initd services`のスレッドに対応した。**
現在の実行fileと管理層の照合が進んだ。chronydのtick11000の内部原因、時計修復、資格確認は未完了。
以前の全setterやT03/T13の失敗へ、この対応を遡及適用しない。

<a id="initd-owner-correlation--採取条件と照合方法"></a>

#### 採取条件と照合方法

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

<a id="initd-owner-correlation--今回対応した実体"></a>

#### 今回対応した実体

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

<a id="initd-owner-correlation--実際の時計変更"></a>

#### 実際の時計変更

MONOTONIC154322.417563秒で上記スレッドが`do_settimeofday64`へ入り、要求UTC nsは1793795939033317000。
同じTIDの`sys_settimeofday`は0を返した。
対応する時刻sampleの前後で、MONOTONICは10,369,756ns進み、REALTIMEは733,087,722ns戻った。
setter event・要求値・成功戻り値が、この逆行sample区間へ対応した。

約0.271秒後にはtimesyncd496の別要求もあり、`ADJ_SETOFFSET | ADJ_NANO`を選んだtime値は
−1秒+362242222ns、すなわち−0.637757778秒だった。両processの経路を一つの要求へ混ぜない。
このtraceではtick11000の要求は0件。chronyd内部値の再採取や、11000が出るまでの延長はしていない。

全CPUのoverrun/commit overrun/dropped eventsは0。専用instance/probeを削除し、global trace制御と
kprobe/uprobe定義の前後hash一致を確認した。

<a id="initd-owner-correlation--binaryから分かった範囲"></a>

#### binaryから分かった範囲

実exeと同じSHA/sizeのコピーを静的解析した。実行・installはしていない。
`.go.buildinfo`はGo公式の[build情報読取実装](https://go.dev/src/debug/buildinfo/buildinfo.go)の形式に従って読み、
Go1.27.3、main path `github.com/docker/pinata/linuxkit/pkg/desktop/rootfs/cmd/initd`を得た。
module版は`(devel)`で、この情報から正確なGit revisionや署名済みprovenanceは取得できない。
symbol文字列には`desktop.runBackendTimeSync`・そのfunc1・`syscall.Settimeofday`が存在した。
backend時刻同期という仮説と整合するが、**当該goroutineの動的call stackやbackendの時刻源を観測した証拠ではない。**

<a id="initd-owner-correlation--検証と残件"></a>

#### 検証と残件

照合parserは今回の2×35照会・exe・開始時刻・namespace・setter成功・時刻sample・cleanupを検査した。
別の合成コピー7ケースで、成功戻り値欠落、未照合TID、照会identity変更、exe SHA不一致、cleanup未完了、
buffer overrun、namespace変更をいずれも拒否した。原本は変更していない。
これは診断parserの検証で、製品unit suite・CIの新しい受入や、実Capital運用性能の証拠ではない。

| 区分 | 次の作業・境界 |
|---|---|
| 確認済み | 今回のinitd setterと実exe・thread group・Docker Desktop bootstrap管理層の対応 |
| 未確定 | backendから受けた時計値/プロトコルとhost同期責任、chronydの飽和時内部値、PHC/UDPの管理者採取、T03歴史的停止 |
| 修復案 | [既存の修復表](clock-history.md#chrony-control-analysis--clock-repair-options)へ今回の管理層を反映。initdはservices全体の管理processなので、時計だけを止めるつもりでkill/停止しない。時刻同期専用のサポートされた設定・保守比較案を先に確認する |
| 影響 | Docker Desktop停止/再起動は既存containerや実配信も止め得る。共有kernelの時計調整は他distro・timeout・証跡へも影響する |
| 切戻し | 変更案を採用する前に管理層の元設定と復元操作を固定し、別途OS変更許可を得る。時計履歴自体は切戻しで消せず、保守後に新しい資格試験が必要 |

future拒否の緩和、時刻clamp、自動retry、OS/時計設定変更、実DB/registry/現在配信の操作はしていない。
新しい管理者ログや状態変化なしに同じ採取を繰り返す必要はない。

<a id="interruption-clock-diagnostics"></a>

## interruption-clock-diagnostics

<a id="interruption-clock-diagnostics--t03t13の中断・時計診断"></a>

### T03/T13の中断・時計診断

**追補:** 基点`b5dbdfd`からの[時計変更元の調査](clock-history.md#clock-origin-investigation)で、現在環境の負方向補正を`systemd-timesyn`のoffset注入へ照合し、`chronyd`によるtick=11000の成功書込も観測した。単調時計もWindows QPCに対して約10%速い区間があった。以下は先行調査時点の記録として保持し、変更元の現在の確度・残件は追補を参照する。OS設定は未変更で、根本原因の全解明・環境修復の完了ではない。

2026-11-01、基点`cfd7ef7`から、T05で残った2件の一過性失敗を調べた。
[検証JSON](reviews/20261101-t03-t13-diagnostics.json)にコマンド・終了値・hashを保存する。

<a id="interruption-clock-diagnostics--分かったことと未確定のこと"></a>

#### 分かったことと未確定のこと

| 対象 | 観測 | 判断 |
|---|---|---|
| T03・SIGTERM後10秒の終了待ち | 基点、診断付き試験、独立3反復、全suiteで自然再発なし。人工的に台帳書込みを止めると、signal受信後に停止し、stackと未確定の監査意図を保存できた | 元の停止位置は未特定。今回の人工停止を過去の原因としない |
| T13・直前のレビュー時刻がfuture | 時計をmockしない合成FakeRegistryで2回再現。2回目はレビューUTC `03:07:08.773964`に対し検証UTC `03:07:05.744075`、差3.029889秒で拒否。同じ承認関数のレビュー時刻検証行から発生 | 現在の環境ではUTC逆行による拒否を確認。過去のSQLite失敗時刻は記録がなく、同一原因とは断定しない |
| 実行環境の時計 | 10万sampleでCLOCK_REALTIMEが4回逆行。単調時計・BOOTTIME・MONOTONIC_RAWの逆行は0回 | 経過時間には単調時計を使う。UTCは証拠日時の検証に必要で、単調時計に置換して承認しない |

独立した2万sampleでは逆行0回だった。その後の10万sampleでは約2.956、2.927、3.873、2.921秒の逆行を観測した。
短い期間で0回でも時計の正常性を証明できない。今回の実行環境はWSL2 kernelだが、どのWindows/WSL同期機構が変更を起こしたかは特定していない。
通常のSQLite承認試験の反復は成功しており、FakeRegistryでの再現と区別する。
OSの読取では`NTP=yes`、`NTPSynchronized=yes`、timesyncdはrunning、clocksourceはtscだった。同期flagがyesでも、今回観測した逆行を否定できない。

<a id="interruption-clock-diagnostics--実装した診断"></a>

#### 実装した診断

`utc_time`の未来拒否に、入力UTC、実際の比較UTC、先行microseconds、拒否時monotonic_nsを例外noteとして追加した。
未来拒否条件、UTC必須条件、承認の時刻順序、tag書込み順序は維持する。
人工的に1µs先行させた場合も拒否し、UTC同値は通す。今回再現した承認前のレビュー時刻検証の拒否では、tagを変更せずblockedを維持した。
書込途中/最後のtag書込後の時計変化は今回注入しておらず、T13の部分書込・復旧検証へ残す。
時計を丸める、現在時刻まで待つ、自動retryする、といった処理は追加していない。

T03の使い捨て子processは、signal受信/handler例外、HTTP client終了、台帳書込前後、process終了をUTC/monotonic/PID付きで記録する。
正常SIGTERMはexit 130、SIGKILLは-9を要求し、永続journalの未確定IDと自動再開拒否も確認する。
通常の終了待ちは元の10秒のまま。5秒経過時のwatchdogはstackを書くだけでprocessを終了させない。
人工台帳停止ケースだけは1秒の待機失敗を期待し、0.25秒のstack採取後に試験が子processをkill/reapする。

試験のtraceは`tests/support.py`で作る。
`BIKE_MLOPS_TEST_DIAGNOSTICS_DIR`を指定すると独立UUIDのdirectoryに保存する。
未指定時は一時directoryを使い、失敗時には削除前にtrace/stack/stderrをunit出力へ出す。
診断I/Oは実行タイミングへ影響するため、診断付き試験の成功だけで元の不具合が解消したとは判断しない。

<a id="interruption-clock-diagnostics--再発時の採取"></a>

#### 再発時の採取

プロジェクト`261021`から実行する。新しいpathを使い、stdout/stderrと終了値も保存する。
以下の試験とprobeは合成fixture専用で、稼働DBや実MLflowへの接続は行わない。

```bash
cd ~/work/claude_practice/261021
TRACE_LOG="data/reports/clock-interrupt-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$TRACE_LOG"
export BIKE_MLOPS_TEST_DIAGNOSTICS_DIR="$PWD/$TRACE_LOG/traces"
set -o pipefail
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM --kill-after=10 100 \
  .venv/bin/python -m unittest discover -s tests -p test_operational.py -v \
  2>&1 | tee "$TRACE_LOG/journal.log"
TRACE_EXIT=${PIPESTATUS[0]}
printf '\nEXIT_CODE=%s\n' "$TRACE_EXIT" | tee -a "$TRACE_LOG/journal.log"
```

SQLite側は同じ方法で`-p test_deployment.py -k report_artifact_review -v`を実行する。
時計を独立採取する場合と、実時計を使って合成承認の拒否を調べる場合は、別の新規directoryを指定する。

```bash
.venv/bin/python scripts/clock_tools.py diagnose \
  --output "$TRACE_LOG/clocks" --samples 20000 --interval 0.001
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python tests/probe_decision_clock.py \
  --output "$TRACE_LOG/natural-decision" --samples 160 --interval 0.2
```

probeの`observed_natural_future_rejection`は「時計をmockせず未来拒否を観測した」という結果で、承認成功ではない。
`no_rejection_in_bounded_observation`も安全性や原因解消の証明ではない。
自然な拒否時には`future-rejection-traceback.txt`、比較時刻、tag不変/blockedを保存する。

<a id="interruption-clock-diagnostics--残る調査"></a>

#### 残る調査

1. T03自然再発時に、parent送信とchild受信、HTTP終了、台帳fsync、interpreter終了のどこで停止したかをtrace/stackで判定する。受信記録がなければsignal配送・schedulingも調べる。
2. 現在の実行環境でUTCを動かした機構を切り分ける。OS同期状態の読取結果だけでは発生源を断定しない。時計設定やWSL再起動は今回実施していない。
3. 独立した評価・実承認・運用測定の前に、当該実行環境でUTCと単調時計を記録する。未来拒否を緩和して実候補をeligibleにしない。
4. T13のHTTP部分書込み/応答喪失/競合decisionと実採否、T07/T17の配信復旧は引き続き別作業。

local全suiteは340件成功。最後の制御時計fixtureの固定後は対象decision 9件を再検証した。
実装`8869ee1`の[CI 41268250834](https://github.com/kenobix/Claude_practice/actions/runs/41268250834)でも全340件44.901秒、compileall、全system/PG試験・cleanupが成功した。
artifact 13068077631の11,322 bytesのZIPとGitHub digest、9 memberを照合し、専用container群のcleanupも確認した。
実Capital候補の精度・監視・配信の証拠には流用しない。
