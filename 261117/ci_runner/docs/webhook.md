# GitHub Webhook受付

Webhook専用のASGIアプリ `ci_runner.webhook:app` は `POST /webhooks/github` を受け付ける。管理APIは同じ専用DBを使うが、別プロセスとしてloopbackで待ち受ける。ジョブを実行するWorkerと実行権の境界は従来どおり。

## 受付と永続化

- リクエスト本文（body）の上限は256 KiB、受信待ちの上限は2秒。受信したバイト列からHMAC-SHA256を計算し、`X-Hub-Signature-256` の署名と定数時間で照合してから、UTF-8のJSONとして処理する。署名・body・共有秘密は保存しない。 [GitHubの署名仕様](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- 設定済みのhook IDと、正規形式の配送ID・イベント名を要求する。pushは署名済みのrepository ID `1197425837` と名前 `kenobix/Claude_practice`、`refs/heads/main`、削除フラグを確認する。許可されたpushの `after` は非ゼロの40桁SHAを要求し、既存の固定workflowへ登録する。対象外のイベント・repository・ref、削除pushは `ignored` として記録する。
- hook IDと配送IDの組を一意キーとして `deliveries` に登録する。同じトランザクションで、既存の業務キーを使ってrun（実行記録）を作るか再利用し、配送のハッシュと結果を確定する。commitが完了してから応答する。イベント名と配送IDはHMACの対象となるbodyに含まれないため、配送キーとrunの業務キーを併用する。
- 同じ配送IDでbodyも判定も一致する再送には、保存済みの結果を返す。配送IDが同じでもbodyまたは判定が異なれば409を返す。配送IDが異なっても、同じコミットなら同じrunを返す。配送記録はMVPの検証期間を通して保持し、定期削除は行わない。保持期間が終わっても、runの業務キーが重複を防ぐ。

| HTTP | 応答 |
|---|---|
| 202 | `status: accepted` とrun ID |
| 200 | `status: ignored` と `run_id: null` |
| 400 / 403 | 不正なヘッダー・JSON・push、または署名とhook IDの拒否 |
| 408 / 413 / 415 | body受信期限・サイズ・形式の拒否 |
| 409 | 配送ID競合 |
| 503 | 設定不足・DB接続/transaction失敗。commit応答喪失の可能性がある場合も同じ配送を手動再送する |

## 起動と障害復旧

専用DBに同じ版のmigrationを適用する。Webhook受信プロセスに `DATABASE_URL`、GitHubで付与された `CI_GITHUB_WEBHOOK_HOOK_ID`、GitHub側と同じ `CI_GITHUB_WEBHOOK_SECRET` を環境変数で設定する。秘密は32バイト以上のランダム値を使用し、リポジトリや起動コマンドの引数に置かない。

```bash
.venv/bin/python -m uvicorn ci_runner.webhook:app --host 127.0.0.1 --port 18118 --no-access-log
```

### 公開試験用の起動と停止

公開先の運用責任者が、CloudflareでTLSを終端すること、配送データがCloudflareを通ること、試験時間帯を承認してから実施する。WSL2上のCaddyは `127.0.0.1:18119` で待ち受ける。転送対象は `POST /webhooks/github` に限り、元のbodyと署名ヘッダーを変えずに `127.0.0.1:18118` の専用アプリへ渡す。Caddyの管理口とリクエストログは無効にし、サイズ上限の256 KiBと受信・接続時間の上限を適用する。管理API・DB・Docker socketへの転送経路は設けない。 [Caddy設定](../Caddyfile.webhook)

`261117/ci_runner` から開始する。専用プロジェクト `ci-runner-261117-p3-live` のラベルを持つコンテナ・volume・networkが既にある場合や、ポート18118・18119・55419が使用中の場合は起動を止める。DBとアプリは同じ専用シェルで操作する。

```bash
set +x
p3_compose() { docker compose --env-file /dev/null -p ci-runner-261117-p3-live "$@"; }
export POSTGRES_USER=ci_runner_261117 POSTGRES_DB=ci_runner_261117 CI_DB_PORT=55419
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${CI_DB_PORT}/${POSTGRES_DB}"
p3_compose up -d --wait db &&
  .venv/bin/python scripts/migrate.py
```

別の端末でCaddyを起動し、loopbackで許可外のパスとメソッドに404が返ることを確認する。承認済みの試験時間帯に、さらに別の端末でQuick Tunnelをフォアグラウンドで起動する。いずれも管理APIの環境や認証情報を継承しない端末で実行する。

```bash
(
  p3_caddy_state=$(mktemp -d) || exit 1
  trap 'rm -rf -- "$p3_caddy_state"' EXIT
  export XDG_CONFIG_HOME="$p3_caddy_state/config" XDG_DATA_HOME="$p3_caddy_state/data"
  env -u CI_WEBHOOK_PROXY_ADDRESS -u CI_WEBHOOK_PROXY_TLS \
    artifacts/webhook-tools/caddy run --config Caddyfile.webhook --adapter caddyfile
)
# 別端末。公開操作なので承認後だけ実行する。
artifacts/webhook-tools/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:18119
```

発行されたURLに `/webhooks/github` を付けてPayload URLに指定する。リポジトリの所有者または管理者が **Settings → Webhooks** で、形式を `application/json`、対象をpushのみ、SSL検証を有効に設定し、hookは無効のまま登録する。DBを起動したシェルで実際のhook IDと共有秘密を対話入力し、受信アプリを起動する。その後、管理者がhookを有効にする。 [GitHubの設定手順](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks)

```bash
read -r -p 'GitHub hook ID: ' CI_GITHUB_WEBHOOK_HOOK_ID
read -r -s -p 'GitHub webhook secret: ' CI_GITHUB_WEBHOOK_SECRET
printf '\n'
export CI_GITHUB_WEBHOOK_HOOK_ID CI_GITHUB_WEBHOOK_SECRET
.venv/bin/python -m uvicorn ci_runner.webhook:app --host 127.0.0.1 --port 18118 --no-access-log
```

公開URLでTLS検証と範囲外の404、無効署名の403を確認する。既存の配送を使う場合は、管理者が **Recent deliveries** の対象GUIDで **Redeliver** を選ぶ。GitHubは自動再送せず、手動再送は過去3日間の配送が対象。GitHub側の応答時間、配送GUID、受理SHA、run ID、DBの記録を照合する。P3の10回再送とDB一意性はローカルHTTPSの検証で確認済み。公開経路での手動再送は目的のイベント1回で照合する。 [GitHubの手動再送手順](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)

証拠を採取したら管理者が試験用hookを削除する。トンネルを `Ctrl+C` で止め、公開URLの転送が終了したことを確認する。Caddyと受信アプリを `Ctrl+C` で止め、専用シェルで `p3_compose down --volumes` を実行する。専用プロジェクトの資源が消えたことを確認し、`unset POSTGRES_PASSWORD DATABASE_URL CI_GITHUB_WEBHOOK_HOOK_ID CI_GITHUB_WEBHOOK_SECRET` を実行して専用シェルを閉じる。

## 検証範囲

使い捨てDBで、署名付きのローカルHTTPS・同じ配送IDの10回再送・DB停止・commit前後の受信プロセス停止を確認する。

```bash
PYTEST_ADDOPTS='-k webhook_postgres -rP' .venv/bin/python scripts/verify_db.py
```

CaddyのローカルHTTPS試験では、`CI_WEBHOOK_PROXY_ADDRESS=https://127.0.0.1:18119` と `CI_WEBHOOK_PROXY_TLS="tls <試験証明書のパス> <試験秘密鍵のパス>"` を設定し、試験証明書をクライアントだけで信頼する。実配送用のloopback HTTP設定でも、公開側のHostに相当する値で転送を確認した。 [検証記録](../../work_log.md#p3公開試験前のcaddyローカル検証)

## 実配送の受入記録

2026-11-21に既存hook `714755178` を使用した。計画§5のP3は、ローカルHTTPSでの10回の再送と無効署名の検証、実際のGitHub配送での受信停止と復旧の記録を合わせて受け入れた。

| 実配送 | GitHubの結果 | 永続化の証拠 |
|---|---|---|
| `47faf210-e564-11f1-8d14-29e21b4acbd6` の手動再送 | 202、4.14秒。SHA `9097f84b12affb0d58f23e51d22c92a6475f4224`、run `b62bf3cb-99f3-47c3-a6dd-2c38a690bae1` | 専用DBの配送・runを直接照合 |
| `e269343e-e568-11f1-8f9c-c6f2c8a1d397` の停止中の配送と復旧後の1回の手動再送 | 停止中502（0.87秒）、復旧後202（0.70秒）。SHA `91afb731a8184717f86b88c04807207c18390e83`、run `665e351e-383e-4227-a1ea-10a63162ee3e` | 停止中は配送・run各0件を直接確認。復旧後はGitHubの受理応答を確認し、DB件数の直接採取は試験枠終了までに行えなかった |

試験の終了後に専用資源を回収した。実pushから始める障害デモの記録は、P5の合格条件に沿って別にまとめた。 [P5実pushデモ](p5-validation.md#実pushを起点にしたデモ)
