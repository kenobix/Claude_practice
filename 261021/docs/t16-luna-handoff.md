# T16: 合成CI証拠の保存と別processでの再計算

担当候補はGPT-6 Luna、Sol review前にcommit/pushしない。作業基点はこの文書を含むcommitをgit logで確認する。T13aのHTTP試験と、合成1,050件計測の証拠を混ぜない。本依頼では後者のT16を進める。

## 先に読むもの

- 計画書11.6のT16、最新work_log、.github/workflows/bike-mlops-contracts.yml。
- scripts/capital_system_harness.pyのrun_synthetic_system/main/cleanup。
- src/bike_mlops/capital_measurement_recheck.pyのrecheck_measurement_records。
- capital_operational_ledger.py、capital_operational_recovery.py、capital_request_journal.pyの読取契約。

既存CI artifactはlogとT08資料だけで、計測原本はharness cleanupで消える。f341e94のCIは1,050件、p95=33.469804999981534msと報告しているが、そのartifactだけでは要求別再計算ができない。値を今回の期待値に固定せず、新実行の原本から再計算する。

## 編集範囲

新規の合成証拠export/inspect scriptまたは専用module、そのunit test、既存harnessへの保存hook、workflowの明示allowlist、README/操作資料/計画/log/検証JSON。既存cleanup安全境界・MLflowタグ・配信gate・時刻検証・計測policy・DB schema/writer・lock・依存版を変更しない。必要ならSolへ戻す。実Capital/UCI DB/registry/配信やOS設定は操作しない。

## 固定する保存契約

1. 明示opt-inの合成harnessだけが、新しい証拠出力directoryを作る。既存pathを上書きしない。自分で作った一時workspaceを確認し、実project/外部rootを受け付けない。symlink、path traversal、読取中の変更、想定外file種別を拒否する。
2. 対象candidate/attemptをharnessの戻り値と保存済み台帳で特定する。globで「最新」を選ばない。原本plan、当該report、operational ledger JSON、当該T03 request journalを**byte変更せず**copyする。要求別latencyはreport.measurementsに1,050行あるため二重の独自CSVを正本にしない。必要なら派生CSVと明記し原本と行単位照合する。
3. archive名は`synthetic-measurement-evidence.zip`、中身は`manifest.json`と、明示した4原本（`payload/plan.json`、`payload/report.json`、`payload/operational-ledger.json`、`payload/request-journal.jsonl`）のみを基本allowlistとする。元relative pathをmanifestへ対応付ける。file名や実際のjournal descriptorは既存契約を読んで解決し、推測で補わない。manifestにはschema、synthetic_only、candidate/attempt、各原本SHA/bytes、source commit/dirty、検証の限界を書く。ZIP SHAは外側の独立descriptorへ保存する。
4. .env、harness.env、DSN/認証情報、token、DB dump、model binary、任意directory、元CSV、raw environment/commandlineをarchiveへ入れない。原本JSONに認証情報や外部pathが含まれるなら、削除して同じSHAの原本だと偽らずexportを拒否してSolへ戻す。公表するのは合成資料のみ。fixture用ランダムpasswordにも同じ禁止を適用する。
5. ZIP検証はextractallで行わない。重複member、絶対/../symlink/余分file、過大bytes、hash違い、欠落を拒否し、明示memberだけ読む。検証過程はDB/MLflowへ接続しない。
6. ZIPだけを渡した別processでmanifest→台帳のcandidate/attempt→plan/report SHA→journal SHA/要求順/1050応答→1,000 measured行のp95を照合する。既存recheck_measurement_recordsを使い、nearest-rankのceil(0.95*n)-1、warmup50除外、measured1000、エラー0、ID digest、reportと数値一致を確認する。既存journal verifierを使う際に必要なroot対応は一時展開先へ限定し、実root/global設定や元原本を書き換えない。新しい計測や速い結果の選別はしない。
7. これは保存値の数値・結合の検算で、DB監査・時計資格・実運用性能・認証済み独立承認を新たに証明しない。operational_gate=not_evaluated/deployment_permission=blockedを維持する。

## cleanupとCI

成功実行では原本の削除前にarchiveを確定し、別process検算を行い、どちらが失敗しても既存finallyのcleanupを必ず試す。export失敗を握り潰さず、cleanup失敗と元失敗を両方記録する。失敗attemptを成功archiveとして補完しない。harnessが失敗したときの不完全資料保存は別schema/未完了を明記し、基本成功archiveと混在させない。

CI artifactへ追加するpathはarchive、外側SHA descriptor、再計算結果だけを明示列挙する。reports/**等の再帰globは禁止。既存log/T08/T13 allowlistは保持する。pipefail/timeout/非0伝播を維持する。local検証後、Sol reviewでcommit/pushし、同commitのActions成功・ZIP digest/内容hash・別process再計算・全cleanupを確認して初めてT16完了とする。

## 受入テストと報告

正常1050行、warmup混入、latency改変、report/plan/journal/ledger/hash改変、別attempt、部分journal、欠落/余分/重複ZIP member、symlink/traversal、外部root、既存出力拒否、export失敗でもcleanup実施を検証する。timeout/SIGTERMは成功扱いにしない。まず新target、次に既存harness/measurement/journalの必要な回帰を実行し、コマンド・全試行・初回失敗・UTC/MONOTONIC・exit・log/hashを新しいignored reportsと公開検証索引へ残す。

可能な限り多くの独立した実装・負例検証・文書整合まで進める。source/schema/writer/gate/時計に判断が及ぶ変更や実DBの必要が出たら実行せず、その部分と理由をSolへ返す。終了時は全差分/statusを示し、Solへのreview依頼文を提示する。ローカル結果だけでCI成功と書かない。
