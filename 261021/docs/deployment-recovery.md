# 配信復旧の状態・承認metadataと隔離受入（T07a/T17a）

基点`444acc9`。[計画§11.6](../../261014/plan_mlops.md)、[証拠JSON](reviews/20261101-t07-t17-checks.json)を参照。
実DB・実registry・現配信への適用は行っていない。合成UCI契約の試験であり、実Capitalの配信証拠producerには使用しない。

## 修正した問題

従来のrecoverは、候補へ切り替わっていてもruntime stateへ新しいapprover/reasonを反映せず、bootstrap時の情報を残していた。
またpromoteの補償reloadが失敗しても台帳をfailedへ確定するため、次のrecoverが未完了を見つけられない場合があった。
recoverにはpromoteと共通のlockがなく、未完了が複数あればcreated_atの降順で一件を選んでいた。UTC逆行下で順序の根拠にできない。

今回の変更:

- promote/recoverは同じruntime-local flockを使う。他操作中はlock取得を拒否する。
- promoteのprepared行にschema2 recovery contextを保存する。変更前stateと、旧/候補のmodel名・版・artifact SHA・gate・特徴量版・profile/targetを固定する。
- normal completionとcandidate側recoverは、同じ台帳行のdeployment ID・approver・reasonをstateへ書く。
- 旧版側recoverは保存した旧stateを復元する。旧metadataを推測で作らない。
- model名/版/hash/gate/feature/profileとreadyを照合し、再読取後の不一致も確定を拒否する。stagingのblocked応答を通常配信として認めない。
- 準備後にregistryとAPIが一緒に別artifactへ変わっても、schema2に固定したidentityとの不一致を拒否する。
- Capitalでは既存の全証拠・決定の配信gateをrecoverでも要求する。
- 切戻しを確認できなければin_progress/recovery_requiredを残す。実際の旧版/readyを照合してからfailedへ確定する。
- 未完了が複数なら自動選択せず拒否する。新規promoteも未完了を先に解消するよう要求する。
- 過去版の履歴は保持するが、rollback要求時に実artifactの読み込み/hash/gateを事前確認し、利用不可ならenv/台帳/候補起動より前に拒否する。

## 中断地点と復旧

| 観測した入口 | 台帳・state | 処理 |
|---|---|---|
| 旧版 | prepared/candidate_ready、state旧版 | 旧版のregistry identity/readyを再検証し、保存済み旧stateへ戻してretry_candidate/failed |
| 候補版 | proxy_switched、state旧版 | 候補の固定identityとreadyを照合し、新しい操作ID・承認者/理由をstateへ反映しcompleted_after_restart/completed |
| 候補版 | state更新済み、台帳確定前 | 同じ操作のstateと台帳を一致させる。別の承認者/理由を作らない |
| 不明な版・profile/hash違い・複数未完了・補償未確認 | 未完了のまま | 自動で確定しない。入口/runtime/台帳を調査する |

既存stateの旧承認情報をcompleted台帳から診断・移行する経路は未実装で、現配信へ自動適用していない。
既存のcontextなし/schema1行は、現在のregistry/入口の照合を行う互換経路で扱う。元artifactの準備時固定を遡及証明しない。
旧版へ戻す際にstateが候補へ進んでいて旧stateを保存していなければ、旧承認metadataを復元できないので拒否する。
新規schema2でidentityが欠けている場合、legacyへフォールバックしない。

## 検証の境界

単体試験は一時SQLiteとregistry/Compose/HTTPのdoubleで、状態・lock・拒否・補償・legacy・Capital gateを確認する。
[scripts/test_deployment_recovery_system.py](../scripts/test_deployment_recovery_system.py)は別のopt-in試験で、実Caddy container、通常FastAPI router、専用PostgreSQL、host上の通常CLI処理を使う。
Compose呼出しは専用Docker resourcesへ変換し、registryとmodel loaderは明示した合成doubleを使う。モデル資料は定数予測を表すJSONで、実MLflow/skopsの復元試験ではない。

ケースは同一ID4並行→元予測1行/異入力409、版切替後の元予測返却、専用PG制約による監査保存503/追加行なし、破損artifact/ready拒否、実SIGKILL3地点、API停止検知/再開、failed gate拒否、DBとモデル資料の対応snapshotの別volume復元。
SIGKILLは専用worker自身だけへ送る。3地点はcandidate_ready、proxy_switched、state書込後/台帳completed前。
復元時は5table全行と各モデル資料hashを比較し、別APIで同じrequest IDを再送して元の版2を返すことを確認する。

全container/network/volumeは固有labelを付け、所有を確認して回収する。既存bike-mlops-* containerのID一覧は前後一致を確認する。
元のDockerfileを再buildする試験ではない。既存API imageにtzdataがなかった初回失敗を保存し、lockと一致するhostのtzdata packageだけを一時workspaceへ複製した。既存imageやvenvへのインストールはしない。
本番imageの再現可能build、実Capital、実MLflow/skopsの対応backup/restoreと証拠producerは別の未了条件。

## 再実行

既存のlocal Unix-socket Docker daemonと、script冒頭の3 cached imageが必要。現在のstackを停止してポートを空ける手順ではない。
新しいevidence directoryを指定する。既存directoryは上書きせず拒否する。

```bash
cd /home/kenshin/work/claude_practice/261021
RUN_ID=$(.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)')
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/test_deployment_recovery_system.py \
  --run-disposable --evidence-dir "data/reports/deployment-recovery-$RUN_ID"
```

raw log/dumpはignored reportsへ残す。summaryのstatusだけでなくcleanup_complete、resources_remaining、protected_container_ids_unchangedも確認する。
CLOCK_REALTIME/MONOTONICの問題は未修復なので、fixtureの待機時間やunit test所要秒を運用p95・時計資格の合格証拠にしない。
通常sandboxではTestClient開始待ちを再現し、stack/timeoutを保存した。同じ試験はhost側で成功したが、sandbox内の根本サブシステムまでは特定していない。

## 残る条件

T17全体は未完了。通常CLIのbuild/artifact取得/待機の段階表示・経過/timeout時の未確定表示、API側での操作metadata提示は別作業。
CLIの現在のDocker subprocess全体timeoutは今回追加していない。harnessの5秒ready設定はfixture用で、通常の90秒を緩める変更ではない。
T07全体も、Capital契約の配信証拠producer、実MLflow/skopsと登録版を含む復元、現行Dockerfileからの隔離build/CI接続などが残る。
既存UCIの版1や実DBへ移行/障害注入していない。T13のHTTP部分書込/応答喪失、T16のCI原本保存は別に進める。


## 最終local検証と制限

最終対象は復旧24件、安全性5件が成功。実Docker受入の最終試行はv6で全ケース成功・cleanup残存0・既存container ID不変。
中間sourceの全361件は成功したが、最終production sourceの全364件は既存の一時SQLite承認試験1件でfuture拒否が自然再発した。
review生成から拒否までMONOTONICは389118413ns進み、review UTCは比較UTCより4170111µs先だった。今回変更していないdecision/evidence/test/diagnosticのhashも照合した。
全体成功とは記載せず、同じ理由でgreenになるまで再実行しない。新たな安全性5件はこの364件の後に追加し、単独成功を確認した（localで全369件通過とは主張しない）。
T13の厳密拒否は維持し、OS修復や時計資格をこの受入の成功で代替しない。CI確認は別途記録する。
