# NICT NTPの取得方法・不確かさのreview

2026-11-09、基点`114af9d`。[前回のWeb源review](clock-web-source-review.md)、
[入力条件](clock-source-readiness.md)、[今回の証拠索引](reviews/20261109-nict-method-checks.json)を参照。
**NICTは候補のまま。実query・source adapter・時計資格・実fitは開始していない。**
新しく判明した利用条件と、計算方法の成立条件を整理した資料である。

## 前回のpoll説明の訂正

[開発者ガイド](https://jjy.nict.go.jp/tsp/PubNtp/clients.html)は16秒以上という下限を示す。
一方、今回新しく確認した[公式FAQ Q1-4](https://jjy.nict.go.jp/tsp/PubNtp/qa.html)は、
平均20回/時・合計480回/日を超える利用に事前連絡を求め、短いpollの例を配下clientへ供給するstratum2と関連付けている。
FAQには古いOSの例も混在するが、制限撤廃や個人計測の例外を確認できない。
**前回の「16秒以上」だけでは利用条件の説明として不足していた。両方を保守的に適用し、超える計画は提供元の回答待ちにする。**
複数IP/processへの分散、短い再試行、同じPC内の中継を利用回数の回避策にしない。
末端PCを、配下を持つstratum2と自己申告して例外扱いにもしない。

現policyは各30分phaseの0〜1800秒に60秒ごとの外部UTCを求めるため31境界。
通常60分の2phaseで同じ境界原本を共有できる最も有利な場合でも61時点、半開区間[0,3600)で60回になる。
再開後の別trialも同じなら合計122時点（共有しない4phaseでは124）。全日480未満でも時間平均の条件を満たさない。
これは各境界へ直接1queryを対応させる案の計数であり、実施回数ではない。
少ないqueryから未観測境界を補うなら、別の独立elapsed/holdoverの上限が必要で、現在は無い。
16秒間隔を守るだけでこの問題は解消せず、policyを180秒窓へ変更して回避もしない。

## 真正性と要求対応を分ける

[NTPの仕様 §8](https://www.rfc-editor.org/rfc/rfc5905.html#section-8)ではorigin照合等で要求対応を調べる。
ただしIP/port一致、DNS、echo値、stratum、root dispersionやSHAは、単独で送信者の認証にはならない。
[NTS §1・§8](https://www.rfc-editor.org/rfc/rfc8915.html#section-8)は認証の仕組みと残る遅延攻撃・証明書検証問題を説明する。
NICTの今回読んだ公式公開NTP資料には、採用できるNTS endpoint/認証方法を確認できなかった。
「未確認」を「NTS非対応確定」へ変えず、別providerの機能をNICTへ転記しない。
外部に問い合わせを送信したり、UDP123/TCP4460や終了済みHTTP APIを試したりしていない。

将来のreaderを実装する前に、以下をsource dossierと取得protocolへ固定する。まだ実装済みの機能ではない。

| 境界 | 原本・拒否条件 |
|---|---|
| 送信前 | trial/reader/設定hash、sequence、要求bytes、期待peerとaddress family、送信intent。未送信・送信失敗も残す |
| 要求対応 | raw originと送信値のbyte一致、未消費の要求、重複/再送/別trial/遅延到着/並行の区別。timestamp欄を独自nonce protocolへ勝手に変更しない |
| packet | 長さ/version/server mode、LI、stratum/KoD、時刻0/順序/era、拡張fieldと認証を採用方式に従い検証。不明な末尾を無視して認証済みにしない |
| 認証 | 証明書/署名等の実検証、または監督下の取得と来歴の根拠。自己申告flagは代用不可。時計不確実を理由にTLS検証を無効化しない |
| 失敗 | raw/error/timeout/欠測と未確定状態を保存。[KoDの指針](https://www.rfc-editor.org/rfc/rfc8633.html#section-5.4)も踏まえ、今回の案では中断してreviewし、自動retry/fallbackで成功だけを選ばない |

独立した複数源は誤動作の検出には役立つ場合があるが、共通の基準源/回線/仮想化経路や誤差が残る。
票数だけで真正性・精度・独立性を認定しない。

## 通信遅延とUTC区間

一般的なNTPのoffset点推定を、そのまま誤差なしのUTC値へ変えない。
非対称経路、server処理時間、OS内の送受信待ち、読取bracket、量子化/丸め、source誤差、leap/eraを評価する。
[NICTの説明](https://jjy.nict.go.jp/tsp/PubNtp/index.html)のserver側精度は、client到着時の誤差保証ではない。
root dispersion等も、認証/仕様/読取条件なしで全誤差の証明にしない。

以下は今回の工学的な**提案式**で、NICTが保証する計算方法ではない。
外部UTCが連続して進む区間で、server受信/送信のUTC表示をT2/T3、誤差半径をe2/e3、
client送信前〜受信後の真の経過時間の独立した上限をDとする。
対応と因果順が確かならclient受信時刻は`[T3-e3, T2+e2+D]`に含まれる。
T2はclient送信以降、T3はclient受信以前に起こるためで、往路/復路の対称性は不要。
上下が逆なら不整合、Dが無いなら計算不能。未校正QPCの秒換算RTTをDの保証として使わない。
受信時点とWindows/WSL sampleの対応にも追加bracketが必要で、packet値を異なるsampleへ貼り付けない。
UTC逆行/非連続区間は別の既知違反として残し、負delayやoffsetをclampしない。

## QPCを自分自身で校正しない方法の候補と限界

外部の同じ種類の送信event A/Bが、各clientのQPC bracket
`[a_send,a_recv]`と`[b_send,b_recv]`の中に因果的に入るとする。
`a_send <= a_recv < b_send <= b_recv`を要求する。
外部表示の差をS、source両端誤差の合計をE、QPCの公称周波数をFとすると、
両event間の平均QPC進行率（QPC公称秒/外部秒）は次の区間へ入る。

```text
lower = ((b_send - a_recv) / F) / (S + E)
upper = ((b_recv - a_send) / F) / (S - E)
S > E >= 0, F > 0
```

QPCの読取順序だけを使い、QPCのSI秒精度や通信の対称性を仮定しない。
sourceの真の差は[S-E,S+E]、event間のQPC差は両bracketから包含できるためである。
ただしsource精度/真正性、要求以降に応答が生成されたこと、QPCの単調性、counter読取誤差の包含が別途必要。
分解能より細かいevent時刻を断定せず、実readerは読取誤差込みのbracketを出す必要がある。

合成例では、60秒差、F=1MHz、両bracket幅1ms、source半径各10µsで数値区間だけは±100ppm内。
幅20msにすると中点比1.0でも全区間は入らない。これらは今回回線やNICTの値ではない。
450通りの有限な隠れevent/誤差の組合せで包含を検算したが、実際のnetwork/認証の検証ではない。

**この式も固定60秒境界の校正値へ直接挿入できない。**
remote eventの時点と、policyが指定するQPC first-reach sampleは異なる。
例として外部秒/QPC公称秒が(0,0)→(70,60)→(120,120)なら、全体比1.0でも最初のQPC60秒窓は60/70。
未観測区間の一定速度や滑らかさを仮定した補間は、この違反を隠す。
固定境界への対応とその不確かさを独立に証明する方法が無い間は、campaignへの変換を実装しない。
1秒ごとのhost/guest採取をNTPへ置換したり、長phaseだけで必須30窓を代用したりしない。

## 依存関係・同期責任

NICTの基準時計は外部候補でも、packetを読むPC・QPC・WSL/PHC/backendは観測経路を共有する。
WSL MONOTONIC/RAWとhost QPCの一致だけを独立elapsedにせず、同じ源へ同期した中継を別々の独立源と数えない。
host同期用peerの変更と、時計を変更しない計測readerの採用は別の判断である。
今回NICTをW32Time/chronydへ設定しておらず、task_started/停止caller/tick11000/T03の過去原因は解決していない。

## 次の判断と必要資料

| 選択肢 | 次に必要なもの | 現在の状態 |
|---|---|---|
| NICTを直接使う | 頻度/用途を明記した提供元の回答、認証/来歴、source誤差と固定境界への対応の根拠 | 未取得。問い合わせ文案は下記。送信していない |
| 利用可能な別の参照 | 利用条件、認証、独立elapsed、校正/誤差・共有依存の資料 | 新資料未提示。機器購入を必須とはしていない |
| 引数なしW32Time起動比較 | [案D](clock-host-maintenance-plan.md#service-start-comparison)のhelper作成/検証と、その後の別許可 | sourceの利用条件/不確かさを解決する比較ではないため今回は着手しない。未許可/未実施 |

提供元への問い合わせ文案（**送信にはユーザーの指示が必要**）:

> 個人の機械学習学習環境で、OS時計を変更せず時計の評価用に参照することを検討しています。
> 通常60分と休止再開後60分で、各分境界に最低1回、理想的な境界共有でも計122時点を想定しています。
> これはFAQの平均20回/時を超えるため、実施可否と必要な申請条件を教えてください。
> 実装を決める際には境界前後の取得や失敗分を含む要求上限を別途確定し、再確認します。
> 利用できる認証方式（NTS等の有無・公式endpoint資料）と、時刻stampの基準・精度・運用条件の資料も教えてください。
> client通信遅延込みの精度保証を、server精度の説明から推定する意図はありません。

頻度の回答だけで源採用とはしない。真正性と不確かさが不足すれば実採取は始めない。
サービス比較は現在の source dossier review の必須前提ではなく、今すぐ実行すべきOSコマンドは無い。

## 今回の検証範囲

`data/reports/20261109-nict-method-review/verify_math.py`の標準libraryだけの13件で、
450組の有限包含、広い区間/明確な逸脱、無効domain、source誤差、負delay、境界補間の反例、query数を検算した。
製品unit/packet parser試験ではない。raw NTP、campaign/registry/DBへの書込は0件。
原本recipe/hash・基点資料・コマンド/cwd/終了値/未資格UTC/MONOTONICは[証拠索引](reviews/20261109-nict-method-checks.json)へ結合する。
Web説明はtoolで読んだURL/要約で、生HTTP archive・動的サービス認証の証拠ではない。
source/schema/writer/lock/workflowを変更せず、受入済みsuite/CI/T09/T10は繰り返していない。
実reader/collector/host deadline/固定負荷/全資格とT02/T11/T12/T13bは前提待ちを維持する。
