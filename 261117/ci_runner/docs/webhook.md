# GitHub Webhook受付

Webhook専用のASGIアプリ `ci_runner.webhook:app` は `POST /webhooks/github` を扱う。同じ専用DBを使う管理APIは別プロセスでloopbackに置く。Workerと実行権の境界は従来どおり。

## 受付と永続化

- JSONのbodyは256 KiBまで、受信待ちを2秒までとする。実際に読んだbytesに対し、`X-Hub-Signature-256` をHMAC-SHA256と定数時間で照合してからUTF-8 JSONとして処理する。署名・body・秘密値は保存しない。 [GitHubの署名仕様](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- 設定済みのhook IDと、正規形式の配送ID・イベント名を要求する。pushは署名済みのrepository ID `1197425837` と名前 `kenobix/Claude_practice`、`refs/heads/main`、削除フラグを確認する。許可されたpushの `after` は非ゼロの40桁SHAを要求し、既存の固定workflowへ登録する。対象外のイベント・repository・ref、削除pushは `ignored` として記録する。
- `deliveries` はhook ID・配送IDの一意キーで予約する。同じtransactionで既存の業務キーを使ってrunを作成・再利用し、配送のハッシュと結果を確定する。応答前にcommitを終える。イベントや配送IDはHMAC対象のbodyに含まれないため、配送キーとrunの業務キーを併用する。
- 同一配送・同一body・同一判定の再送は保存済みの結果を返す。同じ配送IDの異なるbodyや判定は409。配送IDが異なる同じコミットも同じrunを返す。保持期間はMVP検証期間全体とし、定期削除は設けていない。配送行の保持が終わっても、runの業務キーが重複を防ぐ。

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

公開先の運用責任者が、CloudflareでのTLS終端と配送データの通過、試験時間帯を承認してから実施する。WSL2でCaddyは `127.0.0.1:18119` に待ち受け、`POST /webhooks/github` だけを元bodyと署名ヘッダーを保ったまま `127.0.0.1:18118` の専用アプリへ転送する。Caddyは管理口とリクエストのログを無効にし、256 KiBと受信・接続時間の上限を適用する。管理API・DB・Docker socketへの転送経路を設けない。 [Caddy設定](../Caddyfile.webhook)

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

別端末でCaddyを起動し、loopbackで許可外のパスとメソッドが404になることを確認する。承認済みの試験枠で、さらに別端末からQuick Tunnelを前景で起動する。設定とバイナリは管理APIの環境や認証情報を継承しない端末で実行する。

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

発行されたURLの `/webhooks/github` をPayload URLとする。リポジトリの所有者または管理者が **Settings → Webhooks** で、`application/json`、pushのみ、SSL検証有効、無効状態で登録する。DBのシェルで実際のhook IDと共有秘密を対話入力して受信アプリを起動し、その後hookを有効にする。 [GitHubの設定手順](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks)

```bash
read -r -p 'GitHub hook ID: ' CI_GITHUB_WEBHOOK_HOOK_ID
read -r -s -p 'GitHub webhook secret: ' CI_GITHUB_WEBHOOK_SECRET
printf '\n'
export CI_GITHUB_WEBHOOK_HOOK_ID CI_GITHUB_WEBHOOK_SECRET
.venv/bin/python -m uvicorn ci_runner.webhook:app --host 127.0.0.1 --port 18118 --no-access-log
```

公開URLでTLS検証と範囲外の404、無効署名の403を確認する。管理者と、許可refへ通常の運用で届くpushの試験時刻を調整する。通常受信で配送GUID・受理SHA・run IDが対応し、永続化後の202が10秒以内に返ることを確認する。受信アプリだけを `Ctrl+C` で停止し、別の対象pushが失敗したことを確認する。同じ設定でアプリを再起動後、管理者が **Recent deliveries** の同じGUIDで **Redeliver** を選ぶ。GitHubは自動再送せず、手動再送は過去3日間の配送が対象。同じ配送を10回再送しても同じrunが返り、そのSHAのrunは1件であることを確認する。GitHub側の応答時間と永続化結果を記録する。 [GitHubの手動再送手順](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)

証拠を採取したら管理者が試験用hookを削除する。トンネルを `Ctrl+C` で止め、公開URLの転送が終了したことを確認する。Caddyと受信アプリを `Ctrl+C` で止め、専用シェルで `p3_compose down --volumes` を実行する。専用プロジェクトの資源が消えたことを確認し、`unset POSTGRES_PASSWORD DATABASE_URL CI_GITHUB_WEBHOOK_HOOK_ID CI_GITHUB_WEBHOOK_SECRET` を実行して専用シェルを閉じる。

## 検証範囲

使い捨てDBで、署名付きのローカルHTTPS・同じ配送IDの10回再送・DB停止・commit前後の受信プロセス停止を確認する。

```bash
PYTEST_ADDOPTS='-k webhook_postgres -rP' .venv/bin/python scripts/verify_db.py
```

CaddyのローカルHTTPS試験では、`CI_WEBHOOK_PROXY_ADDRESS=https://127.0.0.1:18119` と `CI_WEBHOOK_PROXY_TLS="tls <試験証明書のパス> <試験秘密鍵のパス>"` を設定し、試験証明書をクライアントだけで信頼する。実配送用のloopback HTTP設定でも、公開側のHostに相当する値で転送を確認した。 [検証記録](../../work_log.md#p3公開試験前のcaddyローカル検証)

Quick Tunnelの公開起動、管理下リポジトリからの実push、GitHubの画面からの手動再送は未実施。
