# Capital Bikeshare初回モデルの評価案

更新日: 2026-10-28

この文書はR06のレビュー用提案です。まだ凍結protocolでもユーザー承認済みのgateでもありません。合意前に候補学習、評価期間の消費、モデル登録・昇格は行いません。

## 前提

- Capital Bikeshareは`capital-trip-starts-hourly-v1`、UCIは`uci-bike-sharing-cnt-dataset-local-hour`です。UCI登録版3をCapitalのincumbentとして扱いません。
- Capitalの評価済み期間（2025年上期validation、2025年7月〜2026年4月の後続評価）は再利用しません。
- Capitalの月次履歴は現在の運用時点でのデータ到着を再現しません。`timestamp + 3時間`はシミュレーション上のラベル遅延であり、公開SLAではありません。
- 2026年6〜9月archiveは公式URLへHEAD requestを行いHTTP 200を確認しましたが、内容は未取得・未閲覧です。2026年10月archiveはHTTP 404でした。取得時はURL、取得日時、archive SHAを保存し、公開元の更新可能性も記録します。

## 評価窓の提案

41件の既存原本に、未取得の2026年6〜9月archiveを加え、2023-01-01〜2026-10-01の同一snapshotを新しいCSV/manifestへ集計する案です。既存CSV/manifestを上書きしません。

| 用途 | 対象時刻（naive local wall time） | 理由 |
|---|---|---|
| 学習 | 2023-01-01から2026-06-01 00:00未満 | 既存データと2026年5月までを学習材料にできる。最後のラベル到着は仮定上2026-06-01 02:00。 |
| validation | 2026-06-01 00:00以上、2026-08-01 00:00未満 | 過去に使っていない新しい2か月で候補選択する。想定1,464時間。 |
| 選択判断 | 2026-08-01 02:00以降 | validation最終時刻2026-07-31 23:00のラベルが仮定上到着する時刻。 |
| 一度限りの後続評価 | 2026-08-01 03:00以上、2026-10-01 00:00未満 | 判断時刻より後の2か月。00〜02時は判断時刻との重なりを避けて未使用にする。想定1,461時間。 |

想定件数は実際のmanifest/CSVで確定します。件数が想定と違っても、その場で期間を調整せず停止して原因を調査します。プロトコルを保存する前に期間・原本範囲・gateをレビューし、保存後はSHAを記録して候補学習より先に台帳へ登録します。既存の評価台帳が重複を検出したらその窓を使いません。

## 初回モデルの比較と判定案

同targetの登録modelはありません。初回候補は現在の`HistoricalBaseline`だけと比較します。このbaselineは予測対象時刻の24時間前の実績を使い、存在しなければ学習期間だけの曜日・時刻平均、さらに全体平均へフォールバックします。これは同じprofileの比較対象で、別targetのUCI modelではありません。

提案するvalidation選択gate:

1. validationのMAEがbaselineのMAEを厳密に下回る。
2. RMSEは必ずreportする。候補のRMSEがbaselineを上回らないこともgateにする案（ユーザー確認待ち）。
3. 全体指標を判定の主指標にし、weekday/weekendと通勤時間帯のMAEは診断値として報告する。segment単独のgateはサンプルの小さい区間への過剰適合を避けるため置かない案。
4. validation gate `passed`は「このvalidation上でbaselineより良かった」という意味に限定し、登録許可・運用可能・本番配信可能という意味にはしない。

選ばれたartifactは変更せず一度だけ後続評価し、同じbaselineと比較します。最終判定のMAE/RMSE条件はvalidationと同じものを評価前に固定します。後続評価で不合格なら登録・昇格せず、結果を見て再学習・調整した場合は別の未使用期間を待ちます。

## 運用試験案

精度とは別の測定にします。提案条件は、同一WSL/Docker環境・同一compose資源・model warm-up後、逐次1,000 request、入力/応答の固定、すべての応答でHTTP 2xx、p95 latency 250 ms以下です。これはこの学習用PCでの比較目標であり、本番SLOや顧客要件ではありません。CPU/RAM、Docker制限、Python/イメージ、request body、実測p50/p95/max、エラー数を同時に保存します。

## 実行を始める条件

次の作業はこの提案をレビュー・承認してから行います。

1. RMSEをvalidation/finalのgateに含めるか決める。
2. `p95 <= 250 ms`/1,000逐次requestをローカル目標として採用するか決める。
3. 通勤時間帯やweekday/weekendのsegmentをdiagnostic-onlyとする案を確認する。
4. 2026年6〜9月archiveを取得し、新しいpathへ厳格manifest v2を作る。
5. bootstrap protocol/candidate/final-evaluation経路を実装・回帰試験し、実験前にprotocolを凍結する。

ユーザーがgateを承認しても、データ契約検証・モデルartifact照合・最終評価・隔離runtime検証・手動承認が揃うまでは、Capital profileはmigration-onlyのままです。
