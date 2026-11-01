# GPT-6 Lunaへの次の依頼（T15）

T15 / P2 / D2。T06bとT08は専用PG受入をCIへ追加したが、既存monitor persistence scriptのCI接続はまだ残っている。次の依頼はこの固定範囲だけを対象とする。

```text
GPT-6 Lunaとして、T15（既存monitor persistence PostgreSQL試験のCI接続）を進めてください。
開始時のgit status/main HEAD、AGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.md、
.github/workflows/bike-mlops-contracts.yml、261021/scripts/test_monitor_persistence_postgres.pyを確認してください。

編集範囲はworkflow、READMEまたはrunbookのCI説明、work_log.md、plan_mlops.md、
docs/reviewsの新規T15検証JSONだけです。既存script/source/schema/writer/依存lockは変更しないでください。

既存のsynthetic-system jobへ、install/pip check成功後に独立実行するPG保存試験stepを追加し、
MLFLOW_DISABLE_AGENT_HINT=1、timeout --signal=TERM --kill-after=10 300、pipefail/teeを使って
data/reports/monitor-persistence-postgres-ci.logを保存してください。既存stepが失敗しても、
install済みでcancelされていなければこの検証を実行する既存のif条件に合わせます。
既存artifactの公開pathへこのlogだけを追加し、失敗をcontinue-on-errorで隠さないでください。

まず使い捨てlocal Dockerで既存scriptを実行し、旧行保全・独立run・unique衝突時rollback・
marker欠落/遅延commitと専用container cleanupを確認してください。
YAML parseと全run blockのbash -n、必要な対象回帰testを行い、全試行のstdout/stderr/exit/hashを新規logへ残してください。
本番/既存UCI・CapitalのDBやregistryは操作せず、合成結果を実候補の配信証拠にしないでください。

script自体の失敗、schema/依存/cleanup設計の変更、T03/T13の非決定的失敗が見つかったら、
条件を緩めず証拠を保存し、具体的なSolへの修正依頼文を残してください。
範囲内を完了したらSol review待ちとして差分・検証JSON・log/hashをまとめ、
commit/pushはSol review後に行ってください。無関係な未追跡pathを含めないでください。
最後にSolへ、未commit差分と検証結果をreviewしCI確認後commit/pushする依頼文を提示してください。
```
