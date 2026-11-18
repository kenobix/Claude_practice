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

専用DBに既存のmigration 001〜006を適用する。Webhook受信プロセスに `DATABASE_URL`、GitHubで付与された `CI_GITHUB_WEBHOOK_HOOK_ID`、GitHub側と同じ `CI_GITHUB_WEBHOOK_SECRET` を環境変数で設定する。秘密は32バイト以上のランダム値を使用し、リポジトリや起動コマンドの引数に置かない。

```bash
.venv/bin/python -m uvicorn ci_runner.webhook:app --host 127.0.0.1 --port 18118 --no-access-log
```

実配送では承認済みの公開HTTPS受信口で証明書検証を有効にし、`POST /webhooks/github` だけをこのプロセスへ転送する。プロキシはbodyを変換せず、サイズ・接続・受信時間を制限する。GitHubのhookはJSON、pushイベントだけ、SSL検証有効で設定する。先に非アクティブなhookを作成する場合はIDを受信設定へ反映してから有効にする。 [GitHubのWebhook運用指針](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)

GitHubは失敗配送を自動再送しない。復旧後、管理者がリポジトリの **Settings → Webhooks → Recent deliveries** で対象GUIDの **Redeliver** を選ぶ。対象は過去3日間。停止中に失敗したpushを同じIDで再送してrunが1件であること、GitHubの画面で応答が受信から10秒以内であることを確認する。 [GitHubの手動再送手順](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)

## 検証範囲

使い捨てDBで、署名付きのローカルHTTPS・同じ配送IDの10回再送・DB停止・commit前後の受信プロセス停止を確認する。

```bash
PYTEST_ADDOPTS='-k webhook_postgres -rP' .venv/bin/python scripts/verify_db.py
```

公開HTTPS受信先の準備と、管理下リポジトリからの実push、GitHubの画面からの手動再送は未検証。ローカルHTTPSは試験用証明書をクライアントに明示して検証する。
