# 管理者採取ログの照合と入力待ちの整理

基点`f70cb37380ba4846451cf662807f208033b7a35c`。ユーザー添付と、
`data/reports/clock-owner-admin-053cb32639e54e04aea39f299e82711d/debug-shell.log`を照合した。
[証拠JSON](reviews/20261101-admin-owner-checks.json)と[採取手順](clock-owner-admin-read.md)を参照する。

## 実行結果

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

## 実体と管理層の照合

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

## 原因と残る範囲

今回の実体hash一致により、[前回の上流4.1計算](chrony-control-analysis.md)との対応は強まった。
ただし配布binaryの全patch/build identityまで検証したわけではない。
生成configにはmaxslewrate/maxdrift/includeの指定がなく、上流既定値を使う構成と整合する。
**tick11000の数値が補正限界と一致しても、制限前のbase/corr/offsetを読んでおらず、持続する飽和の内部原因は未確定。**

残る限定読取は、同じdaemonのPHC fdとUDP inode所有、必要ならbuild/package情報と既存logの制御量。
今回のreaderはfd所有まで採っていないため、既存PHC0応答をPID認証と読み替えない。
Windowsの絶対UTC精度、T03歴史的停止、過去T13との同一原因、修復後の時計資格も未了。
修復案・影響・切戻しは[前回の表](chrony-control-analysis.md)を維持する。
OS設定変更や同期service停止、future条件の緩和は行っていない。

この管理者ログの基本inventoryを再採取する必要はない。追加読取が必要になった時だけ、新手順で別directoryへ保存する。
独立したT07/T17の隔離検証は先行する。合成成功を実Capital採否の証拠へ流用しない。
