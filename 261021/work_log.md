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

## 2026-10-28: PSI・直近週誤差の原因調査、新データ準備

### 調査方法

モデル版2・3の学習や変更はせず、PostgreSQLに保存済みのQ4予測を読み取り、UCI `hour.csv`と`target_hour`で結合した。監視実装`score_monitor_windows()`は、2011年全体の学習時特徴量分布を参照し、lag値・hourのPSIとラベル到着後のMAE/RMSEを別々に計算している。対象レポートは`data/reports/model-drift-investigation-2012100100-2013010100.json`。

### PSI警告の切り分け

- 監視窓`2012-12-24 23:00`より後〜`2012-12-31 23:00`まで167予測の`lag_24` PSIは`2.0954`。参照は2011年の8,645行（非欠損8,523）で、平均145.15・中央値111。窓は167行（非欠損165）で、平均64.95・中央値40、欠損率1.20%。設定上の調査基準`0.2`を大きく超える。
- hour PSIは`0.000227`、lag_168 PSIは`0.07675`。警告のほぼ全てがlag_24の分布変化から来ており、両モデル版で同じ値になるのは同じ特徴量行を比べるため。
- 同じPSI関数で2012年12月全体と2011年全体を比べるとlag_24は`0.0635`。2011・2012の12月24〜31日を比べる診断値は`0.1983`。年全体を参照にした値には、年末・祝日週の季節差が強く含まれる。診断比較は正式な監視結果を差し替えない。
- `lag_24`の差は実データにある短期入力変化で、単なるモデル固有の警告ではない。季節調整した参照期間を追加すれば監視の解釈は改善するが、現在のしきい値や計算方式は今回変更していない。

### 直近7日間の誤差差

| 指標 | 版2 | 版3 |
|---|---:|---:|
| 予測数 / 採点数 | 167 / 164 | 167 / 164 |
| MAE | 37.278 | 44.176 |
| RMSE | 58.158 | 74.286 |
| 平均実測 | 75.177 | 75.177 |
| 平均予測 | 90.562 | 99.632 |
| 平均bias（予測−実測） | +15.385 | +24.455 |

最後の3時間は`timestamp+3 hours`のラベル利用可能時刻が監視時計を越すので採点対象外。版3のMAEは版2より18.5%高く、RMSEは27.7%高い。版3はQ4全期間で版2より低誤差だが、直近窓では平均予測を版2より約9.07台/時高く出し、過大予測が増えた。

日別では、版3の版2に対する絶対誤差増分の84.6%が12月25日、26日、29日に集中した。12月25日の23採点行はすべてholiday。12月26日の24行中16行は`weathersit=3`。現行特徴量は`hour, weekday, month, day_of_year, is_weekend, lag_24, lag_168`と欠測flagで、holiday・weather・workingdayを含まない。低需要の祝日・荒天が今回の誤差差に関係する有力な説明だが、事後相関であり因果を証明してはいない。

### 版3がQ4全期間で改善した理由

版2・3は同じ特徴量とHistGradientBoostingRegressorのハイパーパラメータを使う。版2の学習行数8,645に対し版3は13,001（50.4%増）で、学習 cutoffが2012-01-01から2012-07-01へ進んだ。全Q4では版2の平均予測178.86が実測平均219.20を40.34台/時下回り、版3は平均195.41で下回り幅を23.78へ縮めた。MAEは10月`98.575→77.258`、11月`64.414→57.615`、12月`49.604→44.095`と各月で下がった。追加されたより新しいデータが季節・水準を補った可能性が高いが、学習データ量だけを変えるablationはしていないので因果と断定しない。

### 品質ゲートの確認

- 通常`train`のゲート: validation MAEが前日同時刻（欠ける場合は曜日・時刻別平均、その次は全体平均）の履歴baseline MAEを厳密に下回ること。
- rolling `candidate`のゲート: incumbent自体が`passed`であり、候補MAEが同じvalidation timestamp上でincumbentと履歴baselineの両方を厳密に下回ること。等しい場合は不合格。RMSEや固定holdoutに絶対値の合格線はなく、固定holdoutはこの候補ゲートに使わない。
- v3はQ3でMAE`62.416`、版2`84.168`、baseline`83.692`、評価行2,208。版2はpassedで、候補は両方より小さいため合格。昇格にはさらに人の理由付き承認とready/probe確認が必要。

### 同一システムの新しいデータを準備

UCIデータの出典ページで2011–2012年の対象がCapital Bikeshareと確認できた。Capital Bikeshare公式サイトは月次トリップ履歴を公開し、2026年5月ファイルまで存在した。現行データ説明では保守・検査、倉庫テスト駅、60秒未満のトリップを除くと明記される。これは同じシステムだが旧`cnt`と完全同一定義ではないため、次回はターゲット定義変更を伴う移行評価とする。Data License Agreementに従い、元データを単独再配布せず、Gitにも追加しない。

2023-01〜2026-05の公式ZIP 41件を`data/raw/capitalbikeshare/monthly/`へ取得。元ファイルの`started_at`を調べ、秒以下の精度がアーカイブ間で変わること、月次ZIPが隣月に始まったトリップを含むことを発見した。2024年5/6月で141 ride_id、2026年4/5月で64 ride_idが重複。各重複は同一の時間bucketに入り、秒以下の精度だけが異なるため、時単位に正規化して重複除去する処理へ修正した。異なる時間bucketの重複IDはエラーで止める。

時刻文字列にはoffsetが無い。America/New_Yorkのローカル壁時計として扱い、UTC変換をしない。秋に2回出る01時は元ファイルで区別できないため、該当するwall-hour全体を予測対象から除外。春の存在しない02時も作成しない。最終CSVは`dteday,hr,cnt`だけで乗車ID・駅・位置情報を保持しない。

```bash
python scripts/prepare_capitalbikeshare.py
```

- 取得41 ZIP、読込19,440,291行、重複ID205件を除外。2023-01-01〜2026-04-30の派生CSV29,177行、重複timestamp 0、SHA-256 `cc4b39c67db286d30300cc6fb10a0f37e942eb337a7bd964795bbdd891762a96`。
- 時刻除外7件: 2023/24/25年の秋の重複01時3件、2023/24/25/26年の春に存在しない02時4件。監査の欠け7件はこのDST方針と一致。
- ローカルmanifest `data/derived/capitalbikeshare-hourly-2023-202604-manifest.json`は月別アーカイブSHA、件数、前処理、split行数を記録する。CSV・元ZIP・manifestは`.gitignore`の`**/data/`によりGit対象外。
- 事前に固定した時間分割: 学習 cutoff `2025-01-01 00:00`（利用可能な学習行17,538）、候補validation `[2025-01-01, 2025-07-01)`（4,343行）、out-of-time最終評価 `[2025-07-01, 2026-05-01)`（7,294行）。cutoff時にまだラベルを使えない2024-12-31 22/23時の2行は学習から除外。
- 最終期間はモデル誤差を採点しておらず、候補選択にも利用していない。ただし全期間CSVのデータ品質監査を行い、min/max/meanなどの集計は見ているため「未閲覧のblind holdout」ではない。次候補の選定をせず、out-of-time最終評価として保護する。

データ変換scriptは1か月分、月境界重複のある2024年5–6月、および全期間で実行・監査。`py_compile`は成功。テストスイートは今回実行していない。新候補の学習、MLflow登録、runtime配信、最終期間のモデル誤差採点は未実行。現在稼働中のAPI版3に変更はない。

確認先・実験条件・次候補コマンドは[`docs/monitor-investigation-and-next-data.md`](docs/monitor-investigation-and-next-data.md)、集約レポートは`data/reports/model-drift-investigation-2012100100-2013010100.json`。

## 2026-10-28: Capital Bikeshare候補と独立期間の評価

### 出典manifestを候補レポートへ結びつける

新しいCapital Bikeshare CSVは既定のUCI manifestと別ファイルのため、そのまま候補CLIを動かすと出典が「未確認」と記録されることが分かった。`candidate --source-manifest`を追加し、明示されたmanifestが選択CSVのSHA-256と一致しない場合はエラーにするよう変更。学習レポートには出典名・URL・ライセンス・manifest SHA・変換要約を記録する。`py_compile`とCLI helpで構文・引数を確認した。テストスイートは実行していない。

### 2025年上期validationで候補を一度選ぶ

Dockerソケットは通常の隔離コマンドから`permission denied`になったが、許可付きの読み取り確認でCompose全サービス稼働、PostgreSQL healthy、MLflow稼働を確認した。登録済み版3をMLflowから読み込み、候補はMLflowへ登録しない`--no-register`で一度だけ比較した。

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 ./.venv/bin/mlops-bikectl candidate \
  --csv data/derived/capitalbikeshare-hourly-2023-202604.csv \
  --source-manifest data/derived/capitalbikeshare-hourly-2023-202604-manifest.json \
  --incumbent-version 3 \
  --training-cutoff '2025-01-01 00:00:00' \
  --validation-start '2025-01-01 00:00:00' \
  --validation-end '2025-07-01 00:00:00' \
  --output data/reports/candidate-capitalbikeshare-2025h1-v3.json \
  --no-register
```

- 同じUCI-schema特徴量・モデル種・ハイパーパラメータ。学習17,538行、validation4,343行。カットオフ時点までの最新学習ラベル利用可能時刻は`2025-01-01 00:00:00`。
- candidate MAE/RMSE `168.266 / 265.934`、版3 `389.762 / 608.320`、履歴baseline `208.061 / 329.878`。validationの全同一timestamp上で候補MAEが両比較対象を厳密に下回ったため、既定のrolling candidate品質ゲートは`passed`。
- コマンド出力が端末に表示され、完全なJSONは`data/reports/candidate-capitalbikeshare-2025h1-v3.json`へ保存された。`--no-register`のためMLflow UIに新run/versionはなく、版4も作られていない。

### validation確定後に最終out-of-time期間を一度だけ採点

validationで固定した候補を同じtraining cutoff・学習行・特徴量・hyperparameterで再構成し、2025-07-01〜2026-04-30の7,294時間を一度だけ評価するスクリプトを追加した。スクリプトは選定reportとCSVのhash、incumbent artifact SHA、quality tag、特徴量、hyperparameter、学習行数を照合する。既定出力レポートが存在する場合は二度目の採点を拒否する。候補は登録せず、昇格もしない。

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 ./.venv/bin/python scripts/evaluate_capitalbikeshare_holdout.py
```

最初に`./scripts/evaluate_capitalbikeshare_holdout.py --help`を直接起動しようとして、scriptに実行permissionがないため`Permission denied`になった。プロジェクトで案内する通り`python scripts/evaluate_capitalbikeshare_holdout.py`へ変えて正常実行した。結果は`data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json`。

| 指標 | 固定候補 | 版3 | 履歴baseline |
|---|---:|---:|---:|
| MAE | 126.941 | 326.387 | 181.185 |
| RMSE | 211.578 | 518.660 | 296.180 |

全体では候補のMAEは版3比61.1%、baseline比29.9%小さく、RMSEは版3比59.2%、baseline比28.6%小さい。月別では2026-01の候補MAE154.712がbaseline120.524を上回るなど、一部期間でbaselineに劣る。全期間を平均した改善だけで十分とは判断せず、必要なら季節・休日・荒天や通勤夕方帯の誤差を追加分析する。最終期間には独立の固定合否基準を設けていないので、「最終holdout quality gate合格」とは表記しない。

今回の比較はCapital Bikeshareの新ターゲット定義への移行評価で、UCI版3の学習ターゲットと完全一致しない。最終期間の誤差自体は候補選択・パラメータ調整に使っていないが、データ監査で全期間の集計統計は見ているため、完全に未閲覧のblind holdoutではない。3時間ラベル遅延は引き続き実測保証ではなく実験仮定。

### 稼働確認と運用状態

候補評価後もComposeは稼働し、Caddy入口`/ready`はHTTP 200・版3・quality gate passed、`/version`のartifact SHAは`79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43`。モデル版3を維持し、新候補をMLflow登録・API昇格していない。報告書は端末出力とローカル`data/reports/`で確認する。MLflow UIは既存の登録runを見る画面、localhost:8080の`/ready`・`/version`は稼働状態を返すだけで、評価ダッシュボードではない。

このvalidationと最終out-of-time期間は今回の候補に使用済み。次の別候補を比較・調整する場合は、新しい独立データ期間を事前確保する。

## 2026-10-28: 前回確認以降の差分監査と計画追補

ユーザー依頼により、版2のAPI・再送・再起動確認を記録した`cb79db0`を基準に、`485286c`と`6b99746`の変更14ファイルと関連する既存の配信・監視・DB・CIを確認した。詳細は[`docs/reviews/20261028-review.md`](docs/reviews/20261028-review.md)、集計証拠は[`20261028-evidence.json`](docs/reviews/20261028-evidence.json)。途中でチャットが中断されたが、保存済みの監査結果から再開し、同じ最終評価をやり直していない。

### 実行確認

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m compileall -q src tests scripts
./.venv/bin/python -m pip check
docker compose config -q
docker compose ps
gh run list --repo kenobix/Claude_practice --workflow bike-mlops-contracts.yml --branch main --limit 5
```

- 既存13テスト成功（テスト本体0.396秒）、構文検査成功、pip checkは`No broken requirements found`。Starletteのhttpx非推奨警告は残る。最初の隔離実行は最初のAPIテストで進まなくなり、自分の検証プロセスを終了して許可付き実行で成功した。隔離時の停止原因は確定していない。
- 対象2コミットのCIはsuccess。ただし試験は既存13件、CIのcompile対象はsrc/testsのみであり、新規scripts・candidate・manifest・最終評価・配信復旧の十分な回帰試験はなかった。
- Compose全サービス起動、PostgreSQL healthy。MLflow health、Blue版3・Green版2・Caddy版3のreadyが200。配信台帳の版2/3昇格completed・probeあり、入口とartifact SHAが一致。
- 保存済みQ4予測をDBからSELECTし、元UCIと再結合。各版2,168予測、特徴量と入力出典hashの不一致0。全Q4・直近週のMAE/RMSEとPSIは既存reportに一致。新規APIリプレイやモデル再推論は実行していない。
- 新データ41 ZIP（742,925,529 bytes）のSHA、CSVとmanifest SHAが保存済み記録に一致。分割は17,538/4,343/7,294、重複0、境界2行除外、DST処理後の時刻集合差0。
- 月境界・DSTを含む7日166時間を7 ZIPから別のID辞書方式で再集計し、166/166一致。サンプル98,733 ID、重複204行。全1,944万行を別実装で再集計したわけではない。
- 直近6時間・サービスごと末尾300行の保持ログ計923行にERROR/Traceback/HTTP403/503なし。保持外の履歴まで正常と断定しない。

### 見つかった不足と前回説明の訂正

1. 新データ候補のvalidation時modelはno-registerで保存しておらず、最終評価では再学習していた。以前の「固定候補」という説明は不十分だった。設定・行数・seed一致だけでは、同一artifactを使った証拠にならない。元の数値は保存し、暫定評価としてREADMEと評価文書を訂正した。
2. 最終評価の防止策は出力ファイル名だけに依存する。合成CSVと固定モデルで出力名を変えた2回が両方成功した。実データ最終期間は再採点していない。分割planもmanifestへ手作業で加えられており、生成scriptからは出力されない。
3. 新データへ監視を流用すると2012年以前という参照filterで0行になる。配信seedとprobeもUCI固定。新target契約と別DB/Compose profileが必要。
4. 合成検査でarchive0件から24行の全0データを作れること、不正manifest配列`[1]`でAttributeErrorになることを確認。今回の実CSVが壊れた証拠はなく、範囲・型・収支検査の欠如として記録した。
5. 計画した主要時間帯・欠測・応答時間の許容幅は未実装。現行passedはvalidation MAE比較の合格だけ。Q4のlatencyも制御した負荷比較とは扱わない。
6. Q4監視でstart指定を省略したため、各版4,416窓のうちQ3の空窓2,208が余分に保存されていた。最終週と別集計したQ4全誤差には影響なし。runbook例をQ4の明示期間へ修正し、既存DB/レポートは維持した。
7. PSIは参照が定数の合成例`[1,1,1]`対`[100,100,100]`で0となる。今回のlag_24参照は非定数で、報告済み2.095への影響はない。
8. runtime stateの理由は版1bootstrap時のままで、配信台帳が正確な承認根拠。既知の利用不可版1もREADY/passedと過去版一覧に残る。明示的な配信可否・状態同期が必要。
9. 検証ラベルを待つ評価判断時刻が未定義。UCI Q3末尾と新データvalidation末尾は23時で、+3時間の到着は後続評価開始日の02時となり、00/01時の評価対象より遅い。学習や最終正解の混入は確認されなかったが、当時の配信判断を再現するには境界の待機時間が必要。数値は変更せず、計画書R02へ判断時刻の固定を追加した。

rollback、中断復旧、破損artifact拒否、PostgreSQL並行要求、障害注入、DB/artifact復元の完了証拠も不足していた。元計画の「企画・未実装」という状態を現状に更新し、native NaN処理という実装選択を反映。新しく判明した作業をR01〜R10として、優先順位・依存関係・個別チェック項目・完了条件付きで`261014/plan_mlops.md`第11節へ追記した。

今回の変更は監査報告・計画・既存文書の訂正のみ。実装上の未解決項目は計画に明示し、モデルの学習/登録/昇格や評価済み最終期間の再推論は行っていない。詳細ログはGit対象外の`data/reports/review-20261028-*`、原本/乗車ID/認証値を含まない集計証拠はdocs/reviewsへ保存した。

## 2026-10-28: P0残作業R01〜R04

監査計画の優先度に従い、データ取込契約・評価protocol/消費台帳・候補artifact固定・回帰試験とlockを実装した。既存Capital CSV/manifest、過去レポート、MLflow登録版、稼働中APIは変更していない。新たなモデル学習・登録・昇格・既評価期間の再採点も行っていない。

### R01 データ取込・manifest契約

- `prepare_capitalbikeshare.py`は空/重複/月欠落/月順違いのarchive一覧、data期間を含まない月範囲、時間境界違反、空CSV archiveを拒否する。CSV/manifestは同一ディレクトリの一時出力から保存し、manifestのhashがCSVと一致した組だけを利用可能にする。既存出力は上書きせずbuild lockを使う。
- schema v2 manifestにtarget/processing版、各archive SHA/byte count/month、source timestamp bounds、行数、ID重複、DST除外、期間外、出力時間数/件数とSHAを記録する。収支は`raw rows = duplicate rows + unique IDs`、`unique IDs = out-of-window + DST-excluded + output trips`で検査する。
- `capital_data.py`はJSON object/型/必須値/hash、全raw ZIPのbyte数/SHA、連続月、timestamp coverage、CSV列とDST後の時刻gridを再検査する。過去manifest schema v1は履歴証拠として残し、新しい候補protocolには使わない。
- 合成ZIPの空archive、月欠落、同一ID重複・矛盾、月境界重複、分/秒timestamp、春DST、既存出力保全を試験した。stale lockはPIDがそのlockを開いていない場合だけ再取得し、live lockは奪わない。
- 追加確認として既存41 ZIPの全期間再集約を開始したが、2023年1〜11月処理後、追加検証としては時間がかかるため停止した。新CSV/manifestは生成されていない。プロセス終了を確認し、今回残ったstale lockと一時SQLite 292MBだけを削除した。元ZIPと既存CSV/manifestは維持。新コードで全41 archiveを完走した証拠はない。

### R02 評価protocolと消費台帳

- `evaluation_protocol.py`はdata/target/manifest/feature SHA、候補recipe、学習cutoff、validation、全ラベル到着後のselection decision time、後続評価期間、incumbent version/SHA、split件数とgateを正規化してhash化する。後続評価開始が判断時刻より前なら拒否する。
- legacy registryにUCIの初期validation/holdoutとQ3/Q4、Capital Bikeshare 2025H1 validationと2025-07〜2026-04 final periodを登録した。歴史的にprotocol/model hashが不明なものは`null`のままとし、後から埋めない。
- local ledgerはdata SHA、target contract、評価窓、protocol/model SHA、started/failed/completedと試行履歴を記録する。OSの`flock`付き追記で、プロセス終了時はlockを自動解放し、lockファイルが残っても再開を妨げない。失敗後は人によるreview/reopenを要求する。同target contractの期間は、別data hashでも部分重複すれば再利用を拒否する。
- 合成試験で判断時刻境界、legacy窓拒否、異なるdata hashでの重複拒否、同時開始1件のみ許可、失敗状態保持と理由付きreview/reopenを確認した。

### R03 選定artifactの固定

- `candidate --no-register`はskops model、SHA、validation行別actual/predictions/label availability、report、protocol、決定的source snapshotを`data/candidates/<candidate_id>/`へ一度だけ保存する。source snapshot内の全ファイルを再hashし、reportのsource-tree hash/file countと一致することを確認してから保存する。bundle IDはartifact/evidence hashとgate指標を含み、同一性の変更を検知する。
- 新候補は凍結protocolとstrict manifestが必須。登録/昇格はせず、候補reportのdata/row hashes、features、recipe、incumbent、出典、判断時刻をprotocolと照合する。
- 最終評価scriptは選定bundleのartifact hashとskops許可型、bundle ID、source snapshot、validation predictionsを照合してロードし、validationのcandidate/incumbent/baseline予測・MAE/RMSE・gateを再計算する。候補をfitせず、protocolの期間・data/manifest/model/incumbent hashを照合し、行別予測とsummaryを新規出力してledgerへ記録する。
- 既存候補reportには当時のmodel artifactがないため、過去の選定を後から完全なbundleに見せかけていない。未使用期間の候補bundleやfinal evaluationは未生成。

### R04 回帰試験・lock・CI

```bash
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

- 最終確認は32テスト成功（2.462秒）。`pip check`は`No broken requirements found`。隔離環境では既存APIテスト先頭で停止したため、許可付き通常WSL環境でsuiteを完走した。隔離時停止の原因は未確定。
- `requirements.lock`は101 package/versionを固定する。現行venvの99 packageと全て一致し、setuptools/wheelはbuild用に追加固定した。DockerfileとGitHub Actionsで同じlockを使い、CIはPython 3.12.11、scriptsを含むcompile、`pip check`、新テストを実行する。
- source-tree hashに対応する決定的tar snapshotをcandidate bundleへ保存し、合成開発データで2回fitした予測の完全一致を確認した。httpx2の非推奨警告は残す。別checkoutからの完全なlock installとGitHub Actionsの結果はpush後に確認する。

作業ログ: `data/reports/p0-r01-r04-tests-20261028.log`（Git対象外）。R01〜R04のコードと合成回帰検証を実装したが、新しい未使用評価期間はないので実候補学習/最終採点には進めない。CIとclean-checkout確認はこの変更のpush後、P1の新target API/監視/昇格条件はR05〜R07として継続する。

### 追加コードレビューでの修正

- 評価台帳を排他lockファイル作成方式からPOSIX `flock`方式へ変更。プロセスがkill/停止してもOSが排他を解放するため、残ったlockファイルを手動削除しなくてよい。既存lockファイルが残った状態からの開始と並行開始をテストした。
- protocolの宣言期間同士の重複を、観測行の有無に関係なく拒否する。CSVとmanifestへ同じパスを指定する入力も先に拒否する。
- candidate IDがモデルartifact/validation evidence/source snapshot/品質gate/指標を含むようにし、snapshot内ファイルを再hashしてsource-tree hashと比較する。最終評価時はbundle ID、各SHA、row-level validation、候補/incumbent/baseline予測とvalidation指標を照合し、品質gateを再計算する。
- 修正後の確認: compileall成功、32テスト成功（2.462秒）、`pip check`成功。Starlette/httpx deprecation warningは引き続き表示される。
- commit `2820ca4` push後のCI `40638852598` はlock install、`pip check`、32テスト、src/tests/scripts compileを全て通過した。runnerからUbuntu 26移行予定とNode 20 actionのwarningが出たため、Ubuntu 24.04 runnerを固定しNode 24対応版のactionsへ更新した。根拠は[checkout v7.0.1](https://github.com/actions/checkout/blob/v7.0.1/action.yml)、[setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0)。追補commit `870de9d` のCI `40639053590`もsuccess（lock install、pip check、全32テスト、src/tests/scripts compile）。

## 2026-10-28: P1移行プロファイルR05

UCI Bike SharingとCapital Bikeshareの件数は意味が異なるため、UCIの稼働DB/model/portと分離するprofile境界を実装した。Capitalは`migration-only`のまま。モデルの学習・登録・昇格や、UCI版3をCapitalの基準として使う比較は行っていない。

### 実装

- `configs/data-profiles/`にUCI/Capitalのtarget、timezone、ラベル利用仮定、入力パス、model名、監視参照、host portを定義した。設定loaderでID、schema、パス、contract、label delay、portを検査する。
- `DATA_PROFILE_ID`からCSV/manifest、SQLite/MLflowの初期パス、report/runtimeパス、model名とhost portを選択する。MLflow tagsとmodel loaderでprofile/targetの異なる登録modelを拒否する。
- DBをprofile/targetのmetadataに固定し、Capital用の新DB/Compose projectと別volume/portを用意した。古いUCIの行があるDBへCapital metadataを割り当てる操作は拒否する。予測・deploymentにもprofile/targetを保存する。
- API応答・新しい予測/観測要求・replay・deploy probeにprofile/targetを含める。UCIの従来要求とidempotency hashは互換維持。Capital APIはprofile/targetの不一致とDSTの存在しない/曖昧な壁時計時刻を拒否する。
- Seed/audit/model probeを選択profileに対応させ、Capitalはmanifest schema v2と全原本archive hashを検査する。Capitalには凍結monitor referenceがないため、UCI 2011の参照分布を流用せずmonitorを停止する。
- 追加レビューで`./scripts/bikectl`が`.env`から秘密値を読み込んでから接続先をUCI portへ強制していた問題を修正した。明示的なprofile/DB/MLflow URLを保持し、`.env`のDBパスワードでホスト接続を作る場合はCapitalを55433/5001、UCIを設定済みport/5000へ振り分ける。
- `tzdata==2026.5`をlockへ追加し、このWSL venvにもインストールした。profile loaderの不正label設定検査とwrapperのprofile path選択を加えた。（供給元に関する当初の「OSに依存しない」という説明は、下記のR06差分レビューで訂正。）

### 検証と未完了

- `.venv/bin/python -m unittest discover -s tests -v`: 44件成功。最初の隔離実行はFastAPI TestClientの最初のテストで停止したため、その実行を終了。通常WSL実行では3.101秒で完了。Starletteの`httpx`非推奨警告は表示された。
- `.venv/bin/python -m compileall -q src tests scripts`、`docker compose -f compose.yaml -f compose.capital.yaml config -q`、`git diff --check`、`sh -n scripts/bikectl`、`pip check`: 成功。隔離Composeは静的設定検査のみで、CapitalのDockerサービスは起動していない。
- commit `f7ddea2`をpushし、GitHub Actions run `40642840800`がsuccess。CI上で`requirements.lock` install、`pip check`、44テスト、compileallが完了した。
- `DATA_PROFILE_ID=capital-bikeshare ./scripts/bikectl data audit`の限定確認では、UCIではなく新profileの`data/derived/capitalbikeshare-next/manifest.json`を探し、現時点で未作成のため予想どおり停止した。これは接続確認やデータ監査の成功を意味しない。
- 既存41 ZIPを新規pathへmanifest v2で再集計中。既存CSV/manifest、使用済み評価窓、UCI稼働stackは変更していない。処理完了後に全archive hashとCSV/manifestの再読込を検査する。
- Capitalに同targetのmodel/監視参照がなく、新v2 CSV作成後もseed hash、MLflow model loading、offline/API parity、隔離Composeのend-to-end配信証拠はない。R05は完了扱いにしない。PSI監視参照はR06/R07のbootstrap/監視基準決定後に確立する。

## 2026-10-28: R01/R05再集計とR06 bootstrap案

### R01/R05の未記録だった実行結果

以前のR05記録では全41 ZIPの再集計を「処理中」としていたため、その後の実績を追記する。出力は旧成果物と別の`data/derived/capitalbikeshare-next/`を使い、原本ZIP・旧CSV・旧manifestは変更していない。

- 41 ZIPを読み込み、raw行数19,440,291、一意ride ID 19,440,086、同一IDの重複205行を検出した。期間外592,491件、DST曖昧時刻で除外1,568件、出力trip starts 18,846,027件で収支が一致した。
- 2023-01-01〜2026-05-01の時間データ29,177行を作成。時間枠の欠落7件は春の存在しないローカル時刻4枠と、秋のoffset不明な曖昧時刻3枠に対応する。出力CSV SHA-256 `cc4b39c67db286d30300cc6fb10a0f37e942eb337a7bd964795bbdd891762a96`は旧CSVと一致した。したがって集計のbyte再現を確認したが、新しい評価期間は増えていない。
- 最初に作成したmanifestの`audit`が既定UCI profile/targetを記録している問題を発見した。集計CSV/hash自体は一致していたが、manifestをそのまま有効証拠として使わなかった。原因は集計時の監査情報が実行環境の既定profileを参照していたこと。生成scriptをCapital profile設定からtimezone/target/profile/label ruleを明示するよう直し、テストも追加したうえで、新規manifestのaudit sectionだけを原本から再計算して修正した。
- 修正後の`DATA_PROFILE_ID=capital-bikeshare ... data audit`は成功。`data/reports/capital-bikeshare/data-audit-v2.json`でprofile `capital-bikeshare`、target `capital-trip-starts-hourly-v1`、29,177行、CSV hashを確認した。3時間のlabel delayは実測値ではなくsimulation assumptionと記録される。
- 独立SQLite `data/bikeops-capital-bikeshare.db`へ29,177行をseedした。読み取り照会で観測行数29,177、distinct data hash 1、metadata profile/targetが一致した。これはSQLite分離の確認であり、隔離PostgreSQL/MLflowやDockerサービス起動の確認ではない。
- 既存UCI stackと評価台帳は変更せず、新候補学習、MLflow登録、最終評価、API昇格もしていない。

### 未使用期間とR06の次段階

- Capital Bikeshare公式の[system data page](https://capitalbikeshare.com/system-data)から、2026年6〜9月archiveのURLへHEAD requestを行いHTTP 200を確認した。2026年10月archiveは確認時点で404。内容はダウンロードも閲覧もしていないため、使用済み評価期間には数えていない。
- `docs/capital-bootstrap-gate.md`へ、未使用の2026年6〜7月validation、8月1日03時〜10月1日の後続評価、historical baseline比較、validation gateと運用許可の分離を提案として記録した。`p95 <= 250 ms`等の数値は未承認・未凍結で、実験には適用していない。
- 初回モデルは同target incumbentがないため、既存の候補CLI/protocol経路では実行できないことを確認した。次の実装対象は、合意済みgateを用いたincumbentなしbootstrap protocol/candidate/final-evaluation経路とそのテスト。データを取得して期間を消費する前にこの経路を整備する。

### 検証

- 変更後の全44テストが成功。`compileall -q src tests scripts`、`pip check`、`sh -n scripts/bikectl`、`docker compose -f compose.yaml -f compose.capital.yaml config -q`、`git diff --check`も成功。
- commit `05b6c95` push後のGitHub Actions run `40644880384`がsuccess。lock install、`pip check`、44テスト、compileallが完了した。
- `tzdata==2026.5`を依存lockとこのWSL venvに追加した。（当初記載した「zoneinfoがシステム設定に依存しないことを確認」は誤り。下記のR06差分レビューでOS優先検索と未解決の供給元固定を記録。）
- R05の隔離Compose実起動、PostgreSQL接続、Capital model loading、offline/API parity、deployは未実施。R05は部分完了のまま。

## 2026-10-28: 前回以降のログ差分と3点の基準決定

ユーザー依頼に従い、前回最終commit `9131b1f`（15:38:10 JST）以降だけを確認した。対象・コマンド・証拠の限界は[`docs/reviews/20261028-r06-delta-review.md`](docs/reviews/20261028-r06-delta-review.md)に記録する。

- 作業開始時のHEADは同じで追跡差分0。保存済みreports/raw/derived/runtime、Capital SQLiteのmtimeに追加更新なし。candidate/評価台帳の新規ファイルもなし。
- `docker compose logs --since '2026-10-28T15:38:10+09:00' ...`は成功し差分ログ0行。UCI全5コンテナはrunning、PostgreSQL healthy。今回はready再probeをしていないため、稼働可能を新たに実証したとは記録しない。
- MLflowへ基準時刻以降のruns検索とmodel version作成/更新時刻照会を実施し、追加0件。PostgreSQLのSELECTでは新規prediction 0、deployment作成/更新0、monitor window 0。学習・評価・配信を再実行して確かめる方法は使っていない。
- `gh run view 40645058486 ...`で`9131b1f`のCI successを再確認。隔離環境のDocker socket/GitHub接続制限は、許可付きの読み取り実行で解消した。Docker設定・権限変更やサービス再起動はしていない。
- 前回評価案の学習末尾ラベルがvalidation開始より後に到着する不整合を修正。training cutoffを2026-06-01 00:00とし、5月31日22/23時の2行を除外する。Python日時算術でvalidation 1,464時間、後続1,461時間、選択判断8月1日02時を照合した。
- 前回のtimezone説明を訂正。`tzdata` package版2026.5はinstall済みだが、実行時の`TZPATH`とPython標準ライブラリ実装はOS側を優先する。実際のprovider/versionを揃えて記録する作業をR04/P0の残作業に追加した。既存DSTデータの誤りを発見したという意味ではない。

ユーザーから判断を委任されたため、3点をCodexが決定した。validation/後続精度gateはMAEの厳密改善とRMSE非悪化、運用は暖機50件後の逐次1,000件でエラー0・p95 250ms以下、weekday/weekend・通勤時間帯は件数/MAE/差を記録する診断値とする。全要求は新規ID・再試行0とし、保存済み応答の計測を避ける。追加の確認待ちは解消した。

`docs/capital-bootstrap-gate.md`を決定済み基準へ更新し、runbook・data profile文書の未承認表記を修正、計画書R06を仕様決定と未実装/未実測へ分割した。計画書11.4へ実行順序を追記した。今回完了したのは差分レビューと基準/残作業の更新で、新規fit・評価・登録・昇格・負荷試験は行っていない。変更は文書のみのため、ローカルでのテストsuite再実行は省略し、文書リンク・日時算術・diffを確認する。push後は自動CIの結果を確認する。

## 2026-10-28: R04 timezone供給元固定・実環境再現確認

### 実装

- `src/bike_mlops/timezones.py`を追加し、OSの`zoneinfo.TZPATH`に依存せず、lock済みのPython `tzdata` packageからTZif bytesを読み込んで`ZoneInfo.from_file`でtimezoneを作るようにした。zone path traversalを拒否し、timezone file identityをSHA-256付きで返す。
- `pyproject.toml`と既存`requirements.lock`の版を`tzdata==2026.5`に一致させた。Capitalの月次集計・manifest validator・API壁時計検証が同じloaderを使う。学習reportとAPI `/ready`・`/version`、manifest schema 3にprovider/package版/zone名/byte数/hashを記録する。schema 2は既存audit/seed用として読めるが、新protocol/candidate作成ではschema 3を必須にした。
- 回帰試験を追加し、春の欠落時刻、秋の重複時刻、package file hash、traversal拒否、schema 2からの新protocol拒否を確認する。
- Docker buildで`COPY configs/data-profiles`が失敗した。原因は`.dockerignore`の`configs`全体除外で、Dockerfileとの設定不整合だった。`configs/*`を除外しつつ`configs/data-profiles/`だけ再包含するよう修正した。

### 検証

```bash
./.venv/bin/python -m unittest tests.test_timezones tests.test_capital_pipeline tests.test_evaluation_protocol -v
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m compileall -q src tests scripts
./.venv/bin/python -m pip check
```

- 対象3 moduleの17テスト成功。全suiteは48テスト成功（最終実行2.996秒）。隔離sandbox内の実行はFastAPI先頭テストで停止したため中断し、許可付きの通常実行で同じコマンドを完走した。終了コード0。Starlette/httpx利用の非推奨warningは出るが失敗ではない。
- compileall成功、`pip check`は`No broken requirements found`。`git diff --check`も成功。
- Docker Desktop 4.97.0 / Engine 29.9.2へ許可付き接続。修正前の`docker build --tag bike-demand-r04-tz-check:local .`は上記`.dockerignore`理由で失敗。修正後はbuild成功し、image内でpackage版`2026.5`、America/New_York TZif 1,744 bytes、SHA-256 `d7f2206b3a45989fc9ad63d558922532fa7352280d5f87176bf1db79cb1d1fa9`を確認した。コンテナの春DST gridは`00, 01, 03, 04`でhostの結果と一致。検証専用image tagは確認後に削除した。WSL hostのOS timezone database表示は`2026c`。これはpackage版と別記録で、同一版とは推定しない。

### 実データ再集計と独立検査

既存の集計結果や評価データを上書きしないよう、新しい出力pathを使った。候補学習・評価・MLflow登録・API/Compose再起動は行っていない。

```bash
./.venv/bin/python scripts/prepare_capitalbikeshare.py \
  --archive-start 202301 --archive-end 202605 \
  --data-start '2023-01-01 00:00:00' \
  --data-end-exclusive '2026-05-01 00:00:00' \
  --output-csv data/derived/capitalbikeshare-r04-tzdata2026-5/hourly.csv \
  --output-manifest data/derived/capitalbikeshare-r04-tzdata2026-5/manifest.json
```

- 全41 ZIP、source rows 19,440,291、重複205、一意ride ID 19,440,086。内訳は期間外592,491、DST除外1,568、CSV出力18,846,027 trip startsで収支一致。CSVは29,177時間。期間内の空き7時間は春の欠落4時間・秋の曖昧3時間と一致し、timestamp duplicate 0。
- 新CSV SHA-256は`cc4b39c67db286d30300cc6fb10a0f37e942eb337a7bd964795bbdd891762a96`。旧schema 2 CSVと同じhash。新manifestはschema 3 / `capital-hourly-aggregation-v3`で、timezone identityを含む。
- 生成後に`validate_capital_manifest`でschema 3を再読込し、41原本すべてのbyte数/SHA、timestamp coverage/grid、行収支、CSV SHAを検査した。新旧manifest間で41件すべてのarchive SHAが一致し、CSV SHAも一致。再集計の再現証拠であり、原本・期間が同じなので未使用の新評価データではない。
- 検査後の出力先: `data/derived/capitalbikeshare-r04-tzdata2026-5/`（local generated data、Git対象外）。

計画書R04を完了とし、`capital-bootstrap-gate.md`、`data-profiles.md`、`runbook.md`もschema 3/未使用データの条件へ更新した。commit `13ea918`を`origin/main`へpushした。契約テストCI `40655472121`はlock install、`pip check`、48テスト、compileallをすべて通過してsuccess。GitHub Pages build/deploy `40655471660`もsuccess。次の優先作業はR06のincumbentなしbootstrap protocol/candidate/final-evaluation実装。その後に初めて未使用2026年データを取得・監査する。

## 2026-10-28: R06 incumbentなしprotocolの基礎

### 目的と差分

直前の残作業順に従い、未使用期間を取得・採点する前に、既存Capital評価経路が同target incumbentを必須にしている点を解消する最初のコード節目を進めた。作業開始時の追跡差分はなく、今回の対象ファイルに既存の別作業は混在していなかった。

- `src/bike_mlops/evaluation_protocol.py`: schema 2の`capital-bikeshare-bootstrap-v1`を追加。incumbentを`null`にし、合意済みのvalidation/final MAE・RMSE条件、segment診断専用、運用gate分離を含むpolicy本体をprotocolへ保存する。policy IDは`capital-bootstrap-baseline-mae-rmse-v1`、canonical JSON SHA-256も保存・検査する。schema 1の既存incumbent protocol読込は維持。
- `scripts/create_capital_evaluation_protocol.py`: `--incumbent-version`を省略可能にした。省略時にはMLflow model registryを参照せずbootstrap protocolを生成し、指定時には既存schema 1経路を使う。
- evaluation ledger: incumbent不在のvalidation attemptを`incumbent_artifact_sha256: null`として記録可能にした。
- `src/bike_mlops/training.py`: candidate runnerがincumbentなしprotocolを検出したら、評価ledgerへ開始記録する前に理由付きで停止する。candidate/final scoringはまだschema 2 policy未対応で、窓を誤って消費しないための一時的な保護である。
- `tests/test_evaluation_protocol.py`: policy hashと条件の固定、policy改変拒否、incumbent tupleの一部欠落拒否、台帳のnull記録を合成データで確認。
- `docs/capital-bootstrap-gate.md`と`261014/plan_mlops.md`: 実装済み範囲と、まだcandidate/final CLIを使えないことを明記。

### 実行したコマンドと結果

最初にglobal `python`で試したが、このPATHにはコマンドがなく終了コード127だった。プロジェクトvenvの実行ファイルを使うことで解消した。

```bash
python -m unittest discover -s tests -p 'test_evaluation_protocol.py' -v
# /bin/bash: python: command not found (exit 127)

.venv/bin/python -m unittest discover -s tests -p 'test_evaluation_protocol.py' -v
# candidate-runner guard追加前の中間実行: 9 tests, OK

.venv/bin/python -m unittest discover -s tests -v
# 最終実行: 52 tests, OK (3.163s)。Starlette/httpx利用の非推奨warningあり。

.venv/bin/python -m compileall -q src scripts tests
# 成功、構文エラーなし

git diff --check
# 成功、whitespace errorなし

DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/create_capital_evaluation_protocol.py --help
# 成功。--incumbent-versionがoptionalと表示された。

docker compose -f compose.yaml -f compose.capital.yaml config -q
# 成功、静的Compose設定にエラーなし。

sh -n scripts/bikectl
# 成功。

.venv/bin/python -m pip check
# No broken requirements found。pip cacheが書込不可という警告は出たが依存整合性確認は成功。
```

初回にはglobal `python`がなく失敗し、venvへ切替えた。全確認をまとめて実行する際、誤ってworking directoryを`.venv/bin`に指定したため相対pathを解決できず、作業ディレクトリをproject rootへ戻して再実行した。sandbox test runはFastAPIの最初のTestClientテスト開始後に停止したためCtrl-Cで中断し、通常WSL実行へ昇格して全suiteを完了した。candidate CLIでも`--incumbent-version`を省略できるようにし、未対応bootstrap protocolはregistry版を仮指定せず明示エラーにできる。

評価データのダウンロード、protocolの実データ凍結、候補fit、validation/final評価、MLflow登録、Compose起動・再起動、API呼出しは行っていない。したがって新窓の消費も精度・稼働結果もない。

### 未完了と次の節目

現行のcandidate trainerとfinal-evaluation scriptはincumbent予測列・incumbent metricsを直接参照する。schema 2はprotocol/ledgerでのみ有効になった段階で、candidate fit自体は実行不可である。次はcandidate reportと行別validation evidenceをcandidate/baselineの共通timestamp・actual・行集合hashで照合し、MAE厳密改善かつRMSE非悪化のgateを有限値・同一件数の検査込みで再計算する。segmentsは件数/MAE/deltaのみ、gateには使わない。その後、選択artifactのhashを固定してfinal scriptもbaseline-onlyに変更し、final gateを独立に再計算する。両経路のテスト、CIが通るまで2026年未使用データを取得・評価しない。

この節目はプロトコル基盤のみの部分完了である。bootstrap protocolを使ったcandidate runnerが「未対応・窓を未消費」と報告し、ledger開始関数を呼ばないことは回帰テスト済み。R06項目と11.4の順序をこの状況へ更新した。

## 2026-10-28: R06 baseline-only candidate/final scoring

### 実装

前節で固定したprotocol schema 2を候補学習と最終評価へ接続した。未使用2026年データは取得せず、合成データだけを使った。

- `src/bike_mlops/quality_gates.py`を追加。candidate/baselineのMAE・RMSEが不足、非数値、非有限、負値なら`not_evaluable`/不合格。判定はMAE厳密改善、RMSE非悪化で、条件ごとの比較値・operator・reason・policy ID/SHAを返す。
- Capital baseline-only candidateはschema 2かつincumbent引数なしを要求し、MLflow Registryを呼ばず学習する。validation reportにcandidate/baseline指標、各segmentの母数/誤差/差、validation gate、policy ID/SHA、final/operational gate未実施、deployment blockedを記録する。validation row CSVはincumbent列なしで保存する。
- 最終評価はbootstrap時にincumbent loadを行わない。candidate artifact hashを確認し、選定時と同じvalidation予測をモデルから再生成、actual・timestamp・label availability・row-key hash・baseline予測と照合してから評価台帳を開始する。candidateは再fitしない。独立final窓でcandidate/baselineのMAE/RMSE gateとsegment差を計算し、運用測定・人手承認がない限り配信許可をblockedとする。
- legacy schema 1 incumbent経路と過去候補reportの後方互換を維持した。古いreportにsegment comparison列がない場合はlegacyでは受け入れ、bootstrap reportでは必須にする。
- synthetic training/evidenceテスト、gateの非有限値/MAE同値/RMSE悪化テストを追加。`mlops-bikectl candidate --help`の説明もincumbentまたはbaseline-only protocolとした。

### デバッグ記録と検証

合成candidate fitの最初の試験では、archiveに2件しかなく学習特徴がほぼ定数になり、HistGradientBoostingRegressorが`window shape cannot be larger than input array shape`で失敗した。原因は小さすぎるテストfixtureで、実装側の期間を調整しなかった。31日分の時刻・台数変化を含む合成archiveへfixtureを広げ、fitが通るようにした。集計元には2,234 ride recordsがあり、実データではない。

candidate trainer fixtureの初回検証では、`validate_capital_manifest`の既定raw pathが一時fixtureのraw dirを指さずarchive byte count mismatchになった。テスト内だけでvalidatorに一時raw pathを渡し、production codeのarchive検査はそのまま維持した。既存validation-evidenceテストはprotocolにincumbentがなかったためbootstrapと誤認したので、legacy fixtureにincumbent情報を追加した。さらにlegacy candidate reportにはsegment comparisonが存在しないため、schema 1では当該フィールドをoptional、schema 2では必須と分けた。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_evaluation_protocol.py' -v
# 10 tests, OK。baseline-only protocol、台帳、registryを呼ばない合成fitを確認。

.venv/bin/python -m unittest discover -s tests -p 'test_candidate_artifacts.py' -v
# 7 tests, OK。legacyとbaseline-only row evidenceを確認。

.venv/bin/python -m unittest discover -s tests -p 'test_quality_gates.py' -v
# 3 tests, OK。

.venv/bin/python -m unittest discover -s tests -v
# 56 tests, OK (9.958s)。Starlette/httpx利用のDeprecationWarningあり。

.venv/bin/python -m compileall -q src scripts tests
git diff --check
docker compose -f compose.yaml -f compose.capital.yaml config -q
sh -n scripts/bikectl
.venv/bin/python -m pip check
# すべて成功。pip checkはNo broken requirements found。pip cache書込不可warningのみ。

.venv/bin/mlops-bikectl candidate --help
# 成功。--incumbent-versionはoptionalであることを確認。
```

FastAPI TestClientを含むfull suiteはsandboxでは最初のtestで停止する既知挙動があったため、中断後、通常WSL process環境で再実行し全56件を完了した。前のprotocol commit `513436b`のCI `40656621848`とPages build/deploy `40656621926`もsuccessである。

### 未実施と次の作業

この実装検証ではprotocolを実データへfreezeしていない。未使用archiveの取得、candidate validation、final evaluation、MLflow登録、Capital Compose/API起動、operational benchmark、MLflow tagsとdeploy側のgate連携、final script全体を実bundleで通すintegration testは未実施。56 testが通ったことはモデル精度やAPI稼働の証拠ではない。次に実bundleを使ったfinal CLI integration testでcandidate identity/hash・policy・validation gateの不合格拒否・final gate report/台帳を確認し、R06 gateと配信拒否の全コードが揃うまで新評価データを取得しない。

計画書と[`docs/capital-bootstrap-gate.md`](docs/capital-bootstrap-gate.md)を更新し、R06を部分完了のまま残した。運用・人手承認gate未実施時のdeployment permissionはblockedであり、未実測の精度・運用結果は記録していない。

## 2026-10-28: R06 final-evaluation integration・deployment gate・運用benchmark

前項の「final script全体を実bundleで通すintegration testは未実施」は、その記録を書いた時点の状態だった。この後、合成fixtureを使うend-to-end final CLI試験を追加した。以下が本節時点の更新結果。

### 実装

- `quality_gates.py`でCapital bootstrapのvalidation/final精度gateを実装した。MAEはbaselineを厳密に下回る必要があり、RMSEはbaseline以下を許す。欠落・非有限・負値は`not_evaluable`として不合格にする。
- bootstrap candidate trainingとfinal evaluationをMLflow incumbentなしで動かす経路を実装した。合成candidate bundleからcandidate fit、行別validation evidence照合、artifact SHA確認、同一candidateを再fitせず最終評価、final row report/ledgerまでのCLI integration testを追加した。実データは使っていない。
- `deployment_gates.py`にCapital fail-closed checkを追加し、runtime init/promote/API model loaderは4 gate status、固定policy ID/SHA、`deployment_permission=eligible`を要求する。診断指定・rollback指定もCapitalの未達gateを通過しない。UCI policyは変更しない。
- `operational_benchmark.py`と`benchmark_capital_runtime.py`を追加した。localhostのみに接続し、Capital APIのprofile/model/version/artifact identityを事前確認する。固定168時間入力を循環使用して暖機50件+逐次1,000件を送り、自動retryなし・timeout 10秒で全requestのID/時刻/HTTP status/latency/予測値/data-version hash/エラーを保存する。HTTP schema・有限非負予測を検証し、p95 nearest-rank法、p50/max、host OS/CPU/RAM/Python/package版、取得可能なCapital stack container resource/image/`docker stats`/engine、API readiness時のDB疎通を記録する。benchmark単体のdeployment permissionは常にblocked。
- 計測入力のAmerica/New_York wall hour gridで存在しない時刻・二重時刻がある場合は送信前に停止する。reportは`data/reports/capital-bikeshare/`内の新規JSONだけに書き、既存ファイルの上書きやlocalhost以外への送信を拒否する。
- 途中でCtrl-Cされた場合も実行済みのrequestを部分reportへ残し、`completed=false`かつgate failedにする。APIへ送信中の要求はinterrupt errorとして記録し、retryしない。
- 計画書とgate文書で、通常Capital runtimeがeligible modelだけを読める一方、未承認candidateの運用測定には隔離staging APIが必要という循環を明記した。gateを弱めず、blocked candidate登録・staging API・MLflow report/tag書込みを次の先行タスクに置く。

### デバッグと検証

ベンチマークの初回fixture試験でテスト用Capital profileに`target.timezone_name`がなくtimezone grid検査で`KeyError: 'target'`になったため、fixtureを実profileのAmerica/New_York設定に合わせた。nearest-rank p95の期待値も修正した。1000件中1件だけ高遅延でもp95には現れないため、境界を検証するよう高遅延を51件にし、p95=251msとして不合格を確認した。中断時partial reportの追加試験ではversion fixtureの`feature_contract_version`が不足して`KeyError`になったのでfixtureを補った。いずれもテストデータの欠落/誤期待で、production codeをテストに合わせて弱めずfixtureを修正した。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_operational_benchmark.py' -v
# 9 tests, OK。固定要求数、identity pinning、no retry、p95、local URL、168h/DST grid、data hash、interrupt partial report、report write guardを確認。

.venv/bin/python -m unittest discover -s tests -v
# 68 tests, OK (8.927s)。FastAPI TestClientの実行制約を避ける通常WSL processで完了。
# Starlette/httpxのDeprecationWarningは表示されたが、失敗ではない。

.venv/bin/python -m compileall -q src scripts tests
git diff --check
docker compose -f compose.yaml -f compose.capital.yaml config -q
sh -n scripts/bikectl
.venv/bin/python -m pip check
# すべて成功。pip checkはNo broken requirements found。

.venv/bin/python scripts/benchmark_capital_runtime.py --help
# 成功。固定測定スクリプトの引数を確認。
```

通常sandbox内の全suiteはFastAPI TestClientの最初のcaseで停止したため中断し、通常WSL process権限で再実行して68 testsを完了した。pip cacheは`/home/kenshin/.cache/pip`に書込不可という警告を出したが、依存検査結果は正常。Compose確認は静的`config -q`のみ。

実MLflow登録/tag更新、Capital Composeサービス起動、API benchmark実測、未使用データ取得・protocol freeze・実データvalidation/final評価は未実施。今回のテスト件数や合成HTTPのpassは、運用gateまたは精度gateの実測合格を示さない。ここでは新しい評価窓を消費していない。

### 次の優先作業

1. candidate artifact/reportとfinal reportが一致するblocked MLflow registrationを作る。
2. 通常のruntime/promoteから隔離したstaging API/Compose projectを作り、blocked candidateだけを正確なcandidate ID/artifact SHAで起動できるようにする。通常API loaderのgateを迂回する環境変数やdiagnostic経路は使わない。
3. staging modelのHTTP/offline parityとfailure rejectionをsystem testし、staging API経由で固定operational benchmarkを測れる状態を作る。
4. final/operational report hash、policy ID/SHA、candidate identityをMLflow version tagsへ付け、全gate passedかつ理由付き手動承認の場合だけ`deployment_permission=eligible`を最後に記録する。
5. その一式のsystem test後に初めて新しいCapital archiveを取得・manifest検査・protocol freezeし、validation→同一artifactの独立final評価へ進む。

### Commit・push・CI

- コード、テスト、計画書、用語集、作業ログの節目を`35065bd Implement Capital bootstrap gates and runtime benchmark`として`main`へcommitし、`origin/main`へpushした。
- GitHub Actions contract tests: run `40659664076` success。lock install、`pip check`、68 tests、`compileall`が完了。
- GitHub Pages build/deploy: run `40659664964` success。buildとdeployの両jobが完了。
- push後の作業ツリーでは、この作業対象のtracked変更は残っていない。既存の未追跡`260701/charts/`、`260701/logs/`、`260905/sets/bookkeeping-2kyu/images/assekiritono-flow.png`はこの作業に含めず、そのまま保持した。

## 2026-10-28: R06 blocked registrationとcandidate staging経路

### 実装

前回作業の優先順に従い、通常runtimeのdeployment gateを変更せずに、validation/final精度gateを通過したCapital候補の運用測定を可能にする経路を作った。データ・評価期間の新規取得や消費はしていない。

- `src/bike_mlops/capital_staging.py`と`scripts/register_capital_candidate.py`を追加。登録前にcanonical candidate bundle位置、candidate ID、model/protocol/validation evidence/source snapshotのSHA、source snapshot内のソースtree hash、bootstrap protocolのschema/policyを検査する。candidate validationとfinal accuracy gateを保存値だけで信じず、MAE/RMSE値から再計算してreport内の全gate証拠と一致することを要求する。final reportは同じcandidate/model/protocol/data/manifest/validation windowを指し、評価期間・件数が凍結protocolと一致し、再fitなし・deployment blocked・operational未評価であることを要求する。
- 登録先を`http://localhost:5001`または`127.0.0.1:5001`のCapital MLflowに制限した。MLflow version tagsにprofile/target、validation/final=passed、operational=not_evaluated、deployment_permission=blocked、candidate/raw artifact/report/protocol hashとpolicy identityを記録する。登録model artifactを再取得して`MLmodel`とdirectory SHAを確認する。候補IDの重複登録は拒否し、同一IDの再実行は証拠tag/artifactが一致する場合だけ再利用する。
- version・candidate ID・candidate model SHA・selection/final report SHA・protocol SHA・registered artifact SHAを含む`data/runtime-capital-staging/staging.env`を作成する。ファイルは異なるidentityで上書きせず、プロジェクト外へ解決するsymlink pathも拒否する。認証情報はこのファイルへ書かない。
- `src/bike_mlops/staging_api.py`を追加し、既存API routeを専用ASGI appから再利用する。staging loaderはprofile/target、固定policy、registry READY、blocked状態、すべてのpinned candidate/report/artifact identityを検査し、登録artifact hashが一致した後でだけ読み込む。通常API loader、runtime init、deploy promoteは変更していない。APIのready/version/predict応答にstaging candidate IDとblocked permissionを載せる。
- `compose.capital-staging.yaml`を追加。Caddyへ接続しない候補専用APIをloopback `127.0.0.1:8110`で公開し、予測監査DBは`capital-staging-postgres-data` volumeでblue/green側と分離する。DB seed用にloopback `127.0.0.1:55434`を公開する。隔離Capital MLflow serviceは共有する。
- `scripts/seed_capital_staging.py`を追加。現在のCapital profile既定CSV/manifestを既存validatorで確認してstaging PostgreSQLへseedする。パスワードは環境変数から受け取り、URLへpercent-encodeして使用し、出力・ファイルには保存しない。DBをseedしてからstaging APIを起動する手順を`docs/capital-bootstrap-gate.md`と`docs/runbook.md`へ記載した。
- 固定benchmarkに必須の64桁candidate ID引数を追加。`/ready`・`/version`・全prediction応答でcandidate/version/artifact identityと`deployment_permission=blocked`を照合し、レポートのmodel identityにもcandidate IDを残す。default URLをCaddy proxyからstaging API `127.0.0.1:8110`へ変更し、Docker資源取得対象にもstaging DB/APIを含めた。
- `../261014/plan_mlops.md`とgate文書に、今回の実装範囲と、まだ実行していないMLflow/Compose integration、実measurement、tag更新、実データ作業を区別して追記した。

### デバッグ・検証

- 初期synthetic bundle testで、fixture作成directoryをその子directoryへrenameして`OSError: Invalid argument`になった。実装ではなくfixture構造の誤りだったため、temporary bundleをsiblingに置いてからcanonical candidate directoryへrenameする形に直した。
- 次のstaging fixtureでは、selection reportの`validation_gate_status`・固定policy・validation metricと、final reportのselection path/source/window/MAE/RMSEが不足してfalse rejectした。production条件は緩めず、実レポート形状に合わせてfixtureを補った。さらに実装でselection/finalそれぞれのMAE/RMSE gateを再計算することで、ステータス文字列だけを変更した偽の合格reportを登録できないようにした。
- operational benchmark fixtureは新しいpreflight条件`deployment_permission=blocked`とcandidate IDを返さず、正当に拒否された。正常・中断・候補不一致fixtureへ値を追加し、既存API契約の条件は変更しなかった。
- 制限付きshellからFastAPI TestClientを含むテストを実行したところsocket初期化の段階で停止したため中断した。ローカルsocketを使うテストとして承認付き通常実行へ切替え、API testを含むfull suiteを完走した。
- 読み合わせ中にbenchmarkのerror countが二重加算されていると一度誤認したが、該当コードを再確認するとincrementは1回だった。これは出力の読み違いで、実装変更はしていない。

```bash
.venv/bin/python -m unittest discover -s tests -v
# 71 tests, OK (9.293s)。FastAPI TestClientを含む。Starlette/httpx DeprecationWarningあり。

docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
# 成功。3ファイル結合の静的Compose検査のみ。serviceは起動していない。

.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
sh -n scripts/bikectl
git diff --check
# 成功。pip checkはNo broken requirements found。pip cache directory書込不可warningのみ。

.venv/bin/python scripts/register_capital_candidate.py --help
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/seed_capital_staging.py --help
.venv/bin/python scripts/benchmark_capital_runtime.py --help
# 成功。CLI option確認。seed helpのみでDB接続はしていない。
```

現在ある実candidate/final reportと稼働するCapital MLflow/Composeを使った登録・load・DB seed・API system testはしていない。staging API起動や実予測benchmarkも未実施。これは実装と合成/静的検証の節目であり、stagingが稼働した証拠や精度/運用gateの合格を示すものではない。UCI stack/modelは操作していない。未使用評価期間も消費していない。

### 残作業の優先順

1. 実candidate/final reportが作成された後に限り、隔離Capital MLflowでblocked registrationのidempotency・hash・tagsを確認する。
2. staging ComposeのDBだけを起動し、seed scriptでCapital CSV/manifestの検証・投入件数を照合。その後APIを起動し、staging DBの`/ready`・予測監査保存、candidate ID・version・hash応答を確認する。
3. stagingの実candidate APIでoffline/API prediction parity、登録identity不一致・欠落・破損時のready拒否、通常`runtime init`/`deploy promote`がblocked versionを拒否するsystem testを行う。
4. warming 50/serial 1,000 fixed benchmarkをstagingで実測し、operational report hash/statusを正確なMLflow versionへ追記する経路を実装・試験する。全gate後の理由付き手動approvalとeligible tag最終設定も別途検証する。
5. その後でのみ、計画書に沿って未使用archive範囲を確認し、protocolを凍結してvalidation/final評価へ進む。

### 最終レビューで追加した証拠整合性検査

差分レビューで、blocked registrationがfinal reportの集計指標からgateを再計算していたものの、その指標の元になったfinal row CSVと消費台帳との照合が不足していると分かった。登録前に以下を拒否するよう強化した。

- frozen protocolがcandidate reportと同じデータSHA・source manifest SHAを指すこと。
- final reportがproject内canonical report directoryにあるrow-level prediction CSVを指し、CSV SHA、protocol固定件数・期間、時間順/一意な時間別timestamp、ラベル到着遅延を満たすこと。
- 保存済みactual/candidate/baseline予測行からMAE/RMSEを再計算し、final reportの集計値と許容誤差内で一致すること。モデルを再評価・再fitせず、最終期間を再利用しない。
- 評価台帳に同じwindow/data/targetのcompleted recordがあり、attemptのprotocol SHA・candidate artifact SHA・detailのreport SHAが一致すること。
- staging model loaderがMLflow READY statusと必要tagに加え、artifact directoryをhash検査してからだけloadすること。

テストfixtureの初回実行では、MLflow loader fixtureの`target_contract_id`がCapitalではなくdefault UCI値になってregistry tag検証で拒否された。Capital targetを明示するfixtureに直し、production gateは緩めなかった。最初の限定sandbox full-suite実行はFastAPI TestClientのsocket利用で停止したため中断し、承認付き通常WSL実行で完走した。Starlette/httpx DeprecationWarningは残るが、テスト失敗ではない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python -m unittest discover -s tests -v
# 75 tests, OK (10.116s)。FastAPI TestClient、staging loader artifact pinning、row/report/ledger整合性を含む。

.venv/bin/python -m compileall -q src scripts tests
git diff --check
sh -n scripts/bikectl
docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
.venv/bin/python -m pip check
# すべて成功。pip checkはNo broken requirements found。pip cache書込不可warningのみ。
```

この追加検査も合成evidenceである。実candidate/final reportは存在せず、MLflow registration、PostgreSQL seed、staging Compose/API起動、実API/offline parity、1,050件benchmarkは実施していない。データ取得・評価期間の消費・通常UCI稼働環境への変更もない。

### 更新後の最優先残作業

1. 実データ取得前に、disposable Compose project/volumeと一時MLflow storeでsynthetic model/evidence/観測だけを使うsystem testを行う。blocked登録/idempotency、artifact・tag拒否、API ready/predict、DB分離、offline/API parity、通常deploy拒否を確認してtest resourceを破棄する。
2. operational report hash/status/policy/candidateを正確なMLflow versionへ反映するコードを実装し、synthetic reportでfail-closed動作を検証する。全gateと理由付き承認後だけ`eligible`にする。
3. 月境界bufferと未取得原本範囲を決定し、未使用データを取得・manifest監査してprotocolをfreezeする。
4. validationを一度実施し、合格時だけ同一artifactで独立final評価を一度行う。
5. 実candidateをblocked登録し、Capital staging DB/API/HTTP parityを実環境で確認してから、50 warmup/1,000 sequential benchmarkとMLflow report tagを実測・記録する。gate未完了中は配信を承認せず、UCI版を変更しない。

計画の順序に残っていた依存の循環（system testにcandidateが要るがcandidateを作る前に新データ取得を禁じていた）を解消した。実データ取得前のsystem testは合成evidenceを使う使い捨て環境、実候補を使うstaging/benchmarkはvalidation/final評価後と区別し、`261014/plan_mlops.md`と`docs/capital-bootstrap-gate.md`へ反映した。

### push後の環境・CI確認

- commit `d263f8e Add isolated Capital candidate staging lane`を`origin/main`へpushした。
- GitHub Actions contract tests run `40663177779` success。lock install、`pip check`、75 tests、`compileall`がsuccess。
- GitHub Pages build/deploy run `40663177219` success。
- Docker接続をsandbox内で最初に確認した際は`/var/run/docker.sock` permission deniedだった。承認付きread-only確認ではDocker client/serverとも`29.9.2`と応答した。Capital 3-file Composeの`ps -a`は空で、現在このprojectのserviceは起動していない。Composeの起動・停止やvolume操作はしていない。
- data inventoryではCapital derived CSV/manifestとraw monthly archiveが存在し、raw archiveは2026-05まで見つかった。一方、canonical `data/candidates/<candidate-id>/candidate-report.json`および`data/reports/capital-bikeshare/`内のfinal reportは見つからなかった。従って実MLflow登録・staging seed/API/benchmarkにはまだ進めない。既存の2025・UCI評価reportを新しい候補や未使用評価期間の代用にはしない。
- `git status`で残るのは他の日付ディレクトリの既存untracked chart/log/imageのみ。今回のcommitに含めず、変更・削除していない。

## 2026-10-28: 合成candidateのCompose統合検証と修正

### 範囲と準備

`f365dfa`の`261021`を`git archive`で`/tmp/bike-mlops-it-4UTZQoxs/261021`へコピーし、後述の修正sourceをそのコピーへ反映した。元のUCI projectとは別名の`bikemlopsit4utzqoxs`、テスト専用の仮パスワード、port 5001/55433/55434/8110、独立した3 volumeを使用。開始前に同名project/volumeが存在せず、使用portが空いていることを確認した。

人工データは2024年1月の21,612 ride recordsを生成して744時間へ集約した。人工データでのみprotocolを凍結し、validationと独立finalを各1回実行した。合成validationはcandidate MAE 0.143229/RMSE 0.214217、baseline MAE 7.142857/RMSE 13.363062。合成finalはcandidate MAE 0.140567/RMSE 0.210506、baseline MAE 6.818182/RMSE 13.055824で、双方の精度gateがpassedだった。これらは実データの精度を表さない。

初めに既存の`bike-mlops-mlflow:latest`を一時containerで調べたところ、固定依存の`tzdata`がなく、現在のlockを満たしていなかった。代用せず、テスト用MLflow imageを現在のDockerfileと`requirements.lock`からbuildした。API imageはその固定依存imageを基に、今回の修正sourceを再installするテスト用overrideでbuildした。

### 発見した問題と修正

1. 最初のstaging APIは起動直後に再起動を繰り返した。ログは`Unknown data profile 'capital-bikeshare'`と`/usr/local/lib/python3.12/configs/data-profiles`の`FileNotFoundError`を示した。通常installされたpackageの位置を基準に設定を探していたため、Dockerfileへ`PYTHONPATH=/app/src`を追加した。修正Dockerfileの実buildでは`PROJECT_ROOT=/app`となり、UCI/Capital両profileの読込を確認した。APIの実動作は、同じ固定依存と修正source/環境設定を含むテスト用override imageで確認した。
2. 予測の再送時に現在のAPI状態からcandidate IDを付けていたため、候補切替後は保存済みの旧予測へ新候補IDを付ける可能性があった。`predictions.candidate_id`をnullable列として保存し、再送では保存されたIDを返すようにした。既存DBは起動時に列を追加し、過去行のIDは不明として`null`にする。staging予測応答は`deployment_permission=blocked`も返し、benchmarkが全応答で確認する。通常APIはstaging候補IDを環境から取得しない。

### 結果

- 合成candidateを一時MLflowへblocked登録。source修正後に作り直した候補は一時version `2`、candidate ID `4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274`。同じ候補の再登録は同じ版/hashを返し、同IDを持つversionは1件だった。これは使い捨てregistry内の番号で、UCI版番号とは関係しない。
- 合成CSV/manifestをstaging PostgreSQLへseedし、744観測を保存。別のCapital側PostgreSQLの`bikeops`には予測表がなく、予測記録が混在しないことを確認した。
- staging `/ready`と`/predict`は200。同じrequest ID・入力の再送は同一応答、入力変更は409。オフライン予測 `40.052485924333126`、API応答 `40.052486`、絶対差 `0.00000007566687543203443`で許容範囲内。該当予測はstaging DBに1行だけあり、candidate ID・version・artifact SHAも一致した。
- 一時registryのcandidate ID tagを変更してAPIを再起動すると、約45秒のload retry後`/ready`が503となり、原因にcandidate ID不一致を示した。tagを戻して再起動後は200へ復帰。別途、登録artifact SHA不一致は実MLflowからのロード時に拒否した。
- 通常loaderと`runtime init --diagnostic`はblocked候補を拒否した。`deploy promote`の本体も、合成proxyを使い実registryを参照した上で拒否し、状態ファイルは変化しなかった。実Caddy切替・復旧操作はこの試験に含まない。
- 一時APIを合成version 2から1へ切り替えた後、version 2で保存した同一requestの再送はversion 2と元のcandidate IDを返し、新規requestはversion 1のcandidate IDを返した。その後version 2へ戻し、ready 200を確認した。
- 合成候補で暖機50件と逐次1,000件を送信。エラー0、p95 `106.464485 ms`、最大 `254.821038 ms`、監査DBの対応行1,050件、配信許可`blocked`。p95基準は満たしたが、他のhost負荷を測定しておらず、実候補の運用gateの合格証拠ではない。[全要求の合成レポート](docs/reviews/20261028-synthetic-staging-benchmark.json)のSHA-256は`cc68b465ae2dc1b819148f367361d1c92677731d6a883964a9383bf9b503c8f5`。[実行スクリプトの記録](docs/reviews/20261028-synthetic-staging-harness.md)には今回の一時パス・候補IDが含まれ、そのまま再実行するためのものではない。

### 残った入力条件の問題

benchmarkの合成入力は`2024-01-08 00:00`以上`2024-01-15 00:00`未満だった。最後の2対象時刻`2024-01-14 22:00`と`23:00`は+3時間のラベル到着がtraining cutoffの`2024-01-15 00:00`を超えるため、実際のfit行には含まれない。保存済み1,050行を別途読み直し、IDの一意性・HTTP 200・エラー0・p95再計算と、この2時刻を確認した。今回の測定はHTTP・監査・計測処理の統合確認であり、固定した「学習行のみ」の入力条件を満たした証拠にはしない。

また現在のseed scriptはprofileのCSV/manifestを検査するが、そのデータSHAが登録candidateと一致することを強制しない。benchmarkも168時間・時刻妥当性を確認する一方、凍結training splitやラベル到着時刻との照合を強制しない。実データの取得・実candidate benchmark前に、candidate/report/DBのデータ版照合と、全対象時刻のtraining split/ラベル到着/欠損検査を追加する。計画書R06とgate文書へ追記した。

### コマンドと検証・後片付け

```bash
# 以下のcompose操作はすべて一時コピー内で、-pには今回専用のproject名を指定。
docker compose -p bikemlopsit4utzqoxs -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml up -d --build mlflow postgres-staging
# 合成candidate登録を2回、synthetic CSV/manifestのseed、API image build/start後に
# 上記の登録・HTTP・DB・拒否・候補切替・benchmarkスクリプトを実行。
docker compose -p bikemlopsit4utzqoxs -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml -f /tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml down --volumes --remove-orphans
# このprojectの4 container、3 volume、networkと、今回作成した3つのimage tagを削除。

MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python -m unittest discover -s tests -v
# 75 tests, OK (6.128s)。その後に追加した旧DB行のNULL確認はstorage 7 testsで再検証しOK。
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

一時project名のcontainer・volume・imageが残っていないことと、一時ディレクトリ削除を確認した。既存UCI入口`http://127.0.0.1:8080/ready`は片付け前後ともversion 3・passedを返した。新しい実データは取得せず、実評価期間も消費していない。

### コミットとCI

- 上記の修正・検証記録をcommit `a07c581`として`origin/main`へpushした。
- GitHub Actionsのcontract checks（run `40667516861`）とPages build/deploy（run `40667516327`）はいずれもsuccess。
- 他の日付ディレクトリの既存untrackedファイル4件は今回の変更に含めていない。

## 2026-10-28: Capital staging seedのデータ版照合

前回の合成統合検証で、異なるデータ版にしか含まれない既存観測をseedが見逃す問題が見つかった。`scripts/seed_capital_staging.py`にcandidate report、final report、登録済みmodel versionの明示指定を追加。既存の候補・final証拠検証を再実行し、localhost:5001のCapital MLflowのREADY版・固定gate/tag・candidate/report/data identityを照合する。CSVとmanifestのSHAをcandidateと比較し、厳格manifest検証も行う。

DBの新しいstaging専用seedは、既存行を時刻・台数・available_at・data_hashまで候補データと比較する。異なる版、変更行、余分な時刻、空または重複する入力は拒否し、成功時のみ不足行を挿入する。検査と挿入は同じtransactionに置き、PostgreSQLではobservationsをロックする。seed後に別プロセスがDBを変更した場合の検出はまだなく、benchmark実行前の再照合が必要。証拠ファイル・DB・候補データを不変と見なせる権限分離までは実装していない。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_capital_staging_seed.py' -v
# 3 tests OK: 合成SQLiteで初回/再実行、registry・CSV・manifest不一致、
# 余分な行・値違い・版違い時に既存DBが変化しないことを確認。
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
# 78 tests OK (12.136s)。FastAPI testclientのStarletteDeprecationWarningは既存。
.venv/bin/python -m compileall -q src scripts tests
# 成功。
```

全テストの初回実行はsandbox内で先頭FastAPI testが進まなかったため中断し、制限外で同じテストを再実行したところ成功した。sandbox内Docker socket確認はpermission denied。今回PostgreSQL実機での新seed実行、候補登録からの実stack統合、実データ取得、benchmarkは実施していない。次は使い捨てPostgreSQLで同じ不一致とtransactionを検証し、seed後のDB変更を検出した上で、benchmark全対象が凍結training splitに属しラベルがcutoffまでに到着すること・APIデータ出典が候補と一致することを送信前に検査する。

## 2026-10-28: staging観測の再照合とPostgreSQL実検証

前回のseedに、投入済みDBの全観測を候補のCSV/manifest・MLflowタグと照合する`--verify-only`を追加した。DB検査は同一transactionで実行し、時刻・台数・利用可能時刻・データSHAと件数を比較する。余分な行、変更、欠落を拒否し、観測を挿入・更新しない（schema初期化確認は実行する）。照合後の別プロセスの書き込みは防止できないため、snapshotの有効期間とbenchmarkへの組み込みが残る。上記照合を自動で呼ぶ仕組みや、benchmarkのtraining split/出典照合は未実装であり、実候補の運用gate測定にまだ使えない。

一時Pythonスクリプトから`postgres:17.6-bookworm`を一意の`bike-seed-check-<random>`名で起動し、loopbackのDocker割当port・一時パスワード・独立した一時DBを使用した。合成観測2行について、seed 2行、再seed 0行、verify 2行、1行削除後のverify拒否、再seed 1行を確認した。検査transactionを保持した状態で別connectionからUPDATEすると、`lock_timeout=300ms`で`LockNotAvailable`となり、検査中の書き込みがロックされることを確認した。さらに1行を削除し、残る行のSHAを変更すると、seedとverifyは不一致を拒否し、欠落行は途中挿入されなかった。テストはDBメソッドへの実PostgreSQL接続であり、実MLflow候補からCLI全体を通す試験ではない。

```bash
.venv/bin/python /tmp/capital_seed_pg_check.py
# temporary PostgreSQL: seed=2, retry=0, missing rejected, repair=1,
# concurrent write locked, changed hash rejected without inserting missing row
# temporary PostgreSQL container removed
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
# 79 tests OK (10.808s)。既存のStarletteDeprecationWarningあり。
.venv/bin/python -m compileall -q src scripts tests
# 成功。
```

一時PostgreSQL containerは各回のfinallyで停止・削除し、同名prefixのcontainerが残っていないことを確認。一時スクリプトも削除した。既存UCIの`/ready`はテスト前後ともversion 3、passed。実データ・実候補・本番評価期間は使用していない。

## 2026-10-28: benchmarkの学習期間入力確認と誤合格防止

前回の合成benchmarkは学習締切時にラベル未到着の2時間を含んでいた。そこで`capital_benchmark_inputs.py`に、既存の候補・最終report検証、候補ID、設定CSV/manifestのhash、固定protocol、学習行の件数と`training_row_keys_sha256`を再確認し、指定168時間の全時刻が実際の学習行に含まれることを検査する処理を追加した。学習行の選択はtrainerと同じ `timestamp < validation_start` かつ `timestamp + label_delay <= training_cutoff`。ラベルがcutoffちょうどに到着する行は許可し、cutoff後、欠けた時刻、異なる行や件数、delayの不一致は拒否する。元データの読込前後にもCSV/manifest hashを確認する。

`scripts/benchmark_capital_runtime.py`は候補reportとfinal reportを必須とし、現段階では`--check-input-only`による入力検査だけを提供する。DB snapshot・測定中の変更防止・API出典の結び付けが未完了のため、オプションなしのCLI測定はエラーで停止し、HTTP予測を送らない。内部の測定関数を直接呼んだ場合も、数値条件がpassedなら`measurement_status=passed`を記録しつつ、`operational_gate.status=not_evaluated`とする。検査用CLIはMLflow登録版・DB・API出典を確認せず、候補採用や実測合格の証拠ではない。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_capital_benchmark_inputs.py' -v
# 初回: 5 tests中4 failures/1 error。pandasのdatetime64[us]を誤って拒否した。
# 型判定をnaive datetime64全般に修正後: 5 tests OK。
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
# 86 tests OK (17.965s)。最終版では入力確認・CLI拒否・候補/CSV不一致も含む7 tests。
# その後のCLI引数整理とprotocol再読込時のhash照合の追加後に入力確認の7 testsを再実行しOK (2.945s)。
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

実候補の測定・新規データ取得・実評価は行っていない。次はbenchmark実行前に同じ候補とregistry/DBを照合し、測定中に観測表の変更を防ぎ、APIが使用したlag観測の出典を候補データから再計算して各応答と照合する。失敗やロック消失は運用gateに合格させない。CLI全体を合成MLflow/PostgreSQL/APIで検証してから実測を再有効化する。

## 2026-10-28: stagingチェックポイント後の差分レビュー

ユーザー依頼により、前回の正式レビューを確定した`7609af5`から`5df03f7`までの13コミット・45ファイルを点検した。対象はR04 timezone固定、R06 baseline-only gate、候補登録/staging、合成system test、seed/DB再照合、学習期間確認。詳細は[`docs/reviews/20261028-staging-checkpoint-review.md`](docs/reviews/20261028-staging-checkpoint-review.md)、秘密値を含まない集計は[`20261028-staging-checkpoint-evidence.json`](docs/reviews/20261028-staging-checkpoint-evidence.json)に保存した。

実装の方向と評価窓保護は妥当。ローカル86 testsを再実行して成功（10.996秒）、対象13コミットのcontract CI/Pages計26 runも全てsuccess。最新contract run `40693844897`のstepsではlock install、editable install、pip check、unit tests、compileallまでを確認した。CIはDocker buildや実MLflow/PostgreSQL/Caddy integrationを実施しないため、CI成功をそれらの成功へ読み替えない。

新たなレビューでは以下を残課題として具体化した。

- S01/P1: DB照合ロックは検査後に解放され、benchmarkに保持されていない。API出典hashも形式確認だけで候補からの再計算比較がない。任意の有効64桁hashをsynthetic probeが受理することを確認した。
- S02/P1: `require_deployment_eligible`は7個のstatus/policy tagだけで受理する。report・candidate/版・理由付き承認証拠の照合が未実装。registry変更や実配信を試したという意味ではない。
- S03/P1: 対象週・入力規則・policy・資源を最初の要求前に凍結する運用planの保存がない。現在の入力確認はfinal reportが必要で、事前の計画固定とは別物。
- S04/P1: 最新HEADの本来のDockerfileで全stackを再現するharnessがない。過去のPostgreSQL試験は一時スクリプト削除済みで独立した再現証拠が弱い。今後は実行コードとcleanupを保存する。
- S05/P2: runbookのseed照合、計画書のCLI実行状況が古い表現だった。今回現行説明を修正し、過去ログ・旧合成reportは維持した。
- S06/P1: 旧計画はmanual approvalと復旧試験の順序が曖昧だった。実候補の測定はblockedで行い、監視・隔離復旧の証拠後に採否を決めるよう順序を修正した。

合成benchmark JSONのhashを照合し、1,050新規ID/HTTP200/error0、測定1,000件のp95 `106.46448500119732ms`を保存値から再計算した。同時に、学習cutoffまでにラベルが到着しない2対象時刻を再確認。旧JSONの`passed`は当時の数値判定として残すが、運用合格証拠には採用しない。再推論・実評価はしていない。

UCIの入口はready 200・版3で、基準commit時刻以降のDB prediction/deployment/monitor差分0、MLflow新規run/model作成更新0。取得可能なCompose差分ログ10行に検索した既知エラー語は0件。Capital/一時test containerは残っていない。Capital原本41 ZIPは存在し、canonical candidate/final reportは0。既存Capital reportは`data-audit-v2.json`だけだった。これは現存成果物と利用可能なログの点検で、原本全hashの再監査や消去された一時storeの復元ではない。

主な確認コマンド:

```bash
git log --reverse --format='%h %s' 7609af5..5df03f7
git diff --name-only 7609af5..5df03f7
gh run list --limit 50 --json databaseId,workflowName,headSha,status,conclusion,url
gh run view 40693844897 --json headSha,status,conclusion,url,jobs
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
# 86 tests, OK (10.996s)。出力は data/reports/review-20261028-staging/unit-tests.log。
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
sh -n scripts/bikectl
docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml config -q
# 成功。既存Starlette/httpx非推奨警告とpip cache書込不可警告あり。
```

DBは時刻条件付きSELECT、MLflowは全pageの検索API、Composeは`logs --since`を使った。実施した集計probeのコードはGit対象外の`data/reports/review-20261028-staging/review-probes.py`に保存した。これは今回の実施記録で、汎用integration harnessではない。

計画書11.5へ優先度・依存関係・完了条件を再整理した。次の節目は、部品を追加するだけでなく、凍結plan/候補/DB/HTTP出典/監査/reportを再現可能な合成system testでつなぎ、測定CLIを復帰させること。実データ取得・一度限りの精度評価はその後、理由付き承認は監視・復旧証拠の後に行う。今回の追跡変更はレビュー・計画・現行ドキュメントのみ。実装や稼働版は変更していない。

レビュー・計画・現行ドキュメントのローカルリンク49件（anchor含む）は全て解決し、証拠JSONのparse/対象26 CIの成功状態、`git diff --check`も確認した。既存の無関係な未追跡4ファイルはコミット対象から除外した。

## 2026-10-28: 測定中の観測固定・出典と監査の照合部品

計画11.5のS01/S03を進めた。`capital_measurement_guard.py`に、候補データとstaging観測の全行を照合してPostgreSQLの`observations`表を同じtransaction内でロックし、呼び出し元の処理終了まで保持するcontext managerを追加した。ロック待ちは設定したtimeoutで失敗し、接続喪失・ロック解除は`assert_held`で拒否する。監査行が別connectionからcommitされた後に見えるよう、transactionはREAD COMMITTEDを明示した。SQLiteは監査書き込みを妨げるため測定guardでは拒否する。

候補データの24/168時間lag・欠測・ラベル到着からAPIの`data_version_sha256`と特徴量を再計算し、request、response、保存したaudit行のcandidate/版/artifact/profile/入力hash/特徴量/予測値/出典を照合する処理を追加した。request prefix内の全監査行を照合してID重複・欠落・余分な行も拒否する。`data_version_sha256`はCSV全体のSHAではなく、実際に使ったlag情報のhashである。

再現可能な部分試験として`scripts/test_capital_snapshot_postgres.py`を保存した。既存image `postgres:17.6-bookworm`から、ランダム名・loopback自動割当port・使い捨てcredentials・独立DBを作る。DBの所有者は非superuser。合成モデルで起動したFastAPIの同じrouteを用い、2件の予測を実際に保存して次を確認した。

- 観測表の検査中、別connectionの観測更新がtimeoutする一方、予測と監査INSERTは成功。
- 応答/監査の候補ID・入力・特徴量・出典hash・予測値が候補データと一致し、改変値、誤ったhash、監査行の欠落/余分、ID重複は拒否。
- 別writerが先に表を使用している場合のguard取得timeout、観測変更後の不一致、guard connectionの切断、context終了後のlock解放を確認。
- containerは`--rm`付きで起動し、終了時に一意の名前とlabelを照合して削除した。同名prefixの残存containerなし。既存UCI入口はversion 3・readyを返した。

```bash
.venv/bin/python scripts/test_capital_snapshot_postgres.py
# status=passed。上記のPostgreSQL/API合成試験を実施し、一時containerを削除。
# data/reports/20261028-snapshot-guard/postgres-api.log
# SHA-256: 79630d62a0b338835fa6e94d253316689b16a7a367053c5ae2ba8bae3f4f9a25
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -v
# 90 tests, OK (11.680s)。API testclientの既存StarletteDeprecationWarningあり。
# data/reports/20261028-snapshot-guard/unit-tests.log
# SHA-256: 275bd7b70553466f724845485fb9f5ebd36842902777effc46430d02550a50ac
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。全suite後に部分integrationスクリプトを非superuser構成・ローカルDocker socket確認・名前とlabelを照合するcleanupへ修正し、再実行して成功。
```

この部分試験のモデルはmock、FastAPIはTestClientであり、実MLflow登録・staging Docker image・ネットワーク越しのAPI・1,050要求は試していない。これらのguardはbenchmark本体にもまだ接続していない。従って測定CLIは引き続き入力確認専用、運用gateは未完成。次に固定運用planの事前保存、候補・registry identity、guardと全要求の接続、接続喪失時partial reportと監査batchの証拠保存を実装し、最新Dockerfile・隔離MLflow/PostgreSQL/APIを通すsystem testを完成させる。新しい実データ取得、実評価、既存UCIへの予測・配信操作はしていない。

## 2026-10-28: 運用測定planの凍結と候補・登録版の再照合

計画11.5のS03として`capital_operational_plan.py`と`scripts/prepare_capital_operational_plan.py`を追加した。候補・final report、学習期間、CSV/manifest、登録済みCapital版のREADY状態とtagを照合し、検証後にregistered artifactを一時directoryへdownloadして`MLmodel`とdirectory SHAを検査する。version/tagが違えばartifact取得前に拒否する。ローカルCapital MLflow port 5001とstaging API port 8110に限定する。

計画JSONにはcandidate/model/version、candidateおよびregistered artifact・source data・manifest・evaluation protocol・reportのSHA、training row SHA、対象168時間、暖機50・測定1,000・同時数1・retry0・timeout10秒・p95 250ms/nearest-rank、各phaseで先頭から時刻を繰り返す規則と取得可能な資源情報を固定する。専用directoryへの新規作成をatomicなlinkで行い、同時作成時も片方だけ成功し、既存fileやsymlink経由の領域外への上書きは拒否する。SHA不一致、変更されたpolicy、未到着ラベル、別target/版/データ/成果物を拒否する。`verify_operational_plan_binding`は利用時に検証済み候補・データ・登録版・artifactと再照合する部品である。

plan作成は予測要求を送らず、DBを更新せず、運用gate・配信を許可しない。自己ハッシュだけでは内容とハッシュの同時書換えを防げない。**plan SHA/attemptを先に台帳へ固定し測定中のguard・全応答/監査へつなぐ経路はまだない。** 資源取得が不可でもその状態をplanに記録するため、未測定を合格証拠としない。実測CLIは停止中。

### 実行・デバッグ・検証

初めに一時SQLiteの実MLflow registryへ合成artifactを登録して調べたところ、downloadに`registered_model_meta`が追加され、元のdirectoryだけで付けたfixtureのSHAと一致しなかった。既存staging登録はdownload後のdirectory全体のSHAをtagに保存するため、合成fixtureも同じ条件に修正した。download 2回のSHA一致、実MLflowのversion/tag取得、plan作成・再照合、candidate tag変更後の拒否を確認。artifactは合成の小さなplaceholderで、モデルの逆シリアライズや実モデル評価はしていない。

さらにMLflowのコードを確認すると、tracking URIとregistry URIは別に設定でき、`MLFLOW_REGISTRY_URI`がtracking先とは別の登録先を選ぶ場合があると分かった。plan作成だけでなく既存のCapital staging登録・loader・seedも、明示したtracking URIと同じregistry URIを使うよう修正した。登録とloaderはartifact取得のためのMLflow global registry URIも同じ値へ設定する。別registry URIの環境変数を与えたmockで、登録・seedが正しいURIを指定することを確認した。実際の別registryへ接続・書込みはしていない。

最初の手動probeは、エラーをログへredirectした後に`tail`を実行したため、最後の`tail`の終了コード0が返ってしまった。ログの例外を読み、以後はコマンド自体の終了コードを確認して修正後を再実行した。失敗時のログは`data/reports/20261028-operational-plan/mlflow-test-debug.log`に残した。今回の実MLflow検証は独立した一時SQLite storeであり、既存UCI/Capital registryを変更していない。

```bash
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/prepare_capital_operational_plan.py --help
# 成功。実候補なしでplanを生成したわけではない。
MLFLOW_DISABLE_AGENT_HINT=1 timeout 120 .venv/bin/python -m unittest discover -s tests -v
# 登録先固定後、98 tests, OK (15.415s)。実MLflowの隔離SQLite registry testを含む。
# data/reports/20261028-operational-plan/unit-tests.log
# SHA-256: ca11cbc12cebc7ad7e772922d0522f57845f45023bec977820ba51633c06f1d9
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_operational_plan.py' -v
# 同時作成・symlink脱出の負例と登録tag事前確認追加後、8 tests OK (5.973s)。
# data/reports/20261028-operational-plan/plan-tests.log
# SHA-256: 2c3dda8098bcf2e458f993d4de2a0368dbe9c586d22a7e91075fc61af23f9211
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_staging.py' -v
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_staging_seed.py' -v
# 全suite後に追加した登録URIの負例を含め、staging 8 tests、seed 5 tests OK。
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。Starlette/httpxとMLflow/SQLAlchemyの既存非推奨警告あり。
```

手動probeの最終版と生ログはGit対象外の`data/reports/20261028-operational-plan/`に保存。再現用の実MLflow registry testは`tests/test_capital_operational_plan.py`にも追加した。計画書11.5とgate文書を進捗に合わせて更新。実candidate/final reportやCapital MLflow HTTP serverを使ったplan作成、DBロック中の1,050件、MLflow tag更新、実評価・実配信は未実施。次はattempt台帳と既存guardを測定の実行順へ結び付け、ロック喪失・部分失敗・監査不一致を非合格として記録し、隔離した実stackで通す。

### planにregistry接続先を追加

plan作成commit `34b7547`後に、candidate/version/artifactのほか、参照したCapital registry URIもplanへ固定する変更を加えた。必須項目の追加に合わせschema versionを2に上げ、旧schema 1は拒否する。再照合では指定されたregistry URIとplanの値の一致を要求する。`http://localhost:5001`と`http://127.0.0.1:5001`は同一視せず、最初に記録した表記を使う。plan作成は従来どおり予測を送らず、実測・承認の証拠は作らない。

先行commit `34b7547`のcontract CI `40702606419`とPages workflow `40702605286`はいずれもsuccess。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_operational_plan.py' -v
# registry URI変更の拒否を含む8 tests、OK (5.874s)。
# data/reports/20261028-operational-plan/plan-tests-registry-uri.log
# SHA-256: daae7132ec1584d35f7eaf5e7f66bcadf8e41bb2b31a4554ebf7675222e42670
# schema 2への変更と旧schema拒否を追加した後、別名で再実行: 8 tests OK (5.888s)。
# data/reports/20261028-operational-plan/plan-tests-schema2.log
# SHA-256: e2fd9fd5df6c076eca542c5b41b27e69d5635c3488bc718be84471e9aa99fa7a
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

ログの取扱いで誤りがあった。再試験時に、直前の節が記録する`plan-tests.log`を同じpathへ上書きしたため、旧SHA `2c3dda...`の生ログは残っていない。旧テストの結果と記録したSHAは当時の履歴として維持し、上記新ログのSHAと区別する。以後の再実行は別名で保存する。全suite 98 testsはcommit `34b7547`前の実行であり、この追加変更後は関連8 testsを再実行した。実候補・実測は引き続き未実施。

## 2026-10-29: 運用測定の試行台帳

計画11.5の最優先項目の一部として、`capital_operational_ledger.py`と確認用`scripts/operational_attempt_ledger.py`を追加した。候補ごとのロックを取得し、予測要求より前にplanの内容SHA・ファイルSHA、候補・登録版・registry URI、試行IDを台帳へ保存する部品である。要求開始直前の記録、重複実行の拒否、中断と証拠不足の記録、担当者・理由・次のplanを固定する再試行確認を用意した。完了reportは試行・plan・モデル識別とファイルSHAを検査し、contextの正常終了後にだけ台帳を確定する。台帳は一時ファイル、fsync、置換で書き、ロックは同一ローカルファイルシステムで同じlock先を使う協調プロセス間のもの。台帳自体の直接改変を防ぐ仕組みではない。

実装中に、完了操作の直後、context終了前に例外やプロセス停止が起きた場合でも先に完了記録が残る問題に気づいた。完了の書き込みを正常終了まで延期し、書き込み時にreportとplanを再検査するよう修正した。また中断記録の書き込み自体が失敗しても、元の例外を保持し追加情報を付ける。OSによる強制終了はstartedが残り、次の実行前に確認が必要。要求後のpartial reportがない中断は証拠不足として記録する。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 120 .venv/bin/python -m unittest discover -s tests -v
# 110 tests OK (18.634s)。最終の例外保持の補強より前の全体試験。
# data/reports/20261029-operational-ledger/unit-tests.log
# SHA-256: 88b598a140b09bd72320d0398bf75d5bf2fe9c766a904c58b470d106fbe280c9
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_operational_ledger.py' -v
# 最終変更後 11 tests OK (2.873s)。強制終了、同時ロック、再試行plan変更、
# 終了直前例外・report改変・中断記録失敗を合成データで検証。
# data/reports/20261029-operational-ledger/ledger-tests-final.log
# SHA-256: 597b2bf583d291ea763f0368e9c47ef7c292b4c5b4006c108438241477e0f4ee
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/operational_attempt_ledger.py list
# 成功。実台帳は未作成で空のrecordsを表示。実候補への試行やレビューは行っていない。
git diff --check
# 成功。既存のStarlette/httpxおよびMLflow/SQLAlchemy非推奨警告あり。
```

台帳はreportの識別・形を確認する部品であり、測定内容や精度・運用gateの独立した再検証ではない。plan、台帳、PostgreSQL guard、HTTP要求・監査・partial reportをbenchmark本体に統合する作業と、最新Dockerfileの隔離system testは残っている。測定CLIは停止したままで、実候補の登録・1,050要求・運用合格・配信承認は行っていない。次はこれらを固定した実行順でつなぎ、異常時を非合格にする。

## 2026-10-29: 測定処理と台帳・出典確認の接続部

固定benchmark内部に、試行ID・期待するartifact SHA・要求の前後を検査する接続部を渡せるようにした。試行IDはcanonical UUID、artifactは小文字SHA-256を要求し、APIの版・artifactが異なれば予測要求の前に拒否する。接続部を使う場合、要求直前の検査、通信、HTTP/応答検証、応答の出典確認のいずれかが失敗した時点で後続要求を停止する。送信前の検査失敗は送信済み要求として記録しない。timeoutなど送信後の失敗は試行された要求として記録し、未完了・非合格とする。既存の接続部なしの動作は維持した。

`capital_benchmark_observer.py`は各要求の前後にsnapshot lockを確認し、最初の要求前に台帳へ開始を記録する。応答のdata-versionを候補由来のsourceと照合し、照合済みrequest/responseのコピーを保存する。完全な1,050件のときだけ既存の監査batch照合に渡す。これは測定処理の部品を接続する段階であり、候補・plan・登録先の再照合から、DB lock・台帳・報告確定までを結ぶ実行経路は未実装。監査関数の呼び出しはmock試験で、実PostgreSQLを通じた1,050件の全体試験ではない。測定CLIは入力確認専用のままで、実候補の運用合格や配信承認は行っていない。

最初の全体テストでは新規observerのテスト用`Mock`が`assert_held`をunittest独自のassert名と誤認し、4 errorsと1 failureが出た。mockに`unsafe=True`を指定して修正し、失敗ログを上書きせず再実行した。さらにtimeout時の停止をテストに追加した。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 120 .venv/bin/python -m unittest discover -s tests -v
# 初回 116 tests、1 failure / 4 errors。テスト用Mockの設定不備。
# data/reports/20261029-benchmark-hooks/unit-tests.log
# SHA-256: 69e4a5ef4be00c0db69d0a2d767162fd8e8ba6d5fa50d7d2af4e1c98dee0d737
# 修正後 116 tests OK (18.766s)。後続のtimeoutテスト追加前。
# data/reports/20261029-benchmark-hooks/unit-tests-fixed.log
# SHA-256: ad901f6b9b037932dc085df268799f88bc33dd85cc4def62df22f2fa05d0a145
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_operational_benchmark.py' -v
# timeoutテスト追加後 10 tests OK (0.071s)。
# data/reports/20261029-benchmark-hooks/benchmark-tests-final.log
# SHA-256: f8013c508d4f39d1a89cf997cf2c82f04b8414d4b6bdd9e4cf58d2c3f5cba4e9
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存の非推奨警告あり。
```

次はplan/registry/candidateを再照合し、試行台帳とPostgreSQL guardのcontextを正しい順序で保持して測定・監査・部分reportを確定する実行経路を実装する。その後、隔離した最新Dockerfile・MLflow・PostgreSQL・APIで異常系を含むsystem testを行い、CLI復帰の条件を確認する。

## 2026-10-29: 試行開始後の候補・データ・登録モデル再照合

`capital_measurement_preflight.py`を追加した。候補単位の試行台帳context内で、台帳に記録されたcandidate ID・plan内容SHA・ファイルSHAを照合する。候補/final report、固定protocol、学習window、CSVとmanifestのSHAを検証し、CSVを読み込む前後と最後にSHAを確認する。planに記録されたCapital MLflow URIをtrackingとregistryに明示し、対象版のtagを確認してからartifactを一時領域へ取得する。取得したartifactと、取得前後の登録版を固定plan・候補証拠と照合する。最後に候補報告とplanをもう一度照合して、後続のDB観測固定に使うframeを返す。完了・配信の許可を出す処理ではなく、予測要求や本番登録の変更は行わない。

試行中の一時台帳と合成候補、mockしたMLflow client/downloadを使い、plan・CSVの変更、読込み中のCSV変更、取得中の登録tag変更、候補report変更、取得artifactの改変が失敗することを検査した。実MLflow HTTP、実候補、DB固定から測定までの一連の処理はこの試験に含まない。既存の隔離SQLite MLflowによる登録・artifact取得試験は全suiteに含まれるが、今回の事前確認部品をHTTP registryと結んだ試験ではない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_measurement_preflight.py' -v
# 先行版 5 tests OK (1.721s)。後に候補report/artifact改変の2 testsを追加。
# data/reports/20261029-measurement-preflight/preflight-tests.log
# SHA-256: a014bb490150464a567f1d4cc9eaa15ef0a46fb1462d59bd58164d7c74c36494
MLFLOW_DISABLE_AGENT_HINT=1 timeout 120 .venv/bin/python -m unittest discover -s tests -v
# 最終版 123 tests OK (19.330s)。追加した事前確認7 testsを含む。
# data/reports/20261029-measurement-preflight/unit-tests.log
# SHA-256: be30d2775f58761968410a37683393e00ba1fa0a6925ddb14bba250c36c62d33
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存のStarlette/httpx、MLflow/SQLAlchemy非推奨警告あり。
```

残る最優先作業は、台帳context内でこの事前確認を呼び、DBロック・API測定・監査照合・partial report/台帳確定を順序どおりに実行する処理と、正常・異常時の実stack試験である。計画とgate文書を更新した。運用測定CLIは引き続き入力確認専用、実候補の1,050要求・合格・配信承認は未実施。

## 2026-10-29: replay期間の継承と新しいPSI参照方式

R07の監視期間を修正した。`monitor`でstart/endを両方省略するとreplay reportの期間を読み、ID・モデル版・CSV hash・profile/targetを検査する。期間を指定するときは両端を必須とし、reportが存在する場合はその範囲外を拒否する。reportがない場合は明示期間を要求する。古いUCI reportはprofileとtargetが記録されていないため、既知のUCIデータSHAとactive profile/targetが一致する場合に限り推定し、その推定をmonitor summaryに記録する。実在する版2・版3のQ4 reportを読み取り、どちらも`2012-10-01`から`2013-01-01`と識別した。古い出力やDB窓を再計算・削除していない。reportの期間は要求範囲であり、`--limit`やエラー時の全時間処理を保証しない。既存のmonitor出力・DB窓は同じIDの再実行で更新されうるため、履歴保護は引き続き未完了であり、既存結果を保護する場合は再実行しない。

別途`monitoring_psi_v2.py`に、参照が定数のとき値が小・等・大の3区分を使うversion付きPSIを追加した。固定bin・件数・自己SHAと欠測、無限大、件数不足の状態を保持する。定数との比較はfloatの完全一致で、許容幅は未設定。最初の合成検査で、件数に平滑化を加えると同じ分布でも標本数が違う場合にわずかなPSIが出ると分かり、割合に平滑化を適用するよう修正した。新方式は既存monitorと未接続で、自己SHAは真正性やcandidateデータとの結び付きの証明ではない。既存UCIの過去値は書き換えていない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_monitoring_window.py' -v
# 6 tests OK (0.066s)。実際のcommand_monitorへの合成入力も含む。
# data/reports/20261029-continuation/monitor-window-tests-final.log
# SHA-256: c09dc748fe2a5cee13e5346e1db56eb6b091e925448ed99cc2892973df4a1854
# 数値日時を拒否する入力検査追加後も6 tests OK (0.050s)。
# data/reports/20261029-continuation/monitor-window-tests-validated.log
# SHA-256: 588393ff0f7fa8d00e39c4ffd2da26c26169e665248c7e0c7d46480646661cee
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_monitoring_psi_v2.py' -v
# 6 tests OK (0.015s)。
# data/reports/20261029-continuation/psi-v2-tests.log
# SHA-256: 74661e7b72599142716d46710c54826e17a1f7dbe6fcc973d2e36c356b6f6c51
```

Capital候補に紐づく参照の作成、v2のmonitor接続、既存監視履歴の上書き防止、ラベル待ちやmissing増加の区別は残っている。計画書とrunbookに進捗と制約を反映した。

## 2026-10-29: 測定内部の通し処理

`capital_measurement_runner.py`を追加し、候補ごとの試行台帳、plan/候補/registryの事前再照合、staging観測表のロック、50暖機+1,000逐次要求、応答出典、全IDと監査行の照合、report保存、ロック解放と台帳の順に実行する内部経路を作った。入力DBはCapital profile・local PostgreSQL 55434・bike_app/bikeopsに限定する。エラー時は後続を止め、可能な部分証拠を新規failure reportへ保存し、失敗ステージ・例外型と`completed=false`を記録する。例外に含まれる接続先や秘密値の文字列はfailure reportへ書かない。API事前確認中のCtrl-CでHTTP clientを閉じる処理も修正した。

成功しても台帳の`completed`は測定処理の完了を意味するだけで、reportのoperational gateは`not_evaluated`、配信許可は`blocked`のまま。台帳は`not_evaluated`の完了reportを保持できるようにした。監査後でもDBロックの解放や台帳最終書込みが失敗すると、採用されないreportが孤立して残りうるため、台帳の状態と参照先を正規の結果とし、孤立reportの検出・整理は残課題。実際のMLflow・PostgreSQL・APIを組み合わせた一連の検証、実候補への計測と独立したgate判定は未実施。CLIは入力確認専用を維持する。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_measurement_runner.py' -v
# 7 tests OK。benchmark本体を用いた1,050要求は合成通信、DB snapshot/監査/候補はmock。
# data/reports/20261029-measurement-runner/runner-tests-report-fix.log
# SHA-256: 2196cc0d427b1176b6e16e0ec579090a4af7a31fe7c82703215add3bffbb20d7
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 153 tests OK (23.071s)。monitorと隔離harnessのunit testsも含む。
# data/reports/20261029-continuation/unit-tests-all.log
# SHA-256: d709e7e34449e7c7e4e8a6d25be1a3a409cfdd70a1ce9b2b497fa381f9c3d6f9
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存Starlette/httpxとMLflow/SQLAlchemyの非推奨警告あり。
```

## 2026-10-29: 監視結果を実行ごとに保全

`monitor`の既定出力を実行UUID付きの新規CSV/JSONとし、DB窓IDにも同じUUIDを付ける。既存ファイル・JSON・完了markerやsymlinkがある場合は上書きを拒否し、旧DB行は更新しない。CSV/JSONを新規保存し、窓をtransactionでINSERTした後、両ファイルのSHA・件数・run IDを持つ完了markerを新規作成する。ファイルとDBは一体のtransactionではないため、途中失敗でmarkerがなければDB commitが成功したか未確定として、run IDと照会prefixを記録する。CSVだけ残った場合にもCSV内のrun IDから追跡できる。これは自動復旧ではなく、状態を調べてから処理する必要がある。

実装前から存在するUCIのCSV/JSON・DB行は読み書きせず、合成SQLiteで旧行保持、2回の独立実行、既存ファイル・symlink拒否、INSERT途中のrollback、commit後marker失敗を検証した。さらに既存のPostgreSQL imageから一時containerを作り、非superuserの隔離DBで旧行保全・2回の保存・2行目の重複によるtransaction全体のrollbackを実証した。テストcontainerは所有確認後に削除した。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_monitor_persistence.py' -v
# 7 tests OK (0.549s)。
# data/reports/20261029-continuation/monitor-persistence-tests.log
# SHA-256: b9dd1e7d63e2834a5823bc3f7ac74f99cf411cfe24ce08d9438bd865945fe0a4
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_monitoring_window.py' -v
# 新保存経路とのCLI統合を含め6 tests OK (0.082s)。
# data/reports/20261029-continuation/monitor-window-persistence.log
# SHA-256: 8d7ada9348b382cd4aef33c1a92e9a8f5e342c783145224749bc15ba6c4f33dc
.venv/bin/python scripts/test_monitor_persistence_postgres.py
# 使い捨てPostgreSQLで成功。既存imageのみ使用し、自動pullはしない。
# data/reports/20261029-monitor-persistence/postgres-integration.log
# SHA-256: 04dc3b290942c1e199f430b351171900bb6723b7c77609fa28df1e6610002a30
```

## 2026-10-29: 隔離system test用の基盤

`scripts/capital_system_harness.py`を追加した。現行Dockerfile・必要source/configだけを一時directoryにコピーして前後のSHAを確認し、ユーザーdata・`.env`・`.git`・`.venv`はコピーしない。試験用Compose project/認証値/labelは毎回ランダムとする。Dockerは明示の`--run-infrastructure`時だけ使用し、ローカルUnix socketと固定port 5001/8110/55433/55434の空きを検査する。片付け前にproject・label・serviceを照合し、所有が不明なものは削除しない。これはDB/MLflow基盤の準備であり、合成候補・API・予測の全体試験は未完成。

実DockerでCompose `config -q`は成功。DB・MLflowだけの隔離起動はimage build段階で約3分進捗を確認できず、当該projectのCompose upプロセスだけを識別して中止した。原因は不明で、`Docker operation compose failed (exit 130)`は中止の結果。親shellの終了コードは記録を取り損ねたため不明。対象projectのcontainer/network/volume/imageと一時workspaceは残っていない。試験後、段階と経過秒を定期表示し、異常時は原因の判明可否を示すよう改修した。最終コードの実Docker再試行はしていない。

```bash
.venv/bin/python scripts/capital_system_harness.py
# workspace_prepared、docker_started=false、prediction_requests=0。最終版の非Docker確認。
# 過去の実行: --run-infrastructure で起動を試み、中止・cleanupを確認。
# data/reports/20261029-system-harness/infrastructure.log
# SHA-256: eac616866973021555926e8977f0ed18219aeeb9a94c04d5d3e9c63e9ea2723c
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_system_harness.py' -v
# 最終版 12 tests OK (0.320s)。
# data/reports/20261029-system-harness/unit-tests-final-v2.log
# SHA-256: da702c6a445dbde16b03c5bb1285fe3094f47cbd20662913eb9807bf9227b44a
```

## 2026-10-29: 運用測定reportの読み取り点検

`scripts/inspect_capital_operational_reports.py --candidate-id <候補ID>`と照合部品を追加した。すでに存在する候補・台帳ロックを取得できる場合に限り、台帳とplanのSHA、attempt ID、reportの配置・SHA・内容を照合する。紐付いたreport、孤立report、欠損・破損、中断、候補に帰属できないファイルを区別し、実行中やロック不在なら点検を拒否する。report・台帳を修復・削除せず、孤立reportを自動採用しない。合成テストでは読取り前後のreport・台帳の不変性、ロック競合、シンボリックリンクや不一致の拒否を確認した。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_operational_recovery.py' -v
# 最終版 11 tests OK。実候補のreportは操作していない。
# data/reports/20261029-operational-recovery/recovery-tests-identity.log
# SHA-256: 043a09e5c79922fef531539cc3b46ae40b8a7d66e7e11512232c971b0fd410e5
```

点検は状態を分類するだけで、timeout後に遅れてcommitされたDB監査の回収、部分証拠の復元、採用判断は未実装。強制終了ではメモリ上の証拠が失われうる。永続journalは測定時間に影響しうるため、測定policyを先に固定したうえで方式を検証する。

### 今回の全変更を含む最終検証

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 175 tests OK (21.899s)。隔離harness・monitor履歴・測定report点検を含む。
# data/reports/20261029-continuation/unit-tests-final.log
# SHA-256: 4ff1b9775ee761b38bfa11d29b9df24dc745815981d7bdd1aa9f6b0de577dbca
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/inspect_capital_operational_reports.py --help
git diff --check
# 成功。既存UCI入口は読み取り確認でversion 3・ready 200。
```

CIでは監視期間・PSIのcommit `63656ca`と内部runnerのcommit `a0e6041`の各workflow成功を確認した。後続commitには処理中またはキャンセルされたworkflowがあり、その時点で全成功とは扱わない。合成試験と実PostgreSQL試験だけでは、実Capital候補の精度やAPI運用目標の達成を意味しない。

## 2026-10-29: Dockerビルドの追跡と測定失敗時の部分証拠

前回中断した隔離buildのDocker Buildx履歴 `khtt5z0ot54cs60f436e1ojuy` を読み取り、ベースimageのmetadata取得と`COPY src`は完了し、`pip install -r requirements.lock`の段階で中止されたことを確認した。履歴のpip出力には多数のwheelダウンロードがあり、確認した範囲でretry・timeout・ERRORの表示はなかった。最後に見えた取得はnumpyのwheelで、複数wheelの表示速度はおよそ192〜521 kB/sだった。従って依存取得が時間を要していたことは確認できるが、他の停滞要因の有無や完了までの時間は判定できない。通常のDocker image一覧に同名base imageがなくても、BuildKit履歴ではmetadataとFROMが完了していたため、image一覧だけから取得失敗とは判断しない。

測定失敗reportでは、benchmarkからreportが返っている場合も、応答の出典を検証済みの要求ID・件数と処理中の要求IDを`partial_evidence`へ保存するよう補強した。処理中の要求は送信済みか不明で、timeout後に遅れてDBへcommitされる可能性もあるため、`outcome_unknown_may_have_been_sent`とする。これらはメモリ上に残った証拠であり、強制終了時の完全な復元は保証しない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_measurement_runner.py' -v
# 8 tests OK (1.143s)。途中停止・監査失敗での部分証拠を含む合成試験。
# data/reports/20261029-retry/runner-partial-tests.log
# SHA-256: 2a54a7f2f37cd6dd976c48aca16e8c86b412ed14b4d5e522253f22c53ceede67
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 176 tests OK (22.697s)。
# data/reports/20261029-retry/unit-tests.log
# SHA-256: 55d1a866cf146a47065d73d38adf4f0fbde822f0e44e8fd2fb79a7e0fb94125e
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存の非推奨警告あり。
```

## 2026-10-29: 保存済み測定行の数値再計算

`capital_measurement_recheck.py`を追加した。呼出側が台帳の正規reportとファイルSHAを確認する前提で、planとreportのattempt/モデル/endpoint/期間を照合し、暖機50と本測定1,000件の順序、各ID、時刻、HTTP結果、数値、出典hashの形式を検査する。保存されたIDのdigestと件数を再計算し、1,000件のlatencyからnearest-rank p95を計算してreportの記載値・250ms条件と照合する。結果は数値の再確認だけであり、運用gateは`not_evaluated`、配信は`blocked`のまま。記録した内容そのものが正しいこと、DB監査、retryの不在、資源状態、reportファイルの真正性はこの関数だけでは保証しない。候補の承認・MLflow tag更新には未接続。

合成の正常、p95超過、ID重複、時刻・件数・モデル・hash・p95改変、未完了を検査した。実benchmarkループで合成通信から作った1,050件のreportも入力し、再計算できることを確認した。これは実モデルや実APIの性能測定ではない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_measurement_recheck.py' -v
# 5 tests OK (0.467s)。
# data/reports/20261029-retry/measurement-recheck-integration.log
# SHA-256: 4d713ceaf2d73948bc877c614140b81e0cbf7599989251eb5b9ac393664f4a75
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 181 tests OK (24.802s)。
# data/reports/20261029-retry/unit-tests-recheck.log
# SHA-256: 7eb040b6e1aca16189c62d83fb8654169c1e1e35981ac6201f629e9e5d471c20
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存の非推奨警告あり。
```

## 2026-10-29: 隔離基盤の再起動試験と台帳からの数値照合

前回の中断後にBuildx履歴を確認し、依存wheelの取得が進んでいたため、最終版harnessを使用しタイムアウト480秒で再試行した。ベースimageと依存を取得し、専用project `bikemlopsteste40f08cefe079fae`でPostgreSQL・MLflowが起動、固定loopback portとMLflow healthを確認し、正常終了・cleanupした。履歴では少なくとも204件のwheel取得とアプリのpip install完了を確認した。試験開始時に一時directoryへコピーされたsourceは`748f2f8`相当で、その後の部分証拠・数値照合の変更は含まない。試験用projectのcontainer・network・volume・tagged imageの残存はなく、既存UCI入口はversion 3・readyを返した。合成candidate、staging API、予測要求は実行しておらず、運用gateは未評価。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 480 .venv/bin/python scripts/capital_system_harness.py --run-infrastructure
# infrastructure_ready、prediction_requests=0、operational_gate=not_evaluated、cleanup_complete。終了0。
# data/reports/20261029-retry/infrastructure.log
# SHA-256: 5b0468e80a7898131a626135941b4af59ab870dccf47990a183ca34446e909ad
```

数値再計算には、候補・台帳の既存ロックを取得し、正規のplan・completed attempt・reportのSHAとIDを読み取りで確認してから再計算する入口`recheck_linked_measurement`とCLIを追加した。ロック中は拒否し、report改変は検出する。処理は台帳やreportを書き換えず、DB監査・retry・資源・実際の入力の独立した検証や運用gate/配信判定ではない。台帳・ファイル自体を変更できる第三者による真正性も証明しない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_measurement_recheck.py' -v
# 6 tests OK (1.397s)。台帳との対応、SHA改変・ロック競合を含む合成試験。
# data/reports/20261029-retry/measurement-linked-recheck-final.log
# SHA-256: 78e071d4128a46559bfc2cf1b8363d2d93f21e80ce0c8f49d7f1f9b2183beb3d
```

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 台帳連携追加後 182 tests OK (23.509s)。
# data/reports/20261029-retry/unit-tests-linked.log
# SHA-256: 000959e644894bc67017526380f6c1173de9dab43b455942e8fb682c63b051c0
.venv/bin/python scripts/recheck_capital_measurement.py --help
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。先行commit 8a8a458 / 42cfd60 の各2 workflowはsuccess。
```

## 2026-10-29: 隔離ハーネス内の合成候補の再現

`capital_system_harness.py --prepare-synthetic`と単独確認用`scripts/test_capital_synthetic_fixture.py`を追加した。必要なsourceと3 scriptsを一時workspaceへコピーし、コピー前後のSHAを照合する。子プロセスは限定した環境変数・一時workspace内の`PYTHONPATH`を使い、project root・profile・一時directory名とmarkerが一致しない場合は生成前に停止する。外部データや既存評価台帳、registryは使用しない。正常終了と通常の中断時には一時workspaceを削除する。SIGKILLや電源断はcleanupを保証しない。

合成の1月の貸出21,612件を生成し、集計・protocol凍結・候補学習・validation・独立したfinal評価を実施した。今回の合成候補IDは`ec2dd283216fa183cd5fa223434d135074b760159758eda38515ea5f6d78f54a`。固定された精度条件でvalidation/finalは合格したが、実データの精度を示すものではない。運用測定用の168時間 `[2024-01-07 22:00, 2024-01-14 22:00)` は最後のラベル到着が学習cutoff `2024-01-15 00:00` と一致する。旧合成試験の締切違反2時間を使用していない。registry登録・API・予測要求は行わず、配信はblockedである。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 240 .venv/bin/python scripts/capital_system_harness.py --prepare-synthetic
# synthetic passed、candidate_generation=prepared、prediction_requests=0、docker_started=false。
# 成功後、一時workspaceを削除。生成物は永続保存していない。
# data/reports/20261029-retry/harness-synthetic-final.log
# SHA-256: 55359ad93b03216561d51d18fa9e1ad18ec939f54b6d379b6d37fbae985cb17f
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 185 tests OK (22.457s)。実projectからの直接生成拒否、中断時の一時領域cleanup、optionの順序も検証。
# data/reports/20261029-retry/unit-tests-synthetic.log
# SHA-256: bc85a0688565cbb802e56755869469c2c2ff717e8bdea0378b19bee837a42110
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。既存の非推奨警告あり。
```

合成候補を生成したworkspaceと隔離MLflow/DBを同じ実行で維持してregistry登録・seed・API・監査まで渡す経路は未完成。`--prepare-synthetic`と`--run-infrastructure`を同時指定した順序はmockで検査したが、双方を同時に実Dockerで実行していない。次は候補をblocked登録し、seedからstaging API・監査・結果照合へつなぐ。未使用の実評価窓はまだ消費しない。

## 2026-10-29: 結果不明の要求の監査照会

`capital_pending_audit.py`と`scripts/inspect_capital_pending_audit.py`を追加した。候補と台帳の既存lockを取得し、失敗attempt・plan/reportのハッシュ・処理中request ID・固定負荷条件を確認したうえで、隔離staging PostgreSQLの該当IDだけを`READ ONLY` transactionで照会する。見つからない場合は照会時点で未確認とし、その後のcommitを否定しない。見つかった場合も候補・版・artifact・profileなどの識別照合に限定し、入力や予測値の完全な監査とはしない。再送・台帳/report更新・合格や配信判断は行わない。CLIは`DATA_PROFILE_ID=capital-bikeshare`と環境変数`APP_DB_PASSWORD`を使い、値は出力しない。

使い捨てPostgreSQLで、照会後に別transactionから合成監査行をINSERTし、その前後で未確認から識別一致へ変わることを確認した。動的な一時portを使うこの試験では本番用の固定port検査のみmockし、その検査自体はunit testで別portを拒否することを確認した。実API timeoutやネットワーク中断は再現していない。テストcontainerは所有確認して削除した。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 90 .venv/bin/python -m unittest discover -s tests -p 'test_capital_pending_audit.py' -v
# 4 tests OK。後に発見済みの行のlater_commit_possibleをfalseにする補強を追加。
# data/reports/20261029-retry/pending-audit-tests.log
# SHA-256: f5434bdc60f1cc005e1159de822030cf1a32a5411dcc584b18c2ac054741cb23
MLFLOW_DISABLE_AGENT_HINT=1 timeout 120 .venv/bin/python scripts/test_monitor_persistence_postgres.py
# monitor履歴とpending要求の前後照会が成功。既存PostgreSQL imageを使い自動pullせず、containerを削除。
# data/reports/20261029-retry/pending-postgres.log
# SHA-256: 8d5f8fdc9d453fbb706b8e1fd526a6bb35c5d8ebf058033591b7e2dfe4e5ddc9
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 補強後 190 tests OK (29.632s)。
# data/reports/20261029-retry/unit-tests-pending.log
# SHA-256: 5544b3ac200e1a9cfda13c5fdb9ca204820a1e2f4d46f6212febab32601317d4
.venv/bin/python scripts/inspect_capital_pending_audit.py --help
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

## 2026-10-29: 合成候補と最新Dockerfileの隔離基盤を同時に確認

Dockerfileの依存installをsource copyより前のlayerに分離した。依存fileが変わらずsourceだけを変更した場合、Dockerが依存layerを再利用できるようにする目的であり、実際の再build時間短縮は未測定。隔離harnessは所有labelを確認したMLflow container内で、UCI・Capitalそれぞれについてprofile ID・target contract・model nameを確認するようにした。

`--prepare-synthetic --run-infrastructure`で、合成候補のvalidation/final、168時間の学習cutoff条件を確認した後、同一の一時workspaceからDockerfileをbuildし、隔離DB・MLflowを起動した。DB 2台はhealth表示、MLflowのhealthと両profileの設定確認が通り、`infrastructure_ready`、`prediction_requests=0`、`operational_gate=not_evaluated`と`cleanup_complete`を確認した。起動途中の手動health確認では一度connection resetを受けたが、その後harnessのhealth確認は成功した。対象projectのcontainer/network/volume/tagged imageが残っていないことと、既存UCI入口のversion 3・readyを確認した。CLIの最終終了コードは出力取得時に記録し損ねたため、ログ中の正常・cleanup表示と区別して扱う。

試験用sourceは開始時点のcommit `5f3ba9a`と未コミットのDockerfile/profile確認変更をコピーしたもの。並行して後から追加したpending監査照会commit `3035ab1`はimageに含まない。合成candidateのregistry登録・seed・API・予測は実行しておらず、同一stackでの全体試験は未完了。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 600 .venv/bin/python scripts/capital_system_harness.py --prepare-synthetic --run-infrastructure
# synthetic passed、candidate_generation=prepared、infrastructure_ready、cleanup_complete。
# data/reports/20261029-retry/harness-combined.log
# SHA-256: 4b7fa5d9252ab8367abba2cab0fd0f8eba7577f0f0ee8086b08267cac5d6484f
MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 .venv/bin/python -m unittest discover -s tests -v
# 190 tests OK (29.632s)。pending監査・profile検証mockを含む。
# data/reports/20261029-retry/unit-tests-pending.log
# SHA-256: 5544b3ac200e1a9cfda13c5fdb9ca204820a1e2f4d46f6212febab32601317d4
.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

## 2026-10-29: 最新sourceでのcache付き隔離再試験

commit `9b169e6`のsourceを新規一時workspaceへコピーし、合成候補の作成と隔離DB・MLflowの起動、Docker image内のUCI/Capital profile、MLflow health、所有確認付きcleanupを同じ試験で再実行した。合成validation/finalと168時間のcutoff条件は合格、API起動・予測・registry登録は行っていない。Buildx履歴 `wb6a1s0htkvx07iqcawmo1a0d` は`pip install -r requirements.lock`のlayerがcachedと示し、source変更後の依存再利用を確認した。これは当該環境・当該buildの結果であり、一般的な速度の保証ではない。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 240 .venv/bin/python scripts/capital_system_harness.py --prepare-synthetic --run-infrastructure
# synthetic passed、infrastructure_ready、cleanup_complete、harness_exit_code=0。
# data/reports/20261029-retry/harness-cached.log
# SHA-256: 017bb40055089e346eb0e6dcc2add35c77f80c11fac9662b8c0c3157ed622fd7
```

対象project `bikemlopstest8060464b64f968c4` のcontainer/network/volume/tagged imageは残っておらず、既存UCI入口はversion 3・readyを返した。合成候補を同じstackのregistryへ登録し、seed・API・1,050要求・監査・gateまでつなぐ試験は引き続き未実施。

## 2026-10-29: synthetic system testを完了し計測CLIを復帰

### 実施内容

- `capital_system_harness.py --run-synthetic-system`を拡張し、人工データから候補作成・登録・再実行・staging DB seed・API起動・offline/API照合・request retry/409・監査・通常runtime拒否・固定運用測定・再計算・所有確認付きcleanupまでを一時Compose project内で連結した。
- 固定168時間の人工replayから作成した候補をblocked状態で登録し、MLflow model version `1`のidentityをAPI応答と監査記録で照合する。実データ、既存評価台帳、既存registry、既存UCI/Capitalサービスは使わない。
- 前回まで未接続だったDB出典固定・1,050要求測定・監査を同一runner内で確認できたため、`benchmark_capital_runtime.py`に明示選択の`--run-measurement --plan ...`を接続した。`--check-input-only`はread-onlyの入力確認として残し、どちらも指定しない呼び出しはargparseで拒否する。実測はrunnerのlocal Capital staging PostgreSQL・candidate evidence・frozen plan・API data provenance・監査条件をすべて通らなければ開始できない。完了だけでは運用gateをpassedにせず、deployment permissionもblockedのまま。
- PostgreSQLの`predictions.predicted`が`REAL`型だったため保存時に単精度へ丸められ、API値`15.189926`とoffline値`15.189926280821982`が`2.8082198255674484e-7`ずれていた。許容誤差は広げず、新規schemaを`DOUBLE PRECISION`へ変更し、既存PostgreSQL列も初期化時に`REAL`から`DOUBLE PRECISION`へ移す処理を追加した。既存の単精度行は過去の丸め値から復元できないが、今後の保存は倍精度となる。
- 計画作成時のDocker資源調査が、一時workspaceに既定`.env`がなくCompose必須変数を解決できず`unavailable`だった。秘密値を子環境へ追加せず、mode `0600`の使い捨て`harness.env` pathを資源調査に渡す。存在・権限も検査し、不安全なenv fileならDocker呼び出し前に拒否する。

### デバッグの記録

最初の通し試験は順番に異なる問題で止まった。失敗した仮説と修正は次のとおり。

1. Registry登録CLIは成功していたが、stdoutの先頭にMLflowの案内ログがあり、出力全体がJSONだと仮定したparserが失敗した。ログprefix後のJSON objectを抽出する処理と、秘密値を伏せた子プロセス診断を追加した。
2. offline helperの`predict_non_negative`が配列を返すと誤認して`[0]`を参照していた。実際はscalar floatなのでそのまま変換する。
3. すべてのcandidate/version/artifact identityは一致したが、API/offline予測の微差が発生した。PostgreSQL `REAL`の精度不足を特定し、schema変更後は差分0.0を確認した。
4. Docker stack自体は起動しAPIもreadyだったが、plan資源調査のみ`unavailable`だった。安全診断でCompose env fileの未指定を特定した。修正後はDocker engine版、対象container/resource統計をplan・reportに保存できた。

いずれの失敗も測定結果を成功と扱わず、専用Compose projectのcontainer/network/volume/imageをcleanupした。最終成功後も同じ対象を照会し、使い捨て資源が残っていないことを確認した。既存スタックは停止・変更していない。

### 最終の隔離system test

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 600 \
  .venv/bin/python scripts/capital_system_harness.py --run-synthetic-system
```

- 結果ログ: `data/reports/20261029-system/synthetic-system-cli.log`
- ログSHA-256: `f26a6d1a1c5c6894c6cbec73d01c08254583dd6473fb9c2c51d829c1b810e879`
- 人工候補ID: `aa7bb846a03b3c07df75a912db8cb45b63b025e6039a396b1993034d4cef2d1c`
- 試行ID: `f0506ced-14b2-4743-9c0e-331ec7528595`
- staging DBへ744観測をseed・全件照合。Model version `1`はblocked registrationとして2回の登録で版・artifact SHAが不変。
- `/ready`成功、候補artifactをload。offline/API差分`0.0`。同一要求のretryは同じ応答、同じIDで入力変更はHTTP 409。
- `runtime init`は通常と`--diagnostic`の双方で拒否。runtime生成物は残らない。
- warmup 50件と計測1,000件を逐次送信、エラー0、監査1050行。独立再計算が一致。合成試験のp95は`103.25777599791763 ms`で数値条件内だった。
- ただしreportの`operational_gate=not_evaluated`、deployment permissionは`blocked`。この値は人工データとこのPCの隔離環境だけの測定で、Capital実候補・業務負荷・本番SLOの合格根拠ではない。
- `cleanup_complete`を確認し、該当projectのcontainer/network/volume/labeled imageはすべて0件。

直前のrunner直接呼び出し版でも同じsystem testが成功した（p95 `96.83226500055753 ms`）。その後CLIの接続を含む現在版を改めて実行し、同じ結果を再確認した。

### 検証

```bash
PYTHONPATH=tests .venv/bin/python -m unittest \
  test_capital_benchmark_inputs test_operational_benchmark \
  test_capital_measurement_runner test_capital_system_harness -v
# 48 tests OK

MLFLOW_DISABLE_AGENT_HINT=1 timeout 180 \
  .venv/bin/python -m unittest discover -s tests -v
# 199 tests OK (22.847s)
# data/reports/20261029-system/unit-tests-cli-final.log
# SHA-256: 69d7dba4c817379d000d2eba9564fd4a8b2a0039b5a10bcf507c05dd2f6c1dcd

.venv/bin/python -m compileall -q src scripts tests
git diff --check
# 成功。
```

既存のStarlette/httpx deprecation warningは残る。次は、測定report・hash・数値statusをMLflowのexact model versionへ記録する経路、未使用実データの取得・protocol固定と一度限りのvalidation/final評価、監視・障害復旧・採否証拠を計画順に進める。実候補がないため本番データの計測や承認はまだ行わない。

## 2026-10-29: operational evidence tagsと最終確認

### exact MLflow versionへの証拠記録

- `src/bike_mlops/capital_operational_registry.py`と`scripts/record_capital_operational_evidence.py`を追加した。完了済みattemptの台帳/plan/report SHA、1050行の保存recordを使う独立p95再計算、DB audit、snapshot/input provenance、計測前後のhost/container資源、Capital registryの正確なmodel version/artifact/gate tagsを照合してから、証拠の参照tagを追加する。
- recordはcandidate lock・ledger lockとversion別のprivate lockを取得する。同じtag値の再実行は冪等に成功し、異なるreport/attempt値は上書きしない。未知の`capital_operational_*` tagも拒否する。
- このCLIは`operational_gate_status`や`deployment_permission`を書かず、書込前後に`not_evaluated` / `blocked`を検査する。tag記録は独立review・運用gate合格・手動承認ではない。
- system harnessへ初回記録、同じreportの再実行、異なるreport hashの拒否、拒否後のblocked確認を追加した。conflict検証では使い捨てversionのtagを一時変更し、拒否確認後に戻してから冪等再実行した。操作は全て破棄するsynthetic registry内だけで行った。

### 初回失敗とデバッグ

- 初回system testは全体600秒の上限に達し、計測開始後にTERMで中断した。ハーネスは`cleanup_complete`を出力。後続の読み取り確認では対象projectのcontainer/network/volume/imageは0件で、既存stackも変更されていなかった。起動・工程が遅かったため、子scriptの開始/完了時間を進行ログへ追加し、上限1200秒で再実行した。
- 最終system testはcandidate生成後にblocked登録、seed、API、plan、1,050要求、tag記録へ進み完了。measurement処理自体は約92秒。初回timeoutは失敗として扱い、成功値には使っていない。

### 開発テスト依存と実行sandbox

- 全suiteを通常sandbox内で始めたところ、最初のStarlette TestClient testで停止した。最小FastAPI appでも再現。faulthandlerでAnyIO blocking portalのevent loopがselector待ちのまま、別threadの`portal.call()`が待機している状態を確認した。
- AnyIO portalの最小checkはsandbox内でtimeoutし、sandbox外では`portal.call`と`start_task_soon`の双方が完了した。アプリ・Docker・DBの失敗ではなく、このコマンドsandbox内のthread wakeup制約と切り分けた。sandbox外での再試験もローカル・人工dataだけに限定した。
- Starlette 1.9.0の公式推奨に従い`httpx2>=2,<3`をdev extraへ追加。`requirements.lock`には現環境で解決した`httpx2==2.16.0`、`httpcore2==2.16.0`、`truststore==0.10.5`を追加した。通常APIのHTTP client (`httpx==0.28.1`)は変更していない。
- lockを全再解決する試みはsandboxのDNS制限でPyPIへ接続できず失敗。既存の依存versionは変えず、導入された3 packageだけを追加した。`pip check`は通過し、Starlette/httpx warningは消えた。

### 最終隔離system test

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 1200 \
  .venv/bin/python scripts/capital_system_harness.py --run-synthetic-system 2>&1 \
  | tee data/reports/20261029-system/synthetic-system-evidence-tags-final.log
```

- log SHA-256: `2587dbcd320f255cd159416545a31540178c9e1c79e91a5768948478bfbf343e`
- candidate: `58a678230ded1a5eb4220cd7362f3b51a45c7faa11418718b077eacfee6a8ae4`; synthetic MLflow version `1`; attempt `2a9a623b-dbbc-4c44-896b-43ce081b6f61`.
- 744 synthetic observations seeded/verified; API ready; offline/API delta `0.0`; same request retry passed; changed input returned HTTP 409; normal/diagnostic runtime init both rejected the blocked candidate.
- warmup 50 + measured 1,000 requests completed; 1,050 rows matched DB audit; independent p95 recheck matched. Artificial p95: `88.94697499636095 ms`.
- exact-version tags recorded; identical evidence was idempotent; different report hash was refused. Gate remained `not_evaluated`, deployment remained `blocked`.
- `cleanup_complete` printed. Read-only Docker filters for the unique project showed no remaining container/network/volume/labeled image. This synthetic PC result is not real-data quality or production-SLO evidence.

### 全テスト・静的確認

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout 240 \
  .venv/bin/python -m unittest discover -s tests -v 2>&1 \
  | tee data/reports/20261029-system/unit-tests-evidence-tags-final.log
.venv/bin/python -m compileall -q src scripts tests
git diff --check
.venv/bin/python -m pip check
```

- 206 tests passed in `22.759s`; log SHA-256: `7ad4e91316c5f576783b9adc051218ed84a39c674ee2e9ba482902c88a9539dd`.
- `compileall`, `git diff --check`, and `pip check` succeeded. Starlette/httpx warning is resolved; one MLflow/SQLAlchemy `noload` deprecation warning remains in the isolated MLflow test.

正規の優先順位は[`261014/plan_mlops.md` §11.5](../261014/plan_mlops.md)へ更新した。次は記録済み証拠の独立review結果をoperational gateへ反映する経路と、通常loader/runtime/promoteがreport evidence/approvalを確認する接続である。その後、未使用原本とprotocolを固定し、初回実candidate validation/final評価へ進む。Docker統合CI、timeout後遅延commit・強制終了/failure injection、監視、隔離rollback/recovery、最終採否も残っている。実candidateの運用計測・承認は今回実施していない。

## 2026-10-29: 合成Docker system testをGitHub Actionsへ追加

- 既存のunit/compile jobに影響を与えない独立job `synthetic-system` を `.github/workflows/bike-mlops-contracts.yml` に追加した。jobは35分で停止し、runner内では`capital_system_harness.py --run-synthetic-system`を最大20分で実行する。workflowのGITHUB_TOKEN権限は`contents: read`に絞る。合成system testログは常にartifactとしてuploadし、14日保存する。credentialやreal-data secretは渡さない。
- 既存の同じ隔離system testと206件のunit suiteはローカルで成功済み。ただし新workflowのGitHub Actions初回runはpush後まで未確認。従ってCI job追加をCI導入完了とは扱わない。
- YAMLを `261021/.venv/bin/python` のPyYAMLでparseし、`tests`/`synthetic-system`のjob名とstep数を確認した。`actionlint`はこの環境にインストールされていないため、GitHub Actions固有schema lintは未実施。
- 続きはpush後にrun resultとupload artifactを確認する。Dockerでの強制終了、実API timeout後の遅延commit、audit保存失敗などのfailure-injection matrixは別タスクであり、本変更では実施していない。

### 使い捨てPostgreSQL failure-injection確認

```bash
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 300 \
  .venv/bin/python scripts/test_capital_snapshot_postgres.py 2>&1 \
  | tee data/reports/20261029-system/snapshot-postgres-final.log
```

- 結果`passed`; log SHA-256: `88a52392a9ee19dc2bfba9a6656152563a29a68c465dfa5b09530e381bdb5da9`.
- 使い捨て・非superuser DBで、観測writerの阻止、API予測と監査のlock内保存、由来SHA照合、監査行の不足/余剰/重複/改変拒否、観測変化拒否、lock取得timeout、lock接続喪失拒否と解放を確認した。
- scriptは専用labelのcontainerを削除。実行後の`docker ps -a --filter label=bike.snapshot.test`は空で、既存Composeサービスは変更していない。
- GitHub ActionsのDocker jobにもこの試験を追加し、system harnessと同じ14日保持artifactへログをまとめるよう更新した。今回取得中のCI runは直前commitのworkflow定義であり、新しいstepのCI結果は次のpush run待ち。

## 2026-10-29: GitHub system jobのcleanup失敗を検出

- GitHub Actions run `40874109839`のjob/API表示はsuccessだったが、downloadした`bike-mlops-synthetic-system-log`を確認すると、合成measurementとnumeric recheck後に`docker image rm`がexit 1となり、`cleanup_complete`が出ずTracebackで終わっていた。従ってこのrunはsystem testの完全成功として採用しない。artifactのmeasurementは人工データの計測成功を示すが、cleanup失敗を含む。
- runner job logはshellを`/usr/bin/bash -e {0}`としており`pipefail`が無かった。`timeout ... python ... | tee ...`では先頭processの失敗がpipeline statusから隠れるため、workflow両pipelineに`set -o pipefail`を追加した。
- Compose buildで複数serviceが同じimage IDを共有した場合、`docker image ls -q`に同IDが複数現れ、同じIDを繰り返し`image rm`へ渡す可能性がある。このケースをcleanup前にdeduplicateするpure helperとunit testを追加した。runnerはすでに破棄され、当時のimage rm詳細stderrも安全な診断方針により出力されていないため、これが実障害の原因だったとは断定しない。
- 更新workflowで両system testとcleanupの終了コードが検出されることをpush後に再確認する。failureはsuccessとして記録せず、Docker daemonのimage removal詳細causeが分からない場合はログartifactで特定する。

### 修正後のローカル再実行

```bash
set -o pipefail
MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 1200 \
  .venv/bin/python scripts/capital_system_harness.py --run-synthetic-system 2>&1 \
  | tee data/reports/20261029-system/synthetic-system-cleanup-dedup.log

MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 240 \
  .venv/bin/python -m unittest discover -s tests -v 2>&1 \
  | tee data/reports/20261029-system/unit-tests-cleanup-pipefail.log
```

- Full suiteは207 tests passed in `23.082s`。log SHA-256: `f455e60de2c2ffb533679d44d9ce4746702e7f79b1f47bfed4d1b6c4c82ca017`。その後に追加したimage rm診断のsecret redactionを含むharness専用18 testsもpass。
- `compileall`, `pip check`, `git diff --check`は成功。MLflow/SQLAlchemy `noload` deprecation warningは残る。
- Docker system testはsynthetic candidate `4e7f1fa488a8a190788ee627e7b44032a13478386aeb4db65289b068ba9aa6a8`、model version `1`、attempt `8b905f9f-9c46-4500-abf7-69b78f30f55b`で、744観測・API/offline delta `0.0`・50 warmup+1,000 requests・audit 1,050行・独立recheck一致・exact version evidence tag/idempotency/conflict拒否までpass。synthetic p95は`86.32213300006697 ms`。正式gateは`not_evaluated`、deploymentは`blocked`を維持。
- harnessは`cleanup_complete`を出力し、専用projectの残存container/network/volume照会は空。system log SHA-256: `1abb326b41dc0a4d66f43a7fdcc3e625b3480b9181748f0dcb62591216b97639`。これは人工dataとこの計算機の結果で、Capital実candidateや本番SLOの合格根拠ではない。
- GitHub run `40874601329`もunit jobとsnapshot PostgreSQL testはsuccessだったが、downloadしたartifactでimage cleanup失敗を再確認した。これは修正前workflow/sourceのrunなので成功扱いしない。dedup/secret-redacted image diagnostic/pipefailを含む修正後GitHub runは次のpush後に確認する。

## 2026-10-29: cleanupの原因確定とtag単位の削除

修正後run `40876827423`はunit jobがsuccess、synthetic-system jobがfailureとなった。`pipefail`が有効になり、previous runsで見えなかったcleanup失敗を検出できた。job logでは、synthetic測定・numeric recheck・registry evidence tagの検査後にDockerが次のエラーを返していた。

```text
conflict: unable to delete ... (must be forced) - image is referenced in multiple repositories
```

このrunのsynthetic測定は1,050要求・audit 1,050行・p95 `33.777445 ms`で、evidence tagsと数値再確認も成功したが、image cleanup失敗でjobを完全成功とは扱わない。確定した原因は「同じIDが一覧に重複した」だけではなく、Docker image IDが複数のrepository/tag参照を持ち、ID指定の削除自体が拒否されたこと。先に追加したIDの重複除去では根治していなかった。

ハーネスを次のように修正した。

- `docker image ls --format '{{json .}}'`からrepository/tagを読み、ownership labelに加えrandom Compose project prefixの一致を確認してから正確な参照名だけを削除する。foreign tagは拒否し、`--force`やimage IDの一括削除は使わない。
- 削除前にunique image IDのlabelをinspectし、削除後に専用projectのcontainer/network/volume/imageを再確認する。残ればcleanup失敗にする。
- `check_ports_free`で本当に使用中の`EADDRINUSE`と、sandboxなどのbind権限不足`EPERM`を別の診断にする。sandboxでの初回試行はEPERMだったのに「port unavailable」と誤表示したため修正した。VS Code TestClient suiteもrestricted socket環境だと起動待ちで止まった。権限付きの再実行ではsuiteが完走し、sandbox timeoutを実装不具合の失敗と混同しないよう記録した。
- GitHub Actionsのsnapshot PostgreSQL試験は、依存install/checkが成功した後なら先行system testがfailureでも実行し、独立した結果をartifactに残す。YAML parseとstep conditionを確認した。`actionlint`は未導入なのでGitHub固有schema lintはしていない。

現行コードの検証結果:

```bash
.venv/bin/python -m unittest tests.test_capital_system_harness -v
```

- harness専用23 tests passed。全suite log: `data/reports/20261029-system/unit-tests-project-scoped-cleanup.log`。

```bash
set -e -o pipefail
.venv/bin/python -u -m unittest discover -s tests -v 2>&1 \
  | tee data/reports/20261029-system/unit-tests-project-scoped-cleanup.log
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/python -m pip check
git diff --check
```

- 213 tests passed in `23.541s`; log SHA-256 `7f82233f29b179750ed5d3d9d2621b400f52954fd3e60ced04a1b3976cc39f8b`。`compileall`、`pip check`、`git diff --check`も成功。23 testにはproject外のreference拒否とcleanup後残存resource拒否を含む。
- tag-scoped cleanupを使う版での隔離system testは`data/reports/20261029-system/synthetic-system-owned-tag-cleanup.log`。synthetic candidate `37542902cd5784b48374e969af1d90eb1f67faf94e554baad32fa41cfe609313`、version 1、744観測、API/offline absolute delta `0.0`、1,050要求と1,050 audit行、numeric recheck matched、exact version evidence tags/idempotency/conflict拒否まで成功。p95 `127.52083899977151 ms`。log SHA-256 `d9e1c645663fce250f786671492baaf9bc1e198710da50d0896f64cb3d375485`。これは、次に記すproject-prefix guard追加前の人工fixture検証である。
- project-prefix guardを含むexact codeでは、`.venv/bin/python scripts/capital_system_harness.py --run-infrastructure`が`infrastructure_ready`・`cleanup_complete`まで成功。log `data/reports/20261029-system/infrastructure-owned-tag-cleanup.log`、SHA-256 `0c55e55eeac9e7adb5705032822bd008024520b6b83721142763c146892f4c82`。full synthetic system runはport 55433の`EADDRINUSE`をpreflightで検出し、compose起動前に終了した。log `data/reports/20261029-system/synthetic-system-project-scoped-cleanup.log`、SHA-256 `6ed6d860be1dbe64800f648419eb49d8dc04d76798e46753e27f87c472498ffe`。事後に`docker ps -a`・`ss`・`fuser`・`lsof`で同projectまたはLinux listenerを特定できなかった。既存サービス停止やDocker変更はしていない。
- prior synthetic runのmeasurement statusはpassでも正式operational gateは`not_evaluated`、deploymentは`blocked`。人工fixtureの結果で、実Capital dataや本番性能を示さない。新しいproject-prefix guardを含むfull synthetic pathは、port利用可能なGitHub Actions runnerで確認する。

次は修正をレビュー可能な単位としてcommit/pushし、新CIでcleanup failureが解消し、PostgreSQL試験が依存導入後に独立実行されることを確認する。これが通った後も、意図的な中断/API遅延commit/audit保存失敗のfailure injection、独立review gate、未使用data protocolの固定・実候補評価、R07〜R10は残る。

## 2026-10-29: project-scoped cleanup修正のGitHub CI確認

commit `6079903` (`Scope synthetic Docker cleanup to project tags`) のpushで起動したGitHub Actions run [`40879692956`](https://github.com/kenobix/Claude_practice/actions/runs/40879692956)を確認した。前run `40876827423`で確定したDocker imageの複数repository参照によるcleanup失敗に対し、所有labelとrandom Compose project prefixの両方を確認してexact tagを消す変更が、CIの全system経路で成功した。

### CI結果

- `tests` job: **success**。213 tests passed（8.818秒）。
- `synthetic-system` job: **success**。synthetic system test、project-scoped cleanup、PostgreSQL snapshot-lock test、ログartifact uploadがすべて成功。
- synthetic candidate ID: `bcd2842892b6423125a014003d8e79b3b6dd19120a2f4428919e035595ceeb6c`。人工データ744時間をseedし、blocked model version 1の再登録、API ready、offline/API予測差0、同一request retry、変更入力の409を確認。
- 逐次measurement: warmup後1,000 request、合計1,050 request/audit行、エラー0、p95 `26.522195000012516 ms`。独立した数値再計算は一致し、exact model version evidence tagsの同一値再実行は成功、競合する証拠の上書きは拒否された。
- 正式operational gateは`not_evaluated`、deployment permissionは`blocked`を維持。上記はsynthetic fixtureとGitHub runnerの結果であり、実Capital candidate・実需要・本番SLOを示すものではない。
- cleanupは`cleanup_complete`（Compose project `bikemlopstestd5ad46d1fc0ee354`）。強制削除を使わず、所有resourceを除去した。PostgreSQLの使い捨てtest containerも停止・削除された。
- PostgreSQL snapshot-lock test: `status=passed`。非superuserの一時DBで観測更新の阻止、lock中のAPI予測/audit成功、候補とsource hash一致、missing/extra rows・duplicate IDs・changed audit/data・wrong hashの拒否、lock acquisition timeout、lock接続喪失の拒否、context終了後のlock解放を確認。
- 14日保持artifact `bike-mlops-docker-integration-logs`、artifact ID `12898819750`、size 1,840 bytes、digest `sha256:4e53e8e84e17c4d12e9d86ea7bf1f65251728468d65ea7c41908202223c6dbd2`。

### 結果の確認方法

GitHub Actions画面: [run 40879692956](https://github.com/kenobix/Claude_practice/actions/runs/40879692956)。workflowのartifact一覧から`bike-mlops-docker-integration-logs`を取得できる。job logではsynthetic system testの`synthetic_system_passed`・`cleanup_complete`と、snapshot testの`status: passed`を確認した。artifactは2026-11-12まで保持予定。

このCI成功により、前回の計画にあった「project-prefix guardを含むfull synthetic pathはport利用可能なGitHub runnerで確認する」は完了とする。exact codeのlocal full runがport `55433`の`EADDRINUSE`でpreflight停止した事実は失敗のまま記録し、CIの成功でローカル実行まで成功したとは言い換えない。実データのprotocol凍結・candidate/final評価、独立review gate、異常時の中断/遅延commit/audit failure、R07〜R10は未完了である。

### 更新対象

- `261014/plan_mlops.md` §11.5: cleanup/CI taskを「CI合成system/DB検証完了」とし、run、candidate、p95、artifactを追記。
- `261021/docs/capital-bootstrap-gate.md`: GitHub CI evidenceと使い捨てPostgreSQLの確認範囲を更新。実candidate評価・gate・配信はblockedのまま。

## 2026-10-29: Capital operational reviewと別人承認のfail-closed経路

### 実装

- `src/bike_mlops/capital_deployment_decisions.py`を追加。registry versionがREADYで、Capital profile/target、accuracy gate/policy、operational report/plan/request hash、監査・source provenance・numeric recheck・resource contextを照合する。
- review/approvalのtag更新は既存operational evidence writerと同じmodel-version lockを使い、協調する操作同士を直列化する。
- 独立reviewの`passed`は、登録済み測定statusがpassedかつp95が250ms以下の場合に限る。review理由と正確なversion/candidate/registered artifact/reportの組から決定SHAを作りMLflow tagsへ記録する。review後も配信permissionはblockedのまま。`rejected`はoperational gateをfailedにしblockedを維持する。
- 2段階目の`approve`はreviewがpassedで、別人名のapproverと理由があり、review hashと同じmodel version/candidate/artifact/reportへ結び付いている場合だけ許可する。registryでは承認tag群を先に記録し、`deployment_permission=eligible`を最後に設定する。途中で書き込みが途切れた場合はeligibleに到達しない。既存の同一decisionは再実行可能、内容が違うtagは上書き拒否。
- `require_deployment_eligible`を強化し、Capital profileはmodel name/versionを必須にした。通常API model loader、`runtime init`、`deploy promote`が決定SHA、reviewer/approverの分離、運用証拠、p95上限、version/artifact/report bindingを検証する。UCI profileの既存条件は変えていない。
- `scripts/record_capital_deployment_decision.py review|approve`を追加。実候補は存在しないため、MLflow上の実tag更新・実review・実promoteは行っていない。
- 限界: reviewer/approverの名前はこのローカル学習用ツール内の自己申告であり、OS/MLflowの認証identityを検証しない。hashは内容の整合性を検出する仕組みで、権限管理・電子署名・組織の独立監査ではない。reportファイル実体のAPI起動時再取得と、MLflowへのreport artifact upload、partial tag書込からの実MLflow復旧試験は今後の残課題。

### 試験と検証

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*deployment*' -v
# 10 tests OK

MLFLOW_DISABLE_AGENT_HINT=1 timeout --signal=TERM 240 \
  .venv/bin/python -u -m unittest discover -s tests -v \
  2>&1 | tee data/reports/20261029-decisions/unit-tests-review-approval.log
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
git diff --check
```

- 全suite: 220 tests passed in 23.466秒。log SHA-256 `cc910fc1961a068ef479e273f025673d708e9c2431b607c2dc5723a2667fb074`。compileall、pip check、diff checkも成功。
- 合成/mock確認: review→blocked維持→別人approval→eligible、同じdecisionの冪等再実行、review decision競合拒否、同一actor拒否、measurement failed/p95 >250拒否、audit欠落・decision理由改変・別model version拒否、UCI gate後方互換。
- 初回の通常sandbox実行ではTestClient最初のtestの時点で出力を得られず停止した。以前調査したloopback/socket制限と同じ実行環境問題と判断し、socket利用を許可した実行条件で再実行したところ全suiteが完了した。最初の試行は成功扱いしていない。
- CLI `--help`でreview/approve引数とsubcommandを確認した。MLflow実接続・Docker test・実候補はこの作業では未実施。

### 計画の更新

- `261014/plan_mlops.md` §11.5順番2を「review/approval経路は合成実装済み、実候補・実MLflowは未検証」に更新。
- `docs/capital-bootstrap-gate.md`へ2段階コマンドと自己申告identityの制限を追記。現時点で実candidateがないため、approval/eligible/promoteは実行していない。
