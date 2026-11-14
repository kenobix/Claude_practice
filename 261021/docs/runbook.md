# 使用操作・配信・復旧・CI運用

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [実行・運用手順](#runbook)
- [配信処理の進み具合・timeout・旧stateの読取診断（T17b）](#deployment-observability)
- [配信復旧の状態・承認metadataと隔離受入（T07a/T17a）](#deployment-recovery)
- [予測保存列の精度移行と復元の検証（T08）](#prediction-precision-migration)
- [MLflowのnoload非推奨警告の切分け](#mlflow-orm-compatibility)
- [CI artifact actionの単独保守（2026-11-03）](#ci-artifact-maintenance)

<a id="runbook"></a>

## runbook

<a id="runbook--実行・運用手順"></a>

### 実行・運用手順

**2026-11-14現在:** 時計研究は保留。[製品作業の開始条件](capital.md#product-completion-policy)へ改訂し、履歴学習/精度評価と時計依存の運用測定を分離した。過去の時計資格は未合格のまま。日付付きの旧停止条件は当時の履歴として読む。

Capitalの実fit/一度限りfinalは完了。[精度結果](capital.md#capital-first-evaluation)、[監視診断](capital.md#capital-offline-monitoring)、[次の隔離runtime手順](capital.md#capital-runtime-handoff)を参照。両窓を再消費せず、現配信/実registryへ接続しない。

配信中の段階表示、Docker timeout時の未確定状態、読取専用の`deploy inspect`は[配信処理の診断](runbook.md#deployment-observability)を参照してください。
合成Capitalの実MLflow/skops/DBを別volumeへ復元する追加試験は[配信復旧](runbook.md#deployment-recovery)を参照してください。現配信を障害対象にしない手順です。

初学者向けの用語は[用語集](architecture.md#glossary)を参照してください。
データ源を切り替える場合は[データプロファイルとtarget分離](architecture.md#data-profiles)を先に確認してください。通常運用は引き続きUCIのCompose projectです。
合成Capital測定のZIP保存・別process再計算手順は[T16計測証拠アーカイブ](monitoring.md#measurement-evidence-archive)を参照してください。これは隔離fixtureの証拠であり、実データの合格・配信承認ではありません。

<a id="runbook--0-windowswslからプロジェクトを開く"></a>

#### 0. Windows/WSLからプロジェクトを開く

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

<a id="runbook--1-環境とデータ"></a>

#### 1. 環境とデータ

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

<a id="runbook--2-sqliteで学習・apiを試す"></a>

#### 2. SQLiteで学習・APIを試す

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

<a id="runbook--3-composeによるローカル運用"></a>

#### 3. Composeによるローカル運用

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

<a id="runbook--実行結果を見る場所"></a>

##### 実行結果を見る場所

- MLflow UI (`http://127.0.0.1:5000`)ではexperiment `bike-demand-operations`のrun、学習指標、品質ゲート、`training-report.json`などのrun artifactを確認します。候補版3はrun ID `a0c6d543bb464f5aac88dd87a9712339`です。
- Q4リプレイ・監視・比較結果はMLflowへ自動登録されません。`data/reports/`のJSON/CSVをVS Codeで開きます。現在の比較とPSI調査は[`monitor-investigation-and-next-data.md`](capital.md#monitor-investigation-and-next-data)に一覧があります。
- `http://127.0.0.1:8080/ready`と`/version`は稼働版の状態をJSONで返すAPIです。予測APIのローカル画面や結果ダッシュボードではありません。
- ターミナルの出力は実行コマンドのその場の結果です。後から見る結果は`data/reports/`かMLflow run artifactを参照します。

<a id="runbook--4-候補の昇格・差し戻し"></a>

#### 4. 候補の昇格・差し戻し

<a id="runbook--時系列を保った再学習候補"></a>

##### 時系列を保った再学習候補

新データ候補の登録・昇格前に[差分監査](evidence.md#20261028-review)と[計画書R01〜R09](../../261014/plan_mlops.md#11-2026-10-28差分監査後の実行計画)を確認します。過去のCapital最終評価は再学習していましたが、現在の最終評価scriptは保存済み候補artifactを照合・loadします。Capital初回bootstrapは同target incumbentを指定せず、固定した履歴baselineとのMAE/RMSE比較を使います。下記のUCIコマンドは実行当時の履歴として扱います。

2026-10-28に版2をincumbentとして候補版3を比較し、Q3ゲート合格後に昇格しました。現行版は3です。旧版1は誤ったartifact URIで登録され、読み込みできないため比較・rollbackに使いません。Q3・Q4は評価済みで、同じ期間を次候補の選択に使ってはいけません。次の候補には新しいデータと事前に決めた後続期間を用意します。

```bash
# 2026-10-28に実行した履歴コマンド。Q3窓は再利用しない。
./scripts/bikectl candidate --incumbent-version 2
```

当時の出力レポートにはincumbent、履歴ベースラインとの同一行MAE/RMSE、候補のカットオフ、検証窓が記録されています。当時の候補品質ゲートはincumbentが`passed`で、候補MAEがincumbentと履歴ベースラインの双方を厳密に下回る条件でした。現在のCapital初回候補には、下記のbootstrap手順と別の基準を使います。

任意の評価窓は、候補の学習カットオフ以降に置きます。2026-10-28の実行条件は以下です。同じQ3窓を後続候補の選択に再利用しません。

```bash
./scripts/bikectl candidate \
  --incumbent-version 2 \
  --training-cutoff '2012-07-01 00:00:00' \
  --validation-start '2012-07-01 00:00:00' \
  --validation-end '2012-10-01 00:00:00'
```

<a id="runbook--凍結protocolに基づく新規候補"></a>

##### 凍結protocolに基づく新規候補

2026-10-28時点で手元にあるCapital Bikeshareの2025年上期validationと2025-07〜2026-04の後続期間は、いずれも既評価として再利用禁止です。UCI登録版3は別targetのモデルなので、Capital候補のincumbentとして使いません。まだ`bike-demand-capital`配下の承認済み同target incumbentもmonitor参照期間もありません。初回候補には未使用の期間と、事前に固定したbaseline-only protocolを使います。

初回Capital modelの基準は、ユーザーからの判断委任により[`capital-bootstrap-gate.md`](capital.md#capital-bootstrap-gate)へ記録しました。MAEの厳密改善とRMSE非悪化、エラー0件/p95 250ms以下のローカル運用目標、segmentは記録のみを採用します。bootstrapコードと合成データでの検証は実施済みですが、未使用の実データ取得・protocol freeze・実候補評価は未完了です。実験前の残作業は計画書の順序を確認してください。

未使用のvalidation・後続期間を確保し、計画書の実験前作業が終わった後の初回bootstrap手順は次のとおりです。出典の公開が遅れている場合は、新期間が入手できるまで待ちます。

取込先は新しいパスを指定します。既存CSV/manifestの上書きは拒否されます。`--archive-start`/`--archive-end`は取得したZIPの全月を連続して指定し、data期間の境界月も含めます。出力CSV・manifestは一組で保存され、manifestのSHA-256が一致してから利用できます。

```bash
CSV='data/derived/replace-with-new-unique-period/hourly.csv'
MANIFEST='data/derived/replace-with-new-unique-period/manifest.json'

# 出力path、YYYYMM、timestampは、実際に取得した新しい連続月・未使用期間に置き換える。
python scripts/data_tools.py prepare \
  --archive-start YYYYMM \
  --archive-end YYYYMM \
  --data-start 'YYYY-MM-DD 00:00:00' \
  --data-end-exclusive 'YYYY-MM-DD 00:00:00' \
  --output-csv "$CSV" \
  --output-manifest "$MANIFEST"
```

次に、incumbentを指定せず、validation・判断時刻・最終評価期間・分割件数・candidate recipeを含むbootstrap protocolを凍結します。最終評価開始は、validation期間の最後のラベルが到着する時刻以降でなければなりません。プロトコル保存時に既存台帳とlegacy期間を照合し、重複窓を拒否します。

```bash
export DATA_PROFILE_ID=capital-bikeshare
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
mkdir -p configs/evaluation-protocols
python scripts/data_tools.py protocol \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --training-cutoff 'YYYY-MM-DD HH:00:00' \
  --validation-start 'YYYY-MM-DD HH:00:00' \
  --validation-end 'YYYY-MM-DD HH:00:00' \
  --evaluation-start 'YYYY-MM-DD HH:00:00' \
  --evaluation-end 'YYYY-MM-DD HH:00:00' \
  --output configs/evaluation-protocols/capital-next-v1.json
```

候補は明示的に未登録で学習します。候補bundleにはskops model、validationの行別予測、source/依存snapshot、protocol copy、各SHA-256が保存されます。既存bundleは上書きされません。

```bash
mlops-bikectl candidate \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --evaluation-protocol configs/evaluation-protocols/capital-next-v1.json \
  --no-register

python scripts/data_tools.py evaluation-ledger list
```

最終評価はbundle内の選定済みmodelをhash検査してロードします。評価scriptは候補を再学習しません。候補reportは`data/candidates/<candidate_id>/candidate-report.json`です。最終評価には未使用期間を指定したprotocolと同じCSV/manifestを使い、出力先には新規パスを指定します。

```bash
python scripts/data_tools.py evaluate \
  --csv "$CSV" \
  --source-manifest "$MANIFEST" \
  --selection-report 'data/candidates/<candidate_id>/candidate-report.json' \
  --output data/reports/capital-bikeshare/capital-next-final-evaluation.json
```

同じtarget contractの評価期間は、別データhashや出力名でも重複・部分重複が拒否されます。失敗・中断も自動で未使用に戻りません。まず`python scripts/data_tools.py evaluation-ledger list`と成果物を調べ、正当な再試行と判断した場合だけ、記録されたkeyに人の理由を付けて`reopen --key ... --operator ... --reason ...`を実行します。startedのままの場合は実行中プロセスがないことを確認してから判断します。

Capital Bikeshareのtrip startsとUCIのrentalsは同じtarget定義ではありません。ここでのcandidate比較・後続採点は移行調査であり、それだけでUCI配信の昇格承認には使えません。初回candidateがvalidationとfinalの精度条件に合格した後、専用stagingへ`blocked`状態で登録できます。運用条件と手動承認が未完了の間は通常配信へ昇格できません。

Capital用Composeのファイル結合を確かめるだけなら、サービスを起動せず次を実行できます。Compose project名、port、volume、envがUCI側から分かれている設定です。

```bash
docker compose -f compose.yaml -f compose.capital.yaml config -q
```

validation/final gateを通過した未承認candidateの登録と運用測定は、通常のblue/greenやCaddyへ接続せず、専用DB付きstaging APIで行います。candidate/final reportの照合、blocked登録、起動、残る検証項目は[`capital-bootstrap-gate.md`](capital.md#capital-bootstrap-gate)を参照してください。staging seedはprofileのCSV/manifestを候補・registryと照合し、`--verify-only`で投入後の全観測も確認できます。benchmark CLIには入力確認専用の`--check-input-only`と、frozen planに基づく`--run-measurement --plan`があります。後者はDB snapshot lock、API data provenance、監査照合を行いますが、計測完了だけでoperational gateやdeploymentを合格にしません。完了reportの確認後は`runtime_tools.py record-operational`でreport hash/statusをexact MLflow versionへ記録できますが、この記録も承認ではありません。現在は未使用実データのcandidateがないため、実candidateの測定はまだ行いません。Composeのstaging設定だけを確認するときは次を実行できます。これはサービスを起動しません。

```bash
docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
```

<a id="runbook--手動の昇格"></a>

##### 手動の昇格

Capital stagingの測定がtimeout・SIGTERM・SIGKILLで止まった場合は、再実行の前に[中断時のjournal・DB照合手順](monitoring.md#measurement-interruption)を確認します。reportなしのstarted/interrupted attemptも要求別journalで調べられます。UCIのdeploy recoverとは別の手順です。

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

<a id="runbook--5-履歴リプレイと遅延ラベル監視"></a>

#### 5. 履歴リプレイと遅延ラベル監視

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

現在は`--start`と`--end`を両方省略すると、対象replayのJSONから実行時に指定された期間を引き継ぎます。片方だけの指定や、JSONに記録された期間を超える指定は拒否します。JSONがない場合は両方を明示してください。JSONの期間はリプレイで指定した範囲であり、`--limit`やエラーなどにより範囲内の全時刻が実際に処理された証拠ではありません。旧UCIのQ4 JSONにはprofile情報がないため、既知のUCI CSVハッシュに基づき推定したことを監視結果の`monitor_period.profile_identity_source`に記録します。実際の予測件数とCSV由来も別に確認します。

監視は実行ごとにIDを作り、既定のCSV/JSON名とDB窓IDを分けます。`--output`で既存ファイルを指定した場合は上書きせず停止します。正常にDBへ保存できた実行には、CSV・JSONのハッシュと実行IDを含む`*.complete.json`を追加します。ファイルとDBは別々に保存するため、途中停止して完了ファイルがない場合はDB保存の成否を決めつけず、JSONに記載された`database_window_id_prefix`で`monitor_windows.window_id`を調べてください。過去の記録を自動更新・削除しません。

T06の[読取照合契約](monitoring.md#monitor-reconciliation)に沿い、SQLite/PostgreSQLの読取専用照合コマンドを追加しました。受入は使い捨ての合成fixtureで行っています。

```bash
DATABASE_URL='sqlite:////absolute/path/ops.db' \
  mlops-bikectl monitor-inspect --csv /absolute/path/monitor.csv \
  --run-id '3ae0909d-7ae1-4a09-a756-96a7ed582edf'
```

実行結果はJSONで、`matched`は終了値0、確定した不一致は1、未確定/未対応/DB照会不能は2です。終了値2は未送信を意味しません。特にmarkerがない場合、DB行が0件でもlater commitの可能性を残します。コマンドはCSV・JSON・marker・DBを変更せず、再送・修復・marker生成もしません。JSON内のモデル版はreport記載値としてのみ扱い、MLflow登録版の真正性は証明しません。

PostgreSQL版T06bも同じコマンドで使用できます。対象profileの設定を読み込んだ同じシェルで、既存の`DATABASE_URL`を設定して実行します。値はチャットやGitへ貼らないでください。CSV/UUIDは調査対象の実値へ置き換えます。

```bash
# DATABASE_URLには確認対象のPostgreSQL URLが設定済みであること。
# .envをVS Codeで開いただけではシェルへ設定されません。
mlops-bikectl monitor-inspect --csv /absolute/path/monitor.csv \
  --run-id '3ae0909d-7ae1-4a09-a756-96a7ed582edf'
```

PostgreSQLは同一REPEATABLE READ READ ONLYで型・profile・対象行を読みます。接続timeout 5秒、statement timeout 5秒、lock timeout 1秒を固定し、schema/DBを新規作成しません。REALの丸めとSQL NaNは[数値照合ルール](monitoring.md#monitor-reconciliation--数値と欠落値の固定ルール)に従います。設定されたURIのoptionsは安全な固定値で上書きされ、schemaはその接続のcurrent_schema()を使います。接続不能・権限拒否・timeoutはdatabase_unavailable/2であり、必要行がないと確定したわけではありません。最終unit/PG受入/CI結果は[T06b検証記録](reviews/20261030-t06b-checks.json)を参照してください。使い捨てDBの保存型probeや合成受入を、実Capital監視・配信の証拠へ流用しないでください。既存DB/reportの修正やmarker再作成を行わず原本を保持してください。

Capital監視は[学習参照とPSI v2](monitoring.md#training-monitor-reference)の手順を使います。`--psi-version psi-v2-constant-bins`と、同じ登録候補の学習参照artifactが必要です。UCIは従来の呼出しとPSIを維持します。

CSVとDBに窓ごとの予測数、到着ラベル数、到着率、MAE/RMSE、固定ビンPSIを保存します。ラベル到着前は誤差を0扱いせず分母から外します。PSIと誤差悪化は別々に読み、通知は調査開始にだけ使います。

<a id="runbook--2026-10-28実行記録-候補版3とq4比較"></a>

##### 2026-10-28実行記録: 候補版3とQ4比較

以下は実行済みの比較です。Q3を候補選定に使い、Q4は選択後の独立評価に使いました。結果は[`data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json`](../data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json)に保存しています。

- 候補run `a0c6d543bb464f5aac88dd87a9712339`、登録版3。学習カットオフは`2012-07-01 00:00:00`、最新利用可能ラベルも同時刻。学習13,001行、Q3検証2,208行。
- Q3: 版3候補MAE/RMSE `62.416 / 95.799`、incumbent版2 `84.168 / 133.402`、履歴ベースライン`83.692 / 139.929`。候補は両方を上回り`quality_gate_passed=true`。
- 版3はdeployment ID `8a6a63f0-875a-4c5b-8c85-bc0fe1538d1e`でBlue枠へ昇格。固定probeのoffline/API予測差は`0.000000492`、許容差内。Caddy入口は版3、artifact SHA-256 `79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`、quality gate `passed`。
- 版2と版3を、同一UCI CSV SHA-256 `e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`、同じQ4期間`[2012-10-01, 2013-01-01)`でリプレイ。実データの欠損40時間を除き、各版2,168件、HTTPエラー0件、DBへの観測追加0件。
- Q4全期間をラベル到着後に集計: 版2 MAE/RMSE `70.501 / 113.270`、版3 `59.403 / 91.941`。版3は版2に比べMAE約15.7%、RMSE約18.8%改善。p50 API遅延は版2/3で`71.3 / 71.7 ms`、p95は`104.6 / 111.9 ms`。
- 最新7日窓では版2 MAE/RMSE `37.278 / 58.158`、版3 `44.176 / 74.286`で版3の誤差が大きかった。両方の窓は`within_review_threshold`。一方、両版とも`lag_24` PSI `2.095`で分布変化の調査対象。分布変化は精度悪化と同じ指標ではないため、Q4全期間の改善と最新週の差を分けて記録する。
- Q4の全期間結果は見た後の再学習・候補選択に使わない。次候補の検証には新しいデータと独立した期間が必要。

PSIの参照期間、直近7日間の誤差を日付・休日・天候で調べた結果、品質ゲート、Capital Bikeshare新データ候補のvalidationと一度限りのout-of-time評価は[`監視調査と次の評価データ`](capital.md#monitor-investigation-and-next-data)を参照してください。追加レポートは`data/reports/candidate-capitalbikeshare-2025h1-v3.json`と`data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json`です。これらはMLflowへ登録されていません。版3は現行稼働中であり、新候補へ切り替えません。

候補イメージの再ビルドは約5分かかりました。Buildxログでは、Dockerfileの`pip install .`が依存関係を新規取得・インストールしており、昇格コマンドが無表示でもビルドは進行中でした。ビルド中に非稼働のBlue枠が旧版1で503を返しましたが、CaddyはGreen版2を維持し、昇格後にBlue版3へ切り替わりました。

<a id="runbook--障害時"></a>

#### 障害時

Capitalのreview/approveでHTTPが失敗した場合、書込が一部または全部完了している可能性がある。先に登録版・証拠・決定tagを読み戻し、同じ版/入力/担当者/理由を照合する。理由や時刻を変えて上書きしない。挙動と使い捨て再現コマンドは[T13a HTTP障害試験](capital.md#capital-decision-http)を参照。現環境のUTC逆行とは別の検証である。

promote/recoverの状態・承認者/理由・中断地点の扱いは[配信復旧の契約と隔離受入](runbook.md#deployment-recovery)を参照してください。未完了が複数ある場合や旧metadataを復元できない場合は自動確定しません。補償未確認はrecovery_requiredのままです。履歴の旧版は、実artifactを読めることを確認するまでrollback許可を意味しません。

T03のSIGTERM終了待ちやT13の未来時刻拒否が再発した場合は、[中断・時計診断](clock-history.md#interruption-clock-diagnostics)に従ってphase/stackと実際の比較UTCを保存します。今回の環境ではUTC逆行を観測しており、拒否条件を緩めて承認を通さないでください。

2026-11-01の[変更元の追加調査](clock-history.md#clock-origin-investigation)では、timesyncd相当の負方向offset注入とchronydによるtick書込をkernel traceで確認し、MONOTONICもWindows QPCより約10%速い区間を観測しました。単調増加だけで正しい秒数を保証しないため、実候補の運用測定前に時計の資格を確認します。既存の数値を現在の比で補正せず、時計設定・同期service・Docker停止を無断で変更しないでください。現在の呼出元と歴史的T03停止の原因未特定を区別します。

続く[管理元照合](clock-history.md#clock-process-ownership)ではUbuntuのsystemd-timesyncd.serviceをPID対応付きで確認しました。chronydの外側process読取は[Windows管理者向けの限定採取手順](clock-operations.md#clock-owner-admin-read)へ分離しています。PowerShell policy/管理者拒否を成功と扱わず、稼働中の別daemon用とは限らない設定fileを推測で編集しないでください。

- `/ready`失敗: `/version`とAPIログ、MLflow登録版の状態、PostgreSQL状態を確認。`Invalid Host header`の場合はCompose内部のMLflow名を`mlflow --allowed-hosts`へ限定追加し、MLflow/APIを再作成する。候補を入口へ手動で向ける前にreadyを通す。
- 予測記録失敗: 成功応答は返らない。DB障害を直し、同じリクエストID・入力で再送する。
- 候補起動・検証失敗: 入口は旧版のまま。候補のエラーを調査してから再試行。
- 切り替え後に入口版が食い違う: `deploy recover`で照合。未知の版なら手作業で入口と記録を一致させるまで再昇格しない。
- リプレイの再実行: 同じデータ・版・時刻は同じリクエストIDを使い、既存予測を返す。別モデル版での評価は版ごとに独立したIDを使う。


<a id="runbook--旧postgresql予測列の移行検証（t08）"></a>

#### 旧PostgreSQL予測列の移行検証（T08）

現行initializeは`predictions.predicted`がREALならDOUBLE PRECISIONへ移行します。実DBに新コードを適用する前に、[T08の検証・復元手順](runbook.md#prediction-precision-migration)で制約を確認してください。専用fixtureでは保存値・metadata・同一ID再送・中断時rollback・別volumeへのbackup/restoreを検証します。旧保存時に失われた桁は戻らず、APIの十進表示は変わる場合があります（例: 415.72473→415.7247314453125）。過去JSONとの完全一致を保証した検証ではありません。実DBの移行は今回行っていません。

2026-11-03更新: 新規配信のAPI・state・台帳の操作ID照合と、CLI promote900秒/recover300秒の全体deadline、未確定時の手順は[配信診断](runbook.md#deployment-observability--2026-11-03-t17b-操作の照合とcli全体の待機上限)を参照。最終受入状況は[検証索引](reviews/20261103-t17b-t07a-checks.json)を確認する。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。

<a id="runbook--2026-11-08：保存したcapital事前protocolを読む"></a>

#### 2026-11-08：保存したCapital事前protocolを読む

正式snapshot/固定split/完全SHAと読取コマンドは[T10 snapshot review](capital.md#t10-snapshot-review)を参照する。
`data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/`のCSV/manifest/protocolは保存済み。
実行ログは`data/reports/20261106-t10/`、新SQL実装のCIログは`data/reports/20261108-t10-ci/`へ分けて保存する。
学習や登録をしていないため、MLflow UIにはこの新protocolの精度runはまだない。

protocol保存は評価窓の消費予約ではない。fit前にはledger/legacyの未使用状態と同じinput/source/依存bytesを再照合し、
時計資格・独立源/不確かさ・修復/保守・その他の前提が揃うまでcandidate/fit/測定/承認を開始しない。
過去の失敗出力pathやprotocolを上書きせず、失敗・中断した消費窓も無断再利用しない。

<a id="runbook--追加時計資料の提出と合成rawの移送（2026-11-08）"></a>

#### 追加時計資料の提出と合成rawの移送（2026-11-08）

[必要入力一覧](clock-operations.md#clock-input-request)に従い、新しいPHC/socket管理者ログや利用できる独立源資料の保存先を伝える。
資料が無い場合はOS同期操作や同じtraceを反復せず、実時計資格・実fit/採点は保留する。
既存[管理者限定読取](clock-operations.md#clock-owner-admin-read--chrony-resources-admin)にOS変更は含まれない。

合成原本を別processへ渡す場合は[raw bundle手順](clock-implementation.md#clock-raw-bundle)を使う。
export 0は保存完了のみ、review 1は違反、2は不足/数値成功でも未資格（不正入力も2）。
保存reportの有無とstatusを合わせて確認し、既存fileを上書きせず、ZIPを実時計/実候補の証拠へ流用しない。
原本ZIPはignored reports下で保持し、CI artifactの公開allowlistには加えない。

<a id="runbook--2026-11-14：実候補runtimeの受入"></a>

##### 2026-11-14：実候補runtimeの受入

[専用registry・API・監視の実行結果](capital.md#capital-runtime-acceptance)を追加。registry5001/version1、API8110は候補専用で配信blocked。
実API1,461件とmonitor1,464窓のCSV/JSON/marker/PGを照合済み。運用測定・復旧・全producer採否は後続条件を確認する。

追補：[測定保留と部分復元の最終記録](capital.md#capital-operational-hold)。50+1000/エラー0/p95 84.05msは数値上の結果のみで、
全journalのUTC逆行2件を検出し運用受入を保留。同artifactの別volume対応復元と一時resource cleanupは成功。
運用gateはnot_evaluated、配信blocked。再測定/台帳reopen/時計研究を自動で行わない。

<a id="deployment-observability"></a>

## deployment-observability

<a id="deployment-observability--配信処理の進み具合・timeout・旧stateの読取診断（t17b）"></a>

### 配信処理の進み具合・timeout・旧stateの読取診断（T17b）

`deploy promote`と`deploy recover`は、配信の成否とは別に、現在の処理段階をstderrへJSON Linesで表示する。通常の最終結果JSONはstdoutに残る。
同じ診断行は、対象profileのruntime directoryにある`deployment-progress.jsonl`へ追記する。これは承認証拠や配信台帳の代用品ではない。

表示する段階はregistry照合、Compose build/起動、候補のready待ち、artifact取得と予測比較、proxy操作、recover。
開始・15秒ごとの処理中・段階終了・中断/失敗を記録し、UTCとprocess内のMONOTONIC経過秒を分ける。
UTCが逆行しても経過時間はUTC差から算出しない。段階終了は、その段階の関数が返ったという意味で、配信承認やAPIの正常稼働全体を示さない。
prepared後のbuild/ready/probeにはdeployment IDを付ける。registry事前確認やrecoverの外枠ではIDが未確定のためnullになり、process IDとlog内の順序で区別する。
コマンド本文、環境変数、承認理由、接続文字列、Dockerのnative出力は診断行へ入れない。logは新規作成時0600で、symlinkや通常file以外への追記を拒否する。

<a id="deployment-observability--timeoutの意味"></a>

#### timeoutの意味

通常CLIのCompose build/起動は600秒、Caddy validate/reload等は30秒を上限にする。
この上限はDocker clientを待つ時間であり、daemon側のbuildや起動が取り消されたことを保証しない。
promote中のDocker timeoutは、台帳を`in_progress / recovery_required`のまま保存し、切戻し成功や配信失敗の確定を自動申告しない。
まず対象profileで`deploy inspect`を実行し、診断logと入口・runtime・台帳を確認してから、手動で`deploy recover`を判断する。
通常のready失敗には既存の補償と旧版確認を適用し、補償未確認なら同じく未確定を残す。

**2026-11-02時点の制限（後述2026-11-03節でCLI全体deadlineを実装）:** MLflow registry/artifactダウンロード自体を別processで打ち切るend-to-end timeoutは未実装。
その処理中のheartbeatを追加しただけで、全配信が一定時間内に終わるとは主張しない。Dockerのtimeout値を運用p95や本番SLOに流用しない。

<a id="deployment-observability--読取専用の旧state診断"></a>

#### 読取専用の旧state診断

```bash
cd /home/kenshin/work/claude_practice/261021
./scripts/bikectl deploy inspect
# Capitalでは、使っているprofileを明示する。実DBへの実行は運用者が判断する。
DATA_PROFILE_ID=capital-bikeshare ./scripts/bikectl deploy inspect
```

このコマンドはDBを初期化せず、stateを書換えず、registryや配信へ接続しない。
SQLiteは`mode=ro`と`query_only`、PostgreSQLはread-only repeatable-read transactionとquery/lock/connect timeoutを使う。
DB profileを確認してから、未完了行とstateの`last_deployment_id`に対応する行を読む。UTC順で最新の承認を推定しない。
stateは通常file・64KiB以下・重複JSON keyなしを要求し、読取前後でfile identityとbytesを確認する。

| 状態 | 終了値 | 読み方 |
|---|---|---|
| `matched` | 0 | 現stateと対応するcompleted行の版・slot・操作ID・承認者/理由が一致。配信承認やモデル真正性の証明ではない |
| `metadata_mismatch` / profile不一致 | 1 | 差異のある項目名を表示する。旧承認情報を上書きしない |
| `legacy_unverifiable` | 2 | bootstrap等の対応行がない。欠けた履歴を作らない |
| `incomplete_deployment_requires_reconciliation` | 2 | 未完了操作がある。処理の継続・遅延完了を調査する |
| `unavailable` / `state_changed_during_inspection` | 2 | 読めない・形式不正・読取中に変更。正常と確定しない |

既存stateの自動移行、APIへの操作metadata提示と厳密照合、registry/artifactのprocess全体timeoutは後続条件。
T17b全体をこの部分実装だけで完了扱いにしない。

<a id="deployment-observability--2026-11-03-t17b-操作の照合とcli全体の待機上限"></a>

#### 2026-11-03 T17b: 操作の照合とCLI全体の待機上限

以降の新規promoteはschema3 contextをprepared行へ保存し、`DEPLOYMENT_ID`と`DEPLOYMENT_SLOT`を候補APIの環境へ渡す。APIは起動時にこの組を固定し、`/version`と`/ready`で`deployment_operation`を提示する。DB profile確認後、その操作IDの行だけをread-only transactionで読む。モデル版やUTCによる最新行検索は行わない。

照合対象はprofile/target・操作ID・to_slot/to_version・準備時に固定したartifact SHA・実ロードSHA・申告された操作者/理由。stateにも同じ`deployment_operation`を保存する。台帳が`in_progress`なら`matched_pending`、completedなら`matched_completed`と表示し、readyだけで操作完了とはしない。`approval_evidence:false`を維持する。名前・理由は操作者の申告で、本人認証や独立した審査の証明ではない。URI/credential形の申告はAPIへ表示せず`unsafe_declaration`にする。

| APIの状態 | 意味 |
|---|---|
| `matched_pending` / `matched_completed` | 指定した1行と稼働モデルのidentityが一致。`/ready`は200になり得る |
| `legacy_unverifiable` | 起動時に操作IDなし。旧API/bootstrapとの互換用で、過去の承認を補作しない |
| `operation_missing` / `invalid_operation_binding` | 指定行なし、または操作ID/slot不正 |
| `database_profile_mismatch` / `ledger_profile_mismatch` | DBまたは対象行のprofile/target違い |
| `runtime_identity_mismatch` / `artifact_identity_mismatch` / `operation_metadata_mismatch` | 版/slot、実hash、固定された操作metadataの不一致 |
| `operation_not_active` / `unavailable` | failed行、読取失敗等。照合成功としない |

legacy以外の不一致では`/ready`は503、`/version`は診断状態だけを返し、native SQL/URI/例外本文を出さない。新規候補の配信はmatchedな操作bindingを要求する。旧APIへのrollbackは現ソースをbuildして旧モデルをロードする方式を維持し、古いバイナリ自体の履歴照合を保証しない。contextなし/schema1/schema2のrecover互換経路は保持し、schema3へ自動昇格しない。

通常CLIの`deploy promote`は全体900秒、`deploy recover`は300秒のMONOTONIC deadlineを持つ専用process groupで動く。registry/artifact取得・hash照合・deserialize・probe・proxy・state/台帳確定まで含む。モデルobject/pickleを親へ返さず、有限値のJSONだけを返す。Docker/HTTPの個別上限は全体残時間を超えない。段階logに`remaining_seconds`を加え、モデル取得の各段階・probe HTTP・state/台帳確定を区別する。

期限/TERM/INT時は所有PIDの開始identityとPGIDを照合してTERM、最大5秒後にKILL、leader終了待ち最大2秒と子孫停止確認最大2秒を設ける。これはOSスケジューリングを含む厳密なリアルタイム保証ではない。cleanupが確認できなければ非0で未確定を報告する。prepared後の中断はin_progress/recovery_requiredを残し、強制KILLで更新できなくてもprepared等の未完了行が残る。registry事前確認だけで失敗した場合は配信行を作らない。

Docker daemonや送信済みHTTPの完了はprocess停止で取消保証できない。固定probe request ID `deploy-probe:<deployment_id>`と台帳・入口・stateを調査し、必要なら手動recoverする。自動retryは行わない。completed commit後の応答喪失でも完了行を未完了へ戻さず、結果はinspectで確認する。失敗時の補償も同じ全体予算内であり、補償時間だけを無制限に延長しない。

この上限はCLI配信操作の上限で、API起動自身のloader時間や予測応答SLOを変更するものではない。合成試験の時間を実Capital性能・時計資格として使わない。受入原本・失敗・未達ケースは[今回の索引](reviews/20261103-t17b-t07a-checks.json)に追記する。

Composeへ必要なDB設定3項目（`APP_DB_PASSWORD`、`MLFLOW_DB_PASSWORD`、`POSTGRES_ADMIN_PASSWORD`）と`APP_DB_HOST_PORT`も子processの明示環境へ渡す。環境変数だけの構成とport上書きを維持し、これらの値をargv/診断logへ表示しない。その他の無関係な環境変数を一括継承しない。

最終確認は実装4a4474eの[CI索引](reviews/20261103-t17b-t07a-ci-checks.json)。全447件・artifact/cleanupまで確認済み。冒頭の部分実装に関する記述は2026-11-02時点の履歴であり、現在のCLI契約は2026-11-03節を使う。

<a id="deployment-recovery"></a>

## deployment-recovery

<a id="deployment-recovery--配信復旧の状態・承認metadataと隔離受入（t07at17a）"></a>

### 配信復旧の状態・承認metadataと隔離受入（T07a/T17a）

2026-11-02追記: 以下のT07a/T17aは当時の境界を保持する。追加した実MLflow/skops対応復元は本書末尾、通常CLIの部分的な段階表示・読取診断は[配信処理の診断](runbook.md#deployment-observability)を参照。

基点`444acc9`。[計画§11.6](../../261014/plan_mlops.md)、[証拠JSON](reviews/20261101-t07-t17-checks.json)を参照。
実DB・実registry・現配信への適用は行っていない。合成UCI契約の試験であり、実Capitalの配信証拠producerには使用しない。

<a id="deployment-recovery--修正した問題"></a>

#### 修正した問題

従来のrecoverは、候補へ切り替わっていてもruntime stateへ新しいapprover/reasonを反映せず、bootstrap時の情報を残していた。
またpromoteの補償reloadが失敗しても台帳をfailedへ確定するため、次のrecoverが未完了を見つけられない場合があった。
recoverにはpromoteと共通のlockがなく、未完了が複数あればcreated_atの降順で一件を選んでいた。UTC逆行下で順序の根拠にできない。

今回の変更:

- promote/recoverは同じruntime-local flockを使う。他操作中はlock取得を拒否する。
- promoteのprepared行にschema2 recovery contextを保存する。変更前stateと、旧/候補のmodel名・版・artifact SHA・gate・特徴量版・profile/targetを固定する。
- normal completionとcandidate側recoverは、同じ台帳行のdeployment ID・approver・reasonをstateへ書く。
- 旧版側recoverは保存した旧stateを復元する。旧metadataを推測で作らない。
- model名/版/hash/gate/feature/profileとreadyを照合し、再読取後の不一致も確定を拒否する。stagingのblocked応答を通常配信として認めない。
- 準備後にregistryとAPIが一緒に別artifactへ変わっても、schema2に固定したidentityとの不一致を拒否する。
- Capitalでは既存の全証拠・決定の配信gateをrecoverでも要求する。
- 切戻しを確認できなければin_progress/recovery_requiredを残す。実際の旧版/readyを照合してからfailedへ確定する。
- 未完了が複数なら自動選択せず拒否する。新規promoteも未完了を先に解消するよう要求する。
- 過去版の履歴は保持するが、rollback要求時に実artifactの読み込み/hash/gateを事前確認し、利用不可ならenv/台帳/候補起動より前に拒否する。

<a id="deployment-recovery--中断地点と復旧"></a>

#### 中断地点と復旧

| 観測した入口 | 台帳・state | 処理 |
|---|---|---|
| 旧版 | prepared/candidate_ready、state旧版 | 旧版のregistry identity/readyを再検証し、保存済み旧stateへ戻してretry_candidate/failed |
| 候補版 | proxy_switched、state旧版 | 候補の固定identityとreadyを照合し、新しい操作ID・承認者/理由をstateへ反映しcompleted_after_restart/completed |
| 候補版 | state更新済み、台帳確定前 | 同じ操作のstateと台帳を一致させる。別の承認者/理由を作らない |
| 不明な版・profile/hash違い・複数未完了・補償未確認 | 未完了のまま | 自動で確定しない。入口/runtime/台帳を調査する |

既存stateの旧承認情報をcompleted台帳から診断・移行する経路は未実装で、現配信へ自動適用していない。
既存のcontextなし/schema1行は、現在のregistry/入口の照合を行う互換経路で扱う。元artifactの準備時固定を遡及証明しない。
旧版へ戻す際にstateが候補へ進んでいて旧stateを保存していなければ、旧承認metadataを復元できないので拒否する。
新規schema2でidentityが欠けている場合、legacyへフォールバックしない。

<a id="deployment-recovery--検証の境界"></a>

#### 検証の境界

単体試験は一時SQLiteとregistry/Compose/HTTPのdoubleで、状態・lock・拒否・補償・legacy・Capital gateを確認する。
[scripts/runtime_checks.py deployment-recovery](../scripts/runtime_checks.py)は別のopt-in試験で、実Caddy container、通常FastAPI router、専用PostgreSQL、host上の通常CLI処理を使う。
Compose呼出しは専用Docker resourcesへ変換し、registryとmodel loaderは明示した合成doubleを使う。モデル資料は定数予測を表すJSONで、実MLflow/skopsの復元試験ではない。

ケースは同一ID4並行→元予測1行/異入力409、版切替後の元予測返却、専用PG制約による監査保存503/追加行なし、破損artifact/ready拒否、実SIGKILL3地点、API停止検知/再開、failed gate拒否、DBとモデル資料の対応snapshotの別volume復元。
SIGKILLは専用worker自身だけへ送る。3地点はcandidate_ready、proxy_switched、state書込後/台帳completed前。
復元時は5table全行と各モデル資料hashを比較し、別APIで同じrequest IDを再送して元の版2を返すことを確認する。

全container/network/volumeは固有labelを付け、所有を確認して回収する。既存bike-mlops-* containerのID一覧は前後一致を確認する。
元のDockerfileを再buildする試験ではない。既存API imageにtzdataがなかった初回失敗を保存し、lockと一致するhostのtzdata packageだけを一時workspaceへ複製した。既存imageやvenvへのインストールはしない。
本番imageの再現可能build、実Capital、実MLflow/skopsの対応backup/restoreと証拠producerは別の未了条件。

<a id="deployment-recovery--再実行"></a>

#### 再実行

既存のlocal Unix-socket Docker daemonと、script冒頭の3 cached imageが必要。現在のstackを停止してポートを空ける手順ではない。
新しいevidence directoryを指定する。既存directoryは上書きせず拒否する。

```bash
cd /home/kenshin/work/claude_practice/261021
RUN_ID=$(.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)')
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/runtime_checks.py deployment-recovery \
  --run-disposable --evidence-dir "data/reports/deployment-recovery-$RUN_ID"
```

raw log/dumpはignored reportsへ残す。summaryのstatusだけでなくcleanup_complete、resources_remaining、protected_container_ids_unchangedも確認する。
CLOCK_REALTIME/MONOTONICの問題は未修復なので、fixtureの待機時間やunit test所要秒を運用p95・時計資格の合格証拠にしない。
通常sandboxではTestClient開始待ちを再現し、stack/timeoutを保存した。同じ試験はhost側で成功したが、sandbox内の根本サブシステムまでは特定していない。

<a id="deployment-recovery--残る条件"></a>

#### 残る条件

T17全体は未完了。通常CLIのbuild/artifact取得/待機の段階表示・経過/timeout時の未確定表示、API側での操作metadata提示は別作業。
CLIの現在のDocker subprocess全体timeoutは今回追加していない。harnessの5秒ready設定はfixture用で、通常の90秒を緩める変更ではない。
T07全体も、Capital契約の配信証拠producer、実MLflow/skopsと登録版を含む復元、現行Dockerfileからの隔離build/CI接続などが残る。
既存UCIの版1や実DBへ移行/障害注入していない。T13のHTTP部分書込/応答喪失、T16のCI原本保存は別に進める。


<a id="deployment-recovery--最終local検証と制限"></a>

#### 最終local検証と制限

最終対象は復旧24件、安全性5件が成功。実Docker受入の最終試行はv6で全ケース成功・cleanup残存0・既存container ID不変。
中間sourceの全361件は成功したが、最終production sourceの全364件は既存の一時SQLite承認試験1件でfuture拒否が自然再発した。
review生成から拒否までMONOTONICは389118413ns進み、review UTCは比較UTCより4170111µs先だった。今回変更していないdecision/evidence/test/diagnosticのhashも照合した。
全体成功とは記載せず、同じ理由でgreenになるまで再実行しない。新たな安全性5件はこの364件の後に追加し、単独成功を確認した（localで全369件通過とは主張しない）。
T13の厳密拒否は維持し、OS修復や時計資格をこの受入の成功で代替しない。CI確認は別途記録する。

<a id="deployment-recovery--2026-11-01-ci追跡"></a>

#### 2026-11-01 CI追跡

実装f341e94の[CI検証索引](reviews/20261101-t07-t17-ci-checks.json)で、全369件と既存合成system/PostgreSQL/cleanup成功・artifact hashを確認した。本書の新復旧harnessの実Caddy受入自体はlocal実行で、CI接続は残る。local全suiteの既存UTC拒否も取り消さない。

<a id="deployment-recovery--t07b-合成capitalの実mlflow・skops・db対応復元"></a>

#### T07b: 合成Capitalの実MLflow・skops・DB対応復元

`system_harness.py run --run-synthetic-system --verify-paired-restore`で、現Dockerfileからbuildした独自imageを使う。
既存のblocked candidate登録・API・監査・計測が終わった後、専用APIとMLflowを止め、registry DBと予測監査DBを`pg_dump -Fc`で採取し、MLflow artifact volumeの全通常fileを対応させる。
専用fixtureには他writerが存在しない。DB内容のcanonical hash・全public table件数・column定義・sequence値を採取前後で比較する。稼働中の一般システムに対する無停止backup手順ではない。

復元先は別UUIDのCompose project、新しい3 volume、別のランダムloopbackポートを使う。
同一の現Dockerfile image IDを指定して、空のDBへ復元し、MLflowを起動する前にDB全内容・column/sequence・artifact相対path/byte数/hashを比較する。
MLflowへ登録済みのversion・candidate・report/protocol hash・blocked tagを保持し、staging APIの通常loaderでhash確認後にskopsモデルを読む。
保存済み要求への再送が同じ版・予測を返すことと、新しいrequest IDでも同じ予測を生成することを別々に確認する。
復元したskops fileだけを破損させた負例では、通常loaderがSHA不一致で拒否し、保存済み原本へ戻した後にAPIを起動する。
旧stateのPostgreSQL読取診断も、対応行なし/別profileを合成fixtureで確認する。

artifactは通常fileとdirectoryだけを受け入れる。欠落・余剰・同件数の値変更・symlinkは照合を拒否する。
sourceとrestore両projectのlabelを確認して回収し、現配信のcontainer ID不変を確認する。
native dump・artifact原本は一時directoryだけに置き、CIへアップロードしない。CIの既存`synthetic-system-ci.log`へ、相対file hash、table/column/sequenceの照合値、版、拒否結果、別volume/cleanup結果を保存する。
T16のarchive3本のallowlistは増やさない。実装commitのCI確認までは、この追加部分のCI受入を完了としない。

合成Capitalで実MLflow/skopsを動かす試験であり、実候補の復旧producer、実承認、時計資格、実環境の災害復旧証拠ではない。
T07aの実Caddy/SIGKILLによる切替復旧harness自体のCI接続と、実candidateに結合したproducer証拠は引き続き残る。

<a id="deployment-recovery--t07bの実装ci確認（2026-11-02）"></a>

##### T07bの実装CI確認（2026-11-02）

実装`2414b20`の[CI41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218)は419 tests/54.813秒・両job全stepが成功。[照合索引](reviews/20261102-t07b-ci-checks.json)に保存した。
artifact13150206991の外側ZIP SHAは`4c010bcb5591152645c6004a38d99ffac21d61137fcefb2e3ddfb62b4461f802`、全13 memberのhashを照合した。
registry65table・audit5table（744観測/1052予測）・column/sequence・8 artifactの対応、別3volume、破損モデル拒否、同ID再送/新ID推論を確認した。
restoreの残存container/network/volume/imageがすべて0、workspace回収済み、source project cleanupと保護container ID不変も確認できた。
T16の内側ZIPは`c6ca847386fe751a8d559a6b1ffc2e41e2b90e3eaada4581c22ce5fd82cab66d`で、archiveだけを渡した別processの再計算も一致した。
T07bの合成対応復元を完了とし、T07a復旧harnessのCIと実producerは別の未完了条件として維持する。

<a id="deployment-recovery--2026-11-03-t07a更新-現dockerfileとterm-cleanup"></a>

#### 2026-11-03 T07a更新: 現DockerfileとTERM cleanup

上記のcached image/tzdata複製は旧試行の記録。現在のharnessはallowlistで複製したworkspaceの現Dockerfileを固有tag・UUID labelでbuildし、image ID/labelを確認してAPI/worker/復元APIへ渡す。既存stackのtagを上書きしない。全resource回収後はimageも同じ所有確認をして削除し、残存0と保護container ID不変を確認する。

`--term-after-startup`は実PG/API/Caddyの起動後にharness自身へTERMを送り、非0終了・signal記録・cleanupを確認する専用負例。通常runの3停止点SIGKILL/recoverでは、新しいAPI操作binding・state・同じ台帳行も照合する。復元APIの元要求再送はlegacy起動であり、新しい配信操作の承認を作らない。T07bの実MLflow/skopsとは引き続き別の合成受入である。

CIにはnormal/TERMの2 runと明示したlog/summary3 fileだけを追加する。dump、環境file、raw HTTP、model資料は公開allowlistに加えない。CI実装commitのartifact/member hashと両runのcleanupが確認されるまではCI受入を完了扱いにしない。build client自体のtimeoutでdaemonに作業が残る場合の取消は保証せず、残存またはcleanup未確認は成功にしない。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。

<a id="prediction-precision-migration"></a>

## prediction-precision-migration

<a id="prediction-precision-migration--予測保存列の精度移行と復元の検証（t08）"></a>

### 予測保存列の精度移行と復元の検証（T08）

T08は既存の`Database.initialize()`がPostgreSQLの`predictions.predicted`をREALからDOUBLE PRECISIONへ移す処理の検証です。実DBの移行は行っていません。現行の`monitor_windows`のREAL列を移行する作業も含みません。最終結果とsource/log/backupのSHAは[検証記録](reviews/20261101-t08-checks.json)、試行の経緯は[作業ログ](../work_log.md)を参照してください。

REALは約6桁、DOUBLE PRECISIONは約15桁の精度を持ちます。旧REAL保存時に失われた桁は、型を広げても復元できません。T08では`predicted::double precision`で読み出した旧保存値と`struct.pack('!f', value)`のbinary32を照合し、移行後も同じbinary32由来の値を保持することを確認します。新しい予測行についてはbinary64の値を保持できることを別に調べます。[PostgreSQL数値型の仕様](https://www.postgresql.org/docs/17/datatype-numeric.html)を参照。

<a id="prediction-precision-migration--api応答の十進表示に関する制約"></a>

#### API応答の十進表示に関する制約

通常のpsycopg読取では、旧REALの短い十進文字列がPython floatへ変換されます。DOUBLE PRECISION化した後は、保存されていたbinary32の値をより多くの桁で表すため、APIの数値表示が変わる場合があります。合成fixtureで次を観測しました。

| 旧REALの読取/API値 | 移行後の値 | 保存値の照合 |
|---:|---:|---|
| 0.33333334 | 0.3333333432674408 | 同じbinary32 |
| 415.72473 | 415.7247314453125 | 同じbinary32 |
| 15.189926 | 15.189926147460938 | 同じbinary32 |

これは予測モデルを変更した結果ではありません。旧保存値・request ID・入力hash・元model版/metadataは保持され、移行後の同じ入力の再送は同じ保存値を返します。モデルを読み直さなくても、元版7を返すことをAPI routerで確認しています。異入力は409です。ただし**移行前に送ったJSONの十進値との完全一致は保証していません**。完全一致が必要な利用者がいる場合は、過去応答の保全や版付きの応答形式を別途設計してから実DBへ適用してください。T08のために既存予測行を丸め直したり、新しい列や応答policyを追加したりしていません。

<a id="prediction-precision-migration--9件の受入と保存する根拠"></a>

#### 9件の受入と保存する根拠

| ケース | 検証内容 |
|---|---|
| 保存値・反復初期化 | 4種類の旧数値、全table行、列型/null/default、request/model/artifact/profile/feature metadataを保持。予測列だけdouble、monitor列はREALのまま。再initializeでも保持。 |
| API再送 | 実router+専用DB、モデル未読込/現在版99でも旧保存版7を返す。同一要求の反復と異入力409、十進表示の変化を記録。HTTPサーバーを起動する試験ではなくTestClient経由。 |
| 新予測と競合 | 新行のbinary64保持、同じIDで違う候補計算値を渡しても保存済みの版/値を維持、入力hash変更は拒否。 |
| profile違い | initialize途中でprofile不一致を検知したら、先行した型変更もtransactionでrollback。 |
| 例外・接続切断 | ALTER実行後/commit前に人工例外または実backend切断を発生させ、全行・schemaの復元と再開を確認。 |
| 実SIGKILL | ALTER後commit前でkillすると旧schemaのまま、commit後killでは移行済み状態を保持。どちらも再initializeが一貫する。 |
| 並行初期化 | 別接続の2 initializerが既存advisory lockを通して完了し、値/metadataを保全。 |
| 旧列不足 | candidate/profile列とmetadataのない合成UCI旧DBを、既存の規則に従って補完。既存の予測値/metadataは保持。 |
| backup/restore | 移行前/後をpg_dump customで保存し、第二の専用container・別anonymous volumeへpg_restore。型・全行・metadata・API再送を照合し、旧backupはrestoreだけでは移行されず、明示initialize後に移行することを確認。 |

backupはこの試験内で作った合成DBだけを対象とします。dumpに認証値は含めず、ファイル名は`before.dump`/`after.dump`を固定します。CI公開対象はこの2ファイル、`summary.json`、試験logだけです。dumpには作成時刻等があり、別試行とのbytes一致を前提にせず、各試行のSHAを記録します。[pg_dumpの仕様](https://www.postgresql.org/docs/17/app-pgdump.html)を参照。

<a id="prediction-precision-migration--再現手順"></a>

#### 再現手順

Docker Desktop/WSL統合が動き、`postgres:17.6-bookworm`がlocalにある状態で実行します。imageを自動pullせず、既存のDB URLやCompose projectは利用しません。

```bash
cd /home/kenshin/work/claude_practice/261021
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/storage_checks.py migration \
  --run-disposable --evidence-dir data/reports/t08-manual-001
```

出力先は新規directoryを指定します。既存directoryを再利用すると停止し、前回のbackup/summaryを上書きしません。試験は専用labelとランダムloopback portの2 containerを作り、非superuserのapp ownerで試験します。終了時は所有ID/labelを照合して専用container/volumeを削除し、残存も検査します。途中失敗のlogとsummaryを成功結果へ置き換えません。SIGKILLをharness全体へ送る場合やDocker自体の障害ではfinallyが動かない可能性があり、cleanup成功出力の有無と専用labelを確認してください。

T08は合成旧schemaの受入です。既存UCIを停止・backup・移行・復元した結果や、T07のartifactを含む復旧、T17の配信metadata一致、実Capital監視/承認の証拠を示すものではありません。

実装commit `20abdab`の[CI 41263492531](https://github.com/kenobix/Claude_practice/actions/runs/41263492531)は全337 tests（40.391秒）、T08のPG受入9 tests（2.549秒）、既存system/PG/T06b照合25件とcleanupが成功した。artifact ZIPと8 memberのhash、移行前/後dumpのsummary hashも一致した。これは合成fixtureの検証結果であり、実DB移行を実行した結果ではない。

<a id="mlflow-orm-compatibility"></a>

## mlflow-orm-compatibility

<a id="mlflow-orm-compatibility--mlflowのnoload非推奨警告の切分け"></a>

### MLflowのnoload非推奨警告の切分け

2026-11-03、基点`298c6c8`からの独立したP2調査。
[検証索引](reviews/20261103-orm-compatibility-checks.json)を参照。
**警告の発生箇所と更新判断は確認済み。上流の修正は未確認で、依存lockは変更しない。**

<a id="mlflow-orm-compatibility--確認した範囲"></a>

#### 確認した範囲

現行lock/venvはMLflow・skinny・tracingが3.17.1、SQLAlchemyが2.1.5。
MLflowの`SqlMCPAccessEndpoint.resolved_version_rel`は`viewonly=True`、`lazy="noload"`の単一関係である。
`sqlalchemy_store.py:327`の全mapper設定時にSQLAlchemyが非推奨警告を出す。
このため、そのMCP関係を直接利用していない入口からのimportでも警告が出る。

SQLAlchemyの[公式説明](https://docs.sqlalchemy.org/en/21/orm/relationship_api.html#sqlalchemy.orm.relationship.params.lazy)は、
noloadを2.1から非推奨としており、将来の削除と関連値がNoneとなる問題を示している。
本repoのsource/testsを検索した範囲では、当該MLflow MCP関係を直接参照していない。
これはMLflowの全機能が将来も安全だという保証ではない。

接続を拒否する専用processで通常のimportを行い、同じ警告とmapper設定を確認した。
比較用processでこの警告だけをerrorにすると、mapper設定中のimportを拒否した（意図したexit23）。
このerror化は検証process内だけで、製品のwarning policyやCI設定へ追加していない。
未公開のSQLAlchemyで試験したことや、将来版が必ず同じ例外になることを意味しない。

現行の使い捨てSQLite publisher回帰は6件成功した。
モデル精度・実DB/registryや、MLflowのMCP機能全体を検証したものではない。

<a id="mlflow-orm-compatibility--なぜ今lockを変えないか"></a>

#### なぜ今lockを変えないか

確認時点の[最新公開release3.17.2](https://github.com/mlflow/mlflow/releases/tag/v3.17.2)と
[固定版のモデル定義](https://github.com/mlflow/mlflow/blob/v3.17.2/mlflow/store/tracking/dbmodels/models.py)を取得した。
ASTで同じ関係の`lazy="noload"`を確認した。
**3.17.1から3.17.2へ上げるだけで、この警告が直る根拠はない。**
警告を消すためのSQLAlchemy downgrade、site-packages直接patch、全warningの抑制、未release版の採用は行わない。

次の再調査条件は、上流の当該loader変更、修正release、SQLAlchemyの削除予定が具体化したとき。
その時点で依存差分を限定し、一時SQLiteの登録/承認/改ざん拒否と、隔離HTTP/PostgreSQL・対応復元・CI artifact/cleanupまで確認する。
実DBのschema移行やMLflow server image更新を、依存の検査だけで勝手に適用しない。
同じ未修正releaseの確認を繰り返しても、修正完了とは扱わない。

<a id="mlflow-orm-compatibility--上流へ報告する場合の下書き"></a>

#### 上流へ報告する場合の下書き

以下は報告案で、外部issueを作成・送信していない。

```text
MLflow 3.17.1 with SQLAlchemy 2.1.5 emits SADeprecationWarning while importing
mlflow.store.tracking.sqlalchemy_store and configuring the shared ORM mappers.
SqlMCPAccessEndpoint.resolved_version_rel uses lazy="noload", viewonly=True.
The same declaration is present in the released v3.17.2 source.
Promoting SADeprecationWarning to an error reproduces an import refusal;
this is a compatibility probe, not an execution on an unreleased SQLAlchemy.
Please review an appropriate loader strategy and direct/alias contains_eager
paths with regression coverage before SQLAlchemy removes noload.
```

<a id="ci-artifact-maintenance"></a>

## ci-artifact-maintenance

<a id="ci-artifact-maintenance--ci-artifact-actionの単独保守（2026-11-03）"></a>

### CI artifact actionの単独保守（2026-11-03）

時計・原本公開待ちから独立したP2作業。基点`7174a1f`。
workflowの`actions/upload-artifact@v4`を`v7.0.2`へ固定し、`archive: true`を明示する。
Node.js20 actionの強制移行警告を対象とし、Python/MLflow依存lockは変更しない。

[公式release](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)と
[固定版action定義](https://github.com/actions/upload-artifact/blob/v7.0.2/action.yml)を確認した。
実行runtimeはNode24。v7で追加された単一fileの非ZIP uploadは使わず、従来の複数file ZIPを維持する。
name、16pathの明示allowlist、14日保存、失敗時のlog回収条件、hidden-file既定除外を保つ。
最初のlocal検証でYAML parse、全10run blockの`bash -n`、正規化差分がtagとarchive指定だけであることを確認した。

受入には実装commitのCI全job/step成功、16member ZIPの実download、GitHub digestとのSHA一致、
内側archiveのmember hashと別process再計算、T07a normal/TERM・T07b/既存PGのcleanup確認が必要。
local構文検証だけでは完了にしない。初期記録は[local索引](reviews/20261103-upload-action-checks.json)。

このCIは合成検証で、実Capital採否・時計資格・本番SLOの証拠ではない。
他の非推奨警告が残る場合は発生箇所と別taskを記録し、warningを消すための広範な依存更新は行わない。

実装`5a687a7`のunit jobは463件64.677秒成功。次の警告はaction更新とは別に扱う。

- MLflowの`sqlalchemy_store.py:327`からSQLAlchemyの`noload`非推奨警告。現行lockでの試験は成功したが、将来の依存互換は個別に確認する。今回警告の抑制・lock更新・上流fileの直接patchはしない。
- ZIP重複memberのUserWarningは、重複を拒否する負例を作ったときのもの。重複archiveを受理した証拠でも通常処理の破損でもない。

全system job・artifact・cleanupの受入は[CI索引](reviews/20261103-upload-action-ci-checks.json)で確認済み。
実装`5a687a7a58ead84240bfd72bfcbe4cfbe31f3559`の[CI41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089)は全job/step成功。
実行時のaction解決SHAは`cf430e030ddbb5b0abf93d22962f4752f3646cd9`、`archive: true`も実logで確認した。
artifact13214080832の233,299 bytes/16member ZIP SHA
`f02bdd44b3082142f0f5cc38ef7b384cf52b9b744c02d1ddf482a278874a8789`はGitHub digestと一致。
内側archiveの4原本hashとarchive-only別process・独立nearest-rank再計算、T07a normal/TERM・T07b対応復元・PG cleanupを確認した。
system logにNode20強制移行/punycode/url.parse警告は見られなかった。すべての依存警告がなくなったとは呼ばない。
合成p95の19.503491msは保存値の検算で、実運用性能・時計資格へ流用しない。
初期local索引のCI_PENDINGは当時のsnapshotとして保持する。

残るSQLAlchemy noload警告は[発生箇所と最新公開版の調査](runbook.md#mlflow-orm-compatibility)を参照。
調査済みの同じreleaseを繰り返し確認せず、当該loaderの上流変更/修正release時に個別互換試験へ進む。
