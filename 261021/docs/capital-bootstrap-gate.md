# Capital Bikeshare初回モデルの評価基準

更新日: 2026-10-28

ユーザーから3点の判断を委任されたため、Codexが以下を採用した。仕様上の確認待ちは解消したが、実行コードへの適用とデータhash・件数を含む評価protocolの凍結は未完了。現在のCLIがこの基準を実装済みという意味ではない。判断前の結果をこの基準で合格扱いに変更しない。

| 判断対象 | 採用する基準 | 理由 |
|---|---|---|
| 予測精度 | 候補のMAEがbaselineを厳密に下回り、RMSEもbaseline以下 | 平均的な誤差を改善し、大きな誤りを重く数えるRMSEでも悪化を許さない。個々の最大誤差が改善する保証ではない。絶対値の業務目標がないため同じ行のbaselineと比較する。 |
| API運用目標 | 暖機後、逐次1,000要求でエラー0件、p95 250ms以下 | ローカル環境での学習用目標として採用する。250msは事前に選んだ工学上の目標で、過去の実測結果から作った閾値ではない。 |
| weekday/weekend・通勤時間帯 | 件数とMAE、baselineとの差を必ず記録し、個別gateは設けない | 区間ごとの業務上の許容誤差が未定義で、サンプル数にも差がある。個別の閾値を結果に合わせて追加しない。 |

判断根拠と差分レビューは[20261028-r06-delta-review.md](reviews/20261028-r06-delta-review.md)に記録する。

## 前提とデータの状態

- Capital Bikeshareは`capital-trip-starts-hourly-v1`、UCIは`uci-bike-sharing-cnt-dataset-local-hour`。初回Capitalモデルには同target incumbentがないため、比較対象は同targetのhistorical baselineにする。
- Capitalの評価済み期間（2025年上期validation、2025年7月〜2026年4月の後続評価）は新しいvalidation/後続評価に再利用しない。過去の期間を学習材料に含める場合も、未使用の評価期間が増えたとは扱わない。
- `timestamp + 3時間`は履歴再生用のラベル遅延仮定。月次履歴が実際に3時間で公開されることを意味しない。
- 2026年6〜9月archiveは前回のHEAD requestでHTTP 200、10月archiveは404だった。今回それらのURLは再照会していない。6〜9月の内容は未取得・未閲覧のまま。
- 月次archiveが隣月に始まるtripを含む実績がある。新snapshotには対象範囲・隣月bufferの採否と理由を事前記録する。9月末までの評価に10月bufferが必要と判断した場合は公開を待ち、結果を見て期間を短縮しない。snapshotへの収録を超える母集団の完全性は保証しない。

## 評価窓の事前案

新しいCSV/manifestは別pathへ作成する。以下の日時は前回案を維持し、学習締切だけを実装の情報時点契約に合わせて明確化した。データと原本範囲を検証してからprotocolへ日時・件数・SHAを固定する。

| 用途 | 対象時刻（America/New_Yorkのnaive wall time） | 情報時点・想定件数 |
|---|---|---|
| 学習 | 2023-01-01以降、2026-06-01 00:00未満でラベル到着済みの行 | training cutoffは2026-06-01 00:00。+3時間仮定では最後の利用可能targetは2026-05-31 21:00。22時・23時の2行を学習から除く。 |
| validation | 2026-06-01 00:00以上、2026-08-01 00:00未満 | 未使用の2か月。想定1,464時間。 |
| 選択判断 | 2026-08-01 02:00以降 | validation最終targetのラベル到着後。 |
| 一度限りの後続評価 | 2026-08-01 03:00以上、2026-10-01 00:00未満 | 想定1,461時間。8月1日00〜02時は選択判断を待つ境界として評価対象外。 |

前回文書は学習末尾2行のラベルが6月1日02時に到着するとだけ書いており、00時に始まるvalidationより後のラベルを学習へ含めかねなかった。現在のprotocol builderは`training_cutoff <= validation_start`を要求するため、締切を00時に固定し2行を除外する。実際の学習・評価はまだ行っていない。

日時grid、DST除外、重複・欠損target、ラベル利用時点を入力契約で検査する。件数が想定と違えば期間を調整せず原因を調査する。ラグの欠測は学習開始境界等の既知の理由と想定外を区別し、母数付きで記録する。protocolの保存・重複窓検査と、実行開始時の消費台帳記録を候補fitより先に行う。

## validationと後続評価の精度gate

比較対象は現在の`HistoricalBaseline`。予測対象時刻の24時間前の実績を使い、欠けていれば学習期間だけの曜日・時刻平均、さらに学習期間の全体平均へフォールバックする。各要求時点で利用可能な同profileの観測だけを使う。

validationと後続評価の各期間で、候補とbaselineを同一行・同じ正解・同じラベル利用仮定で比較し、次を両方満たす場合のみその期間の精度gateを合格とする。

```text
candidate.mae < baseline.mae
candidate.rmse <= baseline.rmse
```

指標が欠落・非有限、件数や行集合が不一致なら不合格。MAE同値は不合格、RMSE同値は合格条件を満たす。後付けの許容幅は使わない。baseline MAEが0で厳密改善できない場合も合格とはしない。

weekday/weekend、朝7〜9時、夕方16〜18時は各区間の件数・候補MAE・baseline MAE・差を記録する。区間別の自動gateは置かないが、最終採否の手動レビューでは偏りを説明する。全体gate合格でも人が見送ることはできる。不合格を手動承認で合格へ変更する経路は作らない。

validationで選定したartifactはhashを固定し、後続評価で再fitしない。後続評価で不合格なら登録・昇格せず、調整後の候補には別の未使用期間を用意する。現在のUCI `quality_gate=passed`は従来のvalidation MAE条件を指す。新Capital基準はpolicy識別子とともに記録し、過去タグの意味を変更しない。

## API運用測定の固定条件

精度gateとは別の運用gateを作る。対象はCapital隔離stackのproxy入口`http://127.0.0.1:8180/predict`。モデル・DBが準備済みで、readyとprofile/model/artifact識別が確認できてから測定する。

- 暖機50要求を除外し、その後1,000要求を同時数1で逐次送信する。HTTP clientは1つで接続を再利用し、自動再試行は0回、各要求のtimeoutは10秒。
- 暖機で失敗した場合は準備失敗として記録し、その実行を合格扱いしない。
- 暖機を含む全要求に新規のrequest IDを付ける。同じIDの再送で保存済み結果を返す処理を計測対象にしない。benchmark IDで監査行を識別し、件数と保存先をreportへ残す。
- 入力は学習期間内の固定168時間の列を順番に繰り返す。対象週・順序・body生成規則を実行前に保存し、validation/後続期間は使わない。正解との精度計算はこのbenchmarkでは行わない。
- latencyは送信直前から応答body受信完了までの単調時計による経過時間。1,000件すべての計測値を保存し、p95は線形補間の95 percentileで計算する。失敗した要求を除外・成功要求で置換しない。途中中断はincompleteで不合格。
- HTTP 200以外、接続失敗、timeout、不正JSON、異なるrequest/profile/model/artifact識別、非有限・負の予測をエラーに数える。単にHTTP 2xxであるだけでは成功としない。
- エラー0件かつp95 250ms以下で合格。CPU/RAM、Docker資源制限、OS/Python/依存とイメージ、model/data hash、DB開始状態、他負荷の有無、p50/p95/max、失敗数を記録する。条件変更時は別benchmarkとして扱う。

この結果は当該PC・逐次要求の運用目標への適合を示す。同時負荷への耐性や本番SLOの達成を示すものではない。

## 残る作業と実行順序

1. **R04/P0:** timezoneの実際の供給元・版を固定/記録し、集計とAPIのDST解釈を揃える。`tzdata`のinstallだけでOSから独立するわけではない。
2. **R06/P1:** incumbentなしbootstrap経路をprotocol/candidate/final-evaluationへ実装する。新精度gate、policy識別子、hash、未設定・不合格時の拒否を回帰試験する。
3. **R06/P1:** 原本範囲・buffer規則を確定し、未取得archiveを取得、新pathへmanifest v2を生成する。時刻・件数・出典と評価台帳の非重複を確認し、実験protocolを凍結する。
4. **R06/P1:** validationを一度実行しartifact/行別証拠を固定する。合格時だけ同一artifactで一度限りの後続評価へ進む。
5. **R05/R06/P1:** 独立PostgreSQL/MLflow、model loading、offline/API parityを確認する。固定条件の運用benchmarkと配信許可の判定を実装・実施する。
6. **R07〜R10:** 監視参照とPSI、障害・復旧、配信台帳と進行表示、採否記録・デモを計画書に従って完了する。

追加のユーザー確認を開始条件にはしない。精度gate・運用gateの実装/証拠、artifact整合性、入力契約、復旧試験、理由付き手動承認が揃うまでは、Capital profileはmigration-onlyを維持する。
