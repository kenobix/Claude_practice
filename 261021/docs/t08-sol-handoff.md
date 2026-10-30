# 次の依頼: T08 旧REAL schemaの移行・復元検証

推奨担当はGPT-6.1 Sol、P1/D3。T06b後のmain最新commitを基点にする。既存UCIへ新コードを適用する前に必要で、新しい実データを待たずに専用fixtureで検証できる。T03/T13の非決定的失敗の原因追跡、T07/T17の配信復旧、T02実証producerは別の残件として保つ。この文書の作成時点でT08は未着手。

次をそのまま依頼できる。

```text
GPT-6.1 Solとして、T08（P1/D3：PostgreSQL旧REAL→DOUBLE PRECISION移行の検証）を進めてください。
作業開始時のmain HEAD、AGENTS.md、261014/plan_mlops.md §11.6、261021/work_log.md、
docs/reviews/20261030-t06b-checks.json、src/bike_mlops/db.py、既存の移行・監査・再送・復元手順を確認してください。

既存UCI/CapitalのDB・volume・registry・reportには変更を加えず、使い捨てPostgreSQLに
旧schema相当（predictions.predictedがREAL）と旧予測・profile metadataを作ってください。
T06bのmonitor_windowsのREAL型を今回の移行対象へ広げないでください。
既存initializeの移行処理を確認し、必要な修正は根拠と再現例を示して最小限に行ってください。

受入条件:
1. 移行前のschema/全logical rowsとbinary32へ丸められた値を保存し、
   移行後のDOUBLE PRECISIONが保存済みbinary32値を正確に保持すること。
   元の丸め前binary64値が復元できたと説明しないこと。
2. metadata、request ID、入力hash、model/artifact/profile/feature契約、件数と他tableが保持されること。
   移行後の新規予測はbinary64を保持すること。
3. 同一request ID/同じ入力の再送が元の保存予測を返し、異入力は409相当で拒否すること。
   同じ版/契約と既存のidempotency hashを検証し、移行を理由に旧行を書き直さないこと。
4. 移行transaction途中の接続喪失/中断で部分schema・部分rowが残らず、再開も一貫すること。
   実装が外部atomicでない場合は限界と拒否/復旧手順を明示し、失敗を隠さないこと。
5. 専用backupを別の専用DBへrestoreし、型・値・metadata・件数・同一ID再送を照合すること。
   migration前/後のbackupを区別し、旧DBで新initializeを暗黙実行しないこと。
6. 使い捨て資源の所有label/ID・loopback接続・cleanupを検査すること。

コマンド・stdout/stderr・exit code・失敗もdata/reports/の新規pathに保存し、
source/log/hashと受入case対応を新しいdocs/reviewsのJSONへ記録してください。
対象test→必要な回帰test/CIを実行し、work_log.md、runbook、plan_mlops.mdを更新してください。
既知T03/T13失敗が再発したらraw証拠を保持し、timeout/future拒否を緩めて成功扱いにしないでください。
成功した節目はgit statusと全差分を確認し、T08対象pathだけをcommit/pushしてください。
無関係な未追跡pathを含めず、合成結果を実Capitalの配信証拠に流用しないでください。
最後に、達成条件・未確認事項・次の依頼文を示してください。
```

Lunaへ切り出す場合も、移行値・transaction・再送互換の判断はSolで行う。固定された受入結果の一覧・用語/リンク整理（T14）だけならLunaへ依頼できる。
