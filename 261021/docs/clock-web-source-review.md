# time.is・NICTを時計の参照元に使う場合

2026-11-09、基点`67f4586`。ユーザー提示2サイトと提供元の説明をreviewした。
[証拠索引](reviews/20261109-task-source-checks.json)、[既存の源受入条件](clock-source-readiness.md)を参照。

**外部との大きな時刻差を人が確認する用途には使えるが、画面の表示だけで今回の全時計資格は満たせない。**
時計設定をサイトへ合わせる操作、実source reader、継続取得、資格試験は実施していない。

| 候補 | 利用できる範囲 | 今回の採用判断 |
|---|---|---|
| time.is/Japan | 人による時刻/ずれの目安の確認 | 自動収集は採用しない。専用APIは条件未取得 |
| NICT JST5表示ページ | 人による内蔵時計との差の確認 | 表示のみをUTC/elapsedの正式原本にしない |
| NICT公開NTP ntp.nict.jp | 機械向け時刻取得の調査候補 | 真正性・輸送/精度境界・独立elapsedの資料とreaderが不足し、採用保留 |

## time.is

[提供元の説明](https://time.is/about)は、表示精度が通常0.02〜0.10秒で回線/PC負荷に依存するとしている。
典型値は、個々の取得について保証された誤差上限でも、source/transportの全不確かさでもない。
[利用規約](https://time.is/terms_of_use)はスクリプトやアプリからの利用・自動refreshを禁止し、API利用は問い合わせを求めている。
人が開いて確認する用途と自動計測を区別する。scraper/API実装、問い合わせ送信、有料契約は行っていない。
provider名・TLS接続・画面の「正確」表示だけで、sourceのUTC精度や独立elapsedの真正性を認定しない。

## NICTの表示ページと終了済みAPIを混同しない

[JST5ページ](https://www.nict.go.jp/JST/JST5.html)自体が、正確な標準時提供を目的とせず通信状況で誤差が生じると説明している。
[表示の技術説明](https://jjy.nict.go.jp/tsp/link/JST.html)では、取得後はPCの時計を基準に表示が進む。
その画面を継続撮影しただけでは、PCと独立した経過時間の測定にならない。現在のbrowser実装を動かして確認したわけではない。

旧JSON/JSONP/HTTPS時刻APIの説明も残っているが、[公式の終了告知](https://jjy.nict.go.jp/httphttps-index.html)で
HTTP/HTTPS時刻配信は2022-03-31終了と明記されている。
**同じ告知は、JST5の時刻差表示ページとNTPはこの終了の影響を受けないとも明記している。**
JST5まで終了したと説明しない。反対に、JST5のHTMLを取得できたことを、旧APIを自由に利用できる証拠にもしない。
終了済みendpointへ試験queryを送り、動くものを探す作業はしていない。

## NICT NTPを検討する次の段階

[公開NTPの説明](https://jjy.nict.go.jp/tsp/PubNtp/index.html)は、利用者側の精度が回線により数ms〜数百msと変わり、保証しないとしている。
サーバ内の精度を、そのままPCでの取得誤差に流用しない。
[開発者ガイド](https://jjy.nict.go.jp/tsp/PubNtp/clients.html)のpoll間隔は16秒以上。
将来の計測設計ではこの制限を守り、必要サンプル頻度に足りなければ別の源/方法を検討する。高速queryで不足を埋めない。

次にreviewする不足は以下。これは実queryやreader開始の許可ではない。

1. 通常のNTP応答の送信元・要求対応・再送/差替え・認証をどう確認するか。公式hostnameやnonceだけを認証にしない。
2. source自身のUTC基準と不確かさ、送受信bracket/非対称経路/読取/丸め/欠測から作る保守的な区間。
3. 校正対象QPCの秒数を使って同じQPCを正しいと判断しない計算と、host/PHC/guestとの共有依存。
4. 保存するraw応答・sequence・status・reader/config/hashと、固定policy全期間への対応。成功sampleだけを選別しない。

UTC±1秒と、QPCの進む速さ±100ppmは別の条件である。
既存の[理想的な不確かさ予算](clock-source-readiness.md#固定policyから先に分かる不確かさの予算)でも、
60秒窓の各端点の誤差半径は約3ms未満を要する。ネットワーク参照が永久に不可能という意味ではなく、
今回の回線・読取方法でその境界を証明できるかが未確認という意味である。
機器購入を前提とせず、まず上記の資料/方法が成立するか検討する。値が入るよう閾値や不確かさを緩めない。

## 今回の到達点

独立源が「何も提示されていない」状態から、提示2サイトの用途/規約/限界とNTP調査候補を具体化した。
Web説明のreviewであって、源の採用・実採取・時計修復・全資格の合格ではない。
Web toolで読んだ公式説明のURL/要約を記録し、生HTTP原本のローカル保存やbrowser動作試験とは呼ばない。
time.is/accuracyの取得はtoolエラーで根拠に使わず、旧NICT資料のAPI説明は終了告知と区別した。
実fit/採点・T02/T11/T12/T13bは既存の前提待ちを維持する。
