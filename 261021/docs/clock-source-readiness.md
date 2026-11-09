# 独立UTC源・QPC校正の選定前review

**2026-11-09 NICT方法追補:** [頻度・認証・非循環の計算候補](clock-nict-method-review.md)をreview。
FAQの平均20回/時・480回/日は前回の16秒下限とは別条件。直接毎分取得は未許可。
因果bracketの数値包含を合成検算したが、源採用/固定境界への対応/全資格は未達。QPC由来RTTの自己校正や境界補間は採用しない。

**2026-11-09 Web源追補:** ユーザー提示のtime.isとNICTを[用途・規約・不確かさでreview](clock-web-source-review.md)した。
time.isは人の目視用と自動APIを区別し、NICTはJST5表示・終了済みHTTP配信・継続するNTPを区別する。
NTPは調査候補だが、実sample/真正性/輸送誤差/独立elapsedの根拠は未取得。源採用・全資格・実fit保留は維持する。

2026-11-05、基点`002718e`。今回の[合成採取](clock-fake-acquisition.md)を実採取へ置き換える前のD3/Sol判断。
**採用できる源と校正readerは、まだ選定されていない。**
利用可能な独立機器/記録、源の認証・精度根拠、Windows/WSLへ輸送する際の遅延境界が未提出である。
未確認provider名やaddress、`independent=true`の自己申告で不足を埋めない。
現時点の入力から特定製品/サービスの独立性や精度を認定することもできない。

2026-11-09 peer追補：[実peer/status/identity review](clock-host-cause-review.md)で、peerはNoAuth、pending、現在有効sample0と確認した。
個別peerのerror0を全体同期成功へ昇格させない。今回metadataは認証・精度・輸送区間・独立elapsed校正の根拠を欠き、採用できない。
このreaderで資料が不足するという判断で、同名サービスの別の認証方式まで永久に不採用とする判断ではない。
新dossierは標準保存先に無く、源選定保留を維持する。host修復と独立源採用は別の条件である。

2026-11-09 host追補（peer追補前の履歴）：[管理者runtime review](clock-host-runtime-review.md)でhostのsource/configuration取得も完了。
runtimeはNtpClient有効/Type=NTP/time.windows.com,0x9だが、状態はLI3/Local CMOS Clockと報告された。
config flag/precision/root dispersionやquery exit0は真正性・外部UTC誤差・QPC校正の根拠ではない。
peer/last-error等の原因診断と独立源の採用を分け、採用保留を維持する。

2026-11-09（host追補前の履歴）：[管理者PHC/UDPの照合](clock-resources-admin-review.md)で、指定採取区間のchronydがHyper-V PHCとlocal UDP323を所有すると確認した。
これは同期経路の実体照合で、独立源の真正性・UTC精度・elapsed不確かさを満たす資料ではない。
hostのW32TimeはRunningでもstatusは同期未実行/Local CMOS Clockで、source/configurationは非管理者tokenに拒否された。
独立源の採用は引き続き保留。追加資料の入口は[更新した入力一覧](clock-input-request.md)を参照し、同じPHC採取を反復しない。

2026-11-08追補：追加資料は未取得とユーザーが回答した。
[必要入力と保存先](clock-input-request.md)へ、管理者ログ・源のidentity/真正性・依存関係・不確かさの根拠を具体化した。
資料待ちで独立して進められる[合成raw bundle](clock-raw-bundle.md)を追加したが、実源の選定/資格は保留を維持する。

| 案/入力 | 今回の判断 | 採用前に必要な根拠 |
|---|---|---|
| fake readerのcounter/UTC | 合成受入だけに使用 | 実源ではないので実資格へ採用しない |
| 同一host/guestのUTC差、QPCから生成した秒数 | 独立校正とみなさない | 共通の故障源を除いた参照と、その同一性/原本 |
| 現在のPHCや同期flag | 源/同期責任の追加読取資料 | 稼働設定・fd/UDP・process identity・管理層と参照元の対応、独立性のreview |
| HTTP Dateの単発応答 | 原本公開状態の日時補助だけ | 精度/丸め/取得時点/輸送区間/認証を証明しない限り校正に使わない |
| 未選定の外部UTC/elapsed参照 | 採用保留 | 下記の完全なsource dossier、readerと負例、事前承認 |

## source dossierで判断する内容

1. 源のUTC基準/経過秒の定義、識別、管理元、認証/署名または監督下の原本取得方法を明記する。
   hashは受領後の内容結合だけで、源が本物であることや採取前の改変を認証しない。
2. host/backend/guest/QPCと共有する時計・同期・電源/仮想化経路を列挙し、同じ障害が双方へ反映する可能性を評価する。
   「別process」「別IP」だけで独立と判定しない。
3. 源自身の誤差、丸め、counter分解能/周波数の校正、安定期間/有効期限を示す。
   根拠のないゼロ誤差を認めない。
4. 生応答、要求ID/sequence、送受信時点/順序、輸送/読取遅延の上下限からsample時点を覆うUTC intervalを作る。
   対応しない時点のUTC値をそのままsampleへ貼らない。reader停止/遅延/再送/並行/応答差替えの負例を持つ。
5. 校正は固定60秒窓30個とphase全体について独立elapsed区間を出す。
   QPC elapsedから同じreferenceを逆算しない。UTC offsetが小さいだけで経過時間の校正を成立させない。
6. [固定policy](clock-campaign-review.md)のUTC ±1秒、QPC比±100ppmを、**不確かさ区間全体**で満たせるか事前計算する。
   設計の許容値へ入るよう不確かさを切り詰めない。難しい場合は本採取を始めず不足を記録する。
7. 取得実装/設定/依存bytes、source原本、derived interval、事前plan/hash、review者/日時を別原本で結ぶ。
   欠測を後の成功応答で埋めず、再開後は別trialで全観測を実施する。

## 固定policyから先に分かる不確かさの予算

源未選定でも必要な精度の条件は計算できる。以下は**QPCと参照の中心経過時間Cが完全に一致**し、
参照の両端の誤差半径が同じu、他の誤差なしという理想条件だけの必要予算である。
校正比の区間は `[C/(C+2u), C/(C-2u)]`。
全区間を固定の`[9999/10000,10001/10000]`へ入れるには、`2u <= C/10001`が必要になる。

| 参照区間 | 各端点の最大u（理想条件） | 含意 |
|---|---|---|
| 固定60秒窓 | 約2.999700 ms | ±3 msでも上側比がわずかに許容を越え、区間全体の成功にはできない |
| phase全体1800秒 | 約89.991001 ms | 長区間だけに入っても、必須の60秒窓30個の不足を補えない |

実際には中心の進行率差、源/輸送/読取/丸め等も予算を使うため、上表だけで使用できる源と認定しない。
UTC offset ±1秒が良好でも、60秒の校正に必要な不確かさへ入るとは限らない。
両端誤差が相殺するとの根拠が無ければ、保守的に半径を合算する。
14条件をFractionで検算し、60秒ではu=2,999,700nsは区間内、次の1nsは越えることも保存した。
これは源の実測や真正性の証拠ではない。原本とhashは[追加review索引](reviews/20261105-clock-acquisition-startup-checks.json)に記録する。

## 次に進められる範囲

fake transport/原本照合の追加負例と可搬な原本bundleの改善は、実源未選定でも進められる。
実source adapterと実clock collectorを開始するには、上記dossierと採用判断を先に固定する。
OS修復・休止再開・実負荷/配信に関する許可は別であり、この文書は設定変更の許可を与えない。
future拒否、失敗優先、時計資格incompleteは維持する。
