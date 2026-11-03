# chronydの補正量とtick=11000の追加調査

2026-11-03の[追加読取と同時自然再現](clock-followup.md)で、今回のfuture拒否とtimesyncdのstepを対応した。
chronydは今回常に11000ではなく、内部変数・PHC/socket本採取は未了。以下の過去区間の数値/限界は当時の記録として保持する。

続く[管理者ログ照合](clock-admin-log-review.md)で稼働exe/hashと生成設定を確認した。以下の未採取という記述は基点からの当時の状態で、内部制御原因の未確定は継続する。

基点: `2181bfe453bf9c87ec9374a5d74013bcdddb86b0`。
[管理元調査](clock-process-ownership.md)、[限定した管理者採取](clock-owner-admin-read.md)、
[今回の証拠JSON](reviews/20261101-chrony-control-checks.json)を参照する。

**tick/freqの数値の意味は絞れた。稼働chronydの実行file・管理元と、限界値を要求し続ける内部原因は未確定。**
管理者採取ログは指定のreports内に存在せず、ユーザーからも「まだ採取していない」と回答を得た。
現在のWindows tokenは非管理者。管理者への自動昇格や同じ拒否の再試行はしていない。

## 今回の観測と照合

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

## 数値の意味をソースと計算で確認した範囲

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

## 原因・仮説・未確定事項

- **確認済みの現在の原因:** Ubuntu timesyncdが負方向stepを実行する。別のkernel taskはtick/freqを書き、共有時計への複数の調整経路が続いている。
- **有力だが未確定の説明:** PHC追従側が、他の調整者によるstepや周波数変更を外乱として補正し、総量がdriver限界付近になるfeedback。PHC0応答・WSL組込起動設計・今回の数値計算は整合するが、内部状態または管理元を固定した保守比較が必要。
- **未確定:** chronydの実exe/hash・親/開始時刻・namespace・有効な生成設定・PHC fdとUDP socketの所有、限界前の制御量、Windowsの絶対UTC精度、過去のT03停止/SQLite future失敗との同一原因。

[WSL 2.6.3のStartTimeSyncAgent](https://github.com/microsoft/WSL/blob/2.6.3/src/linux/init/main.cpp)はPHC用configを生成してchronydを起動する設計だが、runtimeの所有証明にはしない。
system distroから見えるtime.windows.comのpackage既定fileを稼働設定とみなして修正しない。

<a id="clock-repair-options"></a>

## 次の採取と修復案・影響・切戻し

次は[管理者採取手順](clock-owner-admin-read.md)を実施し、表示された保存directoryをチャットに伝える。
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
