# chronydの配布build照合と内部計算の観測

2026-11-04追補：[initdのsetter・Docker Desktop管理層の照合](initd-owner-correlation.md)を追加した。
今回のinitdは実exe/親/namespaceまで対応したが、PHC/socket管理者採取・tick11000の内部原因・時計資格は引き続き未了。
以下の11月3日の未確定記述と数値観測は、その時点の記録として保持する。

基点`298c6c82db98c2ae607e69f81e9f99695a4a1f59`、2026-11-03。
[前回の時計調査](clock-followup.md)の続き。原本・失敗・hashは
[検証索引](reviews/20261103-chrony-runtime-checks.json)で確認する。

**一致する完全なdebug情報を取得し、非飽和時の内部計算とkernel書込を照合した。
今回tick=11000は再現しておらず、過去の上限到達原因・時計修復・資格確認は未完了。**

## 配布物と稼働実体の対応

Microsoftの[公式binary一覧](https://packages.microsoft.com/yumrepos/cbl-mariner-2.0-prod-base-x86_64/Packages/c/)と
[debuginfo一覧](https://packages.microsoft.com/yumrepos/cbl-mariner-2.0-prod-base-debuginfo-x86_64/Packages/c/)から、
Mariner2.0の`chrony-4.1-3.cm2.x86_64.rpm`と対応するdebuginfoを取得した。
OSへinstallせず、libarchiveで必要な通常fileだけを作業directoryへ読み出した。package内のkeyfileは読んでいない。

| 照合 | 結果 |
|---|---|
| RPM内のchronyd | 320,600 bytes、SHA `bf39d010dfc6177cd864d96ddd0cb32eb796704e8c68fe8bd65088d20d2e64b8`。過去の管理者採取と一致 |
| Build ID | binary/debugとも`5bff56b8a81ac12e2fbf0c249759c6e1271cdac6` |
| debuglink | `chronyd-4.1-3.cm2.x86_64.debug`、CRC32 `c87254da`。取得debug fileと一致 |
| 変数の型・位置 | 完全DWARFのcompile unitと型を照合。sys_generic.cのdouble6項目、sys_linux.cのint2項目とdouble1項目を限定 |
| 同名変数の区別 | `max_freq`・`offset_register`・`set_frequency`等は別fileにも存在。名前だけでアドレスを選ばない |
| 稼働中の実行file | 読取FDのdev42/inode4227/SHAと、同inodeの指定命令で発生したchronyd213のeventが対応 |

対応debug RPMに含まれる`conf.c`・`local.c`・`sys_generic.c`は取得済み上流4.1と全bytes一致した。
`sys_linux.c`の差はseccomp許可syscallのclone3/pread64追加で、確認した差分に周波数計算の変更はなかった。
これは再現buildやRPM署名の検証ではない。公開HTTPS・SHA・Build ID・debuglink CRCの照合範囲として記録する。

## 稼働値を読む前の負例と方法の検証

既に稼働するsystem distroの公開chronyd fileをO_RDONLYで開き、専用Unix socketのSCM_RIGHTSで読取FDだけを渡した。
mountやWindows管理者tokenの取得、daemonへのsignal、ptrace停止、process全メモリのdumpは行っていない。
**このFDはhelperが開いた実行fileであり、daemonのPHC fdを採取したものではない。**

先に、時計syscallを含まない小さなPIE fixtureで、1.5→−2.25という既知のdoubleを検証した。
初回はtracefsのO_CREAT付きopenがEINVALとなり、専用instanceを回収した。
O_CREATを外すとeventは記録できたが、`@+file-offset`方式は既知値を0と読んだ。
data LOADのoffset変換を変更しても0のままで、fixture検証を通らない実行はchronyd採取の前で停止した。
この方式が0となる根本理由は未確定で、offset変更だけで修正済みにしない。

明示pointer・IP相対の対照採取では正しい値が読めた。
そこで入口とret命令直前の2地点を使うIP相対方式に限定し、両地点の既知値が一致してから稼働採取した。
ret命令の位置は一致したbinaryの逆アセンブルで確認し、関数入口と同じ相対値をreturn側へ流用しない。

## 35秒と75秒の固定した観測結果

時間はMONOTONIC基準。現環境の秒数は外部秒として資格確認済みではない。
成功やtick11000が出るまで延長・自動retryする採取ではなく、目的の異なる2つの固定attemptを保存した。

| attempt | 確認結果 | 限界 |
|---|---|---|
| 最初の35秒 | 入口/出口8組で内部状態と計算結果が一致 | kernel書込を同時採取しておらず、driver cache/戻り値からの検算 |
| 続く75秒 | 16組で内部計算、同じTIDのtimex入力、成功戻り値、driver cache/戻り値を照合 | 32秒pollを複数回含む有限区間。tick11000/負方向上限制限は0組 |

基準側の`base_freq`は両区間で約−73,579.259270ppm。
`max_freq`は100,000ppm、`max_corr_freq`は1/12だった。
これは内部の推定・制御値であり、物理時計の誤差を校正して得た値ではない。

確認した計算は、残った時刻差と補正の速さから補正比を作り、基準周波数と合わせてdriver範囲へ制限する経路である。
検算では概ね次を用いた。実際には小さい時刻差の境界や整数量子化も扱う。

```text
duration = max(correction_rate / abs(offset_register), 1)
corr_freq = clamp(offset_register / duration, -max_corr_freq, +max_corr_freq)
total = clamp(base_freq + corr_freq × (1000000 - base_freq), -max_freq, +max_freq)
kernel correction ppm = 100 × (10000 - tick) - freq / 65536
```

75秒区間では16組すべて、量子化の範囲内で計算が一致し、対応するclock_adjtimeは0を返した。
同区間にtimesyncd496の負方向要求3件も記録したが、**それだけで過去の上限到達原因や長期feedbackを確定しない。**
今回観測した非飽和状態を、以前のtick11000期間の内部状態へ遡及適用しない。

## 回収と残件

すべてのfixture/稼働attemptで専用instanceとprobeを回収し、global trace制御・kprobe/uprobe定義の前後hash一致を確認した。
稼働採取のbuffer lost/overrun/droppedは0。実行file bytesの前後SHAも一致し、FD・一時IPCは閉じた。
ただしuprobeは一時的に命令をinstrumentする診断で、観測の負荷がある。今回の時間・挙動を無観測時の性能や時計資格へ流用しない。
OS時計/同期設定、実DB・実registry・現配信は変更していない。

次は[管理者採取](clock-owner-admin-read.md#chrony-resources-admin)でPHC fd/UDP所有、現在の開始時刻・親・namespaceを照合する。
追加readerはexeのdev/inode/sizeを前後比較し、hashが同じ別inodeへの変更も不完全扱いにする。
現在の実行fileの対応が分かっても、元のlauncherやPHC/socket所有が全て確定したわけではない。

tick11000が自然に現れる区間で同じ限定数値とtimexを対応させること、initdの実体、host外部UTC差/同期責任、
許可された保守比較と資格確認は残る。OS修復は別途許可が必要。future拒否の緩和・clamp・自動retryは行わない。
T03は今回9件成功して自然停止未再発、T09は公式202610が404のままで、実fitは行っていない。

## readerのCI受入

実装`f45733a467e09c79d9d557682195c85da37b8b4a`の[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704)は全464件・全system成功。
[CI索引](reviews/20261103-chrony-runtime-ci-checks.json)で実artifact16member/hash・別process再計算・正常/TERM/対応復元cleanupまで確認した。
17件のreader fixtureを含むコード回帰の受入であり、この文書の稼働uprobeをGitHub runnerで再現したことや、時計資格の合格ではない。
初期local索引のCI_PENDINGは当時の記録として保持する。後続文書commitはこの実装CIが直接試験したcommitではない。
