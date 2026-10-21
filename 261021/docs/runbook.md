# 実行・運用手順

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
python -m pip install -e '.[dev]'
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

このプロジェクトでは初回学習時に固定ホールドアウトを評価した後、MLflow/skopsの信頼型検査でモデル登録に失敗しました。失敗runは`http://127.0.0.1:5000/#/experiments/1/runs/42abda5f8da94b9b8a3d5f5f46da42ad`です。Artifactsの`training-report.json`からholdoutのモデルMAE 72.5362、RMSE 111.5060を作業記録に転記済みです。修正後の再学習は`--evaluate-final-holdout`なしで成功し、`data/reports/training-0db7aa07461b4b8785145156d8de9a66.json`に記録されました。`bike-demand` version `1`が登録され、validation MAE 55.0728（ベースライン63.9628）、RMSE 88.6249（ベースライン107.8187）、品質ゲートpassedを確認しています。成功レポートの`final_holdout`が`null`なのは、既に測定した固定holdoutを再利用・再評価しないための想定どおりの状態です。作業記録は[work_log.md](../work_log.md)を参照してください。

この時点で再学習は不要です。次はレポートの登録版を使ってruntimeを初期化し、APIがartifactを読み込めることを実機で確認します。

学習レポートの`registration.model_version`にある登録版番号に置き換えて、青・緑枠と入口を初期化・起動します。初回起動前に、学習登録がMLflow UI/APIから参照可能であることを確かめます。

```bash
./scripts/bikectl runtime init --version 1 --approver kenshin --reason 'validation gate and artifact hash verified'
docker compose up -d --build api-blue api-green caddy
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version
```

検証MAEが固定ベースラインを下回らなかった場合は通常の初期化・昇格を拒否します。基盤の起動・復旧動作を確認する目的に限り、失敗タグが記録された状態で`--diagnostic`を明示できます。

```bash
./scripts/bikectl runtime init --version 1 --diagnostic --approver kenshin --reason '品質ゲート未達版を使うローカル配信障害試験'
```

診断配信は低精度の可能性を残し、実用性能を満たしたモデルとして紹介しません。

MLflow UIはローカルの`http://127.0.0.1:5000`、入口APIは`http://127.0.0.1:8080`です。PostgreSQLは`127.0.0.1:55432`です。必要なとき以外はポートを公開しないでください。

## 4. 候補の昇格・差し戻し

### 時系列を保った再学習候補

初回モデルのモデル版を使って、同一の後続期間で比較します。既定例では初回固定ホールドアウトの後ろにある2012年第3四半期を候補検証に使い、トレーニングカットオフ以降のラベルを学習に入れません。第3四半期の結果を確認後に、第4四半期を独立したAPIリプレイ期間として使います。

```bash
./scripts/bikectl candidate --incumbent-version 1
```

出力レポートにincumbent、履歴ベースラインとの同一行MAE/RMSE、候補のカットオフ、検証窓が記録されます。候補品質ゲートはincumbentの品質ゲートが`passed`で、かつ候補MAEがincumbentと履歴ベースラインの双方を厳密に下回る場合だけ通ります。登録後も人が審査するまで稼働版は変わりません。検証窓を変更するときは、期間を見てから都合のよい窓を選ばず、選択理由を作業記録に残します。

任意の評価窓は、候補の学習カットオフ以降に置きます。

```bash
./scripts/bikectl candidate \
  --incumbent-version 1 \
  --training-cutoff '2012-07-01 00:00:00' \
  --validation-start '2012-07-01 00:00:00' \
  --validation-end '2012-10-01 00:00:00'
```

### 手動の昇格

新しい学習版を作り、validationゲート結果をMLflowで確認したあと、承認理由を付けて昇格します。

```bash
./scripts/bikectl deploy promote --version 2 --approver kenshin --reason '検証MAEが固定ベースラインを下回り、readinessと固定予測を確認'
```

通常昇格は`quality_gate=passed`を必須にします。未達版の試験だけ、明示的に`--diagnostic`を付けます。

更新途中で停止したときは入口の版と台帳を照合します。

```bash
./scripts/bikectl deploy recover
```

以前に稼働した登録版へ戻す場合も理由を残します。

```bash
./scripts/bikectl deploy promote --version 1 --rollback --approver kenshin --reason '新候補に異常を確認したため直前版へ戻す'
```

## 5. 履歴リプレイと遅延ラベル監視

入口が対象登録版を配信していることを確認してから実行します。

```bash
./scripts/bikectl replay --model-version 2 --start '2012-10-01 00:00:00' --end '2013-01-01 00:00:00'
```

この候補例では第3四半期を学習候補の選定に使ったため、独立した後続評価には第4四半期だけを使います。候補をローカルの手動承認済み試験版として配信してから、`--model-version`にその登録版を指定します。初回版と比較する場合は、同じ第4四半期を別途初回版でもリプレイし、版ごとのレポートを並べて確認します。

出力の`replay_id`を指定して監視窓を計算します。

```bash
./scripts/bikectl monitor --replay-id DATAHASH-2012070100-2013010100 --model-version 1
```

CSVとDBに窓ごとの予測数、到着ラベル数、到着率、MAE/RMSE、固定ビンPSIを保存します。ラベル到着前は誤差を0扱いせず分母から外します。PSIと誤差悪化は別々に読み、通知は調査開始にだけ使います。

## 障害時

- `/ready`失敗: `/version`とAPIログ、MLflow登録版の状態、PostgreSQL状態を確認。候補を入口へ手動で向ける前にreadyを通す。
- 予測記録失敗: 成功応答は返らない。DB障害を直し、同じリクエストID・入力で再送する。
- 候補起動・検証失敗: 入口は旧版のまま。候補のエラーを調査してから再試行。
- 切り替え後に入口版が食い違う: `deploy recover`で照合。未知の版なら手作業で入口と記録を一致させるまで再昇格しない。
- リプレイの再実行: 同じデータ・版・時刻は同じリクエストIDを使い、既存予測を返す。別モデル版での評価は版ごとに独立したIDを使う。
