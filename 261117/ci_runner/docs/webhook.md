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

実配送の前に、公開先の運用責任者がTLS終端、転送経路、試験時間帯を承認する。公開側からは `POST /webhooks/github` だけを `127.0.0.1:18118` の専用アプリへ転送し、管理API・DB・Docker socketへの経路を設けない。プロキシは元bodyを保ち、サイズ・接続・受信時間を制限し、bodyや署名ヘッダーをログに残さない。 [GitHubのWebhook運用指針](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)

リポジトリの所有者または管理者が、承認を得て対象リポジトリの **Settings → Webhooks** から設定する。Payload URLは承認済みのHTTPS URL、Content typeは `application/json`、イベントはpushのみ、SSL検証は有効とする。共有秘密はGitHub設定と受信プロセスの環境にだけ登録する。非アクティブで作成した場合は、hook IDを受信設定に反映してから有効にする。 [GitHubの設定手順](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks)

管理者と、許可refへ通常の運用で届くpushの試験時刻を調整する。通常受信で配送GUID・受理SHA・run IDが対応し、永続化後の202が10秒以内に返ることを確認する。専用受信プロセスの停止中に別の対象pushが失敗したことを確認し、復旧後、管理者が **Recent deliveries** の同じGUIDで **Redeliver** を選ぶ。GitHubは自動再送せず、手動再送は過去3日間の配送が対象。同じ配送を10回再送しても同じrunが返り、そのSHAのrunは1件であることを確認する。GitHub側の応答時間と受信側の永続化結果を記録する。 [GitHubの手動再送手順](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)

## 検証範囲

使い捨てDBで、署名付きのローカルHTTPS・同じ配送IDの10回再送・DB停止・commit前後の受信プロセス停止を確認する。

```bash
PYTEST_ADDOPTS='-k webhook_postgres -rP' .venv/bin/python scripts/verify_db.py
```

公開HTTPS受信先の準備と、管理下リポジトリからの実push、GitHubの画面からの手動再送は未検証。ローカルHTTPSは試験用証明書をクライアントに明示して検証する。
