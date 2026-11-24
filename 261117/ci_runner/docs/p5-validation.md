# P5の比較実験と再現手順

## 1 Workerと2 Workerの比較

専用のLinux開発環境で実行する。Python 3.12と固定依存、Docker Engine、Compose v2、ジョブのcgroup v2制限、CPU統計 `/proc/stat` と `/proc/pressure/cpu` を利用する。同一のDocker daemon上でDBとジョブを動かし、計測時に別の負荷試験を重ねない。[環境の準備](../../README.md#python環境とテスト)

```bash
# 261117/ci_runner から実行
mkdir -p artifacts
.venv/bin/python scripts/compare_workers.py --pilot --output artifacts/p5-pilot.jsonl
.venv/bin/python scripts/compare_workers.py --output artifacts/p5-comparison.jsonl
```

出力は既存ファイルを上書きしない。各回の環境・生データをJSON Linesに保存し、全回の終了と専用DBの回収後に `complete` を記録する。
試験専用のComposeプロジェクトにランダムな名前を付け、DBを `127.0.0.1:55418` に置く。使用中なら起動は失敗し、既存接続先へ切り替えない。
別の `DATABASE_URL` は使わない。停止時はWorkerをドレインして専用DBを回収する。ハード停止後の確認では、その試験のrun IDまたはComposeプロジェクト名で資源を特定する。

| 項目 | 条件・測り方 |
|---|---|
| 負荷 | 固定 `fixture-v1` の成功モード。1/2 Workerを `1, 2, 2, 1, 1, 2` の順で各3回。同じ100個の合成commit SHAを1 transactionで待機キューに入れる |
| ウォームアップ | 各回の開始前に各自のWorker構成で2件成功させる。測定対象の100件から除外。Docker build cacheは保持する |
| 実行経路 | GitHub HTTP応答を試験用データに置換。取得物の検証、固定Docker build、claim、Docker制限、DBでの結果・ログ・成果物確定は常駐Workerの通常経路 |
| キュー待ち p95 | DB上の `started_at - claimable_at`。対象runがキューに今すぐ入れる時刻からclaimまで。取得とbuildの時間も入り得る |
| 実行 p95 | Workerの単調時計でDockerの起動から終了確認まで。準備、回収とDB commitを含まない |
| バッチ所要時間 | DB上のキュー投入時刻から、最後の正式resultの終了時刻まで |
| DB待ち | およそ0.5秒ごとに `pg_stat_activity` を読み、実行中のCI Runner接続のwait eventを数える。接続の発行前や短い待ちを取りこぼし得る |
| CPU競合 | Workerホストの `/proc/stat` 差分のbusy、iowait、steal、CPU PSIの `some` 増分。ホスト全体の値を使い、CPUを待つタスクが一つでもあった時間を区別する |
| 合格条件 | 100件に試行各1件、同一SHA・固定digestに結びついた正式成功、ログと結果JSON、計測値を確認。試験対象コンテナ・imageを回収する |

p95は各回の成功サンプルで線形補間する。失敗件数を別に数え、異常終了・計測値の欠落・資源残存があれば比較を停止する。
短い固定fixtureの結果は、Docker準備・終了処理やDBの競合を含めた当環境の観測値。キャッシュがない初回、ネットワーク経由のGitHub、他のジョブへの適用は別に測る。

### 2026-11-19の実測

AMD Ryzen 7 7735HS、WSL2 Linux 6.6.87.2、8論理CPU・約27.4 GiB RAM、Python 3.12.3、Docker Engine 29.9.2、PostgreSQL 17.12（migration 001〜008）を使用した。
実行コードは `649dbd4f01abc6a98de071244879841633d4e8e7`。比較スクリプトのSHA-256は `cb4388faa5dcec2b8b298a550d23b8ad6f787bfdc8733bca395f86479921204c`。

- Pythonベースイメージは `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`。
- PostgreSQLイメージは `postgres:17.12-bookworm@sha256:40865bc877a289da75963d97d72b59633f639fc5af7a4e856bfa3714cae5b536`。
- `fixture-v1` のSHA-256は `9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff`。同じ固定Dockerfileを全回で使った。

| 実行順 | Worker | 正式成功 / 失敗 | キュー待ち p95（秒） | 実行 p95（秒） | バッチ所要（秒） | CPU busy / iowait | CPU PSI some（秒） | DB観測回数 / WalSync |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 100 / 0 | 728.439 | 2.699 | 799.125 | 52.0% / 3.3% | 115.51 | 1,573 / 1 |
| 2 | 2 | 100 / 0 | 567.699 | 3.005 | 595.757 | 62.3% / 3.9% | 159.23 | 1,178 / 1 |
| 3 | 2 | 100 / 0 | 641.698 | 3.222 | 686.851 | 67.9% / 3.3% | 192.13 | 1,356 / 3 |
| 4 | 1 | 100 / 0 | 766.448 | 2.779 | 813.574 | 51.3% / 3.1% | 114.71 | 1,615 / 3 |
| 5 | 1 | 100 / 0 | 795.113 | 2.744 | 849.695 | 50.6% / 2.4% | 125.23 | 1,685 / 1 |
| 6 | 2 | 100 / 0 | 806.831 | 3.848 | 852.616 | 66.0% / 3.9% | 231.54 | 1,686 / 6 |

600件のrunは試行各1件で正式成功し、同じSHA・固定digest・image ID・ログ・結果JSONの対応を確認した。
2 Workerの3回はどちらのWorkerも毎回50件を確定した。測定対象とウォームアップのコンテナ・image、専用DB資源は回収した。
CSVのサンプル番号は合成SHAでソートした順。各回の100件を個別に再集計でき、image IDにはrun別の由来ラベルが含まれる。[600件の実測サンプル](p5-worker-samples.csv)

![各回100ジョブのp95比較。2 Workerではキュー待ちが3組中2組で短縮し、実行時間は3組すべてで増加した。](p5-worker-comparison.png)

グラフはCSVの `queue_ms` と `execution_ms` を実行順の各100件で集計したp95。線は時系列で隣接する1・2回目、3・4回目、5・6回目を結ぶ。縦軸はそれぞれ秒で、範囲が異なる。

1 Workerと2 Workerの各3回の中央値を比べると、キュー待ちp95は766.448秒から641.698秒（約16%減）、実行p95は2.744秒から3.222秒（約17%増）、バッチ所要は813.574秒から686.851秒（約16%減）だった。最後のペアでは2 Workerが約3秒遅かった。
2 Workerの全回でCPU busyとCPU待ちの時間が増えた。Docker起動・実行の競合は実行p95の増加と整合するが、ホスト統計では処理段階や他プロセスの影響を分離できない。
DBの合計9,093回の観測で待ちイベントは `IO/WalSync` 15サンプル、ロック待ちは0サンプルだった。観測間の短い待ちや接続確立はこの回数に含まれない。

## 初回のローカル検証

独立レビュー担当者は、Docker EngineとCompose v2、cgroup v2を利用できるクリーンな専用Linux VMで実行する。既存のvenvやDB設定を持ち込まず、レビュー対象として合意したSHAへcheckoutする。下記は実装レビュー時のSHAを指定した例。

```bash
git clone --branch feat/ci-runner https://github.com/kenobix/Claude_practice.git
cd Claude_practice
review_sha=9531a2ab48055655a5266fe342d88e9957893e53
git switch --detach "$review_sha"
test "$(git rev-parse HEAD)" = "$review_sha"
cd 261117/ci_runner
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pip check
```

GitHubの認証情報や `.env` は不要。ポート `55417` を試験専用に空け、下記を実行する。

```bash
# 261117/ci_runner から実行
PYTEST_ADDOPTS='-k "test_api_to_separate_worker_to_persisted_result and fixture_success" -rP' \
  .venv/bin/python scripts/verify_execution.py
```

受付APIで保存したrunを別Workerプロセスで取得して固定fixtureをDockerで実行する。APIを起動し直して正式結果とログを照会し、同じ要求から新しいrunが増えないことを検査する。

| 合格条件 | レビュー担当者が確認する証拠 |
|---|---|
| 新規環境から同じ版を起動できる | 受入対象SHAとローカルHEADが一致し、固定依存の導入と `pip check` が成功 |
| 実PostgreSQL・実Dockerで固定fixtureが1件成功し、正式結果を再照会できる | pytestが `1 passed`。新規生成された `artifacts/fixture-worker-latest.json` の `git_head_at_execution` が対象SHA、`pytest_failures` が0、唯一の `cases` が `fixture: success`、`receipt.state: succeeded`、`api_restarted: true` |
| 正しいDBを使い、再起動後も初期化状態を保持する | `real DB readiness: 200`、`migration persists after restart`、PostgreSQLの版と固定イメージdigestの出力 |
| 試験資源を回収できる | スクリプトの終了コード0、`cases[0].owned_resources_before` と `cases[0].owned_resources_after` が一致。Composeラベル `com.docker.compose.project=ci-runner-261117-p0-check` のコンテナ・volume・networkが終了後に0件 |

レビュー結果として、SHA、実施日、OS・Python・Docker・Compose・PostgreSQLの版、上の各条件の合否、つまずいた手順をPR #1へ返す。証拠は必要な項目とログの該当行に絞り、認証情報や環境変数の値を添付しない。skipや証拠不足は未完了とする。

計画§5のREADMEによる再現性には次の二つの証拠がある。

- 実装SHA `9531a2a` のクリーンなUbuntu 24.04 VMで、固定依存、新規venv、実PostgreSQLと実Dockerによる固定fixture1件が成功。証拠とcheckoutのSHA、DB再起動後のmigration保持を確認。 [クリーン環境CI](https://github.com/kenobix/Claude_practice/actions/runs/44861993482)
- 2026-11-21の独立レビューでは、同じ実装SHAをWSLの一時cloneへcheckoutし、Ubuntu 24.04.4、Python 3.12.3、Docker 29.9.2、Compose 5.6.1、PostgreSQL 17.12で固定fixture1件に成功。DB readiness 200、API・DBの再起動後の保持、証拠のSHA、専用資源の回収を確認した。

計画§5が求めるREADMEの再現性は確認済み。人による再現結果は未提出で、Draft PR #1のレビュー履歴は2026-11-21確認時点で0件だった。人による再現結果の提出は、元計画のMVP受入条件には含めない。

## 3分の障害デモ

Dockerの固定イメージが既にある状態で始める。端末AでDBのない管理画面、端末Bで実DB・Dockerを使う故障試験を開く。

```bash
# 端末A
env -u DATABASE_URL .venv/bin/python -m uvicorn ci_runner.app:app --host 127.0.0.1 --port 18117
```

```bash
# 端末B
PYTEST_ADDOPTS='-k "test_two_residents_recover_after_sigkill and running" -rP' \
  .venv/bin/python scripts/verify_execution.py
```

| 目安 | 操作と確認 |
|---|---|
| 0:00–0:30 | 端末Bで試験を開始し、ブラウザーの `http://127.0.0.1:18117/ui/demo` で過去のデモを開く。コミットSHA、失効した試行1、正式結果の試行2を確認する |
| 0:30–1:30 | 試験は固定GitHub応答を検証し、コンテナのGO直後に1つ目のWorkerを強制停止する。DB時刻の30秒leaseを待ち、DB記録と完全IDの照合を経て残存コンテナを回収する |
| 1:30–2:30 | 代替Workerがバックオフ後に同じrun・同じSHAを取得する。試験は代替の試行中と正式確定後の両方で旧tokenの結果を拒否し、履歴と正式結果を検査する |
| 2:30–3:00 | 端末Bで復旧秒数とPASS、readiness、専用DBの回収を確認する。ブラウザーの過去デモと、今回の実測を区別する |

45秒は復旧の仮目標。イメージ取得、ディスク待ち、監視周期で実際の時間は変わる。出力が揃うまで待ち、超過や失敗を記録する。
2026-11-19の復旧は45.91秒。初回検証と過去デモの表示テストを含む選択回帰3件とDBの起動から回収までは113.51秒だった。
この手順は固定GitHub応答と試験用runで停止から復旧までを再現する。実pushと同じ受理SHAを使った署名付きローカル試験の結果も下に記録する。

## 実pushを起点にしたデモ

### 2026-11-24のGitHub実配送

- 実push `0b775ce47360a1eb1c7ca72d8924c5abd1dca4e5` を初回配送で受理。GitHubの応答はHTTP 202、0.52秒。配送GUID `f708add0-e80f-11f1-979f-e269083abce9`、受理SHA、run `9b3df966-0889-4319-9232-4f2bfbbe2b86` が専用DBと一致した。削除前に配送1件、run 1件、attempt 2件を採取した。
- Worker 1のコンテナはGO前に終了コード2で終了した。この配送に対する実行中の強制停止は未実施。期限切れのattempt `7b1b91a2-2d9a-44a5-a33d-6811766348c7` とコンテナ不在をDBに記録し、同じrunを別tokenのattempt `534aa6c6-7d5d-4be3-8a59-03e478e350ba` が引き継いだ。
- 旧tokenの報告を正式結果保存の前後に拒否した。DB受付から145.23秒で `succeeded`、固定fixtureのログ `{"ok": true}` と受理SHAをDB・管理画面で照合した。公開経路と専用Docker・DB資源は回収した。

### 終了コード2の再現と修正後のローカル試験

公開試験コントローラーは秘密情報を扱うローカルファイルのため `umask 0077` を設定する。Workerがその設定を継承し、製品の一時ビルドコンテキストに `fixture.py` が `0600` で生成された。Dockerの `COPY` がモードを保持し、コンテナのUID 65534が `/opt/fixture.py` を開けず、PythonがGO前に終了コード2を返した。ローカルの実Dockerで同じ固定fixtureを使い、修正前は `0077` で `Permission denied` と終了コード2、`0022` で終了コード0を確認した。 [DockerのCOPY仕様](https://docs.docker.com/reference/dockerfile/#copying-from-the-build-context)

製品側で検証済みfixtureのモードを `0644` に指定した。ビルドコンテキストのディレクトリは `0700`。同じ `umask 0077` で実PostgreSQLとDockerを起動し、上記実pushのSHAをローカル署名付きPOSTで受け付けた。GitHub上のコミットからのsource取得・digest照合にはWorkerの通常経路を使用した。

| 確認項目 | ローカルで採取した証拠 |
|---|---|
| 受付 | HTTP 202、配送 `3ae020b7-2abc-4311-b65a-0d911f76d657`、run `096a8463-aade-48c2-854a-088c7d5f6051`。DBに配送1件とrun 1件 |
| 実行中の停止と回収 | attempt `9c3c7569-9231-4041-9613-263601ecdc8e` でGOとコンテナの稼働を確認し、Worker 1をSIGKILL。DB時刻のlease失効、残存コンテナの完全IDによる回収を確認 |
| 引き継ぎと旧報告 | 同じrunとSHAをWorker 2がattempt `c046b48f-6706-4406-bf16-7476681e282d` と別tokenで取得。旧tokenの報告を正式結果保存の前後で拒否 |
| 正式結果と回収 | `succeeded`、固定source digest、ログ `{"ok": true}`、結果JSON、管理画面とDBの2試行履歴を照合。専用資源を回収 |
| 所要時間 | DB受付から正式結果55.94秒、SIGKILLから正式結果48.99秒、送信開始から管理画面照合まで62.34秒。計画§6の復旧45秒の仮目標は超過 |

### 受入判定

計画§5と§8のデモは、GitHubの実pushが同じrunの正式結果までつながった証拠と、同じ実push SHAを使ったローカルの実行中停止・引き継ぎ・旧結果拒否の証拠で受け入れる。Webhookは配送とrunを同一transactionで保存し、その後のWorkerはDBのrun ID・SHAを読み、実行権と結果をDBで確定する。公開HTTPSはこのDB境界までを実配送で検証済みで、修正後のローカル試験は同じ受付実装を通ってDB境界から最後までを検証した。2回のrun IDは別であり、公開配送とSIGKILLを同じrunで観測したという判定はしていない。追加の公開試験はマージ前の必須条件にしない。 [元計画](../../../261014/plan_ci_runner.md)・[P2の故障試験](design.md#2-workerの故障復旧試験)・[P3の実配送](webhook.md#実配送の受入記録)

比較実験、クリーン環境CIと新規cloneの再現、3分デモ、静的デモと運用資料を照合し、P5のマージ前の受入条件は完了と判定した。GitHub Pagesの公開確認は承認済みの統合後に行う。

### 2026-11-21の公開試験

承認済みの時間枠でQuick Tunnelを起動し、外部HTTPSで `POST /webhooks/github` が未設定時に503、同じパスへのGETと管理APIへのPOSTが404になることを確認した。終了予定は17:39:46 JST。17:36:59の確認では受信側のSecret入力は未確認、DBの配送は0件、Worker試験は未起動だった。17:39:18のGitHub照合でも配送先URLは更新されておらず、対象GUIDの新しい再送は確認されなかった。

17:39:48に公開終了と専用DB・volume・network、loopbackポート、制御ソケットの回収を確認した。この公開枠ではGitHubの202、run ID、Workerの停止・復旧、正式結果は未実測だった。

## Pagesの公開

静的デモとCSS、比較図、CSVはブランチに揃っている。GitHub Pagesの公開元は `main` のルート。2026-11-21に公開元にデモのHTMLが存在しないことをGitHub APIで確認した。計画§7は公開を提出条件としているため、マージ前には公開物と参照先を確認し、承認済みの統合後に公開を確認する。 [静的デモ](../../demo/index.html)・[計画§7](../../../261014/plan_ci_runner.md#7-実行境界公開費用)

公開時はルートREADMEの一覧にデモへの導線を追加し、既存の公開元を維持する。Pagesのデプロイが成功したら、デモ画面、CSS、比較図の表示、CSVのダウンロードを公開URLで確認する。 [公開URL](https://kenobix.github.io/Claude_practice/261117/demo/)

## 運用と限界

- 受け付け状況はreadinessとrun履歴を照合する。API応答が途切れた手動投入は同じ業務キーで再送する。外部Webhook配送は管理者がGitHubから再送する。[受付API](intake-api.md)
- Worker停止・DB断では、新規取得に進めない。停止位置によっては期限監視と回収が必要。Docker create結果が不明なら不在を推測せず、再実行を保留する。[期限と回収](design.md#失効attemptのコンテナ回収)
- ログと成果物のpayloadに容量上限がある。自動保持期限と複数ホストはMVP対象外。異常時は運用指標から当該runの試行へ戻って調べる。[管理画面と指標](run-operations.md)
- 管理APIはloopback、Webhookは専用入口、ジョブは固定fixtureだけをDockerの制限下で実行する。管理用のDB、Docker socket、認証情報をジョブと分離する。[脅威モデル](threat-model.md)
