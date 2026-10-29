# Capital Bikeshare初回モデルの評価基準

更新日: 2026-10-30

ユーザーから3点の判断を委任されたため、以下を採用した。仕様上の確認待ちは解消したが、実データのprotocol凍結・評価・運用測定は未完了。実装済みの範囲は「実装状況」で区別し、判断前の結果をこの基準で合格扱いに変更しない。

| 判断対象 | 採用する基準 | 理由 |
|---|---|---|
| 予測精度 | 候補のMAEがbaselineを厳密に下回り、RMSEもbaseline以下 | 平均的な誤差を改善し、大きな誤りを重く数えるRMSEでも悪化を許さない。個々の最大誤差が改善する保証ではない。絶対値の業務目標がないため同じ行のbaselineと比較する。 |
| API運用目標 | 暖機後、逐次1,000要求でエラー0件、p95 250ms以下 | ローカル環境での学習用目標として採用する。250msは事前に選んだ工学上の目標で、過去の実測結果から作った閾値ではない。 |
| weekday/weekend・通勤時間帯 | 件数とMAE、baselineとの差を必ず記録し、個別gateは設けない | 区間ごとの業務上の許容誤差が未定義で、サンプル数にも差がある。個別の閾値を結果に合わせて追加しない。 |

判断根拠と差分レビューは[20261028-r06-delta-review.md](reviews/20261028-r06-delta-review.md)に記録する。

その後の実装・作業過程は[2026-10-30 checkpoint差分レビュー](reviews/20261030-checkpoint-review.md)を参照。最新の残課題・依存関係・難易度・Sol/Luna担当推奨は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)を正規の一覧とする。

同監査のA01/A02では、承認後のfinal report/plan SHA変更と負のp95を通常配信判定が受理すること、監視・復旧証拠なしでも合成approveが成立することを確認した。review/approveの実装は配信前条件の全面完成を意味しない。実Capital候補のeligible化・通常配信へ進む前にT01/T02を修正し、必要証拠の欠落・不一致を拒否する。

## 前提とデータの状態

- Capital Bikeshareは`capital-trip-starts-hourly-v1`、UCIは`uci-bike-sharing-cnt-dataset-local-hour`。初回Capitalモデルには同target incumbentがないため、比較対象は同targetのhistorical baselineにする。
- Capitalの評価済み期間（2025年上期validation、2025年7月〜2026年4月の後続評価）は新しいvalidation/後続評価に再利用しない。過去の期間を学習材料に含める場合も、未使用の評価期間が増えたとは扱わない。
- `timestamp + 3時間`は履歴再生用のラベル遅延仮定。月次履歴が実際に3時間で公開されることを意味しない。
- 2026-10-30に公式[system-data page](https://capitalbikeshare.com/system-data)とS3 archive objectを再確認した。検索結果のbucket index表示は202605までだったが、直接HTTP HEADで202609は200、202610は404 Not Foundと確認した。202606〜202609は取得済みで、ZIPとCSV memberを検査した。4 ZIPは`data/raw/capitalbikeshare/monthly/`にローカル保存し、各bytes/SHAは[`work_log.md`](../work_log.md)へ記録した。これらは公開ソース原本で、Gitには入れない。
- 月次archiveが隣月に始まるtripを含む実績がある。新snapshotには対象範囲・隣月bufferの採否と理由を事前記録する。9月末までの評価に10月bufferが必要と判断した場合は公開を待ち、結果を見て期間を短縮しない。snapshotへの収録を超える母集団の完全性は保証しない。
- 6〜9月だけの一時diagnostic aggregationは6/1〜9/30の2,928時間で欠測・timestamp重複0。さらに既存の5月archiveを含む5〜9月diagnosticも3,672時間で欠測・timestamp重複0、読込3,224,860行中、この5 archive内でID重複0だった。ただし202604 archiveと202610 archiveを含まず、9月末の重複/後続境界を確認できない。両CSV/manifestは`/tmp`の診断出力であり、candidate・validation・final評価に使わない。

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

## 実装状況（2026-10-29更新）

- `create_capital_evaluation_protocol.py`は`--incumbent-version`を省略すると、incumbentを持たないprotocol schema 2を生成する。そこには上記gate policy本体、policy ID、policy SHA-256を固定する。versionを指定した既存経路はschema 1として後方互換で残している。
- 評価消費台帳はincumbentがないvalidation試行を`incumbent_artifact_sha256: null`で記録できる。baseline-only candidate trainerはMLflow Registryを参照せずfitし、validationの行別candidate/baseline予測・件数・row-key hash・policy ID/SHAを保存する。
- final-evaluation scriptは登録incumbentをロードせず、同じ選定artifactをhash検査して使う。validation証拠を行単位で再計算・照合した後に限りfinal窓を台帳へ開始記録し、final MAE/RMSE gateを別に算出する。最終報告にはsegment別件数/誤差差、validation/final/operationalの状態と、運用測定・人の承認がない間の`deployment_permission=blocked`を含める。
- 新gate関数は欠落・非有限・負値指標を`not_evaluable`として不合格にする。テストではMAEの同値拒否、RMSE同値許容、RMSE悪化拒否、baseline-onlyの行別証拠照合も確認した。
- `runtime init`、`deploy promote`、Capital APIのregistered-model loaderは、quality/validation/final-accuracy/operational各status、既知policy ID/SHA、`deployment_permission=eligible`を要求する。今回、後述の独立review・別人承認の記録と、exact model version/candidate/artifact/report hashが一致することも通常loader/runtime/promoteで検証するよう追加した。欠落/不合格ならdiagnosticやrollback指定でも通さず、UCIの既存gateは変更しない。したがって現時点でCapital版が登録されていても、新gateタグがないモデルは配信できない。
- 合成dataからimmutable candidate bundleを作り、baseline-only final-evaluation CLI全体を通すintegration testを追加した。registry loaderを失敗するmockにし、非呼び出し、final row artifact、gate/blocked permission report、ledger start/finishを確認する。これは実データ評価ではない。
- 運用測定はlocal endpoint・Capital profile/target/model/version/candidate ID/artifactを事前照合し、DB観測snapshotをlockした状態で50回の暖機と逐次1,000要求を送り、各応答の出典・DB監査を確認する。最新の隔離system testで、現行CLIからmeasurement runner・audit・numeric recheckまでを合成データで通し検証した。CLIは`--run-measurement --plan ...`を明示した場合に実行可能。測定処理完了・数値合格は運用gate合格と分け、独立証拠レビューが済むまでは`not_evaluated`、deploymentは`blocked`のままにする。
- reportには暖機を含む全1,050要求のrequest ID・対象時刻・status・latency・予測値・data-version hash・エラー、ID集合hash、p95/p50/max、API model/data/artifact identity、ホストOS/CPU/RAM/Python/package版、取得できたCapital stackのcontainer resource limits・image・`docker stats`・Docker engine版、`/ready`のDB疎通結果を保存する。別ホスト負荷は自動計測しないので手動記録が必要。p95はnearest-rank法で算出する。
- **staging経路の実装状況:** `scripts/register_capital_candidate.py`は一致するimmutable candidate/final reportと固定bootstrap policyを検査してからMLflowに登録する。candidate検証ではvalidation/final gateを指標から再計算し、final row CSVのhash・件数・時刻・ラベル到着時刻・保存済み予測からのMAE/RMSE再計算を確認する。さらに完了した評価台帳の期間・protocol/candidate SHA・report SHAが一致することを要求する。version tagはvalidation/final accuracyを`passed`、operationalを`not_evaluated`、deployment permissionを`blocked`に固定し、selection/final report hashとcandidate IDを保存する。登録後に生成する`data/runtime-capital-staging/staging.env`は版・ID・raw/registered artifact hash・report hashだけを保持し、認証情報を含まない。`compose.capital-staging.yaml`のstaging APIは`127.0.0.1:8110`だけに公開し、Caddyから独立させ、prediction用DBも専用volumeへ分ける。MLflowは既存の隔離Capital MLflowを参照する。staging loaderはREADY状態、profile/target、固定policy、report/candidate/artifactのidentity、`deployment_permission=blocked`を確認する。運用benchmarkも候補IDをAPIの`/ready`・`/version`・全prediction responseで照合する。
- 2026-10-29、最新ソースと現行計測CLIを使う使い捨てCompose/MLflow/PostgreSQL/APIのsystem testを完了した。744観測seed、blocked登録の再実行、API ready・artifact load、offline/API prediction差0、retry同一応答・changed input 409、runtime init拒否、1,050要求と同数のDB監査照合、ledger-linked numeric recheckを確認し、資源をcleanupした。測定p95 103.258msは人工モデル・人工データの結果で、正式gateは`not_evaluated`、deploymentは`blocked`のまま。ログと前段のデバッグ結果は[`work_log.md`](../work_log.md)に記録した。実Caddy切替と実データのvalidation/final評価は未実施で、新評価窓は消費していない。
- 統合試験で、旧Docker imageがprofile設定を`/usr/local/lib/python3.12/configs/`へ探してAPIの起動に失敗したため、Dockerfileで`PYTHONPATH=/app/src`を指定した。修正前に作成したimageでは修正は反映されないため、次に起動するときは対象imageをbuildし直す。stagingの予測行にはcandidate IDを保存し、候補変更後も同じrequest IDの再送には元のIDを返す。旧予測行の候補IDは`null`として扱う。
- 候補証拠・CSV/manifest・training splitの行hash/件数・対象168時刻を検査する。measurement runnerでDB snapshotの再照合とlock、API応答と監査行の出典照合を接続し、合成PostgreSQLで通し検証した。未使用実データでの検証は別途必要。以前の合成benchmarkは最後の2時刻が学習締切時にラベル未到着であり、training split内の入力条件を満たした証拠には使えない。

## API運用測定の固定条件

精度gateとは別の運用gateを作る。対象はcandidate専用のstaging API入口`http://127.0.0.1:8110/predict`。モデル・DBが準備済みで、readyとprofile/model/candidate/artifact識別が確認できてから測定する。

- 暖機50要求を除外し、その後1,000要求を同時数1で逐次送信する。HTTP clientは1つで接続を再利用し、自動再試行は0回、各要求のtimeoutは10秒。
- 暖機で失敗した場合は準備失敗として記録し、その実行を合格扱いしない。
- 暖機を含む全要求に新規のrequest IDを付ける。同じIDの再送で保存済み結果を返す処理を計測対象にしない。benchmark IDで監査行を識別し、件数と保存先をreportへ残す。
- 入力は学習期間内の固定168時間の列を順番に繰り返す。対象週・順序・body生成規則を実行前に保存し、validation/後続期間は使わない。正解との精度計算はこのbenchmarkでは行わない。
- latencyは送信直前から応答body受信完了までの単調時計による経過時間。1,000件すべての計測値を保存し、p95はnearest-rank法（順位`ceil(0.95*N)`）で計算する。失敗した要求を除外・成功要求で置換しない。途中中断はincompleteで不合格。
- HTTP 200以外、接続失敗、timeout、不正JSON、異なるrequest/profile/model/artifact識別、非有限・負の予測をエラーに数える。単にHTTP 2xxであるだけでは成功としない。
- エラー0件かつp95 250ms以下で合格。CPU/RAM、Docker資源制限、OS/Python/依存とイメージ、model/data hash、DB開始状態、他負荷の有無、p50/p95/max、失敗数を記録する。条件変更時は別benchmarkとして扱う。

CLIには読み取り専用の入力確認モードと、frozen planに従う隔離staging測定モードがある。まだ未使用実データのcandidate/final reportがないため、現段階で実candidateの測定は行わない。次のコマンドは候補とデータを照合し、指定した168時間の各行が学習に使われ、各ラベルが学習cutoffまでに到着したことだけを確認する。APIリクエストは送信しない。モデル登録版、DB状態、測定時のデータ出典はこの入力確認だけでは保証しない。

```bash
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/benchmark_capital_runtime.py \
  --candidate-id '<candidate reportに記録された64桁ID>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --start '2026-04-01 00:00:00' \
  --end '2026-04-08 00:00:00' --check-input-only
```

実測モードは、未使用データによるcandidate/final評価、blocked登録、staging DBのseed、staging APIのready、固定条件のplan作成がすべて終わった後に限り使用する。実行する場合は秘密値を表示・記録せずに読み込む。

```bash
set -a
. ./.env
set +a
export DATA_PROFILE_ID=capital-bikeshare
export DATABASE_URL="postgresql://bike_app:${APP_DB_PASSWORD}@127.0.0.1:55434/bikeops"
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001

.venv/bin/python scripts/benchmark_capital_runtime.py \
  --run-measurement \
  --candidate-id '<candidate-id>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --plan 'data/evaluations/capital-bikeshare/operational-plans/<plan>.json'
```

runnerは専用のlocalhost PostgreSQL (port 55434)以外を拒否する。完了後の数値statusは独立証拠レビューと別であり、reviewが済むまでは運用gateをnot_evaluated、deploymentをblockedに保つ。

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

測定内部処理は試行IDとartifact SHAを指定し、各要求前のロック確認・台帳への要求開始記録・応答の出典確認・監査照合を行う。これらをplanの再照合、DBロック、測定、監査、reportに接続した。現行CLIから使い捨てCompose/MLflow/PostgreSQL/APIのsystem testを行い、合成1,050行の監査・数値再確認・resource captureまで検証した。台帳の`completed`は測定処理が完了した状態で、運用gateは別途`not_evaluated`、deploymentは`blocked`のまま。実candidateの評価は未実施。

試行開始後、台帳と同じplan・候補・学習window・CSV/manifest・登録version・登録artifactを再照合し、確認済みデータをメモリへ読み込む。登録先はplanに固定されたCapital MLflowで、取得前後の登録情報と、読込み前後のCSV・manifestも検査する。DBロック解放や台帳保存が失敗した際、台帳に採用されないreportファイルが残りうるため、reportファイルの存在だけで完了と判断しない。`.venv/bin/python scripts/inspect_capital_operational_reports.py --candidate-id <候補ID>` は既存のロックを取得したうえで、台帳とplan/reportのSHA・IDを読み取り照合し、紐付いたreport、孤立ファイル、不足・破損、中断を列挙する。実行中やロックが存在しない場合は点検を拒否し、reportの採用や修復は行わない。部分証拠の回復と原因の確定は別途必要。

測定の失敗reportには、出典を確認できた応答のIDと件数、処理途中の要求IDを`partial_evidence`に保存する。処理途中の要求は送信されたか、または後からDBに保存されるかが不明なため、未送信や失敗確定とは扱わない。これらはメモリ上の情報から記録するため、強制終了や電源断のときに失われる場合がある。

失敗reportに処理途中の要求が残った場合、`DATA_PROFILE_ID=capital-bikeshare`と`APP_DB_PASSWORD`を環境変数に設定したうえで、`.venv/bin/python scripts/inspect_capital_pending_audit.py --candidate-id <候補ID> --attempt-id <試行ID>`を実行すると、隔離staging DBのそのIDだけを読み取り専用で照会できる。見つからない場合は照会時点で未確認という意味であり、その後に記録される可能性を排除しない。見つかった場合も候補・版・artifact等の識別確認にとどまり、入力や予測の完全な監査、再送、合格判定、配信許可は行わない。使い捨てPostgreSQLで、別transactionによる記録前後の照会を検証した。実APIのtimeoutと遅延commitの再現は未完了。

保存された測定行を固定planと照合し、要求順・時刻・ID・件数・p95を再計算する独立部品を追加した。`.venv/bin/python scripts/recheck_capital_measurement.py --candidate-id <候補ID> --attempt-id <試行ID>`は候補・台帳の既存ロックを取得し、台帳が参照するplanとreportのSHAを確認してから数値を再計算する。これは単体では記録された数値だけを点検し、入力出典、DB監査、retryの有無、資源、記録の真正性や配信可否を証明しない。運用report・入力出典・DB audit・資源情報とexact registry version/artifactを一緒に再照合してから証拠タグを記録するCLIを追加した。

測定が`completed`になり、独立した数値再確認が終わった後は、次で同じ候補・試行IDのreport hashと各statusをCapital MLflowのexact model versionへ記録できる。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/record_capital_operational_evidence.py \
  --candidate-id '<候補ID>' --attempt-id '<完了した試行ID>'
```

このコマンドはledger/候補の既存lockを保持してfrozen plan・linked report SHA・全1,050要求のp95再計算・DB監査・input provenance・前後のhost/container resource context・MLflow version/artifact identityを照合する。同じ証拠の再実行は同じtagを確認して成功するが、別report/attemptで既存tagを上書きしない。tag書込み後も`operational_gate_status=not_evaluated`と`deployment_permission=blocked`を維持する。タグは証拠の参照であり、独立review、運用gate合格、理由付き手動承認、eligible化の代わりにはならない。合成system testで成功・冪等再実行・異なるhashの拒否を確認した。組織的な承認・監査サービスやローカルファイルを直接変更できる人への耐性は提供しない。

review/approveの開発用操作は次の2段階で記録する。実candidateでの使用には上記T01/T02の修正と、監視・復旧を含む必要証拠の完成が先に必要である。reviewerが候補・final report、凍結plan、measurement report、監査/再計算ログと復旧証拠を確認してから、意図した担当者・理由で記録する。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/record_capital_deployment_decision.py review \
  --version '<exact model version>' --reviewer '<reviewer name>' \
  --decision passed --reason '<evidence reviewed and why the gate passes>'
```

reviewがpassedでも配信許可は`blocked`のまま。別のapproverが理由を添えて次のコマンドを実行したときだけ、registryの`deployment_permission`がeligibleになる。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/record_capital_deployment_decision.py approve \
  --version '<exact model version>' --approver '<different approver name>' \
  --reason '<why this exact version is authorized>'
```

review/approvalはexact version、candidate、registered artifact、operational report hashへ結び付き、理由を含む決定レコードのSHAをtagに保存する。成功reviewにはoperational measurement=`passed`かつp95 250ms以下、出典・監査・再計算・資源context成功を必須にする。runtime loader、`runtime init`、`deploy promote`も同じ結び付きを検査する。reviewerとapproverは異なる名前でなければならない。これはローカル学習用の証跡であり、名前は利用者が自己申告する。GitHub/MLflowのIAM、電子署名、独立した組織監査を代替しない。現時点では実candidateがないため、このコマンドは実行していない。合成testは判定拒否・承認経路をmock registryで確認しただけで、実MLflowのeligible遷移や実配信は未検証。

隔離system testは `.venv/bin/python scripts/capital_system_harness.py --run-synthetic-system` で実行する。人工データで候補を作り、使い捨てCompose projectへ登録・seed・API起動・計測・監査・数値再確認・exact version tag記録まで通す。タグの同値再実行、異なるreport hashの拒否、拒否後もblockedを維持することを検査する。ランダムなproject名・一時credential・所有labelを用い、自分が作ったcontainer/network/volume/imageだけを削除する。port 5001・8110・55433・55434が使用中なら既存サービスを止めずに失敗する。test成功は実データの精度やdeployment承認を示さない。Docker imageのbuildと依存取得が必要になる場合がある。

GitHub Actionsでは`.github/workflows/bike-mlops-contracts.yml`の`synthetic-system` jobが合成system testとPostgreSQL snapshot-lock testを実行し、ログを14日間のartifactとして保存する。最初の2 runはcleanup失敗を`pipefail`なしのpipelineが隠していた。修正前のrun `40876827423`はunit job成功、synthetic job失敗となり、偽の総合successを防いだ。保存ログでは合成測定・独立再確認まで成功した一方、Dockerは同じimage IDが複数repository名に参照されているためID指定の削除を拒否していた。単純にIDを重複除去するだけでは直らないため、ownership labelとrandom Compose project prefixを確認し、専用`repository:tag`だけを削除する処理へ修正した。commit `6079903`のGitHub run [`40879692956`](https://github.com/kenobix/Claude_practice/actions/runs/40879692956)では213 unit tests、synthetic system test、cleanup後のresource検査、PostgreSQL snapshot-lock testがすべて成功した。PostgreSQL testは非superuserの使い捨てDBで、観測更新の阻止、lock中のAPI予測/audit、source hash一致、行数/ID/audit/data/hashの不一致拒否、lock timeout、接続喪失時の拒否、context終了時のlock解放を確認した。artifact `bike-mlops-docker-integration-logs`（ID `12898819750`、SHA-256 `4e53e8e84e17c4d12e9d86ea7bf1f65251728468d65ea7c41908202223c6dbd2`）に合成・PostgreSQLログが保存されている。

tag-scoped cleanupを使う以前の版では、blocked synthetic model version 1の登録、744観測seed、API/offline prediction差0、1,050件の逐次要求とaudit、数値再確認、exact version evidence tagsの再実行/競合拒否まで通り、`cleanup_complete`を返した。p95 `127.520839 ms`は人工データ・この実行環境の値で、Capital実候補や本番SLOの合格ではない。さらにexact codeに、image参照名がランダムCompose project prefixで始まる場合だけ削除を認める安全確認を足した。このexact codeのlocal full runはpreflightでport `55433`の`EADDRINUSE`を検出し、Dockerを起動せず終了した。事後の読み取り照会でも該当ハーネスprojectのDocker resourceはなく、既存listenerの正体はこの環境から特定できなかった。既存サービスは停止していない。全213 tests、harness 23 tests、compileall、pip check、workflow YAML parse、diff checkが成功し、さらに同一commitのGitHub Actions run `40879692956`でfull synthetic pathとPostgreSQL testが成功した。synthetic candidate `bcd2842892b6423125a014003d8e79b3b6dd19120a2f4428919e035595ceeb6c`は744観測、1,050要求/audit行、offline/API差0、独立数値照合一致、p95 `26.522195 ms`。`cleanup_complete`後に専用projectのresourceが残らないことも検証した。これは人工fixtureとGitHub runnerの実行結果で、Capital実データの精度や本番SLOの達成を意味しない。正式gateは`not_evaluated`、deploymentは`blocked`のまま。snapshot-lock testは依存install/check後にsystem stepが失敗しても実行する条件を加えた。GitHub Actions固有schema lintは`actionlint`未導入のため未実施。

PostgreSQLの観測snapshot lock、writer競合、接続喪失の使い捨てDB試験は次でも実行できる。イメージ`postgres:17.6-bookworm`がローカルに必要で、テスト自身が一時containerを作成して最後に削除する。

```bash
.venv/bin/python scripts/test_capital_snapshot_postgres.py
```

CI jobではsystem testに続けてこのPostgreSQL試験も実行し、両方のログを同一artifactに保存する。

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

seedは候補・最終reportを再検証し、指定したMLflow版のtag、CSVとmanifestのSHA-256を候補に照合する。DBの既存観測は時刻・台数・利用可能時刻・データSHAまで比較し、余分な行や不一致があればseed全体を拒否する。PostgreSQLでは検査と挿入を同じトランザクション内でロックする。上記seedコマンドに`--verify-only`を付けると、観測を追加せずに全行が揃っていることも検査する。測定runnerは開始時と測定中にもsnapshotを照合・lockし、training splitとAPI由来特徴・監査行を検査する。これは合成system testで確認したが、実候補の証拠はまだない。

測定中のロックと出典照合に使う部品は`capital_measurement_guard.py`に追加済み。PostgreSQLで観測表の照合とロックを同じtransactionに保持し、候補のlagから期待hash・特徴量を求め、応答と監査行を検査する。失われた接続、重複IDや監査の過不足も拒否する。次の再現用スクリプトは一時PostgreSQLと合成モデルのFastAPIでこれらを試す。通常のMLflow・staging image・1,050件測定は含まない。

```bash
.venv/bin/python scripts/test_capital_snapshot_postgres.py
```

このスクリプトはUnix socket経由のローカルDockerと取得済み`postgres:17.6-bookworm` imageが必要。ランダム名、loopback自動port、使い捨てpasswordを使い、作成したcontainerを最後に停止・削除する。実測CLIとは別にlock/監査部品のfailure caseを試す補助scriptである。

計測中にCtrl-Cで止めても、送信済み要求を含むpartial reportを保存し、`completed=false`・運用gate failedとして扱う。完了しなかった計測結果を成功としてMLflowへ記録する経路はない。

この結果は当該PC・逐次要求の運用目標への適合を示す。同時負荷への耐性や本番SLOの達成を示すものではない。

## 残る作業と実行順序

1. **R04/P0: 完了。** Python `tzdata==2026.5`のゾーンファイルを集計・manifest検査・APIで直接読み込み、OSのtimezone検索順に依存しない。manifest schema 3にゾーン名・package版・TZifバイト数・SHA-256を保存する。DST切替テストと新しいmanifestでの実データ再集計・hash検証を行った。詳細は[`data-profiles.md`](data-profiles.md)と`work_log.md`を参照。
2. **R06/P1:** incumbentなしbootstrap経路とcandidate/final精度gateを合成試験する。実装済み。ここで合格した合成結果を実データの合格根拠に使わない。
3. **R06/P1・CI合成system/DB検証完了:** run `40879692956`でblocked登録の冪等性、744行seed、artifact ready、offline/API一致、retry/409、runtime init拒否、1,050要求、DB監査、独立numeric recheck、exact version tags、conflict拒否、project-scoped cleanupを確認。p95 `26.522195ms`は人工fixture/GitHub runnerの数値に限る。snapshot-lock PostgreSQL testも合格し、観測writer阻止、監査/出典/件数/hashの拒否、lock timeout・接続喪失・解放を確認した。artifact ID `12898819750`。正式gateは`not_evaluated`、deploymentは`blocked`。実Caddy切替と実候補評価は未試験。
4. **R06/P1・合成検証済み/実データ前:** seed時・投入後のDB照合、training split/label cutoff/168時刻検査、測定中DB snapshot lock、API応答と監査出典照合を実装し、合成SQLite/PostgreSQLとsystem testで検証した。未使用実データから作る候補での再検証は未実施。
5. **R06/P1・review/approval経路実装・mock検証済み/実候補未検証:** `record_capital_deployment_decision.py review`はexact candidate/version/artifact/reportを含むoperational evidence tagsを検査し、人のreview理由と内容SHAを固定してからoperational gateをpassedにする。reviewだけではblockedを維持する。`approve`は別の名前のapprover、理由、review hashの一致、accuracy/operational gateを要求し、全て有効な場合だけeligibleを最後に書く。通常model loader・runtime init・promoteも決定hashと正確なmodel versionを照合する。部分tag書込後もblockedのまま、同じ内容のretryで完了するmock failure-injection testを追加した。commit `dae897a`のGitHub run `40966168109`で221 testsとsynthetic system/PostgreSQL CIに成功した（[作業ログ](../work_log.md)）。実MLflowでのtag更新と実候補は未確認。名前は認証済みidentityではなく、強いIAM境界でもない。
6. **R01/R02/R06・原本取得済み/10月buffer待ち:** 202606〜202609 archiveはdownload/ZIP integrity/member/hash検査済み。6〜9月、5〜9月の一時aggregationはgrid/count diagnosticsとして成功したが、protocolへ使う全期間manifestではない。診断CSV/manifestは一時`/tmp`に生成して集計値を記録後に削除済み。202610はHTTP 404。202510 archiveに2025-09-30 startsが含まれる過去実績があり、月境界bufferが必要な可能性を無視できないため10月原本公開後に全archiveを別pathへ集計し、manifest schema 3・row accounting・DST/coverage・hashを監査する。既評価Q3/Q4/旧Capital windowは除外し、計画済みsplit/decision time/gateを固定した後にvalidationを一度行う。原本のbytes/SHA-256は[作業ログ](../work_log.md)に記録する。

7. **R05/R06/P1・実候補後:** 実candidateをblocked登録し、Capital隔離PostgreSQLへseed、staging APIを起動してartifact loading、DB分離、offline/API parity、通常deploy拒否を確認する。復帰したCLIで50 warmup/1,000 sequential benchmarkを実測し、report/hash/statusをexact model versionへ記録する。
8. **R07〜R10:** 監視参照とPSI、障害・復旧、配信台帳と進行表示、採否記録・デモを計画書に従って完了する。

最新070b033のCI `40966966672`ではcandidate `ad46f6f2ebbd984010153e613c418e9659a9fbb2fadc12ac4cfe554e8214deb0`のsynthetic-only測定を再現し、744行seed、1,050件要求/監査、offline/API差0、p95 31.070696ms、numeric recheck一致、cleanupとPostgreSQL snapshot-lock test成功を確認した。合成データなので実Capital精度・deployment承認を意味せず、gate=`not_evaluated`、permission=`blocked`を維持した。artifact ID `12938433488`のZIP SHAはGitHub digestと一致した。ただしartifactはsummary中心で要求別recordを含まないため、外部でのp95再計算用の保存をT16へ追加した。Pages run `40966966169`もsuccess。

追加のユーザー確認を開始条件にはしない。精度gate・運用gateの実装/証拠、artifact整合性、入力契約、復旧試験、理由付き手動承認が揃うまでは、Capital profileはmigration-onlyを維持する。
