# 指定された時刻APIの確認（2026-11-14）

時計研究は[保留](product-completion-policy.md)。ユーザーが指定した4ページの公開仕様と、記載されたUTC endpointへのWeb tool読取を各1回だけ確認した。
collector、校正、OS同期、APIキー作成、追加NTP queryは行わない。

| サービス | 公開仕様で確認した取得方法 | 今回の判断 |
|---|---|---|
| [timeapi.world](https://timeapi.world/) / [FAQ](https://timeapi.world/faq) | gateway.timeapi.world/timezone/{tz}。FAQは無料20,000回/月、通常の時間サーバーを利用すると説明。pricingにはRapidAPI keyの入口がある | UTC JSONの提供仕様あり。gatewayへの今回のWeb tool読取はInternal Errorで、HTTP status/本文未取得。匿名呼出の実動作・認証要否を確認済みとはしない |
| [Time.Now](https://time.now/developer) | HTTPS /developer/api/time?timezone=UTC。API key不要、README/About等への帰属リンクを要求 | 表示・大まかな時刻照合の候補。今回endpointのWeb tool読取はInternal Error、成功JSONは未取得。利用する場合は要求される帰属表示と利用条件を再確認 |
| [CurrentTime.now](https://currenttime.now/developer/docs) | HTTPS /public/api/timezone/Etc/UTC、標準endpointはkey不要と説明 | 今回endpointのWeb tool読取はInternal Error。既知の時刻源との独立性・提供元間の共有依存・端末側誤差上限は未確認 |
| [紹介記事](https://note.com/maci_26/n/nbb7734eedb58) | API一般の紹介。ページ内のTime検索は該当なし | 時刻APIの公式仕様・精度保証の根拠にはしない。技術判断には各提供元資料を使う |

APIが現在UTCを返す機能と、PCの精密時計を校正する証拠は別である。
HTTPSは接続先証明書の確認に役立つが、レスポンスの生成時点、cacheの鮮度、通信遅延、
上流時計の誤差・共有依存まで保証しない。表示桁数や「atomic」「high precision」という説明だけを精度保証にしない。
今回のツールエラーはサービス停止・利用拒否・endpoint不存在の証明でもない。
公開ページはWeb toolによる本文読取/要約で、生HTTP原本ではない。取得値を時計資格やfit開始の根拠にしない。

将来、画面表示や手動の大幅なずれ確認に使う余地はあるが、現在の製品完成には採用必須ではない。
追加のAPI試験やsource選定は今回ここで止める。保留を解除して調査を繰り返さない。
