# 作業記録

用語が分からないときは[初学者向け用語集](docs/glossary.md)を参照してください。

## 2026-10-21: 実データ取得・Docker/WSL接続・Compose初回学習

### 目的

UCI Bike Sharingの実データを取得・監査し、Docker Desktop上のPostgreSQLとMLflowを起動して、実データで最初の学習runを記録する。

### データ取得と監査

先行して発生したダウンロードエラーは、UCI ZIP内の`hour.csv`がZIP直下にある一方、抽出処理が階層付きのファイル名だけを検索していたことが原因だった。抽出条件を修正して再実行した結果、ダウンロードと監査は成功した。

実行コマンド:

```bash
mlops-bikectl data download
mlops-bikectl data audit
```

確認結果:

- UCI Bike Sharing Dataset、CC BY 4.0。
- ZIP SHA-256: `b70182d0d0508e9abbb79306ce5c0cec34869000f8220175ac83d11dbe845401`
- CSV SHA-256: `e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`
- 17,379行、2011-01-01 00:00〜2012-12-31 23:00。
- 期間内の期待時間数17,544、欠損時間165、重複タイムスタンプ0。
- `cnt`: 最小1、最大977、平均189.46308763450142。
- 時刻はデータセット内のnaive timestampとして扱う。実績ラベルが時刻+3時間で利用可能になる規則は、実データ測定ではなく実験仮定。
- 欠損時間は行補完・削除せず、監査と時刻一致ラグ特徴量の欠損として扱う。

### Docker DesktopとWSL

初回の`docker version`と`docker run --rm hello-world`は、`unix:///var/run/docker.sock`へのpermission deniedで失敗した。Python venvの問題ではなく、Docker Desktopエンジンへの接続権限・WSL連携の問題と判断した。Docker DesktopのWSL Integration画面には未保存変更があり、UbuntuはWSL 2、WSL自体も2.6.3だった。

連携設定を適用してDocker Desktopを再起動し、PowerShellから次を実行した。

```powershell
wsl.exe --shutdown
wsl.exe -d Ubuntu
```

`wsl.exe --shutdown`後、Ubuntuを明示的に起動して作業シェルに入る必要があった。起動直後は`/mnt/c/WINDOWS/system32`にいたため、`cd ~`でホームへ移動してVS Codeを起動した。その後、プロジェクトディレクトリから接続を再確認した。

```bash
docker version
docker run --rm hello-world
```

Docker Desktop 4.96.0 / Engine 29.9.1でClientとServerの両方が表示され、`Hello from Docker!`が出た。以後、Docker CLI・daemon疎通は成功している。

### VS Codeの`.env`通知とCompose設定エラー

```bash
test -f .env || cp .env.example .env
code .env
docker compose config -q
```

`.env`作成後のVS Code右下通知は、Python拡張が`.env`の値をPythonターミナルへ自動投入しないという案内だった。Composeはルート`.env`を読み、`scripts/bikectl`も明示的に読み込むため、この通知は今回のCompose失敗原因ではない。

`docker compose config -q`は次のエラーで失敗した。

```text
env file .../data/runtime/blue.env not found
```

この時点で不足していたのはルート`.env`ではなく、モデル版登録後に`runtime init`が作成するAPI用`data/runtime/blue.env`と`green.env`だった。PostgreSQL/MLflowだけを起動する段階でも、Composeが全サービスの必須`env_file`を検証していた。モデル未登録のためファイルが存在しないのは想定された順序だった。

`compose.yaml`のAPI用`env_file`を`required: false`に変更した。Compose v5.6.0で`docker compose config -q`が成功することを確認した。APIコンテナ自体は`runtime init`後に起動する。

### Compose基盤起動

実行コマンド:

```bash
docker compose up -d --build postgres mlflow
docker compose ps
```

結果:

- `postgres:17.6-bookworm`をpull。
- `bike-mlops-mlflow`イメージをbuild。
- Compose networkと`postgres-data`、`mlflow-artifacts` volumeを作成。
- `bike-mlops-postgres-1`: Healthy。
- `bike-mlops-mlflow-1`: Started / Up。
- 公開ポート: PostgreSQL `127.0.0.1:55432`、MLflow `127.0.0.1:5000`。

### テストと実データ監査

ユーザーが実行したテスト:

```bash
python -m unittest discover -s tests -v
```

13 tests、0 failures、終了コード0。Starletteから`httpx`利用に関するDeprecationWarningが出たが、テスト失敗ではない。

Compose接続用CLIで再監査した。

```bash
./scripts/bikectl data audit
```

先行監査と同じ17,379行・165欠損・重複0、CSV SHA-256も同じ`e03de4ee...504f`。データ内容が途中で変わっていないことを確認した。

### 初回学習と失敗内容

実行コマンド:

```bash
./scripts/bikectl train --evaluate-final-holdout
```

MLflow experiment `bike-demand-operations`とrun `42abda5f8da94b9b8a3d5f5f46da42ad`が作られ、MLflow UIにrun URLが表示された。

失敗は学習器のfitではなく、MLflowへモデルを記録する段階で起きた。

```text
mlflow.exceptions.MlflowException: The saved sklearn model references untrusted types.
Untrusted type: sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor
```

venv内で確認した版はMLflow 3.17.1、scikit-learn 1.9.0。MLflowの既定scikit-learn形式はskopsで、HistGradientBoostingRegressorが内部で使う`TreePredictor`をskopsの信頼型リストに明記していなかったことが直接原因。

処理順は、検証・固定ホールドアウト指標の計算、MLflowへの指標・`training-report.json`・model cardの記録、その後にskopsでモデルを保存、となっている。したがって失敗runには評価指標とレポートartifactが記録済みと判断できる。一方、ローカルレポート書き出しとモデル登録は`train()`が正常returnした後なので発生せず、登録済みモデル版もまだない。

固定ホールドアウトはこの失敗runで既に評価された。再試行ではデータを見てから評価条件を変えたり、固定期間を再評価したりしない。まずMLflow UIの失敗runから`training-report.json`または`final_holdout_*` metricsを記録し、登録再試行は`--evaluate-final-holdout`なしで行う。

### 修正と再学習結果

- `training.py`: skopsの信頼型に上記`TreePredictor`だけを明示する。全型を一括信頼する指定は使わない。
- `model.py`: 成果物をskopsで読み込む前に、MLflow登録情報のSHA-256と照合する順序に変更する。
- `README.md`と`docs/runbook.md`: 状態、失敗run、ホールドアウトを再評価しない再試行方法を反映。
- `compose.yaml`: モデル版envファイルをruntime init前は任意にする修正を記録。

ユーザーが修正後に次を実行し、学習・登録が成功した。

```bash
./scripts/bikectl train
```

成功runとレポート:

- Run ID: `0db7aa07461b4b8785145156d8de9a66`。
- ローカルレポート: `data/reports/training-0db7aa07461b4b8785145156d8de9a66.json`。
- 実行時刻: `2026-10-21T13:06:54Z`。
- データSHA-256は前回監査と一致する`e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`。
- source commitは`efdabb17fa8c3f99aac217fa0ae8c715f7971f72`、worktreeはclean。
- split counts: train 8,645、validation 2,176、final holdout 2,182。
- validation: モデルMAE 55.0728 / RMSE 88.6249、前日・曜日時刻ベースラインMAE 63.9628 / RMSE 107.8187。MAEは8.8900（約13.9%）低く、品質ゲート`quality_gate_passed=true`。
- `final_holdout`は`null`。初回の失敗runですでに実施したholdout評価を再計算しないため、今回のコマンドに`--evaluate-final-holdout`は付けていない。
- 登録: `bike-demand` version `1`、URI `models:/bike-demand/1`、artifact SHA-256 `894ebde8080a022f5dc28573babf394c2bfbec1d6e6f4abeadb41d86ea331aab`。
- 実行ログに`No artifacts found to download .../artifacts/sklearn-model`というINFO行が出たが、CLIは成功終了し、登録版・artifact SHA・ローカルレポートが出力された。これはエラー終了ではない。一方、この表示だけではAPIプロセス内のモデルロードまでは証明しないため、runtime初期化とreadinessで引き続き確認する。

初回のholdout結果は、MLflow run `42abda5f8da94b9b8a3d5f5f46da42ad`の`training-report.json`で確認した。split countsはtrain 8,645、validation 2,176、final holdout 2,182。holdoutのモデルMAEは72.5362、RMSEは111.5060。今回手元で確認できた抜粋にはholdoutベースライン値がないため、holdoutでベースラインを上回ったとは結論しない。これは今回のvalidation指標とは別の期間・別の評価値である。

### 次の作業

1. 品質ゲートpassedの版1を使ってruntime台帳とblue/green設定を初期化する。
2. ComposeでAPI青緑枠とCaddyを起動し、入口の`/ready`と`/version`を確認する。失敗時はAPIログでskops artifactの取得・ハッシュ検証・デシリアライズ箇所を調べる。
3. readinessが通った後、観測登録・予測・再送の動作を確認する。
4. API動作の確認後、MLflowを使った継続候補と第4四半期リプレイへ進む。

修正後の実データ学習とモデル登録はユーザー環境で確認済み。ユニットテスト13件はコード変更前に成功したものの、変更後には再実行していない。APIのモデルロード・readiness・予測リクエストもまだ未確認。

## 2026-10-27: runtime初期化とAPI起動確認

### 成功した操作

ユーザーがモデル版1を指定してruntimeを初期化した。

```bash
./scripts/bikectl runtime init --version 1 --approver kenshin --reason 'validation quality gate passed; model version 1 registered'
```

- 初期化成功。deployment ID `7152000b-abd4-415f-81f0-f4cc596f1af7`、active slot `blue`、model version `1`。
- データセットの観測17,379件をseedし、CSV SHA-256は前回監査と同一の`e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`。
- `docker compose up -d --build api-blue api-green caddy`は成功。API青緑枠、Caddy、MLflowがStarted、PostgreSQLはHealthy。
- `docker compose ps`では全サービスがUp。

### API readiness失敗

```bash
curl -fsS http://127.0.0.1:8080/ready
```

応答はHTTP 503。続く`/version`応答は次のとおりで、APIは要求された版番号を持つものの、モデルのロード情報が未設定だった。

```json
{"application_version":"0.1.0","model_name":"bike-demand","model_version":"1","artifact_sha256":null,"quality_gate_status":null,"feature_contract_version":"bike-demand-features-v1"}
```

この結果から、コンテナ起動とモデル配信準備完了は別の状態だと確認できた。現時点で原因は確定していない。APIのstartup処理は登録版を取得してSHA-256照合後にskopsモデルをロードするため、artifact取得、hash mismatch、デシリアライズ等の例外詳細が必要。前回trainログの`No artifacts found to download .../artifacts/sklearn-model`は調査上の手がかりだが、今回の失敗原因だとはまだ断定しない。

次にユーザー環境でAPI枠ごとのreadiness本文とログを取得する。runtime initはすでに成功してstateを作成しているため、再実行せず、先に診断する。

```bash
curl -sS -i http://127.0.0.1:8001/ready
curl -sS -i http://127.0.0.1:8002/ready
docker compose logs --tail=120 api-blue api-green
```

この実行環境からはDocker socketがpermission deniedとなり、添付のコンテナログを直接取得できなかった。上記コマンドの出力が揃った時点で原因と修正箇所を特定する。

### 原因特定と修正

ユーザーからAPI枠ごとのreadiness本文とComposeログが共有され、根本原因が確定した。

```text
API request to endpoint /api/2.0/mlflow/registered-models/get failed with error code 403
Invalid Host header - possible DNS rebinding attack detected
```

ComposeのAPIコンテナは`MLFLOW_TRACKING_URI=http://mlflow:5000`でMLflowへ接続する。MLflowのHost header検査の既定許可リストにはComposeサービス名`mlflow`がなく、APIが送る`Host: mlflow:5000`をDNS rebinding対策が拒否していた。青・緑の両APIで同じ応答を確認した。DB疎通やモデルのシリアライズ形式に到達する前のMLflow API拒否であり、前回trainログにあった`No artifacts found to download`とは別の問題。

`compose.yaml`のMLflow起動引数に`--allowed-hosts mlflow:5000,localhost:5000,127.0.0.1:5000`を加えた。許可範囲を必要なCompose内部名とローカルUIのみに限定し、Host検査全体は無効化しない。

修正後、ユーザー環境で次を実行する。runtime initのdeployment stateは既に作成済みなので再実行しない。

```bash
docker compose config -q
docker compose up -d --force-recreate mlflow api-blue api-green caddy
docker compose ps
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version
```

Compose再作成後のMLflow接続、artifact hash照合、skops読み込み、API readinessはまだ未確認。結果を見て次の障害有無を判断する。

### allowlist反映後もreadiness未達

ユーザーがCompose設定を検証し、MLflowとAPIを強制再作成した。

```bash
docker compose config -q
docker compose up -d --force-recreate mlflow api-blue api-green caddy
docker compose ps
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version
```

- Compose設定検証は成功。PostgreSQLはHealthy、MLflow・API青緑枠・CaddyはStarted/Up。
- しかし入口`/ready`は引き続き503。
- `/version`はmodel version `1`を返すが、`artifact_sha256`と`quality_gate_status`は引き続き`null`。モデルロード完了は確認できていない。
- 新しい503本文や再起動後のMLflow/APIログはまだ共有されていない。このためHost allowlist変更が実際に反映されたか、または次のartifact取得・hash照合・デシリアライズ段階に別の問題があるかは判別できない。
- Codexの実行環境からユーザーのDocker socketへ接続するとpermission denied、`127.0.0.1`のAPIポートにも接続できず、ログを直接取得できなかった。

次は再作成後のAPIエラー本文と直近ログ、稼働中MLflowコンテナの引数を確認する。

```bash
curl -sS -i http://127.0.0.1:8001/ready
curl -sS -i http://127.0.0.1:8002/ready
docker compose logs --since=5m --tail=150 mlflow api-blue api-green
docker inspect bike-mlops-mlflow-1 --format '{{json .Config.Cmd}}'
```

## 2026-10-27: `MLmodel`不足の原因と用語集

### API応答とポート競合の切り分け

ユーザーが青・緑APIへ直接問い合わせた。

```bash
curl -sS -i http://127.0.0.1:8001/ready
curl -sS -i http://127.0.0.1:8002/ready
docker compose logs --since=5m --tail=150 mlflow api-blue api-green
docker inspect bike-mlops-mlflow-1 --format '{{json .Config.Cmd}}'
```

両方のポートでHTTP 503とUvicornの応答が返り、本文は同じだった。

```text
Could not load registered model version 1: No such artifact: 'MLmodel'
```

稼働中MLflowの引数に`--allowed-hosts mlflow:5000,localhost:5000,127.0.0.1:5000`が含まれていた。前回の403 Host拒否は解消し、APIはMLflowへ接続して登録モデルを取得する次の段階へ進んだ。過去の`docker compose ps`では`127.0.0.1:8001->8000`が`api-blue`、`127.0.0.1:8002->8000`が`api-green`に割り当てられていた。HTTP応答とUvicornヘッダーも得られているため、今回の503はポート競合ではなく、APIが起動した上でモデル読み込みに失敗した応答と判断する。

### 原因

学習コードは`mlflow.sklearn.log_model()`でモデルを記録する一方、registry versionのsourceに`runs:/<run_id>/sklearn-model`を組み立てていた。MLflow 3ではlogged modelが通常のrun artifactとは別の場所に保存されるため、この旧URIに読み込みに必要な`MLmodel`がなかった。モデル版1はこの不正なsourceで登録済みであり、コードを直しても登録済み版1のsourceは変わらない。

### 修正した内容

- `training.py`: `log_model()`の戻り値`model_info.model_uri`と`model_info.model_id`をregistry登録に使う。登録後に取得したartifactに`MLmodel`がなければ、URIを含む明確なエラーで停止する。
- `model.py`: API起動時もダウンロードしたartifact内の`MLmodel`を先に検査し、不足時は明確なエラーにする。
- `docs/glossary.md`: 5つのMarkdown文書からリンクする用語集を追加し、MLflow、logged model、artifact、skopsの明示的な信頼型、FastAPI/Uvicorn、psycopgなどを説明。
- READMEとrunbookを更新。次は固定holdoutを再評価せずに修正版で通常学習し、レポートにある新しい登録版（現在の履歴からは通常2）を既存runtimeへ昇格する。`runtime init`は既に実行済みなので繰り返さない。

今回のコード変更後、学習・登録、artifact hash照合、skops読込、readiness、予測はまだ実行確認していない。テストスイートも今回の変更後には実行していない。

## 2026-10-27: MLflow 3修正後の再学習とモデル版2登録

ユーザーがmainを更新後、修正版で通常学習を再実行した。固定ホールドアウト評価オプションは付けていない。

```bash
git pull --ff-only origin main
. .venv/bin/activate
./scripts/bikectl train
```

成功runとレポート:

- Run ID: `f55812c02dac4084a8185856cf7b4793`。
- レポート: `data/reports/training-f55812c02dac4084a8185856cf7b4793.json`。
- 実行時刻: `2026-10-27T13:23:04.933830+00:00`。
- 実行source revision: `2142b4dead60b3b57de61b03200dc050c6f1f8d0`。レポート上worktreeはclean。
- データSHA-256: `e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`。
- validationのモデルMAE 55.0728 / RMSE 88.6249。基準MAE 63.9628 / RMSE 107.8187。
- `quality_gate_passed=true`。ルールの文章にある「strictly lower / strictly beat」は合否条件で、実際の結果は別フィールドに記録される。今回のMAEは基準を8.8900（約13.9%）下回るため合格。
- `final_holdout=null`。固定holdoutは再評価していない。
- `bike-demand` version `2`、URI `models:/bike-demand/2`、artifact SHA-256 `42bb5f3c7dc5a28d562d3fe2b01fe2aaf832af34ef5496b05b0fe65e9a79b337`。

ユーザーは続けて次の昇格コマンドを入力したが、共有されたターミナル記録はコマンドの直後で終わっており、完了・失敗の結果は未確認。

```bash
MODEL_VERSION=2
./scripts/bikectl deploy promote --version "$MODEL_VERSION" --approver kenshin --reason 'MLflow 3 model URI fixed; validation gate passed'
```

昇格実装は候補APIを`docker compose up -d --build --no-deps`でbuild・起動し、最大90秒readyをポーリングし、固定予測を照合してからCaddyの入口を切り替える。Composeコマンドの標準出力はCLI内で捕捉されるため、完了するまで端末が無表示の場合がある。現状は旧稼働枠blueからgreen (port 8002)への切替を試す段階。進行中ならCtrl+Cで割り込まず、別端末で`docker compose ps -a`、greenのログ、`http://127.0.0.1:8002/ready`を確認する。中断済みなら再昇格の前に`deploy recover`で入口と台帳の状態を照合する。

Codex実行環境から同じCompose状態を読むため`docker compose ps -a`も試したが、`unix:///var/run/docker.sock`のpermission deniedで取得できず、実際の昇格結果はユーザーのWSLターミナルで確認する必要がある。

### Greenへの昇格完了

ユーザーが別のWSLターミナルから稼働状態を確認した。ComposeではAPI両枠、Caddy、MLflow、PostgreSQLが起動し、PostgreSQLはhealthyだった。`api-green`の直近5分ログは空だった。

```bash
docker compose ps -a
docker compose logs --since=5m --tail=100 api-green
curl -sS -i --max-time 5 http://127.0.0.1:8002/ready
curl -sS -i http://127.0.0.1:8080/ready
curl -sS http://127.0.0.1:8080/version
```

- Green枠`127.0.0.1:8002/ready`: HTTP 200、`status=ready`、model version `2`、quality gate `passed`。
- Caddy入口`127.0.0.1:8080/ready`: HTTP 200。`/version`はmodel version `2`、artifact SHA-256 `42bb5f3c7dc5a28d562d3fe2b01fe2aaf832af34ef5496b05b0fe65e9a79b337`、quality gate `passed`、feature contract `bike-demand-features-v1`を返す。
- 読み取り確認した`data/runtime/deployment-state.json`: active slot `green`、active version `2`、deployment mode `approved`、deployment ID `dded8d98-6e6c-4082-9419-df62cc08adb7`。
- `data/runtime/Caddyfile`: `reverse_proxy api-green:8000`。

この状態とコード内の昇格順序から、モデル版2はartifactを読み込んでreadyになり、固定予測probeとCaddy経由の版確認を通過して昇格完了したと判断する。training reportのartifact SHA-256と`/version`の値も一致した。ユーザーが手動での観測登録・予測保存・同一request ID再送を行った結果はまだないため、次はAPIの通常予測と再送を確認する。

## 初学者向け用語集の再点検

README、作業記録、アーキテクチャ、データ契約、runbookの全Markdownを再走査し、品質ゲート条件と合否、incumbent/candidate、training cutoff、rolling evaluation、API応答性能と予測誤差の違い、source revision/worktree、Docker image build、loopback/TLS、Caddyfile、deployment lock/recover、固定予測probeなどを`docs/glossary.md`に追加した。

## 2026-10-28: API予測保存・再送とDocker再起動後の確認

ユーザーがWSLのUbuntuターミナルからCaddy入口へ予測を送り、結果を`/home/kenshin/work/claude_practice/data/reports/manual-prediction-check.log`へ保存した。このログは`261021`ディレクトリではなくリポジトリ直下を作業ディレクトリにした状態で作成された。

- `GET /ready`: HTTP 200、model version `2`、quality gate `passed`。
- `GET /version`: HTTP 200、artifact SHA-256 `42bb5f3c7dc5a28d562d3fe2b01fe2aaf832af34ef5496b05b0fe65e9a79b337`。
- request ID `manual-check-20261028T005734Z-10567`、target hour `2012-07-01 10:00:00`の予測はHTTP 200、値は`415.72473`。
- 同じrequest IDと同じ入力を再送するとHTTP 200で完全に同じ応答が返った。
- 同じrequest IDでtarget hourを`2012-07-01 11:00:00`へ変更した要求はHTTP 409、`request_id was already used with different input`。

この結果により、APIが予測を記録した後に成功応答を返すこと、同じ操作の再送が保存済み結果を返すこと、同一IDの別入力を拒否することを確認した。

同日にDockerを再起動した後、WSL2上のプロジェクトでCompose状態とCaddy入口を確認した。通常の隔離実行ではDocker socketがpermission deniedとなったが、読み取り専用コマンドを承認された実行権限で再試行すると接続できた。

```bash
docker compose ps -a
curl -sS -i --max-time 5 http://127.0.0.1:8080/ready
curl -sS -i --max-time 5 http://127.0.0.1:8080/version
```

API blue、API green、Caddy、MLflow、PostgreSQLはいずれも`Up`で、PostgreSQLは`healthy`。Caddy入口の`/ready`と`/version`はいずれもHTTP 200で、版2・品質ゲート`passed`・artifact SHA-256が前回と同じ値だった。コンテナは再起動後約3分稼働していた。

今回のCodexコマンド実行環境も`uname -a`でLinuxのWSL2カーネル、`pwd`で`/home/kenshin/work/claude_practice/261021`を確認した。Docker socketとlocalhost APIは通常の隔離権限ではアクセス不可だったが、必要な読み取り確認を承認付きで実行できた。手動予測ログの本文は上記の出力を記録したが、ログファイル自体は`261021/data/reports`の外にあるためGitには追加していない。

## 2026-10-28: Codex WSL実行環境とbubblewrap

ユーザーがUbuntu 24.04 (noble)へ`bubblewrap`をインストールした。

```bash
sudo apt update
sudo apt install bubblewrap
command -v bwrap
bwrap --version
```

結果は`/usr/bin/bwrap`、`bubblewrap 0.9.0`。Codexアプリを終了・再起動後、以前失敗した`ls /home/kenshin/work/claude_practice/261021`が成功した。ユーザーは今後の主環境にVS Code + Codex拡張を選択。作業中のコマンド実行環境は`uname -a`でWSL2、作業ディレクトリは`/home/kenshin/work/claude_practice/261021`だった。Dockerソケットへのアクセスは通常の隔離実行では拒否されたが、読み取り操作は承認付き実行で可能だった。

## 2026-10-28: 時系列候補版3と第4四半期リプレイ

ユーザー依頼に従って、既存版2をincumbentにしたQ3候補比較を実行した。学習カットオフ・検証窓はrunbookに合わせた。

```bash
./scripts/bikectl candidate --incumbent-version 2 \
  --training-cutoff '2012-07-01 00:00:00' \
  --validation-start '2012-07-01 00:00:00' \
  --validation-end '2012-10-01 00:00:00'
```

- Run ID `a0c6d543bb464f5aac88dd87a9712339`、登録版3、レポート`data/reports/candidate-a0c6d543bb464f5aac88dd87a9712339.json`。
- データSHA-256はこれまでのUCI原本と同じ`e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f`。worktree clean、source commit `cb79db02c7d38714f626f40c11fd828f64717e22`。
- 学習カットオフ`2012-07-01 00:00:00`、最後に使った学習ラベルの利用可能時刻も同じ。学習13,001行、Q3検証2,208行。
- 候補MAE/RMSE `62.41599 / 95.79949`。版2 incumbentは`84.16779 / 133.40239`、履歴ベースラインは`83.69203 / 139.92905`。同じ検証時刻で両方に勝ち、`quality_gate_passed=true`。固定最終holdoutは再評価せず`null`。

Q4を同じAPI・データ・日時窓で版2と版3へ送り、どちらも2,168要求、HTTPエラー0件だった。窓は`[2012-10-01 00:00:00, 2013-01-01 00:00:00)`で、元データの欠損40時間分は要求を作らず補完もしない。観測行はすでにDBへ登録済みで、両リプレイの`observations_inserted`は0。replay IDは`e03de4ee4ef4-2012100100-2013010100`。

```bash
./scripts/bikectl replay --model-version 2 --start '2012-10-01 00:00:00' --end '2013-01-01 00:00:00'
./scripts/bikectl replay --model-version 3 --start '2012-10-01 00:00:00' --end '2013-01-01 00:00:00'
./scripts/bikectl monitor --replay-id e03de4ee4ef4-2012100100-2013010100 --model-version 2
./scripts/bikectl monitor --replay-id e03de4ee4ef4-2012100100-2013010100 --model-version 3
```

各リプレイレポートは`data/reports/replay-e03de4ee4ef4-2012100100-2013010100-v{2,3}.json`、監視レポートは`data/reports/monitor-e03de4ee4ef4-2012100100-2013010100-v{2,3}.{csv,json}`。Q4全行を同一の実績値と、最後のラベルが到着する`2013-01-01 02:00`時点で読み取り集計した比較を`data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json`に保存した。

| 評価 | 版2 | 版3 |
|---|---:|---:|
| Q4要求数 / 採点数 | 2,168 / 2,168 | 2,168 / 2,168 |
| 全Q4 MAE | 70.50089 | 59.40270 |
| 全Q4 RMSE | 113.27010 | 91.94132 |
| API p50 / p95 latency (ms) | 71.28 / 104.61 | 71.65 / 111.93 |
| 最終7日 MAE / RMSE | 37.27775 / 58.15821 | 44.17634 / 74.28582 |
| 最終7日ラベル数 / 予測数 | 164 / 167 | 164 / 167 |

全Q4では版3のMAEが版2より約15.7%、RMSEが約18.8%低かった。一方、最終7日だけを見ると版3の誤差は版2より大きい。両版とも最終7日`quality_status=within_review_threshold`、`lag_24` PSI `2.0954`で`investigate_distribution_change`。分布変化アラートは精度悪化そのものを示すものではなく、最終週の誤差と全Q4の集計を分けて読む。Q4はすでに評価に使ったため、ここを見て追加調整しない。

候補版3はQ3品質ゲート通過後、ユーザー依頼のQ4検証用に手動昇格した。

```bash
./scripts/bikectl deploy promote --version 3 --approver kenshin \
  --reason 'Q3 candidate gate passed; user requested independent Q4 replay'
```

- Deployment ID `8a6a63f0-875a-4c5b-8c85-bc0fe1538d1e`、旧版2から新版3、active slot `blue`、mode `approved`。
- Green版2をCaddy配信したままBlue候補を再ビルドしたため、ビルド中のBlue直アクセスは旧版1のartifact欠落503だった。入口CaddyはGreen版2のままで影響なし。Buildx履歴では依存パッケージの新規取得・`pip install .`に約178秒を要し、その後ビルド・probe・切替が完了した。昇格処理の無出力時間は候補build中だった。
- fixed probeはtarget `2011-01-08T00:00:00`、offline/API prediction差`4.92e-7`（許容差内）。
- 昇格後のCaddy `/ready`はHTTP 200、version3、quality gate `passed`。`/version` artifact SHA-256 `79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`。`data/runtime/Caddyfile`は`api-blue:8000`、runtime stateはactive blue/version3。

現時点の稼働版は版3。Q3/Q4はこのデータの最後の時系列窓として消費済みであり、次候補には新しいデータと独立検証期間を用意する。
