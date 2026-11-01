# T13a: 合成証拠を使うMLflow HTTP決定の障害受入

既存のSQLite直接接続試験に加え、実MLflow server/REST clientと障害proxyを使う。実Capital候補の採否、実データの性能、認証された人の独立承認、HTTPでのartifact取得の証拠にはしない。artifact本文は一時file storeから読む。モデルsourceは学習済みモデルではない。

## 実行と安全境界

Python/lockを用意した261021から実行する。Linuxの/procとloopback socketを使用する。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/test_capital_decision_http.py \
  --run-disposable --evidence-dir data/reports/t13-http-local-unique
```

出力directoryは未存在の新しいpathを指定する。任意のregistry URLやDB指定は受け付けない。一時workspace/SQLite/artifacts/lock、自分で起動するMLflow、127.0.0.1の空きport、自分のproxyだけを使う。portを一旦予約して解放する隙間があるため、実際のlistener fdが起動したprocess/子に属することを/procのPID/start tick/process group/socket inodeで確認してから書き込む。trackingとregistryは同じproxy URIへ固定し、継承したMLFLOW_*は除去する。外部認証情報を必要としない。

TERMはfinallyを通る非0終了へ変換し、自分のserver groupへTERM、必要時だけKILLを送る。server/proxy停止、残存group、一時workspace削除をcleanup.jsonへ記録する。任意のprocessや既存Dockerは停止しない。SIGKILL自体にfinallyの保証はない。CI timeoutのTERM/kill-afterを恒久的なOS障害からの回復保証とは説明しない。

合成fixtureのreview/approval時刻を事前固定の`2026-11-01T00:00:00+00:00`として一時registryへ入力する。通信障害だけを検証するための入力であり、時計clamp・未来拒否の緩和・実承認時刻の置換ではない。既存validator/producerは変更しない。この時刻さえ未来である環境では試験は拒否される。自然UTC試験の4.170111秒逆行を解決した証拠として使わない。

## 受入条件と観測

| ケース | 注入と確認 |
|---|---|
| reviewの途中書込 | reviewerまで保存してreason書込前に502を返す。not_evaluated/blockedを維持し、通常配信gateが拒否する。 |
| reviewの最終gate後に応答喪失 | operational_gate_status保存後にproxyが接続を閉じる。clientは例外、読戻しはpassed/blocked。同じreviewで同一hash/時刻へ復旧する。 |
| approvalの途中書込 | approverまで保存してreason書込前に502。blockedを維持し、配信gateが拒否する。 |
| approvalの最終permission後に応答喪失 | eligibleの保存成功後に接続を閉じる。client結果は読戻しまで未確定。全証拠の整合を読戻し検証し、同じ要求の明示retryでhash/tagを変えない。失敗応答だったからDBもblockedだとは推測しない。 |
| 競合する決定 | 異なる理由への上書きを拒否。同じ版のwriterがlockを保持する地点で競合要求を送り、lock拒否と全tag不変を照合する。これは同じ一時lockを共有する協調writerの試験で、別hostの分散lockではない。 |

MLflowのHTTP自動retryを0にし、各障害の注入回数が1であることを照合する。明示retryと最初の失敗を別々に保存する。外見上のMax retries exceededという例外文だけで、自動再送があったと判断しない。

http.jsonlにforward/注入/HTTP status、各地点のtag JSON、cases.jsonに例外・読戻し・復旧hash、server.logに起動/終了、cleanup.jsonに回収、hashes.jsonに元fileのSHAを保存する。hashは改変検出用で、独立した第三者の署名ではない。実コマンド/UTC/MONOTONIC/失敗logは[作業ログ](../work_log.md)、公開索引は[検証JSON](reviews/20261101-t13-http-checks.json)を参照。

初回はimport先誤り、二回目はRESTがrun外のモデルsourceを拒否した。正しい既存moduleとrun内artifactへ修正し、serverの保護を緩めなかった。v3/v4の成功後、TERM cleanupを追加し原本保存/CI logを整えた最終v6と実TERM中断試験が成功した。新guard 7件、既存決定/証拠30件が成功。新scriptはCIにも接続し、失敗終了値をpipefail/timeout経由で保持する。CI実績は対象commitとrunを別記録し、local結果で代用しない。

残りは実候補のproducer証拠、時計資格、監視/配信/復元の同一artifactへの結合を揃えたT13b最終採否である。T13aが通っても実registryのeligible化や現配信変更は行わない。
