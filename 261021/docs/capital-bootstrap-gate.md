# Capital Bikeshare初回モデルの評価基準

更新日: 2026-10-28

ユーザーから3点の判断を委任されたため、以下を採用した。仕様上の確認待ちは解消したが、実データのprotocol凍結・評価・運用測定は未完了。実装済みの範囲は「実装状況」で区別し、判断前の結果をこの基準で合格扱いに変更しない。

| 判断対象 | 採用する基準 | 理由 |
|---|---|---|
| 予測精度 | 候補のMAEがbaselineを厳密に下回り、RMSEもbaseline以下 | 平均的な誤差を改善し、大きな誤りを重く数えるRMSEでも悪化を許さない。個々の最大誤差が改善する保証ではない。絶対値の業務目標がないため同じ行のbaselineと比較する。 |
| API運用目標 | 暖機後、逐次1,000要求でエラー0件、p95 250ms以下 | ローカル環境での学習用目標として採用する。250msは事前に選んだ工学上の目標で、過去の実測結果から作った閾値ではない。 |
| weekday/weekend・通勤時間帯 | 件数とMAE、baselineとの差を必ず記録し、個別gateは設けない | 区間ごとの業務上の許容誤差が未定義で、サンプル数にも差がある。個別の閾値を結果に合わせて追加しない。 |

判断根拠と差分レビューは[20261028-r06-delta-review.md](reviews/20261028-r06-delta-review.md)に記録する。

その後の実装・作業過程の確認は[stagingチェックポイントレビュー](reviews/20261028-staging-checkpoint-review.md)を参照。最新の残課題と依存関係は[計画書11.5](../../261014/plan_mlops.md#115-2026-10-28-stagingチェックポイント後の残課題)を正規の一覧とする。

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

## 実装状況（2026-10-28）

- `create_capital_evaluation_protocol.py`は`--incumbent-version`を省略すると、incumbentを持たないprotocol schema 2を生成する。そこには上記gate policy本体、policy ID、policy SHA-256を固定する。versionを指定した既存経路はschema 1として後方互換で残している。
- 評価消費台帳はincumbentがないvalidation試行を`incumbent_artifact_sha256: null`で記録できる。baseline-only candidate trainerはMLflow Registryを参照せずfitし、validationの行別candidate/baseline予測・件数・row-key hash・policy ID/SHAを保存する。
- final-evaluation scriptは登録incumbentをロードせず、同じ選定artifactをhash検査して使う。validation証拠を行単位で再計算・照合した後に限りfinal窓を台帳へ開始記録し、final MAE/RMSE gateを別に算出する。最終報告にはsegment別件数/誤差差、validation/final/operationalの状態と、運用測定・人の承認がない間の`deployment_permission=blocked`を含める。
- 新gate関数は欠落・非有限・負値指標を`not_evaluable`として不合格にする。テストではMAEの同値拒否、RMSE同値許容、RMSE悪化拒否、baseline-onlyの行別証拠照合も確認した。
- `runtime init`、`deploy promote`、Capital APIのregistered-model loaderは、quality/validation/final-accuracy/operational各status、既知policy ID/SHA、`deployment_permission=eligible`の全てを要求する。欠落/不合格ならdiagnosticやrollback指定でも通さず、UCIの既存gateは変更しない。したがって現時点でCapital版が登録されていても、新gateタグがないモデルは配信できない。
- 合成dataからimmutable candidate bundleを作り、baseline-only final-evaluation CLI全体を通すintegration testを追加した。registry loaderを失敗するmockにし、非呼び出し、final row artifact、gate/blocked permission report、ledger start/finishを確認する。これは実データ評価ではない。
- 運用測定の内部処理はlocal endpoint・Capital profile/target/model/version/candidate ID/artifactの事前照合、50回の暖機、逐次1,000要求、接続再利用、timeout 10秒・自動retryなし、各要求の記録を実装済み。ただし入力出典とDB固定の統合が未完了のため、CLIによる測定は停止中。内部処理を直接実行して数値条件を満たしても、運用gateは`not_evaluated`とする。過去の合成1,050要求は実候補の運用測定ではない。
- reportには暖機を含む全1,050要求のrequest ID・対象時刻・status・latency・予測値・data-version hash・エラー、ID集合hash、p95/p50/max、API model/data/artifact identity、ホストOS/CPU/RAM/Python/package版、取得できたCapital stackのcontainer resource limits・image・`docker stats`・Docker engine版、`/ready`のDB疎通結果を保存する。別ホスト負荷は自動計測しないので手動記録が必要。p95はnearest-rank法で算出する。
- **staging経路の実装状況:** `scripts/register_capital_candidate.py`は一致するimmutable candidate/final reportと固定bootstrap policyを検査してからMLflowに登録する。candidate検証ではvalidation/final gateを指標から再計算し、final row CSVのhash・件数・時刻・ラベル到着時刻・保存済み予測からのMAE/RMSE再計算を確認する。さらに完了した評価台帳の期間・protocol/candidate SHA・report SHAが一致することを要求する。version tagはvalidation/final accuracyを`passed`、operationalを`not_evaluated`、deployment permissionを`blocked`に固定し、selection/final report hashとcandidate IDを保存する。通常runtimeの`load_registered_model`や`deploy promote`は変更していない。登録後に生成する`data/runtime-capital-staging/staging.env`は版・ID・raw/registered artifact hash・report hashだけを保持し、認証情報を含まない。`compose.capital-staging.yaml`のstaging APIは`127.0.0.1:8110`だけに公開し、Caddyから独立させ、prediction用DBも専用volumeへ分ける。MLflowは既存の隔離Capital MLflowを参照する。staging loaderはREADY状態、profile/target、固定policy、report/candidate/artifactのidentity、`deployment_permission=blocked`を確認する。運用benchmarkも候補IDをAPIの`/ready`・`/version`・全prediction responseで照合する。
- 合成候補と使い捨てComposeのMLflow/PostgreSQL/APIで、登録の再実行、DB分離、HTTP/offline一致、candidate切替後の再送、tag不一致時のready拒否と復帰、通常loader/runtime init/promoteの拒否を確認した。promoteの入口は合成proxyで、実Caddy切替は未試験。全1,050要求の[合成記録](reviews/20261028-synthetic-staging-benchmark.json)と[実行スクリプト記録](reviews/20261028-synthetic-staging-harness.md)を保存し、test環境は破棄した。実候補の登録・計測や実データのvalidation/final評価は未実施で、新評価窓は消費していない。
- 統合試験で、旧Docker imageがprofile設定を`/usr/local/lib/python3.12/configs/`へ探してAPIの起動に失敗したため、Dockerfileで`PYTHONPATH=/app/src`を指定した。修正前に作成したimageでは修正は反映されないため、次に起動するときは対象imageをbuildし直す。stagingの予測行にはcandidate IDを保存し、候補変更後も同じrequest IDの再送には元のIDを返す。旧予測行の候補IDは`null`として扱う。
- 現在は候補証拠とCSV/manifestを照合し、training splitの行hash・件数と対象168時刻を検査できる。DB再照合と測定中の固定、API応答の出典照合は未完成。以前の合成benchmarkは最後の2時刻が学習締切時にラベル未到着であり、training split内の入力条件を満たした証拠には使えない。

## API運用測定の固定条件

精度gateとは別の運用gateを作る。対象はcandidate専用のstaging API入口`http://127.0.0.1:8110/predict`。モデル・DBが準備済みで、readyとprofile/model/candidate/artifact識別が確認できてから測定する。

- 暖機50要求を除外し、その後1,000要求を同時数1で逐次送信する。HTTP clientは1つで接続を再利用し、自動再試行は0回、各要求のtimeoutは10秒。
- 暖機で失敗した場合は準備失敗として記録し、その実行を合格扱いしない。
- 暖機を含む全要求に新規のrequest IDを付ける。同じIDの再送で保存済み結果を返す処理を計測対象にしない。benchmark IDで監査行を識別し、件数と保存先をreportへ残す。
- 入力は学習期間内の固定168時間の列を順番に繰り返す。対象週・順序・body生成規則を実行前に保存し、validation/後続期間は使わない。正解との精度計算はこのbenchmarkでは行わない。
- latencyは送信直前から応答body受信完了までの単調時計による経過時間。1,000件すべての計測値を保存し、p95はnearest-rank法（順位`ceil(0.95*N)`）で計算する。失敗した要求を除外・成功要求で置換しない。途中中断はincompleteで不合格。
- HTTP 200以外、接続失敗、timeout、不正JSON、異なるrequest/profile/model/artifact識別、非有限・負の予測をエラーに数える。単にHTTP 2xxであるだけでは成功としない。
- エラー0件かつp95 250ms以下で合格。CPU/RAM、Docker資源制限、OS/Python/依存とイメージ、model/data hash、DB開始状態、他負荷の有無、p50/p95/max、失敗数を記録する。条件変更時は別benchmarkとして扱う。

**現在CLIからの測定は停止中。** 次のコマンドは、候補とデータを照合し、指定した168時間の各行が学習に使われ、各ラベルが学習cutoffまでに到着したことだけを確認する。APIリクエストは送信しない。モデル登録版、DB状態、測定時のデータ出典はこの入力確認だけでは保証しない。例の期間が有効かどうかは選択した候補で検査される。

```bash
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/benchmark_capital_runtime.py \
  --candidate-id '<candidate reportに記録された64桁ID>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --start '2026-04-01 00:00:00' \
  --end '2026-04-08 00:00:00' --check-input-only
```

実候補のblocked登録とfinal reportが揃った後、運用測定の条件だけを先に凍結するコマンドを用意した。候補・final report・学習行・CSV/manifestを検証し、Capital MLflowの指定version/tagと、ダウンロードしたregistered artifactのハッシュを照合する。入力168時間、要求数、順序、タイムアウト、p95条件、registry接続先、モデル・データ・protocol・reportのハッシュ、取得可能な資源情報を新規JSONに保存する。予測要求は送信しない。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/prepare_capital_operational_plan.py \
  --candidate-id '<candidate-id>' --model-version '<登録版番号>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --start '2026-04-01 00:00:00' --end '2026-04-08 00:00:00' \
  --output 'data/evaluations/capital-bikeshare/operational-plans/<plan-name>.json'
```

計画ファイルの上書きは拒否し、内容のSHAを保持する。自己ハッシュは偶発的な変更の検出用であり、本人が内容とハッシュを両方書き換えた場合の真正性は証明しない。別途、最初の要求前にplanの内容とファイルのSHAを試行台帳に記録し、候補ごとの同時実行を防ぐ部品を追加した。途中終了や失敗後の再試行には、担当者・理由と次に使用するplanのSHAを記録する。記録済みのplanと異なるファイルによる再試行は拒否する。要求開始後に証拠を残せず中断した場合は証拠不足を記録する。`completed`は測定処理の正常終了時に初めて確定し、台帳内の状態だけでは運用gateや配信の合格証拠にならない。

台帳の内容は `.venv/bin/python scripts/operational_attempt_ledger.py list` で確認できる。失敗・中断した試行の原因と部分report・監査記録を調査してから、再試行を認める場合に限り、`review-retry --candidate-id <候補ID> --operator <担当者> --reason <理由> --next-plan <planのパス>` を同じスクリプトに渡す。この操作は候補の配信承認ではない。ロックは同じファイルシステムとロック先を使う協調プロセス間に限られ、台帳を直接変更できる人による改変を防ぐものではない。

測定内部処理は、試行IDとartifact SHAを指定し、各要求前のロック確認・台帳への要求開始記録、応答の出典確認と監査照合を行う接続部を受け取れる。接続部を使う場合は、要求や出典確認の失敗時に後続要求を止め、送信前の失敗を送信済みとして数えない。ただしplanの再照合から台帳・DBロック・測定・監査・report確定までを組み立てる実行経路は未完成で、この接続部だけではgateは合格にならない。resourceの取得不能や他の負荷の未測定も合格証拠にしない。現在の測定CLIは停止中。

測定前にblocked registrationを作成し、生成された`data/runtime-capital-staging/staging.env`を使って、staging DBへのseed、staging API起動の順に行う。seedは検査済みCapital profileのCSV/manifestを使い、専用PostgreSQLのloopback port 55434へ記録する。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/register_capital_candidate.py \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json'

docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml \
  up -d postgres-staging

set -a
. ./.env
set +a
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/seed_capital_staging.py \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --model-version '<登録した版番号>'

docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml \
  up -d --build api-staging
curl -fsS http://127.0.0.1:8110/ready
```

登録先はlocalhostのport 5001にあるCapital用MLflowに限定する。精度gateが不合格、またはreport/artifact identityが一致しない場合は登録しない。staging loaderは配信許可を`blocked`のまま保ち、`runtime init`/`deploy promote`は全gateと明示的な承認を引き続き要求する。診断flagや環境変数でこれらの確認を迂回しない。benchmark reportは`data/reports/capital-bikeshare/`へ新しいファイルとして保存し、既存reportを上書きしない。

MLflowでは実験のtracking先とモデルregistry先を別々に設定できる。このstaging経路の登録、seed、loader、plan作成は、registry先も指定したtracking先と同一に固定する。異なる`MLFLOW_REGISTRY_URI`が環境にある場合も、別のregistryへ誤って登録・参照しないための設定である。

seedは候補・最終reportを再検証し、指定したMLflow版のtag、CSVとmanifestのSHA-256を候補に照合する。DBの既存観測は時刻・台数・利用可能時刻・データSHAまで比較し、余分な行や不一致があればseed全体を拒否する。PostgreSQLでは検査と挿入を同じトランザクション内でロックする。上記seedコマンドに`--verify-only`を付けると、観測を追加せずに全行が揃っていることも検査する。これは照合した時点のDB状態を示し、確認後の変更を防ぐものではない。benchmark側での再照合、training split、APIデータ出典の検証は未対応であり、実候補のbenchmarkを実行する前に対応する。

測定中のロックと出典照合に使う部品は`capital_measurement_guard.py`に追加済み。PostgreSQLで観測表の照合とロックを同じtransactionに保持し、候補のlagから期待hash・特徴量を求め、応答と監査行を検査する。失われた接続、重複IDや監査の過不足も拒否する。次の再現用スクリプトは一時PostgreSQLと合成モデルのFastAPIでこれらを試す。通常のMLflow・staging image・1,050件測定は含まない。

```bash
.venv/bin/python scripts/test_capital_snapshot_postgres.py
```

このスクリプトはUnix socket経由のローカルDockerと、取得済み`postgres:17.6-bookworm` imageが必要。ランダムな名前とloopback自動port、使い捨てパスワードを用い、作成したcontainerを最後に停止・削除する。既存サービスのDBは使わない。これらの部品はまだbenchmark本体に接続していないため、CLIは引き続き入力確認専用。

計測中にCtrl-Cで止めても、送信済み要求を含むpartial reportを保存し、`completed=false`・運用gate failedとして扱う。完了しなかった計測結果を成功としてMLflowへ記録する経路はない。

この結果は当該PC・逐次要求の運用目標への適合を示す。同時負荷への耐性や本番SLOの達成を示すものではない。

## 残る作業と実行順序

1. **R04/P0: 完了。** Python `tzdata==2026.5`のゾーンファイルを集計・manifest検査・APIで直接読み込み、OSのtimezone検索順に依存しない。manifest schema 3にゾーン名・package版・TZifバイト数・SHA-256を保存する。DST切替テストと新しいmanifestでの実データ再集計・hash検証を行った。詳細は[`data-profiles.md`](data-profiles.md)と`work_log.md`を参照。
2. **R06/P1:** incumbentなしbootstrap経路とcandidate/final精度gateを合成試験する。実装済み。ここで合格した合成結果を実データの合格根拠に使わない。
3. **R06/P1・合成統合検証済み:** 使い捨てCompose/MLflow/PostgreSQLと人工model/evidence/観測で、登録、拒否、API/DB、parity、再送を確認しtest resourceを破棄した。実Caddy切替は未試験。
4. **R06/P1・新データ前:** seed時の候補・CSV/manifest・registry・既存DB行の照合と投入後の完全性検査を実装。合成SQLiteと使い捨てPostgreSQLで検証した。benchmark側でのDB再照合、凍結training splitの検査とAPIデータ出典の照合は未完了。
5. **R06/P1・新データ前:** operational report hash/statusとcandidate identityをexact versionへ記録する経路を実装・synthetic testする。全gate passedかつ理由付き手動承認がある時だけ`eligible`、failed/missing/mismatchならblockedのままにする。
6. **R06/P1:** 月境界buffer規則を確定→未取得の原本を取得→新pathのmanifest schema 3監査→非重複を確認→protocol freeze→validationを一度→合格時のみ同じartifactで独立final評価を一度行う。
7. **R05/R06/P1・実候補後:** 実candidateをblocked登録し、Capital隔離PostgreSQLへseed、staging APIを起動してartifact loading、DB分離、offline/API parity、通常deploy拒否を確認する。50 warmup/1,000 sequential benchmarkを実測し、結果hash/statusをそのversionへ記録する。
8. **R07〜R10:** 監視参照とPSI、障害・復旧、配信台帳と進行表示、採否記録・デモを計画書に従って完了する。

追加のユーザー確認を開始条件にはしない。精度gate・運用gateの実装/証拠、artifact整合性、入力契約、復旧試験、理由付き手動承認が揃うまでは、Capital profileはmigration-onlyを維持する。
