# Capital運用測定の中断・タイムアウト（T03）

この手順は隔離Capital staging専用です。UCI版3の配信・DBは変更しません。新しい実candidateの測定はT10/T11の完了後です。

## 実行前に固定するルール

operational planの`policy.request_journal.policy_id=capital-request-journal-v1`に次の条件を固定します。従来の50暖機・1,000逐次要求、HTTPXの接続・読取・書込・接続待ちそれぞれの10秒timeout（要求全体の厳密な10秒期限ではない）、再送0、エラー0、nearest-rank p95 ≤250msは維持します。journalのない旧planは履歴として読めますが、新たな測定では拒否します。既存planを書き換えず、新しいplanと理由付きの別attemptを作ります。

- HTTP時間: HTTP呼出し開始からJSONを読み終えるまで。応答検証・監査照合・journal保存は除外します。例外時もHTTP呼出し終了時の時間を使います。
- 全体時間: `timing.request_loop_elapsed_ms`。各要求の検証・journal書込・fsync、逐次ループ終了時のclient closeを含みます。事前ready/version、事後resource取得・全件DB照合・report/台帳確定は含みません。「プログラム全体の起動から終了まで」の時間ではありません。
- `request_journal.write_and_fsync_ms`: journalのheaderとeventのwrite/fsync時間の合計。初回親directoryのfsyncは除外します。全体時間・この値に合格閾値はありません。
- 中断・タイムアウト・監査不一致・記録失敗は合格にしません。部分的なp95だけで1,000件の測定結果を置き換えません。測定完了でも配信はblocked、operational gateは別途reviewです。

journalはディスクへの記録を増やし、測定負荷にも影響します。新旧の全体時間を同一条件とみなさず、policy ID、資源、全attemptを併記します。

## 保存するもの

`data/reports/capital-bikeshare/operational-<attempt UUID>.requests.jsonl`へ追記します。最初の行にcandidate/planとplanファイルのSHA・固定1,050要求のID/入力を保存し、各eventに順番・UTC時刻・前eventのSHA・自身のSHAを付けます。ファイルは新規作成のみ、permissionは600です。

| 状態 | ディスク記録と解釈 |
|---|---|
| `scheduled_only_no_durable_send_intent` | 固定予定のみ。送信直前の確定記録なし。「絶対に未送信」とは断定しません。 |
| `outcome_unknown_may_have_been_sent` | `sending`をfsyncしてからHTTPを呼ぶ。timeout、失敗応答、検証失敗、途中停止では結果未確定。DBへ後で保存される可能性があります。 |
| `response_verified` | 200応答と入力由来等の検証後、HTTP時間を含むeventをfsync済み。全件DB監査完了や配信承認は意味しません。 |

headerのfsyncと作成directoryのfsyncが終わるまで要求を送りません。各`sending`と結果eventもwrite→fsyncを完了してから次へ進みます。write/fsync失敗後はjournalを再利用しません。電源断やストレージ装置の虚偽応答まで保証するものではありません。

SIGTERMはCLIのmain threadでKeyboardInterruptへ変換し、可能なら失敗reportを保存します。SIGKILLではhandlerが動かず、台帳がstartedのまま、reportなしでもjournalが残ります。末尾の途中行は`torn_tail`として読み取り、ファイルを修復しません。完全な行のhash不一致・順序不正はinvalidです。SHA鎖は偶発破損の検知用で、管理者による書換えへの電子署名ではありません。

## 停止後の確認順

まずプロセスと隔離サービスの状態を確認し、実行中のまま再試行しません。既存のcandidate lockを取れない間は検査も拒否します。

```bash
cd ~/work/claude_practice/261021
. .venv/bin/activate
python scripts/operational_attempt_ledger.py --help
python scripts/inspect_capital_operational_reports.py --candidate-id <64桁candidate ID>
```

`request_journal.status`（readable/torn_tail/missing/invalid）、要求別状態、linked reportとplanの照合結果を確認します。`started/interrupted`でreportがない場合もjournalを読み取れます。ファイルも台帳も更新せず、結果を自動採用しません。

未確定要求が1件あるときは、同じstaging DBの監査行をSELECTだけで確認します。既存のAPP_DB_PASSWORDを設定した環境で実行し、認証情報をログへ表示しません。

```bash
DATA_PROFILE_ID=capital-bikeshare python scripts/inspect_capital_pending_audit.py \
  --candidate-id <candidate ID> --attempt-id <UUID>
```

`not_seen_at_query_time`は「その照会時点で見えない」です。後のcommitを否定しません。`identity_matches_unverified`も版等の一致のみで、予測値・全入力の検証や運用合格ではありません。DBが切断されていたら照会成功として扱わず、元の証拠を保全します。古い失敗reportだけがあるattemptも従来の読取手順を維持します。

再試行が必要なら、失敗原因・未確定要求・DBの観測・資源条件を調べ、人のoperator/reasonを付けて`operational_attempt_ledger.py review-retry --help`の現行オプションに従います。同じ要求をその場で再送せず、新しいUUIDのattemptを作り、古いattempt・journal・reportを保持します。成功・高速なattemptだけを選びません。

完了したreportは`recheck_capital_measurement.py`で固定1,050要求と保存HTTP時間からp95を再計算します。新policyのlinked reportではjournalのSHA・1,050応答確定・要求別HTTP時間も照合します。これも承認操作ではありません。
