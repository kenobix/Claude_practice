# 実行・運用手順

初学者向けの用語は[用語集](glossary.md)を参照してください。
データ源を切り替える場合は[データプロファイルとtarget分離](data-profiles.md)を先に確認してください。通常運用は引き続きUCIのCompose projectです。

## 0. Windows/WSLからプロジェクトを開く

Docker DesktopのWSL Integrationを変更した後や、`wsl.exe --shutdown`を実行した後は、Windows PowerShellからUbuntuを明示して起動します。`wsl.exe -l -v`でUbuntuが`Running`と表示されても、Ubuntuのシェルに入っているとは限りません。Docker DesktopがWSLディストリビューションを自動起動することもあるため、作業用シェルは次のコマンドで開きます。

```powershell
wsl.exe -l -v
wsl.exe -d Ubuntu
```

Ubuntuのシェルに入ったら、PowerShellの現在位置（例: `/mnt/c/WINDOWS/system32`）ではなく、WSL内のプロジェクトディレクトリへ移動します。

```bash
cd ~/work/claude_practice/261021
code .
. .venv/bin/activate
docker version
docker compose version
```

今回の環境では、Ubuntuと`docker-desktop`はいずれもWSL 2で、Docker Desktop 4.96.0 / Engine 29.9.1への接続と`hello-world`コンテナの実行が確認できています。`wsl.exe --shutdown`の後は一覧の状態にかかわらず、`wsl.exe -d Ubuntu`で作業用シェルを開いてから続行してください。WSLを終了する前に、開いている作業を保存します。

## 1. 環境とデータ

Python 3.12とパッケージインデックスへの接続を用意します。

```bash
cd /home/kenshin/work/claude_practice/261021
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e '.[dev]'
mlops-bikectl data download
mlops-bikectl data audit
python -m unittest discover -s tests -v
```

監査JSONを読み、時間欠損があるときはラグ特徴量のmissing値として扱われることを確認します。時刻重複・型・値域違反はロード時に失敗させます。

## 2. SQLiteで学習・APIを試す

この節はSQLiteで個別に試す場合の任意手順です。ComposeでMLOps基盤を動かす場合はこの節を飛ばして、節3の手順を使います。固定最終ホールドアウト評価は同じ実験で一度だけ実施してください。

```bash
mlops-bikectl train --evaluate-final-holdout
```

このオプションは指定した初回実験で一度だけ使います。固定ホールドアウトの結果はモデル選択に使わず、後続の学習は`mlops-bikectl train`で検証期間だけを評価します。出力のMLflow `model_version`を記録します。検証ゲート未達の版は候補記録として残りますが、Compose通常昇格には使えません。APIはバージョンを必ず固定して起動します。

```bash
MODEL_VERSION=1 uvicorn bike_mlops.api:app --host 127.0.0.1 --port 8000
```

別の端末で観測を登録し、APIを使います。

```bash
curl -s http://127.0.0.1:8000/ready
curl -s -X POST http://127.0.0.1:8000/observations \
  -H 'content-type: application/json' \
  -d '{"timestamp":"2012-06-29T10:00:00","cnt":120,"available_at":"2012-06-29T13:00:00"}'
curl -s -X POST http://127.0.0.1:8000/predict \
  -H 'content-type: application/json' \
  -d '{"request_id":"manual-example-001","target_hour":"2012-06-30T10:00:00","as_of":"2012-06-30T10:00:00","feature_contract_version":"bike-demand-features-v1"}'
```

実例ではその予測以前の観測履歴を十分に登録します。監査DBへの保存に失敗した予測は応答成功になりません。

## 3. Composeによるローカル運用

Docker EngineとCompose pluginの接続を確認してから進めます。UCIデータをすでに取得・監査済みなら、再ダウンロードは不要です。`.env.example`を`.env`へコピーし、3つのパスワードを互いに異なる十分に長いローカル値へ変更します。`.env`はGit対象外なので、値をGitやチャットに貼り付けないでください。

```bash
test -f .env || cp .env.example .env
code .env
docker compose config -q
docker compose up -d --build postgres mlflow
docker compose ps
```

`data/runtime/blue.env`と`green.env`はモデル版登録後に`runtime init`が生成するため、この時点ではなくて構いません。Compose設定ではこれらの`env_file`を任意にし、PostgreSQLとMLflowだけを先に検証・起動できるようにしています。APIサービスは`runtime init`後に起動してください。`docker compose config -q`が成功したら、ホスト側CLIラッパーは`.env`を読み、運用DBとMLflowへ接続します。データの取得が済んでいない場合だけdownloadを実行し、いずれの場合も監査結果を確認してから初回学習へ進みます。まだ固定最終ホールドアウトを評価していない場合は、次の初回学習で一度だけ評価します。節2ですでに評価した場合は、コマンドから`--evaluate-final-holdout`を外します。

```bash
./scripts/bikectl data audit
./scripts/bikectl train
```

初回学習時に固定ホールドアウトを評価したrunは`http://127.0.0.1:5000/#/experiments/1/runs/42abda5f8da94b9b8a3d5f5f46da42ad`です。holdoutのモデルMAE 72.5362、RMSE 111.5060を記録しました。信頼型を限定して再学習したrun `0db7aa07461b4b8785145156d8de9a66`はvalidation MAE 55.0728（ベースライン63.9628）、RMSE 88.6249（ベースライン107.8187）で品質ゲートを通過しました。固定holdoutは再評価していません。

ただし、最初に登録されたモデル版1はAPIから読み込めませんでした。エラーは`No such artifact: 'MLmodel'`です。原因はMLflow 3でモデルartifactの保存場所が変わったのに、登録処理が`runs:/<run_id>/sklearn-model`という従来のrun artifact URIを登録元に使っていたことです。登録処理を`mlflow.sklearn.log_model()`が返す`model_info.model_uri`を使うよう修正しました。MLflow 3はログ済みモデルを通常のrun artifactと別に保存し、返却されたモデルURIを読み込みに使うよう案内しています。[MLflow 3移行ガイド](https://mlflow.org/docs/latest/ml/mlflow-3)

修正版を作業環境へ取り込んでから、固定holdoutを再評価せずに通常学習を再実行します。版1は履歴として残し、新しい版を登録した後、既に初期化済みのruntimeから手動昇格します。`runtime init`をやり直してはいけません。別のチェックアウトで作業する場合は、まずローカル変更がないことを確認して更新します。

```bash
git status --short
git pull --ff-only origin main
. .venv/bin/activate
./scripts/bikectl train
```

2026-10-27にこの手順を実行し、run `f55812c02dac4084a8185856cf7b4793`からモデル版`2`が登録されました。validation MAEは55.0728、基準MAEは63.9628、`quality_gate_passed`は`true`、`final_holdout`は`null`です。`quality_gate`文字列は合格条件の説明で、合否は`quality_gate_passed`で確認します。

`git status --short`に自分の未保存作業が表示された場合は、先にその変更を保全してからpullします。レポートでは`registration.model_version`と`quality_gate_passed`を確認します。2026-10-27の学習ではモデル版2が作られ、ゲートに合格して当時の稼働版になりました。2026-10-28に版3へ更新済みです。以下の版2学習・昇格コマンドをもう一度実行しないでください。

```bash
MODEL_VERSION=2 # 学習レポートのregistration.model_versionに置き換える
./scripts/bikectl deploy promote --version "$MODEL_VERSION" --approver kenshin --reason 'MLflow 3 model URI fixed; validation gate passed'
curl -sS -i http://127.0.0.1:8080/ready
curl -sS http://127.0.0.1:8080/version
```

昇格コマンドは候補APIをbuild・起動し、readyと固定予測を検査してから入口を切り替えます。内部でDocker Composeの出力を捕まえ、最後にまとめて表示するため、処理中はターミナルが無表示になることがあります。今回の実行では、完了確認前に別ターミナルから次を確認しました。

```bash
docker compose ps -a
docker compose logs --since=5m --tail=100 api-green
curl -sS -i http://127.0.0.1:8002/ready
```

今回の旧稼働枠はblue、候補はgreen（8002）でした。`/ready`の一時的な503は起動中ならあり得ます。Ctrl+Cで中断すると自動復旧処理が走らない可能性があるため、中断済みの場合は再昇格の前に`./scripts/bikectl deploy recover`を実行し、状態を確認します。

2026-10-27にGreen枠の`/ready`とCaddy入口の`/ready`がHTTP 200を返し、両方ともモデル版2を示しました。Caddy入口の`/version`はartifact SHA-256 `42bb5f3c7dc5a28d562d3fe2b01fe2aaf832af34ef5496b05b0fe65e9a79b337`、quality gate `passed`を返しました。`data/runtime/deployment-state.json`はactive slot `green`、active version `2`、mode `approved`、deployment ID `dded8d98-6e6c-4082-9419-df62cc08adb7`でした。これは2026-10-27時点の状態です。

昇格処理はGreen側でモデルを読み込み、readinessと固定予測を確かめてからCaddyの入口を切り替えます。成功後に`/ready`と`/version`を確認します。`final_holdout`は過去の初回評価を再利用するため、再実行レポートでは`null`が正しい状態です。

runtimeは当初モデル版1で初期化し、2026-10-27に版2、2026-10-28に版3へ昇格済みです。以下の初回初期化コマンドは実行しないでください。次の新版を昇格するときも`runtime init`ではなく`deploy promote`を使います。初回構築時の参考として手順を残します。

```bash
MODEL_VERSION=1 # 学習レポートのregistration.model_versionに置き換える
./scripts/bikectl runtime init --version "$MODEL_VERSION" --approver kenshin --reason 'validation quality gate passed'
docker compose up -d --build api-blue api-green caddy
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version
```

`/ready`が503の場合、`curl -f`はエラー本文を表示しないため、API枠へ直接アクセスしてモデルロードの詳細を確認します。`/version`にモデル版はあるのにartifact hashや品質ゲート状態が`null`なら、モデルを読み込めていません。runtime initはすでに完了しているため、再実行せず、まず応答本文とAPIログを確認してください。

```bash
curl -sS -i http://127.0.0.1:8001/ready
curl -sS -i http://127.0.0.1:8002/ready
docker compose logs --tail=120 api-blue api-green
```

この環境では2026-10-27時点で、Compose上の全コンテナ起動後も入口`/ready`が503を返し、`/version`はmodel version `1`、artifact hash `null`、quality gate `null`でした。最初の根本例外はAPI応答本文からHost allowlist問題と判明しました。

2026-10-27に最初に観測した503の原因は、`MLFLOW_TRACKING_URI=http://mlflow:5000`に対してMLflowが`Invalid Host header - possible DNS rebinding attack detected`を返し、`/api/2.0/mlflow/registered-models/get`が403になったことでした。Docker内部DNS名`mlflow:5000`がMLflowのHost allowlistに含まれていなかったためです。`compose.yaml`では、内部名とローカルUI用のホスト名だけを許可しています。DNS rebinding対策を無効化したり、全Hostを許可したりしないでください。

設定を反映するにはCompose設定を検証してからMLflowとAPI枠を再作成します。`runtime init`はすでに完了しているので、再実行しません。

```bash
docker compose config -q
docker compose up -d --force-recreate mlflow api-blue api-green caddy
docker compose ps
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version
```

このHost allowlist修正後も、次のモデル登録元の問題が見つかりました。下記の再学習・昇格が済むまではreadiness 503が続きます。修正版で昇格した後は`/ready`の`status`が`ready`、`/version`の`model_version`が昇格した版、`artifact_sha256`が非null、`quality_gate_status`が`passed`であることを確認します。

検証MAEが固定ベースラインを下回らなかった場合は通常の初期化・昇格を拒否します。基盤の起動・復旧動作を確認する目的に限り、失敗タグが記録された状態で`--diagnostic`を明示できます。現在の実行環境ではruntimeは版1で初期化後、版2、版3へ順に昇格済みです。以下の初回初期化例を再実行しません。

```bash
MODEL_VERSION=1 # 学習レポートの登録版番号に置き換える
./scripts/bikectl runtime init --version "$MODEL_VERSION" --diagnostic --approver kenshin --reason '品質ゲート未達版を使うローカル配信障害試験'
```

診断配信は低精度の可能性を残し、実用性能を満たしたモデルとして紹介しません。

MLflow UIはローカルの`http://127.0.0.1:5000`、入口APIは`http://127.0.0.1:8080`です。PostgreSQLは`127.0.0.1:55432`です。必要なとき以外はポートを公開しないでください。

### 実行結果を見る場所

- MLflow UI (`http://127.0.0.1:5000`)ではexperiment `bike-demand-operations`のrun、学習指標、品質ゲート、`training-report.json`などのrun artifactを確認します。候補版3はrun ID `a0c6d543bb464f5aac88dd87a9712339`です。
- Q4リプレイ・監視・比較結果はMLflowへ自動登録されません。`data/reports/`のJSON/CSVをVS Codeで開きます。現在の比較とPSI調査は[`monitor-investigation-and-next-data.md`](monitor-investigation-and-next-data.md)に一覧があります。
- `http://127.0.0.1:8080/ready`と`/version`は稼働版の状態をJSONで返すAPIです。予測APIのローカル画面や結果ダッシュボードではありません。
- ターミナルの出力は実行コマンドのその場の結果です。後から見る結果は`data/reports/`かMLflow run artifactを参照します。

## 4. 候補の昇格・差し戻し

### 時系列を保った再学習候補

新データ候補の登録・昇格前に[差分監査](reviews/20261028-review.md)と[計画書R01〜R09](../../261014/plan_mlops.md#11-2026-10-28差分監査後の実行計画)を確認します。現行CLIの新データ最終評価は候補artifactを再利用せず再学習し、配信/監視はUCIデータを前提にしています。下記の過去コマンドをそのまま新データ移行へ流用しません。

2026-10-28に版2をincumbentとして候補版3を比較し、Q3ゲート合格後に昇格しました。現行版は3です。旧版1は誤ったartifact URIで登録され、読み込みできないため比較・rollbackに使いません。Q3・Q4は評価済みで、同じ期間を次候補の選択に使ってはいけません。次の候補には新しいデータと事前に決めた後続期間を用意します。

```bash
# 2026-10-28に実行した履歴コマンド。Q3窓は再利用しない。
./scripts/bikectl candidate --incumbent-version 2
```

出力レポートにincumbent、履歴ベースラインとの同一行MAE/RMSE、候補のカットオフ、検証窓が記録されます。候補品質ゲートはincumbentの品質ゲートが`passed`で、かつ候補MAEがincumbentと履歴ベースラインの双方を厳密に下回る場合だけ通ります。登録後も人が審査するまで稼働版は変わりません。検証窓を変更するときは、期間を見てから都合のよい窓を選ばず、選択理由を作業記録に残します。

任意の評価窓は、候補の学習カットオフ以降に置きます。2026-10-28の実行条件は以下です。同じQ3窓を後続候補の選択に再利用しません。

```bash
./scripts/bikectl candidate \
  --incumbent-version 2 \
  --training-cutoff '2012-07-01 00:00:00' \
  --validation-start '2012-07-01 00:00:00' \
  --validation-end '2012-10-01 00:00:00'
```

### 凍結protocolに基づく新規候補

2026-10-28時点で手元にあるCapital Bikeshareの2025年上期validationと2025-07〜2026-04の後続期間は、いずれも既評価として再利用禁止です。UCI登録版3は別targetのモデルなので、Capital候補のincumbentとして使いません。まだ`bike-demand-capital`配下の同target incumbentも承認済みmonitor参照期間もありません。このため、現在は新protocolの作成・候補選定・最終評価を実行しないでください。まず別target用の初回modelと合否規則をR06に従って確立します。

初回modelが用意され、未使用のvalidation・後続期間を確保した後の手順は次のとおりです。出典の公開が遅れている場合は、新期間が入手できるまで待ちます。

取込先は新しいパスを指定します。既存CSV/manifestの上書きは拒否されます。`--archive-start`/`--archive-end`は取得したZIPの全月を連続して指定し、data期間の境界月も含めます。出力CSV・manifestは一組で保存され、manifestのSHA-256が一致してから利用できます。

```bash
CSV='data/derived/capitalbikeshare-next/hourly.csv'
MANIFEST='data/derived/capitalbikeshare-next/manifest.json'

# YYYYMMとtimestampを、実際に取得した新しい連続月・未使用期間に置き換える。
python scripts/prepare_capitalbikeshare.py \
  --archive-start YYYYMM \
  --archive-end YYYYMM \
  --data-start 'YYYY-MM-DD 00:00:00' \
  --data-end-exclusive 'YYYY-MM-DD 00:00:00' \
  --output-csv "$CSV" \
  --output-manifest "$MANIFEST"
```

次に、既存の品質合格incumbentをMLflowからhash確認し、validation・判断時刻・最終評価期間・分割件数・candidate recipeを含むprotocolを凍結します。最終評価開始は、validation期間の最後のラベルが到着する時刻以降でなければなりません。プロトコル保存時に既存台帳とlegacy期間を照合し、重複窓を拒否します。

```bash
export DATA_PROFILE_ID=capital-bikeshare
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
CAPITAL_INCUMBENT_VERSION='<bike-demand-capitalの承認済み版>'
mkdir -p configs/evaluation-protocols
python scripts/create_capital_evaluation_protocol.py \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --training-cutoff 'YYYY-MM-DD HH:00:00' \
  --validation-start 'YYYY-MM-DD HH:00:00' \
  --validation-end 'YYYY-MM-DD HH:00:00' \
  --evaluation-start 'YYYY-MM-DD HH:00:00' \
  --evaluation-end 'YYYY-MM-DD HH:00:00' \
  --incumbent-version "$CAPITAL_INCUMBENT_VERSION" \
  --output configs/evaluation-protocols/capital-next-v1.json
```

候補は明示的に未登録で学習します。候補bundleにはskops model、validationの行別予測、source/依存snapshot、protocol copy、各SHA-256が保存されます。既存bundleは上書きされません。

```bash
mlops-bikectl candidate \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --evaluation-protocol configs/evaluation-protocols/capital-next-v1.json \
  --incumbent-version "$CAPITAL_INCUMBENT_VERSION" \
  --no-register

python scripts/evaluation_ledger.py list
```

最終評価はbundle内の選定済みmodelをhash検査してロードします。評価scriptは候補を再学習しません。候補reportは`data/candidates/<candidate_id>/candidate-report.json`です。最終評価には未使用期間を指定したprotocolと同じCSV/manifestを使い、出力先には新規パスを指定します。

```bash
python scripts/evaluate_capitalbikeshare_holdout.py \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --selection-report data/candidates/<candidate_id>/candidate-report.json \
  --output data/reports/capital-bikeshare/capital-next-final-evaluation.json
```

同じtarget contractの評価期間は、別データhashや出力名でも重複・部分重複が拒否されます。失敗・中断も自動で未使用に戻りません。まず`python scripts/evaluation_ledger.py list`と成果物を調べ、正当な再試行と判断した場合だけ、記録されたkeyに人の理由を付けて`reopen --key ... --operator ... --reason ...`を実行します。startedのままの場合は実行中プロセスがないことを確認してから判断します。

Capital Bikeshareのtrip startsとUCIのrentalsは同じtarget定義ではありません。ここでのcandidate比較・後続採点は移行調査であり、それだけでUCI配信の昇格承認に使いません。初回modelと後続評価が揃うまでは、隔離stackの`/ready`は稼働可能判定に使わず、候補を登録・昇格しません。

Capital用Composeのファイル結合を確かめるだけなら、サービスを起動せず次を実行できます。Compose project名、port、volume、envがUCI側から分かれている設定です。

```bash
docker compose -f compose.yaml -f compose.capital.yaml config -q
```

### 手動の昇格

新しい学習版を作り、validationゲート結果をMLflowで確認したあと、承認理由を付けて昇格します。

```bash
./scripts/bikectl deploy promote --version 3 --approver kenshin --reason 'Q3 candidate gate passed; user requested independent Q4 replay'
```

通常昇格は`quality_gate=passed`を必須にします。未達版の試験だけ、明示的に`--diagnostic`を付けます。

更新途中で停止したときは入口の版と台帳を照合します。

```bash
./scripts/bikectl deploy recover
```

以前に稼働した登録版へ戻す場合も理由を残します。

```bash
./scripts/bikectl deploy promote --version 2 --rollback --approver kenshin --reason '新候補に異常を確認したため直前の確認済み版へ戻す'
```

## 5. 履歴リプレイと遅延ラベル監視

入口が対象登録版を配信していることを確認してから実行します。

```bash
./scripts/bikectl replay --model-version 3 --start '2012-10-01 00:00:00' --end '2013-01-01 00:00:00'
```

この候補例では第3四半期を学習候補の選定に使ったため、独立した後続評価には第4四半期だけを使います。候補をローカルの手動承認済み試験版として配信してから、`--model-version`にその登録版を指定します。初回版と比較する場合は、同じ第4四半期を別途初回版でもリプレイし、版ごとのレポートを並べて確認します。

出力の`replay_id`を指定して監視窓を計算します。

```bash
./scripts/bikectl monitor --replay-id e03de4ee4ef4-2012100100-2013010100 --model-version 3 \
  --start '2012-10-01 00:00:00' --end '2013-01-01 00:00:00'
```

2026-10-28の既存実行はstartを省略していたため、既定の7月から各版4,416窓を保存し、そのうちQ3の2,208窓は予測0件でした。上記はQ4だけを扱う修正後の期間指定です。既存の空窓は監査記録として保持し、この説明のために再計算・削除していません。最終週の数値と、別集計したQ4全体誤差は影響を受けません。

CSVとDBに窓ごとの予測数、到着ラベル数、到着率、MAE/RMSE、固定ビンPSIを保存します。ラベル到着前は誤差を0扱いせず分母から外します。PSIと誤差悪化は別々に読み、通知は調査開始にだけ使います。

### 2026-10-28実行記録: 候補版3とQ4比較

以下は実行済みの比較です。Q3を候補選定に使い、Q4は選択後の独立評価に使いました。結果は[`data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json`](../data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json)に保存しています。

- 候補run `a0c6d543bb464f5aac88dd87a9712339`、登録版3。学習カットオフは`2012-07-01 00:00:00`、最新利用可能ラベルも同時刻。学習13,001行、Q3検証2,208行。
- Q3: 版3候補MAE/RMSE `62.416 / 95.799`、incumbent版2 `84.168 / 133.402`、履歴ベースライン`83.692 / 139.929`。候補は両方を上回り`quality_gate_passed=true`。
- 版3はdeployment ID `8a6a63f0-875a-4c5b-8c85-bc0fe1538d1e`でBlue枠へ昇格。固定probeのoffline/API予測差は`0.000000492`、許容差内。Caddy入口は版3、artifact SHA-256 `79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`、quality gate `passed`。
- 版2と版3を、同一UCI CSV SHA-256 `e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`、同じQ4期間`[2012-10-01, 2013-01-01)`でリプレイ。実データの欠損40時間を除き、各版2,168件、HTTPエラー0件、DBへの観測追加0件。
- Q4全期間をラベル到着後に集計: 版2 MAE/RMSE `70.501 / 113.270`、版3 `59.403 / 91.941`。版3は版2に比べMAE約15.7%、RMSE約18.8%改善。p50 API遅延は版2/3で`71.3 / 71.7 ms`、p95は`104.6 / 111.9 ms`。
- 最新7日窓では版2 MAE/RMSE `37.278 / 58.158`、版3 `44.176 / 74.286`で版3の誤差が大きかった。両方の窓は`within_review_threshold`。一方、両版とも`lag_24` PSI `2.095`で分布変化の調査対象。分布変化は精度悪化と同じ指標ではないため、Q4全期間の改善と最新週の差を分けて記録する。
- Q4の全期間結果は見た後の再学習・候補選択に使わない。次候補の検証には新しいデータと独立した期間が必要。

PSIの参照期間、直近7日間の誤差を日付・休日・天候で調べた結果、品質ゲート、Capital Bikeshare新データ候補のvalidationと一度限りのout-of-time評価は[`監視調査と次の評価データ`](monitor-investigation-and-next-data.md)を参照してください。追加レポートは`data/reports/candidate-capitalbikeshare-2025h1-v3.json`と`data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json`です。これらはMLflowへ登録されていません。版3は現行稼働中であり、新候補へ切り替えません。

候補イメージの再ビルドは約5分かかりました。Buildxログでは、Dockerfileの`pip install .`が依存関係を新規取得・インストールしており、昇格コマンドが無表示でもビルドは進行中でした。ビルド中に非稼働のBlue枠が旧版1で503を返しましたが、CaddyはGreen版2を維持し、昇格後にBlue版3へ切り替わりました。

## 障害時

- `/ready`失敗: `/version`とAPIログ、MLflow登録版の状態、PostgreSQL状態を確認。`Invalid Host header`の場合はCompose内部のMLflow名を`mlflow --allowed-hosts`へ限定追加し、MLflow/APIを再作成する。候補を入口へ手動で向ける前にreadyを通す。
- 予測記録失敗: 成功応答は返らない。DB障害を直し、同じリクエストID・入力で再送する。
- 候補起動・検証失敗: 入口は旧版のまま。候補のエラーを調査してから再試行。
- 切り替え後に入口版が食い違う: `deploy recover`で照合。未知の版なら手作業で入口と記録を一致させるまで再昇格しない。
- リプレイの再実行: 同じデータ・版・時刻は同じリクエストIDを使い、既存予測を返す。別モデル版での評価は版ごとに独立したIDを使う。
