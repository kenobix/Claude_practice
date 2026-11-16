# Capital計画・評価・runtime受入

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [製品完成に向けた開始条件（2026-11-14改訂）](#product-completion-policy)
- [Capital Bikeshare初回モデルの評価基準](#capital-bootstrap-gate)
- [T09：2026年10月原本の取得・構造と右端境界の照合](#t09-october-acquisition)
- [T09取得監査とT10開始手順](#t10-input-readiness)
- [T10：固定原本の正式snapshot・事前protocolのreview](#t10-snapshot-review)
- [Capital初回候補：実データの精度評価（2026-11-14）](#capital-first-evaluation)
- [PSI・直近誤差の調査と新データ評価](#monitor-investigation-and-next-data)
- [実候補の保存済み予測を使う監視診断（2026-11-14）](#capital-offline-monitoring)
- [Capital実候補の隔離runtime受入（2026-11-14）](#capital-runtime-acceptance)
- [実候補の運用測定保留と部分復元（2026-11-14）](#capital-operational-hold)
- [completed試行の追記専用reviewと適格性head（2026-11-15）](#operational-eligibility-review-20261115)
- [次のSol作業：同じ実候補の隔離runtime/producer受入](#capital-runtime-handoff)
- [T13a: 合成証拠を使うMLflow HTTP決定の障害受入](#capital-decision-http)
- [Capital配信証拠の束ね方と検証](#capital-deployment-evidence)

<a id="product-completion-policy"></a>

## product-completion-policy

<a id="product-completion-policy--製品完成に向けた開始条件（2026-11-14改訂）"></a>

### 製品完成に向けた開始条件（2026-11-14改訂）

ユーザーは時計研究の保留と、作業別の開始条件への変更を承認した。
この文書は、旧手順にある「全時計資格が完了するまで実fitも禁止」を置き換える。
固定済みのデータ・protocol・recipe・精度gateは変更しない。
時計原因・独立UTC/elapsed源選定・精密校正・全campaignは再開指示まで保留する。
過去の逆行、T03停止、tick11000、未確定の同期責任を解決済みとせず、資格statusも変更しない。

<a id="product-completion-policy--作業ごとの開始条件と停止条件"></a>

#### 作業ごとの開始条件と停止条件

| 作業 | 開始前に必要な確認 | 停止条件と主張できる範囲 |
|---|---|---|
| T10直前照合 | 47原本、CSV/manifest/protocolの固定SHA・schema・TZif、11 source/config/lockのbytes、Python 3.12/Linux x86_64・全lock版、legacy/現在の消費台帳、別実行の証跡を照合 | 不一致・既消費・別実行との競合・不明な出力があればfit前に停止。hashは真正性/全checkout未消費を証明しない。全原本再集計は不要 |
| T11 validation | T10成功、隔離したsource/依存/出力、単一の消費台帳、事前の実行計画保存。台帳startがfitより先 | 一度だけ固定recipeでfit。MAE厳密改善かつRMSE以下。不合格/中断/失敗は保存して停止。同じ窓の調整・自動retry・台帳の再作成禁止。PCの精密時計資格は不要。結果は履歴データ上の精度 |
| T11 final | 合格したvalidationの同じcandidate ID/artifact SHAと行別証拠を再計算、選択後の固定final窓、消費台帳の一致 | 再fitなし。一度だけ評価。不合格なら登録・後続採否を停止。label到着+3h、選択時点、DST、境界除外を維持。blindな将来試験とは呼ばない |
| T02/T12の監視・復旧 | 合格candidate/同じartifact、専用DB/registry/volume/port、training-only監視reference、現在の配信と分離、原本とmetadataの対応 | 欠落/不一致は証拠不足。合成試験を実producerへ転用しない。実データを使った隔離試験の証拠であって、現在の本番配信実績ではない |
| T12応答時間/timeout | 実行先identity、通常の同期状態の読取、短区間でUTCの順序と経過時計の正方向、strict future拒否/timeout/中断の既存回帰、同一candidate、凍結した測定plan | 新しい時計異常・負のelapsed・承認時刻矛盾・不完全要求/監査で停止。異常試行を合格まで繰り返さない。暖機50除外・逐次1000・error0・nearest-rank p95≤250ms。当該環境の未精密校正の実測のみ、本番SLOや精密秒の保証ではない |
| T13b採否 | 精度・監視・復旧・metadata・運用の全実producer証拠、同じcandidate/artifact/report SHA、理由付きreviewと別承認 | futureは従来どおり厳密拒否。clamp/許容幅/成功までretry禁止。欠けた条件はblocked/not_evaluated。今回の許可は現registryのeligible化や現配信変更を含まない |

履歴fitはPCの現在UTCからtrain/validation/finalの行を選ばない。固定したdataset-local時刻とラベル到着仮定で選ぶ。
そのため独立UTC校正を開始条件にしない。一方、実行の開始/終了UTCは未資格の観測値と明記し、
逆行や消費台帳の順序矛盾が判明した試行は保存してreview待ちにする。これを時計研究再開の指示とは解釈しない。
運用時計の短区間チェックは通常の実行前確認で、保留したdaemon/PHC/外部UTC調査を再開しない。

<a id="product-completion-policy--実行先と分離"></a>

#### 実行先と分離

優先先は[記録済みデスクトップ](../../261109/README.md)。記録は現在の接続・正常性の証明ではない。
接続先が提示されていなければIPやSSH鍵を探索せず、既知のlocal path/hostとの一致だけを確認する。
デスクトップに接続できない間も、履歴fitは現在環境の専用directory・専用venv・専用出力へ隔離して進められる。
その場合は旧hostであることを明記し、同環境の時計依存の運用測定は別途上記の開始条件を確認する。
同じネットワークで開始してよい。別PC/別ネットワークだけで時計の正常性や独立性を認定しない。

既存`.env`、実DB、実registry、配信状態、OS/service/task/log設定には触れない。
元sourceをcommitから別directoryへ展開し、既存venvをコピーする場合も元venvへinstallしない。
コピー先で全lock版/pip checkとsource SHAを照合する。原本は読取だけにし、既存protocolを再発行しない。
消費台帳を複数に分岐させない。隔離実行の台帳は開始前に既存local台帳とlegacyを照合し、
正式な保存先として計画に宣言し、終了後に元checkoutから読取参照できるpath/hashを残す。
未使用を装うために台帳を空にして試し直すことは禁止する。

<a id="product-completion-policy--デスクトップ側で必要な最小操作"></a>

#### デスクトップ側で必要な最小操作

このチャットが旧hostを実行している場合、デスクトップのVS Code Remote WSLでrepositoryを開き、
`hostname`、`pwd`、`git rev-parse HEAD`の結果と、この作業の実際の保存先を伝える。
SSH導入・ポート開放・OS変更は不要。実fitの前に最新の消費台帳と候補証拠を移し、
この文書と[固定snapshot](capital.md#t10-snapshot-review)に従って読取照合する。既消費なら再fitしない。

<a id="capital-bootstrap-gate"></a>

## capital-bootstrap-gate

<a id="capital-bootstrap-gate--capital-bikeshare初回モデルの評価基準"></a>

### Capital Bikeshare初回モデルの評価基準

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

更新日: 2026-10-30

ユーザーから3点の判断を委任されたため、以下を採用した。仕様上の確認待ちは解消したが、実データのprotocol凍結・評価・運用測定は未完了。実装済みの範囲は「実装状況」で区別し、判断前の結果をこの基準で合格扱いに変更しない。

| 判断対象 | 採用する基準 | 理由 |
|---|---|---|
| 予測精度 | 候補のMAEがbaselineを厳密に下回り、RMSEもbaseline以下 | 平均的な誤差を改善し、大きな誤りを重く数えるRMSEでも悪化を許さない。個々の最大誤差が改善する保証ではない。絶対値の業務目標がないため同じ行のbaselineと比較する。 |
| API運用目標 | 暖機後、逐次1,000要求でエラー0件、p95 250ms以下 | ローカル環境での学習用目標として採用する。250msは事前に選んだ工学上の目標で、過去の実測結果から作った閾値ではない。 |
| weekday/weekend・通勤時間帯 | 件数とMAE、baselineとの差を必ず記録し、個別gateは設けない | 区間ごとの業務上の許容誤差が未定義で、サンプル数にも差がある。個別の閾値を結果に合わせて追加しない。 |

判断根拠と差分レビューは[20261028-r06-delta-review.md](evidence.md#20261028-r06-delta-review)に記録する。

その後の実装・作業過程は[2026-10-30 checkpoint差分レビュー](evidence.md#20261030-checkpoint-review)を参照。最新の残課題・依存関係・難易度・Sol/Luna担当推奨は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)を正規の一覧とする。

同監査のA01/A02では、承認後のfinal report/plan SHA変更と負のp95を通常配信判定が受理すること、監視・復旧証拠なしでも合成approveが成立することを確認した。review/approveの実装は配信前条件の全面完成を意味しない。その後T01とT02のschema/拒否処理を実装した。[配信証拠bundle仕様](capital.md#capital-deployment-evidence)に従って11報告書本体と全hashを結合し、欠落・不一致を拒否する。実Capital候補のeligible化には、T04/T05/T06/T07/T08/T12/T17等での実証が引き続き必要である。

<a id="capital-bootstrap-gate--前提とデータの状態"></a>

#### 前提とデータの状態

- Capital Bikeshareは`capital-trip-starts-hourly-v1`、UCIは`uci-bike-sharing-cnt-dataset-local-hour`。初回Capitalモデルには同target incumbentがないため、比較対象は同targetのhistorical baselineにする。
- Capitalの評価済み期間（2025年上期validation、2025年7月〜2026年4月の後続評価）は新しいvalidation/後続評価に再利用しない。過去の期間を学習材料に含める場合も、未使用の評価期間が増えたとは扱わない。
- `timestamp + 3時間`は履歴再生用のラベル遅延仮定。月次履歴が実際に3時間で公開されることを意味しない。
- 2026-10-30に公式[system-data page](https://capitalbikeshare.com/system-data)とS3 archive objectを再確認した。検索結果のbucket index表示は202605までだったが、直接HTTP HEADで202609は200、202610は404 Not Foundと確認した。202606〜202609は取得済みで、ZIPとCSV memberを検査した。4 ZIPは`data/raw/capitalbikeshare/monthly/`にローカル保存し、各bytes/SHAは[`work_log.md`](../work_log.md)へ記録した。これらは公開ソース原本で、Gitには入れない。
- 月次archiveが隣月に始まるtripを含む実績がある。新snapshotには対象範囲・隣月bufferの採否と理由を事前記録する。9月末までの評価に10月bufferが必要と判断した場合は公開を待ち、結果を見て期間を短縮しない。snapshotへの収録を超える母集団の完全性は保証しない。
- 6〜9月だけの一時diagnostic aggregationは6/1〜9/30の2,928時間で欠測・timestamp重複0。さらに既存の5月archiveを含む5〜9月diagnosticも3,672時間で欠測・timestamp重複0、読込3,224,860行中、この5 archive内でID重複0だった。ただし202604 archiveと202610 archiveを含まず、9月末の重複/後続境界を確認できない。両CSV/manifestは`/tmp`の診断出力であり、candidate・validation・final評価に使わない。

<a id="capital-bootstrap-gate--評価窓の事前案"></a>

#### 評価窓の事前案

新しいCSV/manifestは別pathへ作成する。以下の日時は前回案を維持し、学習締切だけを実装の情報時点契約に合わせて明確化した。データと原本範囲を検証してからprotocolへ日時・件数・SHAを固定する。

| 用途 | 対象時刻（America/New_Yorkのnaive wall time） | 情報時点・想定件数 |
|---|---|---|
| 学習 | 2023-01-01以降、2026-06-01 00:00未満でラベル到着済みの行 | training cutoffは2026-06-01 00:00。+3時間仮定では最後の利用可能targetは2026-05-31 21:00。22時・23時の2行を学習から除く。 |
| validation | 2026-06-01 00:00以上、2026-08-01 00:00未満 | 未使用の2か月。想定1,464時間。 |
| 選択判断 | 2026-08-01 02:00以降 | validation最終targetのラベル到着後。 |
| 一度限りの後続評価 | 2026-08-01 03:00以上、2026-10-01 00:00未満 | 想定1,461時間。8月1日00〜02時は選択判断を待つ境界として評価対象外。 |

前回文書は学習末尾2行のラベルが6月1日02時に到着するとだけ書いており、00時に始まるvalidationより後のラベルを学習へ含めかねなかった。現在のprotocol builderは`training_cutoff <= validation_start`を要求するため、締切を00時に固定し2行を除外する。実際の学習・評価はまだ行っていない。

日時grid、DST除外、重複・欠損target、ラベル利用時点を入力契約で検査する。件数が想定と違えば期間を調整せず原因を調査する。ラグの欠測は学習開始境界等の既知の理由と想定外を区別し、母数付きで記録する。protocolの保存・重複窓検査と、実行開始時の消費台帳記録を候補fitより先に行う。

<a id="capital-bootstrap-gate--validationと後続評価の精度gate"></a>

#### validationと後続評価の精度gate

比較対象は現在の`HistoricalBaseline`。予測対象時刻の24時間前の実績を使い、欠けていれば学習期間だけの曜日・時刻平均、さらに学習期間の全体平均へフォールバックする。各要求時点で利用可能な同profileの観測だけを使う。

validationと後続評価の各期間で、候補とbaselineを同一行・同じ正解・同じラベル利用仮定で比較し、次を両方満たす場合のみその期間の精度gateを合格とする。

```text
candidate.mae < baseline.mae
candidate.rmse <= baseline.rmse
```

指標が欠落・非有限、件数や行集合が不一致なら不合格。MAE同値は不合格、RMSE同値は合格条件を満たす。後付けの許容幅は使わない。baseline MAEが0で厳密改善できない場合も合格とはしない。

weekday/weekend、朝7〜9時、夕方16〜18時は各区間の件数・候補MAE・baseline MAE・差を記録する。区間別の自動gateは置かないが、最終採否の手動レビューでは偏りを説明する。全体gate合格でも人が見送ることはできる。不合格を手動承認で合格へ変更する経路は作らない。

validationで選定したartifactはhashを固定し、後続評価で再fitしない。後続評価で不合格なら登録・昇格せず、調整後の候補には別の未使用期間を用意する。現在のUCI `quality_gate=passed`は従来のvalidation MAE条件を指す。新Capital基準はpolicy識別子とともに記録し、過去タグの意味を変更しない。

<a id="capital-bootstrap-gate--実装状況（2026-10-29更新）"></a>

#### 実装状況（2026-10-29更新）

- `data_tools.py protocol`は`--incumbent-version`を省略すると、incumbentを持たないprotocol schema 2を生成する。そこには上記gate policy本体、policy ID、policy SHA-256を固定する。versionを指定した既存経路はschema 1として後方互換で残している。
- 評価消費台帳はincumbentがないvalidation試行を`incumbent_artifact_sha256: null`で記録できる。baseline-only candidate trainerはMLflow Registryを参照せずfitし、validationの行別candidate/baseline予測・件数・row-key hash・policy ID/SHAを保存する。
- final-evaluation scriptは登録incumbentをロードせず、同じ選定artifactをhash検査して使う。validation証拠を行単位で再計算・照合した後に限りfinal窓を台帳へ開始記録し、final MAE/RMSE gateを別に算出する。最終報告にはsegment別件数/誤差差、validation/final/operationalの状態と、運用測定・人の承認がない間の`deployment_permission=blocked`を含める。
- 新gate関数は欠落・非有限・負値指標を`not_evaluable`として不合格にする。テストではMAEの同値拒否、RMSE同値許容、RMSE悪化拒否、baseline-onlyの行別証拠照合も確認した。
- `runtime init`、`deploy promote`、Capital APIのregistered-model loaderは、quality/validation/final-accuracy/operational各status、既知policy ID/SHA、`deployment_permission=eligible`を要求する。今回、後述の独立review・別人承認の記録と、exact model version/candidate/artifact/report hashが一致することも通常loader/runtime/promoteで検証するよう追加した。欠落/不合格ならdiagnosticやrollback指定でも通さず、UCIの既存gateは変更しない。したがって現時点でCapital版が登録されていても、新gateタグがないモデルは配信できない。
- 合成dataからimmutable candidate bundleを作り、baseline-only final-evaluation CLI全体を通すintegration testを追加した。registry loaderを失敗するmockにし、非呼び出し、final row artifact、gate/blocked permission report、ledger start/finishを確認する。これは実データ評価ではない。
- 運用測定はlocal endpoint・Capital profile/target/model/version/candidate ID/artifactを事前照合し、DB観測snapshotをlockした状態で50回の暖機と逐次1,000要求を送り、各応答の出典・DB監査を確認する。最新の隔離system testで、現行CLIからmeasurement runner・audit・numeric recheckまでを合成データで通し検証した。CLIは`--run-measurement --plan ...`を明示した場合に実行可能。測定処理完了・数値合格は運用gate合格と分け、独立証拠レビューが済むまでは`not_evaluated`、deploymentは`blocked`のままにする。
- reportには暖機を含む全1,050要求のrequest ID・対象時刻・status・latency・予測値・data-version hash・エラー、ID集合hash、p95/p50/max、API model/data/artifact identity、ホストOS/CPU/RAM/Python/package版、取得できたCapital stackのcontainer resource limits・image・`docker stats`・Docker engine版、`/ready`のDB疎通結果を保存する。別ホスト負荷は自動計測しないので手動記録が必要。p95はnearest-rank法で算出する。
- **staging経路の実装状況:** `scripts/runtime_tools.py register`は一致するimmutable candidate/final reportと固定bootstrap policyを検査してからMLflowに登録する。candidate検証ではvalidation/final gateを指標から再計算し、final row CSVのhash・件数・時刻・ラベル到着時刻・保存済み予測からのMAE/RMSE再計算を確認する。さらに完了した評価台帳の期間・protocol/candidate SHA・report SHAが一致することを要求する。version tagはvalidation/final accuracyを`passed`、operationalを`not_evaluated`、deployment permissionを`blocked`に固定し、selection/final report hashとcandidate IDを保存する。登録後に生成する`data/runtime-capital-staging/staging.env`は版・ID・raw/registered artifact hash・report hashだけを保持し、認証情報を含まない。`compose.capital-staging.yaml`のstaging APIは`127.0.0.1:8110`だけに公開し、Caddyから独立させ、prediction用DBも専用volumeへ分ける。MLflowは既存の隔離Capital MLflowを参照する。staging loaderはREADY状態、profile/target、固定policy、report/candidate/artifactのidentity、`deployment_permission=blocked`を確認する。運用benchmarkも候補IDをAPIの`/ready`・`/version`・全prediction responseで照合する。
- 2026-10-29、最新ソースと現行計測CLIを使う使い捨てCompose/MLflow/PostgreSQL/APIのsystem testを完了した。744観測seed、blocked登録の再実行、API ready・artifact load、offline/API prediction差0、retry同一応答・changed input 409、runtime init拒否、1,050要求と同数のDB監査照合、ledger-linked numeric recheckを確認し、資源をcleanupした。測定p95 103.258msは人工モデル・人工データの結果で、正式gateは`not_evaluated`、deploymentは`blocked`のまま。ログと前段のデバッグ結果は[`work_log.md`](../work_log.md)に記録した。実Caddy切替と実データのvalidation/final評価は未実施で、新評価窓は消費していない。
- 統合試験で、旧Docker imageがprofile設定を`/usr/local/lib/python3.12/configs/`へ探してAPIの起動に失敗したため、Dockerfileで`PYTHONPATH=/app/src`を指定した。修正前に作成したimageでは修正は反映されないため、次に起動するときは対象imageをbuildし直す。stagingの予測行にはcandidate IDを保存し、候補変更後も同じrequest IDの再送には元のIDを返す。旧予測行の候補IDは`null`として扱う。
- 候補証拠・CSV/manifest・training splitの行hash/件数・対象168時刻を検査する。measurement runnerでDB snapshotの再照合とlock、API応答と監査行の出典照合を接続し、合成PostgreSQLで通し検証した。未使用実データでの検証は別途必要。以前の合成benchmarkは最後の2時刻が学習締切時にラベル未到着であり、training split内の入力条件を満たした証拠には使えない。

<a id="capital-bootstrap-gate--api運用測定の固定条件"></a>

#### API運用測定の固定条件

精度gateとは別の運用gateを作る。対象はcandidate専用のstaging API入口`http://127.0.0.1:8110/predict`。モデル・DBが準備済みで、readyとprofile/model/candidate/artifact識別が確認できてから測定する。

- 暖機50要求を除外し、その後1,000要求を同時数1で逐次送信する。HTTP clientは1つで接続を再利用し、自動再試行は0回、各要求のtimeoutは10秒。
- 暖機で失敗した場合は準備失敗として記録し、その実行を合格扱いしない。
- 暖機を含む全要求に新規のrequest IDを付ける。同じIDの再送で保存済み結果を返す処理を計測対象にしない。benchmark IDで監査行を識別し、件数と保存先をreportへ残す。
- 入力は学習期間内の固定168時間の列を順番に繰り返す。対象週・順序・body生成規則を実行前に保存し、validation/後続期間は使わない。正解との精度計算はこのbenchmarkでは行わない。
- latencyは送信直前から応答body受信完了までの単調時計による経過時間。1,000件すべての計測値を保存し、p95はnearest-rank法（順位`ceil(0.95*N)`）で計算する。失敗した要求を除外・成功要求で置換しない。途中中断はincompleteで不合格。
- HTTP 200以外、接続失敗、timeout、不正JSON、異なるrequest/profile/model/artifact識別、非有限・負の予測をエラーに数える。単にHTTP 2xxであるだけでは成功としない。
- エラー0件かつp95 250ms以下で合格。CPU/RAM、Docker資源制限、OS/Python/依存とイメージ、model/data hash、DB開始状態、他負荷の有無、p50/p95/max、失敗数を記録する。条件変更時は別benchmarkとして扱う。

CLIには読み取り専用の入力確認モードと、frozen planに従う隔離staging測定モードがある。まだ未使用実データのcandidate/final reportがないため、現段階で実candidateの測定は行わない。次のコマンドは候補とデータを照合し、指定した168時間の各行が学習に使われ、各ラベルが学習cutoffまでに到着したことだけを確認する。APIリクエストは送信しない。モデル登録版、DB状態、測定時のデータ出典はこの入力確認だけでは保証しない。

```bash
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/runtime_tools.py benchmark \
  --candidate-id '<candidate reportに記録された64桁ID>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --start '2026-04-01 00:00:00' \
  --end '2026-04-08 00:00:00' --check-input-only
```

実測モードは、未使用データによるcandidate/final評価、blocked登録、staging DBのseed、staging APIのready、固定条件のplan作成がすべて終わった後に限り使用する。実行する場合は秘密値を表示・記録せずに読み込む。

```bash
set -a
. ./.env
set +a
export DATA_PROFILE_ID=capital-bikeshare
export DATABASE_URL="postgresql://bike_app:${APP_DB_PASSWORD}@127.0.0.1:55434/bikeops"
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001

.venv/bin/python scripts/runtime_tools.py benchmark \
  --run-measurement \
  --candidate-id '<candidate-id>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --plan 'data/evaluations/capital-bikeshare/operational-plans/<plan>.json'
```

runnerは専用のlocalhost PostgreSQL (port 55434)以外を拒否する。完了後の数値statusは独立証拠レビューと別であり、reviewが済むまでは運用gateをnot_evaluated、deploymentをblockedに保つ。

実候補のblocked登録とfinal reportが揃った後、運用測定の条件だけを先に凍結するコマンドを用意した。候補・final report・学習行・CSV/manifestを検証し、Capital MLflowの指定version/tagと、ダウンロードしたregistered artifactのハッシュを照合する。入力168時間、要求数、順序、タイムアウト、p95条件、registry接続先、モデル・データ・protocol・reportのハッシュ、取得可能な資源情報を新規JSONに保存する。予測要求は送信しない。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py plan \
  --candidate-id '<candidate-id>' --model-version '<登録版番号>' \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --start '2026-04-01 00:00:00' --end '2026-04-08 00:00:00' \
  --output 'data/evaluations/capital-bikeshare/operational-plans/<plan-name>.json'
```

計画ファイルの上書きは拒否し、内容のSHAを保持する。自己ハッシュは偶発的な変更の検出用であり、本人が内容とハッシュを両方書き換えた場合の真正性は証明しない。別途、最初の要求前にplanの内容とファイルのSHAを試行台帳に記録し、候補ごとの同時実行を防ぐ部品を追加した。途中終了や失敗後の再試行には、担当者・理由と次に使用するplanのSHAを記録する。記録済みのplanと異なるファイルによる再試行は拒否する。要求開始後に証拠を残せず中断した場合は証拠不足を記録する。`completed`は測定処理の正常終了時に初めて確定し、台帳内の状態だけでは運用gateや配信の合格証拠にならない。

台帳の内容は `.venv/bin/python scripts/runtime_tools.py attempt-ledger list` で確認できる。失敗・中断した試行の原因と部分report・監査記録を調査してから、再試行を認める場合に限り、`review-retry --candidate-id <候補ID> --operator <担当者> --reason <理由> --next-plan <planのパス>` を同じスクリプトに渡す。この操作は候補の配信承認ではない。ロックは同じファイルシステムとロック先を使う協調プロセス間に限られ、台帳を直接変更できる人による改変を防ぐものではない。

測定内部処理は試行IDとartifact SHAを指定し、各要求前のロック確認・台帳への要求開始記録・応答の出典確認・監査照合を行う。これらをplanの再照合、DBロック、測定、監査、reportに接続した。現行CLIから使い捨てCompose/MLflow/PostgreSQL/APIのsystem testを行い、合成1,050行の監査・数値再確認・resource captureまで検証した。台帳の`completed`は測定処理が完了した状態で、運用gateは別途`not_evaluated`、deploymentは`blocked`のまま。実candidateの評価は未実施。

試行開始後、台帳と同じplan・候補・学習window・CSV/manifest・登録version・登録artifactを再照合し、確認済みデータをメモリへ読み込む。登録先はplanに固定されたCapital MLflowで、取得前後の登録情報と、読込み前後のCSV・manifestも検査する。DBロック解放や台帳保存が失敗した際、台帳に採用されないreportファイルが残りうるため、reportファイルの存在だけで完了と判断しない。`.venv/bin/python scripts/runtime_tools.py inspect --candidate-id <候補ID>` は既存のロックを取得したうえで、台帳とplan/reportのSHA・IDを読み取り照合し、紐付いたreport、孤立ファイル、不足・破損、中断を列挙する。実行中やロックが存在しない場合は点検を拒否し、reportの採用や修復は行わない。部分証拠の回復と原因の確定は別途必要。

測定の失敗reportには、出典を確認できた応答のIDと件数、処理途中の要求IDを`partial_evidence`に保存する。処理途中の要求は送信されたか、または後からDBに保存されるかが不明なため、未送信や失敗確定とは扱わない。これらはメモリ上の情報から記録するため、強制終了や電源断のときに失われる場合がある。

失敗reportに処理途中の要求が残った場合、`DATA_PROFILE_ID=capital-bikeshare`と`APP_DB_PASSWORD`を環境変数に設定したうえで、`.venv/bin/python scripts/runtime_tools.py pending-audit --candidate-id <候補ID> --attempt-id <試行ID>`を実行すると、隔離staging DBのそのIDだけを読み取り専用で照会できる。見つからない場合は照会時点で未確認という意味であり、その後に記録される可能性を排除しない。見つかった場合も候補・版・artifact等の識別確認にとどまり、入力や予測の完全な監査、再送、合格判定、配信許可は行わない。使い捨てPostgreSQLで、別transactionによる記録前後の照会を検証した。実APIのtimeoutと遅延commitの再現は未完了。

保存された測定行を固定planと照合し、要求順・時刻・ID・件数・p95を再計算する独立部品を追加した。`.venv/bin/python scripts/runtime_tools.py recheck --candidate-id <候補ID> --attempt-id <試行ID>`は候補・台帳の既存ロックを取得し、台帳が参照するplanとreportのSHAを確認してから数値を再計算する。これは単体では記録された数値だけを点検し、入力出典、DB監査、retryの有無、資源、記録の真正性や配信可否を証明しない。運用report・入力出典・DB audit・資源情報とexact registry version/artifactを一緒に再照合してから証拠タグを記録するCLIを追加した。

測定が`completed`になり、独立した数値再確認が終わった後は、次で同じ候補・試行IDのreport hashと各statusをCapital MLflowのexact model versionへ記録できる。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py record-operational \
  --candidate-id '<候補ID>' --attempt-id '<完了した試行ID>'
```

このコマンドはledger/候補の既存lockを保持してfrozen plan・linked report SHA・全1,050要求のp95再計算・DB監査・input provenance・前後のhost/container resource context・MLflow version/artifact identityを照合する。同じ証拠の再実行は同じtagを確認して成功するが、別report/attemptで既存tagを上書きしない。tag書込み後も`operational_gate_status=not_evaluated`と`deployment_permission=blocked`を維持する。タグは証拠の参照であり、独立review、運用gate合格、理由付き手動承認、eligible化の代わりにはならない。合成system testで成功・冪等再実行・異なるhashの拒否を確認した。組織的な承認・監査サービスやローカルファイルを直接変更できる人への耐性は提供しない。

review/approveの前に、監視・復旧・復元・metadataの必須証拠を完成させ、[配信証拠bundleの保存CLI](capital.md#capital-deployment-evidence)を実行する。bundle保存はblockedを維持する。未実証の報告書をpassedとして作ってはいけない。その後のreview/approveは次の2段階で記録する。reviewerが候補・final report、凍結plan、measurement report、監査/再計算ログと復旧証拠を確認してから、意図した担当者・理由で記録する。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py decision review \
  --version '<exact model version>' --reviewer '<reviewer name>' \
  --decision passed --reason '<evidence reviewed and why the gate passes>'
```

reviewがpassedでも配信許可は`blocked`のまま。別のapproverが理由を添えて次のコマンドを実行したときだけ、registryの`deployment_permission`がeligibleになる。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py decision approve \
  --version '<exact model version>' --approver '<different approver name>' \
  --reason '<why this exact version is authorized>'
```

review/approvalはschema 2でexact version、candidate、registered artifact、11報告書のbundleと関連hashへ結び付き、UTC時刻・担当者・理由を含む決定レコードのSHAをtagに保存する。成功reviewにはoperational measurement=`passed`かつp95 250ms以下、出典・監査・再計算・資源context成功を必須にする。runtime loader、`runtime init`、`deploy promote`も同じ結び付きを検査する。reviewerとapproverは異なる名前でなければならない。これはローカル学習用の証跡であり、名前は利用者が自己申告する。GitHub/MLflowのIAM、電子署名、独立した組織監査を代替しない。現時点では実candidateがないため、このコマンドは実行していない。合成testの判定拒否・承認経路に加え、一時SQLite registry/file storeを使う実MLflow clientでbundle保存/取得・eligible遷移・保存元artifact破損拒否を確認した。HTTP経由の部分書込/応答喪失と実candidateの配信は未検証。

隔離system testは `.venv/bin/python scripts/system_harness.py run --run-synthetic-system` で実行する。人工データで候補を作り、使い捨てCompose projectへ登録・seed・API起動・計測・監査・数値再確認・exact version tag記録まで通す。タグの同値再実行、異なるreport hashの拒否、拒否後もblockedを維持することを検査する。ランダムなproject名・一時credential・所有labelを用い、自分が作ったcontainer/network/volume/imageだけを削除する。port 5001・8110・55433・55434が使用中なら既存サービスを止めずに失敗する。test成功は実データの精度やdeployment承認を示さない。Docker imageのbuildと依存取得が必要になる場合がある。

GitHub Actionsでは`.github/workflows/bike-mlops-contracts.yml`の`synthetic-system` jobが合成system testとPostgreSQL snapshot-lock testを実行し、ログを14日間のartifactとして保存する。最初の2 runはcleanup失敗を`pipefail`なしのpipelineが隠していた。修正前のrun `40876827423`はunit job成功、synthetic job失敗となり、偽の総合successを防いだ。保存ログでは合成測定・独立再確認まで成功した一方、Dockerは同じimage IDが複数repository名に参照されているためID指定の削除を拒否していた。単純にIDを重複除去するだけでは直らないため、ownership labelとrandom Compose project prefixを確認し、専用`repository:tag`だけを削除する処理へ修正した。commit `6079903`のGitHub run [`40879692956`](https://github.com/kenobix/Claude_practice/actions/runs/40879692956)では213 unit tests、synthetic system test、cleanup後のresource検査、PostgreSQL snapshot-lock testがすべて成功した。PostgreSQL testは非superuserの使い捨てDBで、観測更新の阻止、lock中のAPI予測/audit、source hash一致、行数/ID/audit/data/hashの不一致拒否、lock timeout、接続喪失時の拒否、context終了時のlock解放を確認した。artifact `bike-mlops-docker-integration-logs`（ID `12898819750`、SHA-256 `4e53e8e84e17c4d12e9d86ea7bf1f65251728468d65ea7c41908202223c6dbd2`）に合成・PostgreSQLログが保存されている。

tag-scoped cleanupを使う以前の版では、blocked synthetic model version 1の登録、744観測seed、API/offline prediction差0、1,050件の逐次要求とaudit、数値再確認、exact version evidence tagsの再実行/競合拒否まで通り、`cleanup_complete`を返した。p95 `127.520839 ms`は人工データ・この実行環境の値で、Capital実候補や本番SLOの合格ではない。さらにexact codeに、image参照名がランダムCompose project prefixで始まる場合だけ削除を認める安全確認を足した。このexact codeのlocal full runはpreflightでport `55433`の`EADDRINUSE`を検出し、Dockerを起動せず終了した。事後の読み取り照会でも該当ハーネスprojectのDocker resourceはなく、既存listenerの正体はこの環境から特定できなかった。既存サービスは停止していない。全213 tests、harness 23 tests、compileall、pip check、workflow YAML parse、diff checkが成功し、さらに同一commitのGitHub Actions run `40879692956`でfull synthetic pathとPostgreSQL testが成功した。synthetic candidate `bcd2842892b6423125a014003d8e79b3b6dd19120a2f4428919e035595ceeb6c`は744観測、1,050要求/audit行、offline/API差0、独立数値照合一致、p95 `26.522195 ms`。`cleanup_complete`後に専用projectのresourceが残らないことも検証した。これは人工fixtureとGitHub runnerの実行結果で、Capital実データの精度や本番SLOの達成を意味しない。正式gateは`not_evaluated`、deploymentは`blocked`のまま。snapshot-lock testは依存install/check後にsystem stepが失敗しても実行する条件を加えた。GitHub Actions固有schema lintは`actionlint`未導入のため未実施。

PostgreSQLの観測snapshot lock、writer競合、接続喪失の使い捨てDB試験は次でも実行できる。イメージ`postgres:17.6-bookworm`がローカルに必要で、テスト自身が一時containerを作成して最後に削除する。

```bash
.venv/bin/python scripts/storage_checks.py snapshot
```

CI jobではsystem testに続けてこのPostgreSQL試験も実行し、両方のログを同一artifactに保存する。

測定前にblocked registrationを作成し、生成された`data/runtime-capital-staging/staging.env`を使って、staging DBへのseed、staging API起動の順に行う。seedは検査済みCapital profileのCSV/manifestを使い、専用PostgreSQLのloopback port 55434へ記録する。

```bash
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py register \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json'

docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml \
  up -d postgres-staging

set -a
. ./.env
set +a
DATA_PROFILE_ID=capital-bikeshare MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py seed \
  --selection-report 'data/candidates/<candidate-id>/candidate-report.json' \
  --final-report 'data/reports/capital-bikeshare/<final-report>.json' \
  --model-version '<登録した版番号>'

docker compose -f compose.yaml -f compose.capital.yaml -f compose.capital-staging.yaml \
  up -d --build api-staging
curl -fsS http://127.0.0.1:8110/ready
```

登録先はlocalhostのport 5001にあるCapital用MLflowに限定する。精度gateが不合格、またはreport/artifact identityが一致しない場合は登録しない。staging loaderは配信許可を`blocked`のまま保ち、`runtime init`/`deploy promote`は全gateと明示的な承認を引き続き要求する。診断flagや環境変数でこれらの確認を迂回しない。benchmark reportは`data/reports/capital-bikeshare/`へ新しいファイルとして保存し、既存reportを上書きしない。

MLflowでは実験のtracking先とモデルregistry先を別々に設定できる。このstaging経路の登録、seed、loader、plan作成は、registry先も指定したtracking先と同一に固定する。異なる`MLFLOW_REGISTRY_URI`が環境にある場合も、別のregistryへ誤って登録・参照しないための設定である。

seedは候補・最終reportを再検証し、指定したMLflow版のtag、CSVとmanifestのSHA-256を候補に照合する。DBの既存観測は時刻・台数・利用可能時刻・データSHAまで比較し、余分な行や不一致があればseed全体を拒否する。PostgreSQLでは検査と挿入を同じトランザクション内でロックする。上記seedコマンドに`--verify-only`を付けると、観測を追加せずに全行が揃っていることも検査する。測定runnerは開始時と測定中にもsnapshotを照合・lockし、training splitとAPI由来特徴・監査行を検査する。これは合成system testで確認したが、実候補の証拠はまだない。

測定中のロックと出典照合に使う部品は`operational_validation.py`に追加済み。PostgreSQLで観測表の照合とロックを同じtransactionに保持し、候補のlagから期待hash・特徴量を求め、応答と監査行を検査する。失われた接続、重複IDや監査の過不足も拒否する。次の再現用スクリプトは一時PostgreSQLと合成モデルのFastAPIでこれらを試す。通常のMLflow・staging image・1,050件測定は含まない。

```bash
.venv/bin/python scripts/storage_checks.py snapshot
```

このスクリプトはUnix socket経由のローカルDockerと取得済み`postgres:17.6-bookworm` imageが必要。ランダム名、loopback自動port、使い捨てpasswordを使い、作成したcontainerを最後に停止・削除する。実測CLIとは別にlock/監査部品のfailure caseを試す補助scriptである。

計測中にCtrl-Cで止めても、送信済み要求を含むpartial reportを保存し、`completed=false`・運用gate failedとして扱う。完了しなかった計測結果を成功としてMLflowへ記録する経路はない。

この結果は当該PC・逐次要求の運用目標への適合を示す。同時負荷への耐性や本番SLOの達成を示すものではない。

<a id="capital-bootstrap-gate--残る作業と実行順序"></a>

#### 残る作業と実行順序

1. **R04/P0: 完了。** Python `tzdata==2026.5`のゾーンファイルを集計・manifest検査・APIで直接読み込み、OSのtimezone検索順に依存しない。manifest schema 3にゾーン名・package版・TZifバイト数・SHA-256を保存する。DST切替テストと新しいmanifestでの実データ再集計・hash検証を行った。詳細は[`data-profiles.md`](architecture.md#data-profiles)と`work_log.md`を参照。
2. **R06/P1:** incumbentなしbootstrap経路とcandidate/final精度gateを合成試験する。実装済み。ここで合格した合成結果を実データの合格根拠に使わない。
3. **R06/P1・CI合成system/DB検証完了:** run `40879692956`でblocked登録の冪等性、744行seed、artifact ready、offline/API一致、retry/409、runtime init拒否、1,050要求、DB監査、独立numeric recheck、exact version tags、conflict拒否、project-scoped cleanupを確認。p95 `26.522195ms`は人工fixture/GitHub runnerの数値に限る。snapshot-lock PostgreSQL testも合格し、観測writer阻止、監査/出典/件数/hashの拒否、lock timeout・接続喪失・解放を確認した。artifact ID `12898819750`。正式gateは`not_evaluated`、deploymentは`blocked`。実Caddy切替と実候補評価は未試験。
4. **R06/P1・合成検証済み/実データ前:** seed時・投入後のDB照合、training split/label cutoff/168時刻検査、測定中DB snapshot lock、API応答と監査出典照合を実装し、合成SQLite/PostgreSQLとsystem testで検証した。未使用実データから作る候補での再検証は未実施。
5. **R06/P1・review/approval経路実装・mock検証済み/実候補未検証:** `runtime_tools.py decision review`はexact candidate/version/artifact/reportを含むoperational evidence tagsを検査し、人のreview理由と内容SHAを固定してからoperational gateをpassedにする。reviewだけではblockedを維持する。`approve`は別の名前のapprover、理由、review hashの一致、accuracy/operational gateを要求し、全て有効な場合だけeligibleを最後に書く。通常model loader・runtime init・promoteも決定hashと正確なmodel versionを照合する。部分tag書込後もblockedのまま、同じ内容のretryで完了するmock failure-injection testを追加した。commit `dae897a`のGitHub run `40966168109`で221 testsとsynthetic system/PostgreSQL CIに成功した（[作業ログ](../work_log.md)）。実MLflowでのtag更新と実候補は未確認。名前は認証済みidentityではなく、強いIAM境界でもない。
6. **R01/R02/R06・原本取得済み/10月buffer待ち:** 202606〜202609 archiveはdownload/ZIP integrity/member/hash検査済み。6〜9月、5〜9月の一時aggregationはgrid/count diagnosticsとして成功したが、protocolへ使う全期間manifestではない。診断CSV/manifestは一時`/tmp`に生成して集計値を記録後に削除済み。202610はHTTP 404。202510 archiveに2025-09-30 startsが含まれる過去実績があり、月境界bufferが必要な可能性を無視できないため10月原本公開後に全archiveを別pathへ集計し、manifest schema 3・row accounting・DST/coverage・hashを監査する。既評価Q3/Q4/旧Capital windowは除外し、計画済みsplit/decision time/gateを固定した後にvalidationを一度行う。原本のbytes/SHA-256は[作業ログ](../work_log.md)に記録する。

7. **R05/R06/P1・実候補後:** 実candidateをblocked登録し、Capital隔離PostgreSQLへseed、staging APIを起動してartifact loading、DB分離、offline/API parity、通常deploy拒否を確認する。復帰したCLIで50 warmup/1,000 sequential benchmarkを実測し、report/hash/statusをexact model versionへ記録する。
8. **R07〜R10:** T04の学習参照/PSI v2接続は実装・合成検証済み（[仕様](monitoring.md#training-monitor-reference)）。T05の全監視受入・T06照合・実候補監視、障害/復旧、配信metadata、採否記録・デモを計画書に従って完了する。

最新070b033のCI `40966966672`ではcandidate `ad46f6f2ebbd984010153e613c418e9659a9fbb2fadc12ac4cfe554e8214deb0`のsynthetic-only測定を再現し、744行seed、1,050件要求/監査、offline/API差0、p95 31.070696ms、numeric recheck一致、cleanupとPostgreSQL snapshot-lock test成功を確認した。合成データなので実Capital精度・deployment承認を意味せず、gate=`not_evaluated`、permission=`blocked`を維持した。artifact ID `12938433488`のZIP SHAはGitHub digestと一致した。ただしartifactはsummary中心で要求別recordを含まないため、外部でのp95再計算用の保存をT16へ追加した。Pages run `40966966169`もsuccess。

追加のユーザー確認を開始条件にはしない。精度gate・運用gateの実装/証拠、artifact整合性、入力契約、復旧試験、理由付き手動承認が揃うまでは、Capital profileはmigration-onlyを維持する。

<a id="t09-october-acquisition"></a>

## t09-october-acquisition

<a id="t09-october-acquisition--t09：2026年10月原本の取得・構造と右端境界の照合"></a>

### T09：2026年10月原本の取得・構造と右端境界の照合

2026-11-06。前回の404から約22時間後、公式S3のHEADが200へ変化した。
[公式ZIP](https://s3.amazonaws.com/capitalbikeshare-data/202610-capitalbikeshare-tripdata.zip)を既存の
`scripts/data_tools.py prepare`の`acquire_archive()`で新規取得した。
**T09の取得・構造検査は完了。T10の正式集約/全manifest/protocol凍結、学習・採点はこの検査に含めない。**

<a id="t09-october-acquisition--原本の同一性"></a>

#### 原本の同一性

| 項目 | 確認値 |
|---|---|
| 公式HEADのDate | 2026-11-06 11:11:56 UTC（JST20:11） |
| Last-Modified | 2026-11-05 16:54:56 UTC |
| ZIP bytes | 32,848,117（HEADと取得後が一致） |
| SHA256 | `f57da7b7b93f95d3e14f4377369b1db2aeb2c5f6542d30a60ecd25f4507a69fe` |
| CSV member | `202610-capitalbikeshare-tripdata.csv`、1件 |
| CSV非圧縮bytes | 134,922,053 |
| CSV行数/列数 | 677,607行、13列 |
| 保管原本範囲 | 固定済みの202212〜202610、連続47件 |

取得先はgitignore下の`data/raw/capitalbikeshare/monthly/`。
既存ZIP/`.part`の上書きを拒否し、既存46件のfile名集合が変わらないことを確認した。
SHAは受領bytesの結合であり、HTTP Dateは公開状態の記録であって独立UTC校正の証拠ではない。
ZIPのCRC、member名重複なし、CSV1件、9月と同じ13列のheaderを照合した。
原本の再配布やtrip ID・station名・座標・個別rowの公開はしない。

<a id="t09-october-acquisition--右側2原本の品質・境界検査"></a>

#### 右側2原本の品質・境界検査

`ride_id/started_at/ended_at`をpandasで分割読取し、専用の使い捨てSQLiteでIDと開始hourを照合した。
続いて別processで標準`csv`/`datetime`/整数のnanosecond計算とID mapを使い、同じ原本SHAから再確認した。
後者はpandas/SQLを使わず、全rowの列数13・必須値・日時形式も検査した。

| 検査 | 202609 | 202610 |
|---|---:|---:|
| 全行数 | 676,808 | 677,607 |
| 必須値欠測/日時parse不正/終了が開始より前 | 各0 | 各0 |
| 原本内ID重複 | 0 | 0 |
| 開始が表示月より前の行 | 50 | 0 |
| 開始が翌月以降の行 | 0 | 0 |

2原本間の共有ID/開始hour不一致はいずれも0。
合計1,354,415行と開始時刻bounds、上表の検査値が独立parserでも一致した。
両原本のSHA/dev/inode/size/mtime/ctimeは検査前後で不変。使い捨てSQLiteは削除済み。

**9月原本には8月30日開始を含む50行があった。**
これは「file名の月＝全rowの開始月」という仮定を置けない具体例である。
既存の202212左buffer/202610右bufferを維持し、10月の越境0件を理由に右bufferを除外しない。
正式集計で全原本間のID重複・conflict/収支を検査する必要がある。
同じ開始hourのID重複は既存のhourly target契約に従って扱い、境界確認のためにrecipeや評価窓を変えない。

<a id="t09-october-acquisition--証拠と限界"></a>

#### 証拠と限界

[検証索引](reviews/foundation.json#/reviews/20261106-t09-checks.json)と`data/reports/20261106-t09/`に、
取得/主監査/独立照合のscript・command・終了値・log bytes/SHA・入力SHAを保存する。
公開HEADの原本は時計作業の`data/reports/20261106-clock-scheduler/t09-head-resume-v1.*`と結合する。
初期のschema previewはチャットのstdinコマンドと保存JSON、主要な取得/監査/検証は保存scriptとrunner記録を使う。
local UTC/MONOTONICは未資格である。2原本の検査だけで出版社の全母集団の完全性や、47件の正式manifest受入を宣言しない。

次は[固定済みT10手順](capital.md#t10-input-readiness)で、新pathへ正式集計し、schema3/全原本hash・DST/収支/coverageをreviewする。
初回protocolを固定し、消費履歴・時計資格・必須producer等の前提が揃うまでfit/採点/実採否へ進めない。
OS/時計設定、実DB・実registry・現在の配信、`.env`を変更していない。

<a id="t10-input-readiness"></a>

## t10-input-readiness

<a id="t10-input-readiness--t09取得監査とt10開始手順"></a>

### T09取得監査とT10開始手順

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

**2026-11-08の現在状態:** [T09の10月原本取得・構造/右端境界検査](capital.md#t09-october-acquisition)は完了。
固定した202212〜202610の47原本で正式CSV/manifest schema3を作り、全32,849 hourを独立照合した。
[正式snapshotと事前protocol](capital.md#t10-snapshot-review)は固定済み。train29,919/validation1,464/final1,461、境界5 hourは除外する。
原本/CSV/manifest/protocol/11 sourceのSHAを結合し、local消費台帳/legacy窓を読取確認した。実fit/採点は未実施。
SQL修正9147027の[CI570件・artifact/hash/cleanup](reviews/foundation.json#/reviews/20261108-t10-snapshot-checks.json)まで受入済み。時計資格・独立源/校正・修復/実受入は未達で、protocol保存はfit開始許可ではない。
[SQL切り分けのlocal索引](reviews/foundation.json#/reviews/20261106-t10-query-checks.json)は正式pair監査後/CI前のsnapshot。
初回意図中断と次の1800秒timeout、3回目exit0を別々の試行として保存し、上限や窓は変更していない。
以下の404/46of47/未集約等は当時の履歴として保持し、現在の公開待ち/未集約と解釈しない。

**891a420以降の更新:** 左側は202212を保守的な隣月bufferに採用し、公式原本を取得・202301との境界を監査した。
以下の開始手順を`202212`開始へ更新した。target/評価窓・recipeは変更していない。
追加の[検証索引](reviews/clock-history.json#/reviews/20261104-backend-boundary-checks.json)と、末尾の境界reviewを参照する。

**1e622dd以降のT09確認:** 2026-11-04 13:42:25UTCの公式S3 HEADも202610=404。
通常sandboxのDNS失敗を含む試行/終了値/hashは[追跡索引](reviews/clock-history.json#/reviews/20261104-followup-checks.json)へ保存した。
既存原本の追加取得・正式集計・protocol作成・fitは行わず、10月原本待ちを維持する。

2026-11-04、基点`d8ec164`。[今回の検証索引](reviews/clock-history.json#/reviews/20261104-clock-readiness-checks.json)。
**既存45 ZIPの完全性と既存手順を確認した。T09/T10の完了、protocol凍結、fit・採点はまだ行っていない。**
計画の[最新残件](../../261014/plan_mlops.md#remaining-work-latest)と
[固定済みの初回評価方針](capital.md#capital-bootstrap-gate)を優先する。

<a id="t10-input-readiness--今回確認したこと"></a>

#### 今回確認したこと

- 公式S3 objectへのHEADは2026-11-04 12:34UTCで202609=200/25,848,624 bytes、202610=404。
  初回の通常sandboxはDNS接続失敗でexit1。外部接続可能な読取試行と分けて保存した。
- 手元の202301〜202609は連続45件、合計842,692,154 bytes。
  41件は既存manifest、6〜9月の4件は以前のwork_log記録のbytes/SHAと一致した。
  全ZIPのCRC、CSV member1件、member名重複なし、ride_id/started_at/ended_atのheaderを確認した。
  読取前後のdev/inode/size/mtimeも不変。CSV本文の再集計や精度の計算はしていない。
- 202604原本は存在する。過去の一時診断が4月を含まなかったことを「4月原本がない」と解釈しない。
- 既存legacy registryと提案validation/final窓は非重複。現在の既定消費台帳JSONは不存在。
  残っているlock fileを削除していない。JSON不在だけでは、他checkoutを含む過去の未消費を証明できない。
- 既存aggregation CLIのhelpは成功。protocol CLIはprofile未設定で拒否し、
  `DATA_PROFILE_ID=capital-bikeshare`を明示した別試行のhelpが成功した。拒否条件を変更していない。

ZIP検査は、行ごとの重複・時刻異常・月境界・DST・出版社の欠落を監査したことにはならない。
それらは10月原本到着後の正式snapshotで検査する。

<a id="t10-input-readiness--開始時の停止条件と実施順"></a>

#### 開始時の停止条件と実施順

1. **T09:** 10月objectが公開されてから既存`acquire_archive()`で取得し、取得元・日時・bytes/SHA・ZIP/CSV schemaを新規logへ保存する。
   HEAD200だけで完了にせず、既存45原本と追加した202212 bufferとの組合せを固定する。原本の差替えやschema差があれば保存してSol判断へ戻す。
2. **T10の境界review:** 原本範囲は202212〜202610、対象target窓は2023-01-01〜2026-10-01未満。
   12月を左側buffer、10月を右側bufferとして採用する。採否理由と限界は末尾を参照する。
   月名の連続やmin/maxだけで全tripの完全性を宣言しない。追加の原本範囲変更が必要なら取得・監査へ戻し、評価窓は短縮しない。
3. **新pathへ集計:** 以下のコマンド案を既存原本・buffer review後に使う。古いdefaultの202604終了や、既存CSVを使わない。
   この文書内のblockは今回実行していない。集計が途中で失敗した場合もlog/途中状態を保持し、別の期間へ自動retryしない。

```bash
cd /home/kenshin/work/claude_practice/261021
TASK_T10_DIR="data/derived/capital-bootstrap-$(.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)')"
.venv/bin/python scripts/data_tools.py prepare \
  --archive-start 202212 --archive-end 202610 \
  --data-start '2023-01-01 00:00:00' \
  --data-end-exclusive '2026-10-01 00:00:00' \
  --output-csv "$TASK_T10_DIR/hourly.csv" \
  --output-manifest "$TASK_T10_DIR/manifest.json"
```

4. **正式manifest review:** schema3、commit-marker相当のCSV/manifest pair、全原本hash、TZif identity、row収支、
   重複IDと値不一致、時刻coverage、DST除外・欠測理由を監査する。旧schema1/2を新protocolへ流用しない。
   想定件数と違えば原因を調べ、合格する件数へ窓を変更しない。bufferの根拠を含めた全snapshotを保存する。
5. **protocolの固定:** profileを明示し、Capital初回は`--incumbent-version`を省く。
   UCI版3をCapital incumbentへ代用しない。以下は前段のreview後のコマンド案で、まだ実行していない。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_MODEL_NAME=bike-demand-capital \
FEATURE_CONTRACT_VERSION=bike-demand-features-v1 \
MLFLOW_DISABLE_AGENT_HINT=1 \
.venv/bin/python scripts/data_tools.py protocol \
  --csv "$TASK_T10_DIR/hourly.csv" \
  --source-manifest "$TASK_T10_DIR/manifest.json" \
  --training-cutoff '2026-06-01 00:00:00' \
  --validation-start '2026-06-01 00:00:00' \
  --validation-end '2026-08-01 00:00:00' \
  --evaluation-start '2026-08-01 03:00:00' \
  --evaluation-end '2026-10-01 00:00:00' \
  --output "$TASK_T10_DIR/protocol.json"
```

`TASK_T10_DIR`は同じshellで保持する。新しいshellでは、採取logの実際のdirectoryを指定し直す。
初期profileのdefault入力は古い`capitalbikeshare-next`なので、後続CLIでも新pathを明示する。
この準備では`.env`・profile default・DB・registry・配信を変更しない。

<a id="t10-input-readiness--凍結時に確認する値"></a>

#### 凍結時に確認する値

| 項目 | 維持する条件 |
|---|---|
| training cutoff | 2026-06-01 00時。+3時間遅延仮定で最終利用可能targetは5月31日21時、22/23時を含めない。学習件数は正式CSVから確定 |
| validation | 6月1日00時〜8月1日00時未満、想定1464時間 |
| 選択判断 | validation最終targetのラベル到着は8月1日02時。全ラベル利用可能後に選択 |
| final | 8月1日03時〜10月1日00時未満、想定1461時間。00〜02時は境界として除外 |
| recipe | 現行HGB Pipeline、learning_rate0.08 / max_iter160 / max_leaf_nodes31 / l2_regularization1.0 / early_stopping false / random_state17。source snapshotと実際のprotocolにも結合 |
| 精度gate | 同一行baselineにMAE厳密改善かつRMSE以下。segmentは件数付き診断のみ。policy本体/ID/SHAを固定 |
| 監視 | training-only referenceと現行monitor policyを凍結。後続分布でbins/閾値を選び直さない |
| 消費 | 保存時の重複拒否を確認し、fit直前に消費台帳の開始記録。失敗・中断した窓も無断再利用しない |

入力準備時の1464/1461は日時算術による想定だった。2026-11-08には正式CSV/manifestから29,919/1,464/1,461を固定し、標準datetimeだけの別processでも照合した。
protocol作成だけでT11や運用合格を開始しない。[改訂した作業別の条件](capital.md#product-completion-policy)を満たし、
T10のレビューが完了してから一度だけcandidate fitへ進む。validation不合格時は同じ窓で調整し直さない。

時計資格の前提は[修復後の受入設計v1](clock-operations.md#clock-qualification-plan)を参照する。
設計と合成算術の検算は資格の合格ではない。採取器/外部UTC比較の実装・修復許可・実受入は未完了であり、
この設計追加によってT10以降の停止条件が満たされたとは扱わない。

<a id="t10-input-readiness--891a420以降の左端buffer-review（2026-11-04）"></a>

#### 891a420以降の左端buffer review（2026-11-04）

公式[system-data説明](https://capitalbikeshare.com/system-data)は月次公開と開始/終了日時を説明するが、
すべてのrowが`started_at`の年月で厳密にpartitionされることを保証していない。
以前に202510原本へ9月開始tripが含まれた記録もある。
そこで、**境界のrowを数える前に**原本範囲を202212〜202610とする保守的な判断を`boundary-design.json`へ保存した。
結果が0でも12月を後から除外しない。1か月bufferで公開母集団全体の完全性や最大遅延を保証するものではない。

- 202212を既存`acquire_archive()`で取得。6,207,590 bytes、SHA
  `f3044ca2af1e1688c3f305e5806ac256d3c8b327c63726e82523a954cb8ffc97`。
  取得時刻は2026-11-04 13:16:50UTC（未資格のlocal時計）。ZIP CRC/CSV memberを確認した。
- 202212の175,233行、202301の204,077行を`ride_id/started_at/ended_at`だけで監査。
  必須欠測、各file内ID重複、両file間の共有ID、開始年月の越境はいずれも0。
  read前後のstat不変、202301のSHAは前回索引と一致。別processの標準`csv` parserでも件数・ID集合・時刻boundsを照合した。
- これは左端2fileの入力監査。原本45件の全件再検査、全行の品質保証、hourly targetの再集約、
  validation/finalの採点、消費台帳の開始ではない。trip IDを公開log/derived CSVへ出力していない。
- 既存45件に今回1件を追加し、保管原本は46件になった。全範囲案47件のうち202610は13:15:46UTCの公式HEADで404。
  今回も10月待ちを維持し、正式schema3 manifest/全原本snapshot/消費確認/protocol凍結は未実施。
- 既存の合成pipeline回帰8件で、連続archive、月をまたぐID重複排除、conflict拒否、row収支/DST/hash、既存pair保護を確認した。
  最初はrepo rootから起動して`ModuleNotFoundError: scripts`となった。`261021`をcwdにした別試行で全8件成功。
  source・import条件・検証基準を変えて通したものではない。

今回の12月原本の開始時刻はすべてtarget開始より前なので出力targetへ含めず、学習開始を2022年へ延ばさない。
ラグの初期欠測を消すための追加学習データにも使わない。6月/8月の独立窓とlabel cutoffも維持する。

<a id="t10-snapshot-review"></a>

## t10-snapshot-review

<a id="t10-snapshot-review--t10：固定原本の正式snapshot・事前protocolのreview"></a>

### T10：固定原本の正式snapshot・事前protocolのreview

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

2026-11-08更新。[T09の取得](capital.md#t09-october-acquisition)後、固定済みの47原本で正式CSV/manifestを独立照合し、事前protocolを固定した。
学習・採点・candidate作成は開始していない。SQL改善9147027の[CI・正式pair/protocol索引](reviews/foundation.json#/reviews/20261108-t10-snapshot-checks.json)まで受入済み。
合成CI、実原本のデータ検査、実候補の学習/採点、実時計資格を別々に扱う。

<a id="t10-snapshot-review--変えない入力と判断条件"></a>

#### 変えない入力と判断条件

- 原本：202212〜202610の47 ZIP、合計881,747,861 bytes。各SHAは過去の取得記録と照合した。
  12月/10月のbufferを結果に応じて除外しない。
- target：2023-01-01 00時〜2026-10-01 00時未満。naiveなAmerica/New_Yorkの壁時計を使う既存契約。
  rawの文字列にUTC offsetが付いているという意味ではない。
- training cutoff：2026-06-01 00時。ラベル3時間遅延は測定済みSLAではなく実験仮定。
- validation：6月1日〜8月1日未満、final：8月1日03時〜10月1日未満。
  validation全ラベル到着後の選定時点は8月1日02時。境界3時間をfinalへ混ぜない。
- recipe：現行HGB Pipeline、learning_rate 0.08/max_iter 160/max_leaf_nodes 31/l2 1.0/early_stopping false/random_state 17。
  同一行baselineへMAE厳密改善かつRMSE以下。segmentは件数付き診断、運用gateは別の証拠を必要とする。
- 初回Capitalのincumbentは無し。UCI版3を代用せず、既評価のQ3/Q4・旧Capital期間を選定/最終評価へ再利用しない。

<a id="t10-snapshot-review--原本から独立に再計算したこと"></a>

#### 原本から独立に再計算したこと

既存producerとは別processで、標準CSV parserを使い全22,925,436行の構造・必須ID/開始日時・月別boundsを再計数した。
さらにpandas/SQLiteを使わず、privateな200,000行単位の外部ソートとheap mergeで全IDを照合した。
同じIDをmonth/hour順に並べ、最初の1件だけ数え、同IDの開始hour不一致は拒否する。
比較は既存の開始hour契約であり、全列が同じかを判定したものではない。

| 独立計算の項目 | 値 |
|---|---:|
| 元の行数 | 22,925,436 |
| 重複行 | 205（202406に141、202605に64） |
| unique ID | 22,925,231 |
| 固定window外のunique trip | 852,840 |
| DST除外のunique trip | 1,568 |
| targetへ数えるtrip | 22,070,823 |
| 有効なnaive hour | 32,849 |

重複0を仮定したraw histogramを最終CSVと同一視しない。
実際に205件があったため、重複除去後のhour histogramを作り、producerの完成後に全hour/月別収支と照合する。
ソート用115 fileは全てclose/削除済み。ID/個別rowは公開せず、histogramもgitignore下で保持する。

DSTは同じpinned TZif bytesを読み、標準`datetime`のfold/UTC round-trip法で独立にgridを作った。
2023/2024/2025年11月の重複01時3件と、2023/2024/2025/2026年3月の存在しない02時4件を除外する。
これは日付の計算であり、OSの時計や独立UTC源を観測・校正する処理ではない。

<a id="t10-snapshot-review--初回中断と集計処理の改善"></a>

#### 初回中断と集計処理の改善

最初の出力先は`capital-bootstrap-c66c8888e4c243d7ad88d527b1899c20`。
長い行ごとの時刻変換を改善するため、完了/timeout前に意図的にSIGINTした。
PID・開始tick・exe・namespace・exact argv・専用lockを照合した自分のprocessだけを対象にし、
終了後にprocess/temp/lock消滅とCSV/manifest不存在を確認した。
child `-2`、wrapper `254`を保持し、成功や自然T03再発へ読み替えない。
中断時のstackはCSV parse内で、Timestamp変換が全処理時間の原因と証明したものではない。

ID dedup/conflict照合を済ませた各chunkについて、同hourの件数を束ねて加算するようにした。
target・範囲外・DST全てで重みを保ち、schema/出力列/zero-fill/ID基準/期間は変えない。
合成90,000行では両計算の収支が一致し、Timestamp構築は90,000回対6回だった。
これは処理回数の比較であり、物理的な速度倍率やSLOの実測ではない。
spring/fall・複数trip・重複を含む9件、既存protocol10件、独立sort helper5件が成功した。

2回目の出力先は`capital-bootstrap-f5daf2e4114e4788b8c77f357249422f`。
同じ1800秒の上限を使い、専用TMPDIRのlease/cleanupを残す。旧attemptの出力を再利用しない。
開始時はsource未commit差分を含む36b9306だったことをplanに明記し、保存bytesのSHAを後の実装bae010aへ結合する。
[local索引](reviews/foundation.json#/reviews/20261106-t10-grouping-checks.json)はCI/実pair確認前のsnapshotとして保持する。
実装bae010aは[CI42214102466](https://github.com/kenobix/Claude_practice/actions/runs/42214102466)の全569件/全systemで成功し、
[CI索引](reviews/foundation.json#/reviews/20261106-t10-grouping-ci-checks.json)で16 artifact/hash/archive-only/cleanupも照合した。
この成功は合成契約の受入であり、実47原本のsnapshot/実候補採否の受入ではない。

<a id="t10-snapshot-review--sql計画の退化と正式pairの受入"></a>

#### SQL計画の退化と正式pairの受入

重み付き加算だけの2回目は1800秒/exit124で終了し、CSV/manifestは存在しなかった。
専用tempは親が回収した。残った専用build lockも、終了値・exact argvのprocess不在・lockのstat/内容を照合して保存後に回収した。
失敗結果を成功に書き換えず、新しい出力先だけで3回目を行った。

同じDDLの使い捨てDBで、従来JOINが蓄積済みID全体を走査していた。
current10行を固定して履歴1,000→100,000行にするとSQLite VM命令数が4,012→400,012へ増加した。
currentから履歴の主キーを検索するEXISTSでは119→119。同hour再送許容/異hour拒否を保つ回帰は修正前FAIL、修正後10件成功、既存protocol10件も成功。
命令数とquery planによる機構の切り分けであり、実時計で測った速度倍率や過去T03の原因確定ではない。
[local索引](reviews/foundation.json#/reviews/20261106-t10-query-checks.json)へ負例・失敗・source bytes・原本hashを保存した。

3回目は同じ47原本/窓/1800秒上限でexit0。実行開始時は1bf9584+未commit差分で、保存script SHAを後の実装9147027とbyte照合する。
正式出力は`data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/`。
2026-11-08にschema3 validatorによる47 ZIP SHA/sizeとstat・月別min/max/収支・pinned TZifを照合し、
独立した外部sort/merge後の全hour histogramとCSVの完全一致を確認した。専用temp/lockも回収済み。
7つの欠けhourは秋の曖昧3+春の存在しない4であり、ゼロを挿入しない。その他32,849 hourは既存の有効gridと一致する。

<a id="t10-snapshot-review--固定した事前protocolと確認場所"></a>

#### 固定した事前protocolと確認場所

正式folderの`hourly.csv`、`manifest.json`、`protocol.json`はGit対象外で保存する。原本の再配布やMLflowへの登録はしていない。

| 保存物 | SHA-256 |
|---|---|
| hourly.csv | `df216153f825904d3cc06f4b20d4be9f3de8e9270b971ea6a11590b13dea87c0` |
| manifest.json（file bytes） | `721ee82537ddc4118f222b395d9b671e5d20a20b73cbdf84d5dad6b057b7edce` |
| protocol.json（file bytes） | `6cfb89a6a226209e2139abb64d40f9b3f7cdd72d781501d886c51ac18f59fcda` |
| protocol_sha256（自身のhash欄を除いた正規化本文） | `925fc86ff297bba922318c45bff95623e38471a09cec8c365f235ed84f112714` |

| 区分 | 件数 / 境界 |
|---|---|
| train | 29,919。最終targetは2026-05-31 21時、そのラベルが6月1日00時に到着する仮定 |
| validation | 1,464。6月1日00時〜8月1日00時未満。最後のラベル到着後、8月1日02時に選択 |
| reserved final | 1,461。8月1日03時〜10月1日00時未満。選択に使わず一度だけの同一candidate評価へ予約する設計 |
| 境界として使用しないhour | 5。5月31日22/23時と8月1日00/01/02時。訓練/validation/final間の重複は0 |

11 source/config/lockのbytesを実装9147027と照合してから、既存CLIでbootstrap protocolを保存した。
`incumbent`はnull。UCI版3のモデルを読み込まず、Capital初回は履歴baselineと比較する固定方針を維持する。
MAE厳密改善かつRMSE以下、segment診断のみ、training-only monitor policy、固定recipeは変更しない。
別processの標準CSV/datetime/hash計算でも、期間の件数/label到着/境界5時間/3 splitの非重複とpolicy/hashを照合した。
監視policyのdigestは末尾LFを含み、protocol/精度policyは含まない。最初の独立検算のLF見落としFAILもログに残した。

確認用の読取だけなら、project rootで次を実行する。

```bash
.venv/bin/python -m json.tool < data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/protocol.json
```

生logと実行command/終了値/UTC・MONOTONIC/hashは`data/reports/20261106-t10/`にある。
`aggregate-v1`/`aggregate-v2`/`aggregate-v3`、`formal-audit-v3`、`freeze-protocol-v3`、`independent-protocol-audit-v1/v2`を区別する。
時刻は未資格のlocal値。protocolの`created_at_utc`も真正性や実時計資格の根拠にしない。
MLflowサイトに新しい学習run/Capitalモデルはまだないため、そこで新しい精度結果を探す段階ではない。
今回見た原本集計値を隠さず`historic_source_aggregate_statistics_were_seen=true`を維持し、blindな将来試験と呼ばない。

<a id="t10-snapshot-review--実fit前に維持する停止条件"></a>

#### 実fit前に維持する停止条件

producerのschema3 manifest、全47 input SHA/stat、CSV/manifest pair、全hourの独立histogram一致、
月別unique/duplicate・範囲外/DSTの収支を確認してprotocolを新pathへ保存済み。
legacy/default消費台帳の重複拒否を維持し、保存しても消費開始や実fitの許可とは扱わない。
他checkoutも含む未消費の保証や実行真正性は、local hashの一致だけでは得られない。

原本fileの連続/min/maxは出版社の欠落ゼロを証明しない。
現在の開始条件は[作業別方針](capital.md#product-completion-policy)へ変更した。履歴fit/精度評価に全時計資格を要求しない。運用測定・採否にはそれぞれの実証が必要。
確認できた範囲と残件は[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を正規の一覧とする。

実装9147027/[CI42447659584](https://github.com/kenobix/Claude_practice/actions/runs/42447659584)の全570件/対象pipeline10件は成功した。
全system/16 artifactのhash・archive-only検算・cleanupも照合した。CIで本47原本を集約したという意味ではない。
正式pair/protocolの独立照合はlocalの保存原本で、合成CI・実時計資格・実候補性能の証拠と分ける。
公開索引にはpath/size/SHA/コマンド/終了値と限界を残し、raw trip ID・ZIP原本・実DBや.envは含めない。
後続の文書commitを、先行9147027のCIで直接試験したとは呼ばない。

<a id="capital-first-evaluation"></a>

## capital-first-evaluation

<a id="capital-first-evaluation--capital初回候補：実データの精度評価（2026-11-14）"></a>

### Capital初回候補：実データの精度評価（2026-11-14）

固定した47原本のsnapshot/protocolを変えず、隔離したsource/venvでvalidationを1回、
その合格後に同じskops artifactを再学習せずfinalを1回評価した。**両精度gateは合格、配信許可はblocked**。
[開始条件の改訂](capital.md#product-completion-policy)、[検証索引](reviews/capital.json#/reviews/20261114-capital-evaluation-checks.json)を参照。

| 期間 | 行数 | 候補MAE | baseline MAE | 候補RMSE | baseline RMSE |
|---|---:|---:|---:|---:|---:|
| validation：2026-06-01〜08-01未満 | 1,464 | 137.392476 | 226.003415 | 237.549095 | 361.975333 |
| final：2026-08-01 03時〜10-01未満 | 1,461 | 143.693098 | 228.689254 | 247.608899 | 371.052215 |

単位は1時間あたりのtrip開始件数。baselineは24時間前の実績、欠測時には訓練行だけの曜日/時刻平均等へfallbackする固定実装。
同じ行・同じ正解でMAE厳密改善かつRMSE以下の条件を満たす。曜日/週末/通勤帯は件数付き診断のみ。
finalの夕方通勤帯MAEは候補313.931412、baseline411.032787（183行）。全体より誤差が大きく、個別業務目標を満たすと宣言しない。
精度改善は現在の需要・本番SLO・事業効果・時計資格の証明ではない。

<a id="capital-first-evaluation--どこに保存したか"></a>

#### どこに保存したか

repository rootからのpath。全実体はGit対象外で、現MLflowへ登録していないため既存localhost UIには新runは出ない。

- 専用root：`261021/data/reports/20261114-product-resume/`
- source：同rootの`project/`。commit `767760d04fcb3a20659d4f7a6d5188fb9b8de65c`から298追跡fileをコピーし、`.env`を含めない。
- venv：同rootの`venv/`。元venvコピー後、欠けたlock指定build tools2件だけをコピー先へinstall。105指定版とpip check成功。
- 候補：`project/data/candidates/e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b/`
- final：`project/data/reports/capital-bikeshare/final-evaluation.json`、行別の`final-evaluation.predictions.csv`
- **単一の正式消費台帳**：`261021/data/evaluations/consumption-ledger.json`。専用projectの`data/evaluations`はここへの参照。両窓completed、各1 attempt。
- CSV/manifest/protocolと原本は元の固定pathを読取参照。再集計・再発行・原本変更はしていない。

候補artifact SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
candidate report SHA：`74c0417ba661fd8b32d771349df092760fc7a7f8f3caf1d86b879df0031653cc`。
final report SHA：`8312ed9f22d9f93c1d55f736369ad5f2e66839626297b160800b5a49e9860893`。

<a id="capital-first-evaluation--検証と限界"></a>

#### 検証と限界

正式CLIからledger開始→fit→immutable bundle→ledger完了を実行。
final CLIはartifact/validation/訓練専用監視referenceを検証してからfinal窓を開始し、candidateをfitしない。
別processの標準CSV/mathでvalidation/finalの全行、ラベル+3h、MAE/RMSE、finalの4 segmentと実CSVの正解、
hash/台帳を照合した。製品の`verify_candidate_for_staging`も読取で成功し、registry書込は実行していない。

既存のprotocol10・gate3・training monitor reference26テストを専用環境で実行し全39成功。
製品source/schema/writer/lock/workflowは変更せず、全suite/新CIを合格したとは主張しない。
計測対象は履歴予測精度。hostは旧DESKTOP-J589D75、精密時計は未資格・調査保留。
UTCは実行境界の順序を確認した未資格値で、実行秒数/性能倍率の測定結果には使わない。
既知の集計統計を見た履歴評価でありblindな将来試験でも、他checkout全ての未消費を認証したものでもない。

<a id="capital-first-evaluation--次にすること"></a>

#### 次にすること

**このvalidation/final窓で再fit・再採点して選定をやり直さない。** desktopへ移す場合も候補・final・消費台帳を一緒に移す。
次は同candidate/artifactのT02実producer/T12監視・予測監査・復旧・metadata・運用測定。
staging登録は既存実registryへ行わず、専用環境のidentityと分離を確認してからblocked状態で行う。
review/別承認と全実証が揃うまでT13b採否は未完了。時計研究を自動的に再開しない。

追補：[保存済み予測の監視診断](capital.md#capital-offline-monitoring)を完了。誤差警告234窓を含むため、全体精度の合格だけで採用せず、実producer証拠とともにreviewする。[次のruntime作業](capital.md#capital-runtime-handoff)の入力確認は成功、運用実測は未実施。

追補：同候補の[専用registry/実API・監視受入](capital.md#capital-runtime-acceptance)を実施した。
登録は別port5001のversion1、運用未評価/配信blockedのまま。上記の「未登録」は精度評価時点の記録。

<a id="monitor-investigation-and-next-data"></a>

## monitor-investigation-and-next-data

<a id="monitor-investigation-and-next-data--psi・直近誤差の調査と新データ評価"></a>

### PSI・直近誤差の調査と新データ評価

この文書は2026-10-28の記録です。冒頭のPSI・直近誤差の調査はQ3/Q4を見た後の分析で、次候補の調整には使いません。後半に、独立した新データで候補選定と最終評価を行った結果を記録します。

同日の[差分監査](evidence.md#20261028-review)で、候補artifactを保存せず最終評価時に再学習していたこと、再実行防止が同一出力ファイルだけに依存することを確認しました。後半の結果は同じ学習設定による後続期間評価の暫定記録であり、同一モデルartifactを評価した証拠としては不足しています。元の数値は保存し、計画書R01〜R04を解消するまで新たな最終評価や新候補の昇格根拠には使いません。

<a id="monitor-investigation-and-next-data--調査結果"></a>

#### 調査結果

調査レポート: [`data/reports/model-drift-investigation-2012100100-2013010100.json`](../data/reports/model-drift-investigation-2012100100-2013010100.json)。保存済みのDB予測と元のUCI `hour.csv`を時刻で照合しました。

- 2012 Q4全体では版3のMAE/RMSEが`59.403 / 91.941`、版2は`70.501 / 113.270`でした。月ごとのMAEも版3が小さく、10月`77.258 vs 98.575`、11月`57.615 vs 64.414`、12月`44.095 vs 49.604`です。
- 直近7日窓は167予測のうち、3時間のラベル遅延を考慮して164件を採点しました。版2のMAE/RMSEは`37.278 / 58.158`、版3は`44.176 / 74.286`です。版3は平均予測が版2より約9.07台/時高く、低需要日に過大予測しました。
- 版3と版2の絶対誤差差の約84.6%は12月25日・26日・29日に集中しました。25日は祝日、26日は24時間中16時間が`weathersit=3`でした。実装の特徴量には`holiday`、`weathersit`、`workingday`がありません。これは誤差差を説明する有力な仮説ですが、原因を証明したものではありません。
- PSIは入力分布を見る値で、モデル誤差そのものではありません。監視では2011年全体の学習時特徴量を参照し、直近週の`lag_24` PSIが`2.095`、調査基準`0.2`を超えました。参照の中央値111台に対し、直近週は40台でした。
- 2012年12月全体の`lag_24` PSIは同じ2011年全体参照に対して`0.063`でした。2011年の同じ12月末週を参照にした診断比較では`0.198`でした。今回の大きな値には、年全体と年末の比較による季節・祝日差が強く含まれています。正式な監視値を置き換える結果ではありません。
- PSIが版2と版3で同値なのは正常です。同じ時刻の同じ特徴量を使うため、モデル版に依存しません。

版3は版2と同じモデル種・ハイパーパラメータですが、学習行数は8,645から13,001へ50.4%増え、学習 cutoff も2012-01-01から2012-07-01へ進んでいます。Q4各月の過小予測が減ったことから、より多く・より新しいデータが寄与した可能性があります。2版だけの比較なので、データ量だけが改善原因とは断定しません。

<a id="monitor-investigation-and-next-data--結果を見る場所"></a>

#### 結果を見る場所

- MLflow UI: `http://127.0.0.1:5000` → experiment `bike-demand-operations` → run `a0c6d543bb464f5aac88dd87a9712339`。候補学習のvalidation指標、品質ゲート、`training-report.json`を確認できます。
- Q4比較と誤差調査: `data/reports/q4-comparison-e03de4ee4ef4-2012100100-2013010100.json`、`data/reports/model-drift-investigation-2012100100-2013010100.json`、`data/reports/monitor-...-v2/v3.{json,csv}`。これらのリプレイ・監視レポートはMLflow画面ではなくローカルファイルです。
- 現在配信中の版: `http://127.0.0.1:8080/ready` と `http://127.0.0.1:8080/version`。このAPIには結果ダッシュボードはありません。

<a id="monitor-investigation-and-next-data--次に使う独立データ"></a>

#### 次に使う独立データ

UCIの現在のデータはCapital Bikeshareの2011–2012年で、最後の独立時系列窓を使い切っています。[UCIの出典説明](https://archive.ics.uci.edu/dataset/275/bike%2Bsharing%2Bdataset)と[Capital Bikeshare公式月次データ](https://capitalbikeshare.com/system-data)は同じ自転車シェアシステムを扱います。公開データ一覧で確認できた最新月は2026年5月です。

公式トリップ履歴を`started_at`の時間別件数へ集計する[`data_tools.py prepare`](../scripts/data_tools.py)を追加し、2026-10-28に実行しました。2023-01〜2026-05の41 ZIP（19,440,291行）を読み、同じ乗車IDの重複205行を除外して、2023-01〜2026-04の29,177時間を作成しました。CSV SHA-256は`cc4b39c67db286d30300cc6fb10a0f37e942eb337a7bd964795bbdd891762a96`です。監査では重複timestamp 0、DST処理による時間スロット欠け7件でした。詳細な取得元ファイルSHAと月ごとの件数はローカルmanifest `data/derived/capitalbikeshare-hourly-2023-202604-manifest.json`にあります。manifestとCSVは`data/`以下でGit対象外です。

月次ZIPには隣月に始まったトリップも含まれていました。2024年6月には5月ファイルと141件のID重複、2026年5月には4月ファイルと64件のID重複があり、開始時刻の秒以下の精度だけが違う場合もありました。スクリプトは`ride_id`と時間単位のローカル時刻で重複排除し、異なる時間帯のID重複はエラーにします。データ列形式は2023年1月と2026年3月の実ファイルで確認済みです。

この準備では以下を固定します。期間行数は学習用17,538、validation4,343、最終評価用7,294です。カットオフ時にラベルがまだ利用できない2024-12-31 22時・23時の2行は学習から外れます。

| 役割 | ローカル時刻の範囲 | 用途 |
|---|---|---|
| 学習 | 2023-01-01〜2024-12-31 | 候補モデルの学習。ラベル利用可能時刻は実験仮定の3時間遅延で制限 |
| 候補選択用validation | 2025-01-01〜2025-06-30 | 候補と版3・履歴ベースラインの比較に一度だけ使う |
| 取り置きholdout | 2025-07-01〜2026-04-30 | 候補を確定した後にだけ最終評価する。候補の選択には使わない |

コマンド上は学習 cutoff とvalidation startを`2025-01-01 00:00:00`、validation endとholdout startを`2025-07-01 00:00:00`、データend exclusiveを`2026-05-01 00:00:00`にします。元データのQ3/Q4 2012は再利用しません。

公式ファイルは`started_at`にタイムゾーンoffsetを含みません。サービス地域の`America/New_York`のローカル時刻と解釈し、UTCへ変換しません。秋のDST終了時は同じローカル01時が2回現れますが、元の文字列だけでは区別できないため、そのローカル時刻の予測対象を丸ごと除外します（2023-11-05、2024-11-03、2025-11-02）。春のDST開始で存在しないローカル時刻4件も作りません。集計CSVには`dteday,hr,cnt`だけを保存し、乗車ID・駅・位置情報は残しません。

元UCIデータの`cnt`と新データの件数は定義が完全には同じではありません。Capital Bikeshareは公式月次データから、スタッフの保守・検査、倉庫のテスト駅、60秒未満の乗車を除外したと説明しています。そのため新validationは現行モデルの時系列外部評価であり、ターゲット定義の変化も含む移行評価です。

データ監査時点ではモデル誤差を採点しておらず、候補選択にも利用していません。一方、取得・行数・全期間のデータ監査は実施済みで、CSV監査には最小・最大・平均件数も記録されています。完全に未閲覧のblind holdoutではありません。候補選択とは分離したout-of-time評価期間として保護し、validationで候補を固定した後に一度だけ採点します。

<a id="monitor-investigation-and-next-data--新データ候補と最終out-of-time評価"></a>

##### 新データ候補と最終out-of-time評価

2026-10-28に、候補CLIへ`--source-manifest`を追加しました。CSV SHA-256がmanifestの`data_sha256`または`derived_csv_sha256`と一致しない場合は処理を止め、学習レポートへ出典・manifest SHA・変換情報を記録します。

validation `[2025-01-01, 2025-07-01)`で候補を一度だけ比較しました。候補は版3と同じ特徴量・ハイパーパラメータで17,538行を学習し、4,343行を評価しました。

| validation 指標 | 新データ候補 | 現行版3 | 履歴baseline |
|---|---:|---:|---:|
| MAE | 168.266 | 389.762 | 208.061 |
| RMSE | 265.934 | 608.320 | 329.878 |

候補MAEは同じvalidation時刻上で版3とbaselineを厳密に下回り、品質ゲートを通過しました。これはCapital Bikeshareの新しい件数定義へ移行する候補としての結果です。UCIの版3に学習時と異なる新システムのデータを与えた比較なので、旧データ上のMAEと数値を直接比較して「実運用精度が何%上がった」とは言えません。候補はMLflowへ登録せず、API配信も変更していません。

validation後に同じcutoff・行数・ハイパーパラメータで候補を再学習し、後続期間`[2025-07-01, 2026-05-01)`の7,294行を採点しました。候補の選定時artifactは保存されておらず、同一artifactかは未証明です。比較対象の版3については登録済みartifact SHAがvalidation時と同じことを確認しています。

| out-of-time評価指標 | 同設定で再学習した候補 | 現行版3 | 履歴baseline |
|---|---:|---:|---:|
| MAE | 126.941 | 326.387 | 181.185 |
| RMSE | 211.578 | 518.660 | 296.180 |

この再学習候補は後続期間でも版3とbaselineより誤差が小さい結果でした。モデルの同一性を示す記録が不足し、out-of-time結果に別の合否基準も定義していないため、これだけを昇格の根拠にはしません。元データのターゲット定義差、集計監査で全期間統計を見たこと、3時間のラベル遅延が実測でなく実験仮定であることも含めて解釈します。月別MAEは`data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json`にあります。

実行レポートはローカルの`data/reports/candidate-capitalbikeshare-2025h1-v3.json`と`data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json`です。どちらもMLflowに自動登録していません。候補validationの実行時JSONはターミナルにも表示されます。MLflow UIには過去に登録したrunが表示され、APIの`/ready`と`/version`には稼働状態だけが表示されます。

最終期間は採点済みのため、今後このvalidationまたは最終期間を使って別候補を選んだり調整したりしません。候補を修正して比較する場合は、新しい期間または新しい外部データを先に確保します。現行scriptは同じ出力ファイルの存在を検査するだけで、出力名の変更による重複採点は防げません。計画書R02の消費台帳を実装する必要があります。

生の月次データは`data/raw/capitalbikeshare/monthly/`、集計CSVと取得・変換manifestは`data/derived/`に置きます。どちらもGitに追加しません。公式[Data License Agreement](https://capitalbikeshare.com/data-license-agreement)に従い、単体データセットとして再配布しません。集計結果は学習・評価のローカル分析に使い、実サービスの効果を主張しません。

実行時の候補validationコマンドを履歴として示します。audit中や次の候補選定のためには再実行せず、既存reportを読みます。次の候補にはR01〜R04の修正と別のデータ期間が必要です。

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 ./.venv/bin/mlops-bikectl candidate \
  --csv data/derived/capitalbikeshare-hourly-2023-202604.csv \
  --source-manifest data/derived/capitalbikeshare-hourly-2023-202604-manifest.json \
  --incumbent-version 3 \
  --training-cutoff '2025-01-01 00:00:00' \
  --validation-start '2025-01-01 00:00:00' \
  --validation-end '2025-07-01 00:00:00' \
  --output data/reports/candidate-capitalbikeshare-2025h1-v3.json \
  --no-register
```

実行時のout-of-time採点コマンドを履歴として示します。この期間は採点済みです。出力名を変えた再実行は現行scriptで防止できないため行いません。

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 ./.venv/bin/python scripts/data_tools.py evaluate
```

現行API版3は、別途承認されるまで稼働を続けます。

<a id="capital-offline-monitoring"></a>

## capital-offline-monitoring

<a id="capital-offline-monitoring--実候補の保存済み予測を使う監視診断（2026-11-14）"></a>

### 実候補の保存済み予測を使う監視診断（2026-11-14）

[初回精度評価](capital.md#capital-first-evaluation)と同じcandidate/artifactのfinal行別予測から、固定監視関数で履歴窓を計算した。
**offline診断であり、HTTP/registry/DB監査を含むT02/T12の実producer受入は未完了**。
再fit・新しい候補predict・選定・基準変更はしていない。

事前plan：`data/reports/20261114-product-resume/offline-monitor-plan.json`。
train-only reference、PSI v2のbins、minimum count24、PSI≥0.2、validation MAE×1.25を固定した。
時点はdataset-local。窓は`(clock-168h, clock]`、正解はlabel_available_at≤clockだけ利用する。
finalの最初2026-08-01 03時から、最後の正解到着を含む10-01 03時未満まで1,464時点を計算した。

| 診断 | 結果 |
|---|---|
| 分布警告 | 1,336窓。警告なし105、母数不足23 |
| 誤差増加警告 | 234窓。警告境界内1,204、ラベル不足26 |
| 最初の分布警告 | 2026-08-02 02時。24予測が揃う最初の時点 |
| 誤差が評価可能になる時点 | 2026-08-02 05時。ラベル+3h後に24正解が揃う |
| 最初の誤差増加警告 | 2026-08-02 20時 |
| lag24/lag168/hourの最大PSI | 0.665503 / 1.729054 / 0.108145 |
| 最後の窓 | 2026-09-24 02時を含まず〜10-01 02時まで。予測165/正解165、欠測率0 |
| 最後の窓の候補MAE / baseline MAE | 224.346232 / 274.084848 |
| validation基準の誤差警告境界 | 137.392476×1.25 = 171.740595。最後の窓は超過 |

最終targetは09-30 23時なので、10-01 02時まで窓を動かすと古い3予測が外れ165件になる。
「168時間窓だから168正解」と仮定せず、各時点の予測数/到着済み正解数を記録した。

最後の窓では09-26/27/28の候補日別MAEが347.46/330.67/277.25件と大きい。
09-27/29/30は候補がbaselineより悪い。全体の精度gate合格だけで全ての日の改善とは言わない。
天候・イベント等の原因はこの保存予測だけでは未特定。新しい基準や閾値を後付けせず、実producer証拠と合わせて採否時に説明する。
PSIは分布差の指標で因果説明ではなく、分布警告と誤差警告の発生は別々に扱う。

<a id="capital-offline-monitoring--確認場所と検算"></a>

#### 確認場所と検算

同ignored rootの`offline-monitor-windows.csv`、`offline-monitor-summary.json`、`offline-monitor-diagnosis.json`を読む。
SQLite/CSV/JSON/complete markerを通したmonitor CLIの結果ではなく、DB commit markerは作っていない。
現MLflowにも登録していない。候補の採用・運用gate・本番SLO・時計資格の証拠へ格上げしない。

producer内で全窓の所属/到着済み正解数/MAEを別算術で確認した後、さらに別processの標準libraryだけで、
1,464窓の件数・MAE/RMSE・品質状態と、4,392個のfeature PSI/分布状態を再計算した。
後者はpandas/sklearn/製品monitor関数をimportせず、元CSVの件数からexact lagを作り、bisect histogramとmath.logを使った。
両方成功。referenceの再作成やbins変更はしていない。[証拠索引](reviews/capital.json#/reviews/20261114-capital-monitor-checks.json)。
次の作業は[実候補の隔離runtime手順](capital.md#capital-runtime-handoff)を参照する。

追補：[実API・監視受入](capital.md#capital-runtime-acceptance)で独立したHTTP/DB producerを実行し、全1,464窓の一致を確認した。
このoffline原本自体を実producerへ読み替えず、別run ID/CSV/marker/DBの証拠を保持する。

<a id="capital-runtime-acceptance"></a>

## capital-runtime-acceptance

<a id="capital-runtime-acceptance--capital実候補の隔離runtime受入（2026-11-14）"></a>

### Capital実候補の隔離runtime受入（2026-11-14）

追補：[運用試行の保留と部分復元](capital.md#capital-operational-hold)まで進んだ。運用数値は条件内だったが、
全journalのUTC逆行を検出したため採用せず、再測定も行っていない。以下は先行API/監視の節目の記録。

同じ評価済み候補を専用MLflowへblocked登録し、実HTTP予測・PostgreSQL監査・monitor CLIを通した。
**API/監視の今回の受入は成功。T02/T12全体とT13b採否は未完了**。
[今回の証拠索引](reviews/capital.json#/reviews/20261114-capital-runtime-checks.json)、[実精度評価](capital.md#capital-first-evaluation)、
[作業別の開始条件](capital.md#product-completion-policy)を参照する。

<a id="capital-runtime-acceptance--実行環境とidentity"></a>

#### 実行環境とidentity

- 実行hostはDESKTOP-J589D75。画像のAI-DESKTOPへは接続せず、ユーザー指定の現在PCで実行した。
- 専用Compose projectは`capital-acceptance-20261114`。MLflow 5001、DB 55434、API 8110をlocalhostだけへ公開。
- 独立したPostgreSQL/artifactの2 volumeを新設。既存UCIの5コンテナはID・image・起動時刻が不変。
- sourceは先行専用projectの`767760d`。現在のDockerfileから別名imageをbuildし、全105固定依存版を照合。
  container Pythonは3.12.11、host専用venvは3.12.3。差を記録し、同一Python patchとは主張しない。
- candidate ID：`e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b`。
- 元skops SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
- 専用registry内の`bike-demand-capital` version **1**。
  登録artifact directory SHA：`13dbe8447e816b29ecf76c1cf4e173f679a50204ab8d9d682c113ed405810f8c`。
  MLflowが読み込んだ候補を包装・保存したdirectoryのhashで、元skopsファイルのhashとは対象が違う。再fitはしない。
- operational gateは`not_evaluated`、deployment permissionは`blocked`。
  APIの`deployment_operation=legacy_unverifiable / approval_evidence=false`を承認済みに読み替えない。

<a id="capital-runtime-acceptance--確認したこと"></a>

#### 確認したこと

| 経路 | 実行結果 |
|---|---|
| 固定入力→専用DB | 同一CSV/manifestから32,849観測行を投入。元default pairと正式消費台帳は不変 |
| readiness/version | 実APIが200。同candidate・version・登録artifact hash・profile/TZif identityを照合 |
| offline/API一致 | 04-01 10時の予測700.7861957728056が一致。同時2要求と同一再送も同じ保存結果 |
| audit/競合 | 対象request IDの監査は1行。同IDで時刻を変えると409。通常deploy eligibility検査は拒否 |
| 実HTTP replay | finalと同じ1,461 targetを予測し、HTTPエラー0。保存済みfinalの全予測・時刻とDB監査を照合 |
| monitor CLI | 登録candidateの訓練専用referenceを取得し1,464窓を保存。CSV/JSON/complete marker/DBの照合は`matched` |
| offlineとの比較 | 全1,464窓の母数・MAE/RMSE・PSI詳細・状態が一致。新しいfit/選定や閾値調整なし |

replay/monitorの事前planを保存した。予測対象は08-01 03時〜10-01未満、
監視clockは末尾ラベル+3hを含む10-01 03時未満とした。
CLIのendを後者にし`--limit 1461`を固定して、10月の追加targetが混入していないことを全監査行で確認した。
finalを再選定へ消費したものではない。

分布警告1,336、誤差警告234、初期のラベル不足26窓は[offline診断](capital.md#capital-offline-monitoring)と同じ。
最新は予測/正解165件、MAE224.346232で誤差警告、分布警告なし。警告を正常に保存できたことと、
業務上許容できることを分ける。天候等の因果は未特定。bins・閾値・recipeを後付けで変えない。

monitor DBのMAE/RMSE/coverageは既存schemaのREAL（binary32）。照合CLIは元binary64を型どおり丸めて比較した。
初期の欠測指標はSQL NaN計52個として明示し、0扱いしない。予測監査のpredictedはDOUBLE PRECISION。
`monitor-inspect`自体のmodel bindingはreport_onlyなので、別途登録reference・API/監査のidentity照合を結合した。

<a id="capital-runtime-acceptance--ログ・画面・再開時のpath"></a>

#### ログ・画面・再開時のpath

MLflow UIはこのPCの[専用実験run](http://127.0.0.1:5001/#/experiments/1/runs/7827b63cd9034ce7809367cf162e76e5)。
既存UCIの5000と混同しない。APIは[ready](http://127.0.0.1:8110/ready)、[version](http://127.0.0.1:8110/version)、
[操作schema](http://127.0.0.1:8110/docs)。監視結果はMLflowへ自動uploadしていない。

原本はGit対象外の`data/reports/20261114-capital-runtime/`。
`monitor.csv`、`monitor.json`、`monitor.complete.json`、`monitor-inspect-v1.log`、
`monitor-comparison-result.json`、全command/exit/UTC未資格値/monotonic/log hashを保存した。
秘密値は専用`private.env`（0600）だけへ保存し、既存`.env`を読まない。原本とvolumeは続きを行うため保持する。

専用projectの`data/derived/capitalbikeshare-next`は正式snapshotへのaliasのまま。
`data/evaluations`全体を元directoryへ向ける旧aliasは、運用証拠のproject内path契約と両立しないため、
専用directoryへ変更し、**consumption-ledger.jsonファイルだけを元の単一正式台帳へsymlink**した。
そのSHAは`c42de5e869d4765ac5f776ed098e98cbbed347c0dbe2c967016b14d64986b652`のまま。
運用plan/attempt台帳は専用project内。既存の同candidate運用attemptは開始前に存在しないことを確認した。

<a id="capital-runtime-acceptance--失敗・検証・残件"></a>

#### 失敗・検証・残件

- 最初のMLflow接続はworker起動前でreset。起動完了ログを確認後の読取でhealth200/登録モデル0を確認してから登録した。
- 中断回帰の初回はsandboxによるsocket作成拒否3件。権限を限定して実行した同9テストは成功。
  T03の自然再発・解決とは扱わない。
- helperの運用plan出力先誤りを製品CLIが拒否。指定directoryへ修正し、保存後の全bytesを保って上記専用pathへ移した。
  この時点では測定attemptを開始していない。
- runner9、journal9、publisher6、future/証拠15の計39回帰が成功。全suite/新CI成功は主張しない。
  製品source/schema/writer/lock/workflowは変更していない。
- 通常の同期表示はNTP=yes/NTPSynchronized=yes、約3秒31点のUTC順序・monotonic/perf_counter正方向を確認。
  精密校正や全時計資格ではなく、過去異常の解決にも使わない。時計研究は保留。

次は凍結したplanの暖機50＋逐次1,000要求、同artifactのDB/artifact対応復元とmetadata、
全22 prerequisite caseの実producer不足、警告reviewを進める。結果が揃うまで運用gateと採否を合格扱いにしない。

<a id="capital-operational-hold"></a>

## capital-operational-hold

**2026-11-15追補:** [consumerのUTC拒否・事後review契約・警告review](#operational-evidence-review-20261115)へ進んだ。
以下の2026-11-14記録と原本は保持する。新測定・証拠tag・採否は実施していない。

<a id="capital-operational-hold--実候補の運用測定保留と部分復元（2026-11-14）"></a>

### 実候補の運用測定保留と部分復元（2026-11-14）

**実API・監視と対応復元は部分受入。運用測定はUTC逆行のため保留、T13b採否はblocked。**
[先行runtime受入](capital.md#capital-runtime-acceptance)、[最終証拠索引](reviews/capital.json#/reviews/20261114-capital-operational-restore-checks.json)、
[作業別policy](capital.md#product-completion-policy)を参照する。時計研究は再開していない。

<a id="capital-operational-hold--1回の実測と、その後の停止判断"></a>

#### 1回の実測と、その後の停止判断

同じcandidate `e2df579…`、専用registry version1、登録artifact SHA `13dbe8447e816b29ecf76c1cf4e173f679a50204ab8d9d682c113ed405810f8c`。
04-01〜04-08未満の訓練内168時間を巡回する固定planを使い、attemptは`918ccaf6-d342-41aa-8ce1-e3b52f31f213`の1件だけ。

| 保存された数値・整合検査 | 結果 |
|---|---|
| 暖機 / 測定 / error / retry | 50 / 1,000 / 0 / 0 |
| nearest-rank p95 | 84.049708ms（数値上は250ms以下） |
| DB監査 | 全1,050行、入力出典・candidate/artifact・保存予測を照合 |
| journal | 2,101 eventのhash連鎖・要求順・latencyが一致 |
| 製品のlinked recheck / stdlib別process | 要求数・p95・hash・監査一致。ただし後者がUTC順序違反を検出してexit1 |

専用hostはRyzen 7 7735HS、WSL2、8 logical CPU、約29.43GBメモリ。
APIはOMP/OPENBLAS各2 thread、container CPU/メモリ上限なし。host/container依存版・実image・負荷・資源原本を保存した。
p95はHTTP要求（transport/JSON decode）を対象にし、observer/要求journalのfsyncは別の全体経過へ含める固定仕様。
PC上の未精密校正の実測で、本番SLOでも精密秒の保証でもない。

測定前は通常同期表示がyes、約3秒31点でUTC/monotonic/perf_counterの順序が正常だった。
しかし全journalを後から検算すると次の2対が逆行していた。短区間の正常を測定区間全体へ一般化しない。

| sequence / request | 前→後のUTC | 逆行量 |
|---|---|---:|
| 940→941、measured0419結果→0420送信 | 11:45:21.888190→11:45:21.846610 | 41.580ms |
| 1723→1724、measured0811送信→結果 | 11:45:51.801170→11:45:51.799001 | 2.169ms |

原本のrunner出力は`completed / measurement_status=passed`で、実行完了と数値条件の状態を表す。
**今回の受入判断は`measurement_acceptance_on_hold_clock_order_violation`**。
`operational-acceptance-hold.json`を別に保存し、report/journal/運用台帳の過去bytesを書き換えていない。
registryの`operational_gate_status=not_evaluated`、`deployment_permission=blocked`を維持し、
この試行の`capital_operational_*`証拠tagも記録していない。再測定・reopen・clamp・resyncをしない。
調整process/同期源を調べず、原因は未特定のまま。過去T03/tick11000の原因へ遡及適用しない。

<a id="capital-operational-hold--時計に依存する主張をしない、隔離した部分復元"></a>

#### 時計に依存する主張をしない、隔離した部分復元

所有確認済みの専用API/MLflowだけを静止してwriterを止め、同じPostgreSQL内のregistry/audit両DBをdumpしartifact全体と組にした。
収集中にsourceのtable/sequence/schema manifestが変わっていないことも確認。
別project `capital-restore-20261114`、別の2 volume、空きlocalhost portへ復元した。

- registry/auditの全table内容・sequence・column manifestが復元前後で一致。
  auditは観測32,849、予測2,512、monitor1,464、deployments0行。
- artifact全8ファイルのbytes/hashが一致。復元先のskopsだけを故意に破損し、実loaderがhash不一致でdeserialize前に拒否した。
  計画された負例として復元先のcopyだけをbackup bytesへ戻し、元artifactは変更しない。
- 復元先APIのready200、同candidate/version/artifact/profileを確認。保存済みrequestの再送と、新IDの予測700.7861957728056・DB監査が一致。
- 復元先container/volume/networkはcleanup済み。元の専用stackは再開しready200、blocked維持を確認。
  既存UCIの5コンテナID/image/起動時刻は試験前後で不変。

これは同versionの対応復元・保存結果維持の部分証拠である。二版切替、Caddy切替、旧REAL移行、承認operationの成立とは呼ばない。
別のAPI停止観測probeは、すでにsource再開後でrunning=true/connection resetだったため、停止検知caseの合格には使わなかった。
raw dumpとartifactのprivate backupはignored directory（0700）で保持し、公開索引にはhashだけを載せる。

<a id="capital-operational-hold--残課題と再測定を勝手に行えない理由"></a>

#### 残課題と再測定を勝手に行えない理由

全22 caseの一覧を`final-gap-inventory.json`へ整理した。
実観測のある8領域はcontract review待ち、5領域は部分的、9領域は未実証。全categoryの正式attestation/bundleは未作成。

| 優先度 / 難易度 | 次の作業 |
|---|---|
| P1 / D3・Sol | 証拠consumerがhashの正しいjournalでもUTC逆行をfail closedで拒否することを、負例と回帰/CIで製品側へ結合する。今回の手動検算による保留を迂回できないようにする。future拒否や時刻を緩めない |
| P1 / D3・Sol | completedだが事後に不適格となった試行の理由付き無効化/reviewを、原本と履歴を保持する形で設計する。現retry reviewはfailed/started/interruptedだけを許可しcompletedを拒否する。台帳削除/状態書換え/別台帳作成/自動reopenで回避しない |
| P1 / D3・Sol | 実candidate結合を保つ監視の不足負例、audit書込503、破損時HTTP readiness、二版/停止/Caddy/中断復旧、旧REAL移行、承認operation/timeout metadataを隔離して補う。合成CI成功を実証へ転用しない |
| P1 / D3・Sol | 分布警告1,336・誤差警告234の理由付きreview。閾値や評価窓を変えず、実producer原本との対応を説明する |
| P1・証拠後 / D3・Sol＋別承認 | validな運用試行と全producer証拠を揃えてT13b採否。今は未達 |

別PCへ移せば正常とは認めず、候補・正式消費台帳・今回の運用台帳/保留原本も合わせて移す。
git pullだけでignored成果物は移らない。**別PCでの再測定も、上記の事後不適格review契約が未整備の現時点では開始しない。**
ユーザーに今すぐ必要なコマンドはない。独立した製品の拒否検証・不足case整理は、時計研究を再開せず進められる。

先行文書commit9a1f433の[自動CI検算](reviews/capital.json#/reviews/20261114-capital-runtime-docs-ci-checks.json)は590unit/全job成功、
16 artifact memberとarchive-only再計算/cleanupを確認。既存合成CIの結果であり、今回の実運用保留を解除しない。

<a id="operational-evidence-review-20261115"></a>

## 2026-11-15：UTC順序の拒否と事後review

基点9479201以降のファイル統合（e9e3bda）を保持した変更。
[今回の検証索引](reviews/capital.json#/reviews/20261115-operational-consumer-checks.json)にコマンド、失敗、原本hash、CIを保存する。
時計研究は保留、validation/finalは消費済み。再fit・再選定・閾値変更はしない。

実装commit **b67902f**の[CI43640947101](https://github.com/kenobix/Claude_practice/actions/runs/43640947101)で
全600unit/compileallと全Docker/PG/HTTP/Caddy回帰が成功。artifact16member・archive/member SHA、
archive-only別processの1,000 measured/50 warmup除外/1,050応答再計算、全cleanupを照合済み。
local最終600件も成功。これは製品の拒否実装・合成回帰の受入であり、実運用保留や時計資格を解除しない。

### 証拠consumerの実装

共通journal readerはhash連鎖とsequenceを確認した上で、scheduleを含む**全ての完全なevent**の
`recorded_at_utc`を確認する。UTCのISO日時（`Z`又は`+00:00`、秒以下は最大6桁）が必要。
欠落・不正日付・naive・他offset・1µsでも逆行した場合はValueErrorで拒否する。
同時刻は時計分解能の範囲として認めるが、時計資格やelapsedの正確さを証明しない。
要求内だけでなく要求間、暖機/測定間、unknown outcomeも検査し、torn tailは成功にしない。
不正な既知prefixにtorn tailを追加しても逆行を隠せない。原本の修復・clamp・retryは行わない。

- linked recheck、運用証拠tag recorder、deployment evidence publisher、T16 archive-only verifierが同じreaderを使う。
  recorder/publisherはjournalのないlegacy planを新しい証拠として記録しない。
  legacyの保存数値だけを読む診断は残るが、承認や運用受入にはならない。
- **配信bundle schema/policyをv2**（`capital-deployment-evidence-v2`）へ改訂。
  `reports.request_journal`に原本JSONLの本文/SHAを必須化し、reportのjournal descriptor、固定schedule、
  1,050応答、各latencyと照合する。受信側も一時directoryで独立に全eventのUTCを検査する。
  数値検算・22case・別承認・future拒否も引き続き必要。v1を現行承認へ暗黙変換しない。
  既存registryのtagや過去bundleは変更しない。将来移行する場合も原本を含むv2の別生成と再reviewが必要。
- hashをjournal→report→ledger/manifest/bundle/registry identityまで再計算した負例で、
  local recheck/記録前の拒否、archiveの別CLI process、remote bundle受信/承認入口の拒否を確認する。
  合成fixtureの成功は実candidate受入の証拠にしない。

保存済み実attempt `918ccaf6-d342-41aa-8ce1-e3b52f31f213`は全2,101eventのhash連鎖が正しいが、
新readerが**940→941**で拒否し、linked recheckも拒否した。既知の1723→1724違反を取り消さない。
実原本に対するこの確認は読取専用で、registry接続・予測・新attempt・台帳更新は0件。
report/journal/plan/運用台帳のbytesは不変。raw completedは実行履歴としてそのまま保持する。
今回の拒否は現checkoutの製品sourceで確認した。過去の専用project/source copyや起動中containerへ
自動適用していない。旧snapshotのCLIを新consumerとして呼ばず、実行するsource revisionと入力aliasを
改めて結合すること。現配信の更新や、保留中試行へのtag記録を行う許可は含まない。

### completed試行の事後不適格review：設計済み、writer未実装

この小節は設計時点の記録。後続の[隔離実装と適用範囲](#operational-eligibility-review-20261115)を参照。

**今回実施したのは契約設計。台帳を無効化/reopenするCLIや、新attempt開始許可は未実装・未実行。**
実行状態（completed等）と証拠の適格性を分け、後者を別の追記専用review系列へ保存する。
raw ledger/report/journal/planを変更せず、completedをfailedへ書換えない。

| 項目 | 必須の契約 |
|---|---|
| identity | schema/policy ID、candidate、attempt、profile/target、登録artifact/model SHA、固定planの内容SHA/file SHA、report/journal SHA。全て原本から照合し、他attemptへの転用を拒否 |
| 原本binding | review開始時の運用台帳全体SHA＋対象record/attemptのcanonical SHA、正式消費台帳SHAを保存。後続の正当なappendで台帳全体SHAが変わっても過去snapshotと対象attemptを照合し、履歴を再hashして書換えない |
| review | reviewer、理由、UTC（future拒否）、実装/source revision、検査器/policy、欠陥code、参照証拠の相対path/bytes/SHA、UTC違反のsequence対等。UTCはseriesの順番を決める代わりに使わない |
| 系列 | candidate/attemptごとにrevision、previous-review SHA、今回document SHAを結合。新規fileのO_EXCL、協調lock、file/親directory fsync。既存reviewの上書き、fork、欠番、重複key、symlink、競合appendは拒否/未確定として保全 |
| 適格性 | `ineligible`又は`on_hold`。どちらも運用証拠/配信/採否への使用禁止。unknown/欠落/破損/競合/未読込でもfail closed。数値passedやraw completedで上書きしない |
| consumers | ローカルrecorder/publisher/recheckとportable bundle/最終採否が、同じreview履歴/最新headを結合して照合する。任意flagや古いbundleだけで無効化を迂回できないよう、headの保存/読取責任とTOCTOUも実装前に固定する |
| 再試行 | reviewの保存はretry許可と独立。`next_plan`/reopen/自動UUID生成を含めない。今回の不適格journalを後の好都合なreviewで有効へ戻さない。新attemptは別途明示承認と実装済みretry契約・固定入力・履歴照合が揃うまで禁止 |
| 認証・限界 | 人名/ローカルhashは本人認証や改ざん不能な外部署名ではない。所有者以外による変更・履歴head隠蔽への信頼境界を文書化し、実採否の別承認を置き換えない |

次の実装受入は、①正常なcompletedを一度ineligibleとして保存、②保存前後の全原本hash不変、
③同値再提出の扱いと競合/fork/破損/中断の保全、④全consumerの拒否、⑤未来review拒否、
⑥新attemptを作らないこと、⑦原本不在/差替/別candidate/旧head/応答喪失の拒否、
⑧使い捨てfixture→実装commit CI/artifact/cleanup確認。現実の台帳への適用は別途review後に判断する。
格納・最新性については次の実装案を固定する。**設計上の予約であり、現registryへ新tagを発行しない。**

- 正式原本と同じ専用projectの`data/evaluations/capital-bikeshare/operational-reviews/<candidate>/<attempt>/`
  に`<8桁revision>-<document SHA>.json`を新規作成する。複数PCの個別系列を正本にせず、移設時は全系列を移し照合する。
  document SHAはhashフィールドを除くcanonical bytesで計算し、previous SHAと対象attemptを内部にも保存する。
- 発行責任は明示的なreview publisherとし、candidate/versionの既存lockに加えてreview系列のlockを取得。
  原本・既存headの読取照合→durableなpublication intent→immutable review file→registry head公開→完了receiptの順に進める。
  raw運用台帳に状態を書き戻さない。中断・応答喪失時のintentを削除して成功とせず、読戻し照合する回復操作を別に要求する。
- 専用registryのexact versionに予約するheadは`capital_operational_eligibility_head_sha256`、
  `capital_operational_eligibility_revision`、`capital_operational_eligibility_status`、
  `capital_operational_eligibility_publication`。公開中は`publishing`、全tag読戻しと同一head確認後だけ`committed`。
  複数tag更新はtransactionではないため、consumerは欠落・途中・不一致を全て使用禁止とする。
  このheadを新bundle identityへ必須結合する将来改訂と、既存版の新規承認拒否を一緒に実装する。
- 使用許可を判断するonline consumerは、発行前後のexact registry version/run/artifact/headを読戻し、
  bundle内の全系列と一致しなければ拒否。最新headを取得できないoffline消費は算術診断だけに限定し、
  配信承認/operational gate合格を発行しない。古いbundle単体は最新性の証拠にならない。
- `ineligible`が1件でもある当該attemptは不可逆に証拠不適格。後の説明revisionが旧原本を有効へ戻すことはない。
  statusやheadを単独で消したり別versionにコピーしたりしても、完全系列/必須head欠落として拒否する。
  ownerの権限でregistryと全原本を同時にrollbackする攻撃まで防ぐ外部公証ではない、という信頼境界を保持する。

この契約はwriterと全consumerを揃えて隔離検証するまで適用しない。
現状を無効化済み、review済み再実行可、又は新規head公開済みと呼ばない。

<a id="operational-eligibility-review-20261115"></a>

### 2026-11-15：completed試行の追記専用reviewと適格性head

[検証索引](reviews/capital.json#/reviews/20261115-operational-review-checks.json)とwork_logへ全試行を保存する。
`EligibilityReviewStore`をoperational_validation.pyに実装。実台帳/registryへは適用せず、
一時directory/FakeRegistryと合成CIで検証する。再測定・新attempt・台帳reopenは機能に含めない。

- 正式sourceのledger/plan/report/journal/消費台帳をbounded readし、原本SHA、candidate/attempt/model版/
  登録artifact/profile/target、対象record/attemptのcanonical SHAを結合する。元ledger/消費台帳の原本bytesは
  immutableなanchorへ保存。現在の対象recordは不変を要求し、他candidateのledger追記や既存消費recordを保つappendは許容する。
  正式消費台帳には同protocol/model artifactのvalidation/final双方のcompleted記録が必要。
  用途名は既存契約の`validation_selection`/`reserved_final_evaluation`を使い、metricの期間名`final_evaluation`で代用しない。
- 初期状態は明示的な`no_adverse_review`（不適格review未登録）。**品質合格・人の承認ではない。**
  journalの全UTC/schedule/hash/1050応答と数値を検算できなければこの初期化を拒否する。
  既にjournalが不正なcompletedにも、最初から`ineligible`又は`on_hold`を記録できる。
  初期化後は悪い方向のreviewだけ追記でき、ineligibleは後のon_holdでも取り消せない。
- 保存先は設計と同じcandidate/attempt別directory。eventは`<8桁revision>-<SHA>.json`、
  publicationは`intent-<revision>.json`と`receipt-<revision>.json`。全てO_EXCL/0600/file・directory fsync。
  candidate/運用台帳/version/系列の協調lockを使う。親を含むsymlink、FIFO、未知file、fork、欠番、
  欠落receipt、過大history、不正/重複JSON、元source差替え、他candidate、未来reviewを拒否する。
- **公開順序を設計から補強:** 原本/metadataを検証後、registryの`publication=publishing`を読戻し確認して
  online承認を止めてからintent/eventを保存する。その後head/revision/statusを記録し、committedを最後に書く。
  その読戻し後だけlocal receiptを新規作成する。原本・operational gate・deployment permissionは書換えない。
  fence後のdisk失敗でもregistryはpublishingで停止し、新しい不適格recordをclean headの裏へ発行しない。
- 通信失敗/中断を成功へ読み替えず、自動retryしない。intent/eventが揃った途中公開は、exact headを指定した
  `resume`だけで再照合する。committed応答だけ失った場合はheadを再公開せずreceiptだけ保存する。
  同じ完了headのresumeはread-only。同値requestのpublish再提出で新revisionを作らない。
  **fenceだけ成功してevent未作成、torn event、複数不完全publicationは自動修復しない。**
  publishingのまま保全し、別途明示的な照合/回復契約が必要。最初のfence RPCが未受理ならreviewは未発行であり、
  古い正常headの失効が完了したとは報告しない。
- recorder/publisherはlocal系列が欠落/不完全/不適格なら拒否。registryのexact version/run/identityと
  committed headを照合し、記録の前後にも確認する。配信bundleは**schema/policy v2とraw journal必須を維持**し、
  新policy SHAで`reports.eligibility`と4つのhead tagを必須化。古いv2の無review bundleを新承認へ流用しない。
  受信側は全系列・原本snapshot・report/plan/journal SHAを検算し、onlineはlive headをartifact取得前後で確認する。
- offline bundle検算、数値recheck、T16 archiveだけではhead最新性を証明しない。
  出力は`not_proven_offline_requires_live_registry`又は`not_verified...`で、gateはnot_evaluated/配信blockedを保持。
  local recheckは履歴なしでも数値診断を残すが、履歴が存在して不適格/不完全なら拒否する。
  `inspect`も適格性が未確認であることを表示する。これらの診断をproducer証拠・承認へ変換しない。

CLIは`runtime_tools.py eligibility-review`。`--inspect`は読取のみ、変更は`--publish-review`と
candidate/attempt/reviewer/reason/source commit/dispositionを明示する。`--resume`も明示操作。
CLIでは当該操作だけHTTP transport retry0/要求timeout15秒とし、process環境を元へ戻す。
独自clientをlibraryへ渡す場合はsingle-attempt transportを呼出側で用意する。全体hard deadlineの保証ではない。
停止時は固定の診断codeを出し、native例外の本文やURI/秘密値を表示しない。
今回の実candidateに対する発行・初期化・復旧は承認していない。
合成system harnessだけが、固定の測定完了後に合成用の空履歴を明示的に作り、同じconsumerを通す。

信頼境界：ローカルhashとregistry headは本人認証・外部署名・外部公証ではない。
協調writerによる旧head再提出や、live値と異なる古いtag/bundleは拒否する。
registry管理権限で全control tagを整合した旧値へ巻き戻す攻撃を、遠隔consumerが独立に検出する保証はない。
独立したrollback防止anchorを導入していないことを明示する。稼働中modelの自動evict/traffic停止も行わない。
既存専用projectの旧sourceやsymlink入力aliasへ自動適用せず、実適用時にはsource revision・正式path・全historyを再照合する。
caseごとの実producer証拠、警告の人によるリスク受容、最終採否は未達のまま。

受入結果：修正実装`207c686d5c86a40bbd0261afbfb0d3c26f7c775c`の
[CI43663095854](https://github.com/kenobix/Claude_practice/actions/runs/43663095854)で
全623件（175.813 test表示秒）、全job/step、artifact16 memberのhash、別process数値再計算、cleanupを確認。
最初の343fac0はunit622件成功後、消費台帳purposeの誤用でsystem初期化が失敗した。
正式`reserved_final_evaluation`への修正と、その誤用を拒否する回帰を追加し、失敗原本も保持した。
T16 archiveだけで暖機50回を除く1,000要求/全1,050応答/p95 37.96615999999631msが再計算で一致した。
これは合成数値診断であり、実candidateの運用成績ではない。
既存4payload archiveは新review全系列を含まないため、archiveだけでreview系列/最新headも再現できたとは主張しない。
系列の可搬保存、orphan fence/torn fileの明示回復、旧source/aliasへの実適用前照合は計画の残件とする。
実原本194ファイル・正式消費台帳は不変、実candidateへのhead発行・tag変更・新attemptは未実施。

### 実monitorの理由付き警告review

保存済みmonitor CSV/JSON/complete markerのSHAとrun ID、登録training referenceと候補結合を確認。
既存finalの行別予測とラベル到着時刻を使い、全1,464窓の母数・MAE/RMSE・既存閾値による状態を再計算し、
前回の実API/offline一致記録と照合した。DB再接続・新予測・新しい採点窓・選定は行っていない。

| 観測 | 理由と判断 |
|---|---|
| 分布1,336、非警告105、母数不足23窓 | PSI>=0.2はlag24で1,032窓、lag168で1,150窓（重複あり）、hourでは0窓。最大は0.665503/1.729054/0.108145。training-only参照からlag需要値の分布が変化した信号。季節等が原因だとは確定できない |
| 誤差234、閾値内1,204、正解不足26窓 | validation MAE137.392476×1.25=**171.740595**を超えた窓。8/2 20時〜8/4 9時の38窓と9/22 23時〜10/1 2時の196窓。最初の警告は39正解、立上がりの短い母数を含む |
| 分布だけ1,126、両方207、誤差だけ27窓 | 分布変化と誤差増大は別の信号。分布警告3窓は正解不足で誤差判定不能。PSIが下がっただけで誤差警告を解消扱いにしない |
| 最新165正解/MAE224.346232 | PSIは全feature0.2未満でも誤差は閾値超。同行のbaseline MAE274.084848より良いが、validation時の誤差水準に比べ悪化している |
| baselineとの比較 | 採点可能な7日窓1,438件では候補MAEがbaselineより悪い窓は0。一方、暦日61日のうち17日は候補が悪い。9/22は候補482.510713対baseline307.791667。全体合格だけで個別リスクを否定しない |
| 欠測と窓の数え方 | lag24/168の今回窓内欠測率は最大0。23/26は立上がりのfeature母数24/到着済み正解24未満。168時間を1時間ずつ進める重複窓であり、1,336/234件を独立障害数や異なる正解数と解釈しない |

**技術reviewの結論:** 警告は既存ルールと整合し、誤警報と判断して消す根拠はない。
需要lagの分布変化と9月末の局所的誤差悪化、17日のbaseline劣後を未解決リスクとして採否reviewへ渡す。
原因はこの集計だけでは特定できず、閾値/bins/recipeを消費済み窓に合わせて変更しない。
この説明は新しいproducer attestationでも人の最終受容判断でもない。運用gate not_evaluated / 配信blockedを保持する。
全日比較と入力SHA・検算recipeは検証索引から辿れる。

<a id="capital-runtime-handoff"></a>

## capital-runtime-handoff

<a id="capital-runtime-handoff--次のsol作業：同じ実候補の隔離runtimeproducer受入"></a>

### 次のSol作業：同じ実候補の隔離runtime/producer受入

2026-11-14追補：[実runtime受入](capital.md#capital-runtime-acceptance)と[証拠索引](reviews/capital.json#/reviews/20261114-capital-runtime-checks.json)まで完了。
現在PCの専用project capital-acceptance-20261114、registry5001/version1、DB55434、API8110。
入力/候補/正式消費台帳は維持。data/evaluationsは専用directory＋consumption-ledger.jsonだけ正式台帳へのsymlinkへ変更済み。
最新：[運用保留・部分復元と残件](capital.md#capital-operational-hold)、[最終索引](reviews/capital.json#/reviews/20261114-capital-operational-restore-checks.json)。
同planの測定は1回完了したがjournalのUTC逆行により受入保留、対応復元は別volumeで部分受入済み。
次はconsumerのUTC順序拒否、completed事後不適格の履歴保持review設計、独立した不足caseの実証。
現台帳のまま別PCで再測定したり、証拠tagを記録したりしない。以下の開始前記録は当時の経緯として参照する。

2026-11-14。T10直前照合とT11は[実候補評価](capital.md#capital-first-evaluation)まで完了。
[offline監視診断](capital.md#capital-offline-monitoring)には警告がある。T02/T12全体は未完了で、T13b採否はblocked。
[作業別の開始条件](capital.md#product-completion-policy)に従い、時計研究は保留を維持する。

<a id="capital-runtime-handoff--続きを行う実体"></a>

#### 続きを行う実体

- source/venv：repository内`261021/data/reports/20261114-product-resume/project/`と隣の`venv/`。
- candidate ID：`e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b`。
- model SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
- selection/finalのpath/hashと正式ledgerは初回評価記録と検証JSONにある。両窓は消費済み。再fit/再採点による選び直し禁止。
- 固定snapshot：`261021/data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/`。
- runtime用入力は2026-04-01〜04-08未満の168時間。既存`runtime_tools.py benchmark --check-input-only`が実候補で成功。
  05-25〜06-01の168時間はラベル未到着の末尾2行を含むため、実候補CLIが予測/DB接続前に拒否した。

<a id="capital-runtime-handoff--入力pathに関する注意"></a>

#### 入力pathに関する注意

profileの既定`data/derived/capitalbikeshare-next`は元checkoutでは旧CSV（SHA cc4b39…）のまま。
旧defaultのhashが今回の候補と違うので、そのままstaging seedやbenchmarkへ進めない。
source/config/lockを変えず、**今回の専用projectだけ**の`data/derived/capitalbikeshare-next`を固定snapshotへのsymlinkにした。
元checkoutの旧pairは変更せず前後SHAを照合した。専用projectの`data/derived`はもはや元derived全体へのsymlinkではなく、
固定snapshot名と上記default aliasだけを持つdirectory。正式ledgerは元の単一pathを共有したまま。
このaliasは候補の実行環境の結合であり、旧成果物の置換や新しいデータの再集計ではない。
移設時はaliasの解決先と全bytesを再照合し、sourceを変えて候補を作り直さない。

<a id="capital-runtime-handoff--次に必要な実証（優先順）"></a>

#### 次に必要な実証（優先順）

1. 実行先を確認。デスクトップを優先するが、現在のチャットは旧hostで、接続先は未提示。
   接続不能でもsource/candidate/入力/台帳の整合など独立した作業を進める。新しい時計採取/精密校正へ戻らない。
2. 専用MLflow・PostgreSQL・API・volume/image/Compose projectを用意し、現配信/実registryとの分離を確認。
   製品CLIはlocalhost registry5001、DB55434、API8110を前提にする。占有されていたら既存サービスを停止せず、
   その実体を確認する。勝手なport緩和や既存registryへの登録をしない。必要なら隔離namespace/別PCの案をreviewする。
   デフォルトcomposeのnameは既存Capitalと同じなので、既存project名のままup/downしない。
3. このcandidateを専用registryへblocked登録し、exact source/artifact/report/tagを照合。固定pairから専用DBへseedし、
   staging APIのready・offline/API parity、retry/変更入力409、DB監査、通常deploy拒否を確認。
4. 登録同一artifactのtraining-only referenceで実monitor CLIを通し、SQLite/PG・CSV/JSON/complete markerの照合と、
   警告/欠測/到着遅延を記録。offline診断を実HTTP/監査証拠の代用にしない。
5. 通常の同期状態と短区間の順序/elapsed・strict future/timeoutを確認してから、固定planを保存し暖機50+逐次1000を実測。
   異常・中断時はその試行を保存して停止し、自動retry/clampや時計研究の再開をしない。
   当該PCの未精密校正の実測で、本番SLOや時計資格としない。
6. 同candidate/DB/artifactの隔離復旧・metadataを結合しT02/T12の不足を埋める。誤差/PSI警告の説明、
   理由付きreviewと別人承認が必要。現registry/配信を変更せず、T13b最終採否は証拠が揃ってから扱う。

デスクトップ側でユーザーに必要な最小操作は、そちらのVS Code Remote WSLでrepositoryを開き、
`hostname`、`pwd`、`git rev-parse HEAD`と実体の保存先を伝えること。SSH/ポート開放の導入は要求しない。
候補/行別証拠/原本はgitignore下なのでgit pullだけでは移らない。正式ledgerとともに私用領域へ移し、
path/hashを照合してから続行する。venvは別PCへそのままコピーせず、固定lockから専用環境を作る。

全試行/失敗/command/exit/hash/限界を記録し、可能な限り多くの独立課題を進める。
節目ごとに対象だけcommit/pushし、製品source/CI対象を変えたら実装commitのCI artifact/cleanupまで確認する。

<a id="capital-decision-http"></a>

## capital-decision-http

<a id="capital-decision-http--t13a-合成証拠を使うmlflow-http決定の障害受入"></a>

### T13a: 合成証拠を使うMLflow HTTP決定の障害受入

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

既存のSQLite直接接続試験に加え、実MLflow server/REST clientと障害proxyを使う。実Capital候補の採否、実データの性能、認証された人の独立承認、HTTPでのartifact取得の証拠にはしない。artifact本文は一時file storeから読む。モデルsourceは学習済みモデルではない。

<a id="capital-decision-http--実行と安全境界"></a>

#### 実行と安全境界

Python/lockを用意した261021から実行する。Linuxの/procとloopback socketを使用する。

```bash
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/runtime_checks.py decision-http \
  --run-disposable --evidence-dir data/reports/t13-http-local-unique
```

出力directoryは未存在の新しいpathを指定する。任意のregistry URLやDB指定は受け付けない。一時workspace/SQLite/artifacts/lock、自分で起動するMLflow、127.0.0.1の空きport、自分のproxyだけを使う。portを一旦予約して解放する隙間があるため、実際のlistener fdが起動したprocess/子に属することを/procのPID/start tick/process group/socket inodeで確認してから書き込む。trackingとregistryは同じproxy URIへ固定し、継承したMLFLOW_*は除去する。外部認証情報を必要としない。

TERMはfinallyを通る非0終了へ変換し、自分のserver groupへTERM、必要時だけKILLを送る。server/proxy停止、残存group、一時workspace削除をcleanup.jsonへ記録する。任意のprocessや既存Dockerは停止しない。SIGKILL自体にfinallyの保証はない。CI timeoutのTERM/kill-afterを恒久的なOS障害からの回復保証とは説明しない。

合成fixtureのreview/approval時刻を事前固定の`2026-11-01T00:00:00+00:00`として一時registryへ入力する。通信障害だけを検証するための入力であり、時計clamp・未来拒否の緩和・実承認時刻の置換ではない。既存validator/producerは変更しない。この時刻さえ未来である環境では試験は拒否される。自然UTC試験の4.170111秒逆行を解決した証拠として使わない。

<a id="capital-decision-http--受入条件と観測"></a>

#### 受入条件と観測

| ケース | 注入と確認 |
|---|---|
| reviewの途中書込 | reviewerまで保存してreason書込前に502を返す。not_evaluated/blockedを維持し、通常配信gateが拒否する。 |
| reviewの最終gate後に応答喪失 | operational_gate_status保存後にproxyが接続を閉じる。clientは例外、読戻しはpassed/blocked。同じreviewで同一hash/時刻へ復旧する。 |
| approvalの途中書込 | approverまで保存してreason書込前に502。blockedを維持し、配信gateが拒否する。 |
| approvalの最終permission後に応答喪失 | eligibleの保存成功後に接続を閉じる。client結果は読戻しまで未確定。全証拠の整合を読戻し検証し、同じ要求の明示retryでhash/tagを変えない。失敗応答だったからDBもblockedだとは推測しない。 |
| 競合する決定 | 異なる理由への上書きを拒否。同じ版のwriterがlockを保持する地点で競合要求を送り、lock拒否と全tag不変を照合する。これは同じ一時lockを共有する協調writerの試験で、別hostの分散lockではない。 |

MLflowのHTTP自動retryを0にし、各障害の注入回数が1であることを照合する。明示retryと最初の失敗を別々に保存する。外見上のMax retries exceededという例外文だけで、自動再送があったと判断しない。

http.jsonlにforward/注入/HTTP status、各地点のtag JSON、cases.jsonに例外・読戻し・復旧hash、server.logに起動/終了、cleanup.jsonに回収、hashes.jsonに元fileのSHAを保存する。hashは改変検出用で、独立した第三者の署名ではない。実コマンド/UTC/MONOTONIC/失敗logは[作業ログ](../work_log.md)、公開索引は[検証JSON](reviews/deployment-ci.json#/reviews/20261101-t13-http-checks.json)を参照。

初回はimport先誤り、二回目はRESTがrun外のモデルsourceを拒否した。正しい既存moduleとrun内artifactへ修正し、serverの保護を緩めなかった。v3/v4の成功後、TERM cleanupを追加し原本保存/CI logを整えた最終v6と実TERM中断試験が成功した。新guard 7件、既存決定/証拠30件が成功。新scriptはCIにも接続し、失敗終了値をpipefail/timeout経由で保持する。CI実績は対象commitとrunを別記録し、local結果で代用しない。

残りは実候補のproducer証拠、時計資格、監視/配信/復元の同一artifactへの結合を揃えたT13b最終採否である。T13aが通っても実registryのeligible化や現配信変更は行わない。

<a id="capital-decision-http--実装commitのci確認"></a>

#### 実装commitのCI確認

f1cc9ffの[CI41300378098](https://github.com/kenobix/Claude_practice/actions/runs/41300378098)で全376 tests、既存system/PG、新HTTP stepが成功。artifact内のcapital-decision-http-ci.log、5ケースとcleanup、ZIP/全member SHAを[CI検証索引](reviews/deployment-ci.json#/reviews/20261101-t13-http-ci-checks.json)で確認した。元のlocal索引は実装review時点の記録として維持する。合成時刻入力・file artifact・未学習sourceという限界も変わらない。T13aの範囲は完了し、T13b実候補の最終採否は残る。

<a id="capital-deployment-evidence"></a>

## capital-deployment-evidence

**2026-11-15改訂:** 現行bundleはschema/policy v2で、`reports.request_journal`の原本とUTC順序検査が必須。
v1を新規承認へ使用しない。[変更点と既存原本の扱い](#operational-evidence-review-20261115)を優先する。
以下の以前の日付の実装記録は当時の仕様・証拠として保持する。

<a id="capital-deployment-evidence--capital配信証拠の束ね方と検証"></a>

### Capital配信証拠の束ね方と検証

2026-10-30、計画T01とT02の必須証拠schema・拒否処理を実装した。監査A01/A02/A03の修正であり、実Capital候補の採用完了ではない。現在は実candidateがなく、監視・復旧・復元・metadataの実証も残る。正規の残課題は[計画書11.6](../../261014/plan_mlops.md#remaining-work-20261030)、測定からの手順は[bootstrap gate](capital.md#capital-bootstrap-gate)を参照する。

<a id="capital-deployment-evidence--証拠bundleとは"></a>

#### 証拠bundleとは

承認対象の報告書一式を入れたJSONファイルである。報告書の場所や「passed」というタグだけを残すと、別の報告書に変わったことを見逃しうる。そこで報告書の本文、本文のSHA-256、対象モデル・候補・データの識別情報を同じファイルに保存する。

このbundleはモデルファイルとは別のMLflow run artifactとして保存する。既存モデルartifactを変更しない。登録versionの元runに、次の場所で保存する。

```text
capital-deployment-evidence/<bundleファイルのSHA-256>/bundle.json
```

bundle schemaは`1`、policy IDは`capital-deployment-evidence-v1`。policy SHA-256は`b91bb7f3846e78ff38fa5311847328ade64f92aafe022fc5725aa8fba2161685`。

policyはソート済みキー・空白なし・UTF-8・末尾改行1個のJSONからSHA-256を計算する。実装は`capital_deployment_evidence.canonical_bytes`。各報告書は再整形せず、元ファイルのUTF-8文字列と元バイト列のSHAを保存する。NaN/Infinity、重複JSONキー、未知policy、32MiBを超えるbundleは拒否する。

<a id="capital-deployment-evidence--必須の内容"></a>

#### 必須の内容

最上位は`schema_version`、`kind=capital-deployment-evidence`、`policy`、`identity`、`reports`を持つ。identityはexact model name/version/runと、profile/target/feature契約、candidate、モデルartifact、データ、protocol、報告書、plan、policy、運用attempt・要求集合・検証status・p95のタグを結ぶ。

reportsには次の11項目すべてを入れる。各項目は`{"sha256":"<元ファイルSHA>","content":"<元ファイルの内容>"}`とする。

| 項目 | 保存するものと検査 |
|---|---|
| candidate | 候補選定報告。candidate identityを再計算し、データ・artifact・policyを照合する。 |
| final | 同じ候補を再学習せず評価した報告。選定に使っていない窓・同じartifact・blocked状態を検査する。 |
| protocol | 凍結したschema 2のbaseline-only評価手順。窓・件数・ラベル遅延・元データhashを照合する。 |
| validation_rows | 保存済み行別予測CSV。MAE/RMSEとvalidation gateを再計算する。 |
| final_rows | 保存済み最終評価の行別予測CSV。MAE/RMSEとfinal gateを再計算する。 |
| plan | 凍結運用計画。candidate/final/protocol/学習行集合/登録artifactと照合する。 |
| operational | 完了attemptの測定報告。50暖機+1,000逐次要求の順序・ID・p95・監査・出典・資源記録を再確認する。 |
| monitoring | 下表の監視6ケースの完了証拠。 |
| recovery | 障害・復旧8ケースの完了証拠。 |
| restore | snapshot・復元・移行4ケースの完了証拠。 |
| metadata | 配信metadata4ケースの完了証拠。 |

CSVの再計算は保存済み予測の計算確認である。新しいfit、モデルを使った予測のやり直し、未使用評価窓の消費はしない。元CSV/manifestとcandidate ledgerの照合は、公開CLIの準備段階で既存`verify_candidate_for_staging`も使って行う。runtimeは束ねた証拠を照合するため、開発PCの元ファイルpathへ依存しない。

<a id="capital-deployment-evidence--必須受入ケース"></a>

#### 必須受入ケース

各case IDと結果はpolicyで先に固定する。必要な実装・実証を完了する前に、空のpassed報告書を作って埋めてはいけない。

| 分類 | case ID | 必須observed_outcome |
|---|---|---|
| monitoring | training_reference_bound | matched |
| monitoring | psi_v2_constant_shift | detected |
| monitoring | distribution_error_separation | distinguished |
| monitoring | delayed_missing_duplicate_labels | handled |
| monitoring | insufficient_labels_and_unset_reference | explicit |
| monitoring | monitor_persistence_reconciliation | matched |
| recovery | concurrent_request_retry | committed_once |
| recovery | changed_input_conflict | 409 |
| recovery | version_switch_preserves_saved_prediction | preserved |
| recovery | audit_write_failure | 503 |
| recovery | bad_artifact_and_ready | rejected |
| recovery | api_stop | detected |
| recovery | caddy_switch_and_rollback | recovered |
| recovery | interrupted_deployment | recovered |
| restore | database_artifact_snapshot_binding | matched |
| restore | separate_volume_restore | restored |
| restore | restored_version_prediction_audit | matched |
| restore | legacy_real_double_migration | matched |
| metadata | entry_runtime_database_identity | matched |
| metadata | approval_operation_metadata | matched |
| metadata | unloadable_version | excluded |
| metadata | timeout_stage_outcome | explicit |

4分類の報告書は次の形式にする。`identity`には`model_name`、`model_version`、`candidate_id`、`artifact_sha256`、`data_profile_id`、`target_contract_id`、`data_sha256`、`feature_contract_version`の8項目をすべて含める。versionは文字列とする。

```text
schema_version: 1
kind: capital-deployment-prerequisite
category: monitoring / recovery / restore / metadata のいずれか
identity: 上記8項目
policy_sha256: 上記固定policyのSHA
completed: true
status: passed
evidence_state: confirmed
started_at_utc: UTCの開始時刻
completed_at_utc: UTCの完了時刻
checks:
  各case ID:
    sha256: ケース本文のSHA
    content: ケース結果JSONの元UTF-8文字列
```

各ケース本文は`case_id`、同じ`identity`、`completed=true`、上表の`observed_outcome`、空でないJSON objectの`observations`を持つ。observationsにはproducerが実際に観測した値・件数・参照log等を保存する。必須caseの欠落・余分なcase、failed、未確定、別候補、別artifact、不正hash、未来時刻、開始後より前の完了、空observationsを拒否する。

**検証の限界:** 現schemaはローカルで信頼するproducerの観測申告である。observationsの内容から独立して障害試験を再実行したり、実際に観測した本人を認証したりはしない。内容と全hashを同時に書き換えられる管理者への改変防止、電子署名、IAMを提供しない。今回の正例は明示した合成fixtureだけで、実証producerはT04/T05/T06/T07/T08/T12/T17で実装・検証していく。

<a id="capital-deployment-evidence--保存と承認の順序"></a>

#### 保存と承認の順序

以下は必要な実証が揃った後の手順。今すぐ実candidateに実行するコマンドではない。261021から実行し、入力報告書はproject内data配下の通常ファイルに置く。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
MLFLOW_REGISTRY_URI=http://127.0.0.1:5001 \
  .venv/bin/python scripts/runtime_tools.py evidence \
  --candidate-id '<candidate ID>' --attempt-id '<completed attempt UUID>' \
  --final-report 'data/reports/<final-report>.json' \
  --monitoring-report 'data/reports/<monitoring-evidence>.json' \
  --recovery-report 'data/reports/<recovery-evidence>.json' \
  --restore-report 'data/reports/<restore-evidence>.json' \
  --metadata-report 'data/reports/<metadata-evidence>.json'
```

既存candidate/ledger/version lockを保持し、completed attemptが指すplanとreportを検査する。元データ・候補・全証拠を照合し、bundleを保存してから取得し直して検査する。SHA/run/path/policyを登録タグへ記録する。同じbundleの再試行は許し、別bundleへの上書きは拒否する。保存だけでは`deployment_permission=blocked`、運用gateも従前のままとする。

その後、[review/approve手順](capital.md#capital-bootstrap-gate)で、人が意図した担当者と理由を記録する。review passedもblockedを維持し、別の承認者によるapproveが全条件を満たした場合だけeligibleを最後に記録する。決定schemaは`2`で、証拠全体のsnapshotとUTC時刻をreview/approvalのhashへ含める。部分書込後の同一要求retryでは保存済み時刻を使う。

旧schemaのCapital決定は通常配信で拒否する。既存決定をその場で新schemaへ格上げしない。実Capital承認済み候補は現在ない。UCIのgateや配信対象を変更する移行ではない。

<a id="capital-deployment-evidence--配信前の再確認と今回の試験"></a>

#### 配信前の再確認と今回の試験

2026-11-01追記: 後続の[T13a HTTP障害受入](capital.md#capital-decision-http)で、一時MLflow RESTの部分書込、最終gate/permission後の応答喪失、読戻しと同一retry、競合拒否を確認した。以下の241件とHTTP未実施という記述は当初checkpointの記録。実候補の最終採否とHTTP artifact取得の実証は依然別作業である。

review、approve、通常loader、runtime init、deploy promoteでbundle本文を取得してSHA/content照合する。取得前後で登録版、run、証拠/決定タグの変更がないことも確認する。Capitalのtracking/registry URIは同じ先へ固定し、環境の別registryへ流れるのを防ぐ。承認後のreport/plan SHA変更、負/NaN/Infinity p95、本文破損、証拠不足、旧schemaを拒否する。読み込んだAPIプロセス内で毎要求bundleを再取得する仕組みではない。

今回、全241 testsが成功した。mock registryによる負例と、一時SQLite registry・file artifact storeを使った実MLflow clientの保存/取得/review/approve/保存元artifact破損拒否を含む。一時SQLite試験はHTTP serverの障害やネットワーク切断を再現しない。T13のHTTP経由部分書込・応答喪失、T07のCaddy復旧、実candidateの運用測定・承認は未完了。コマンド、途中の試験失敗と修正、証拠logのSHAは[作業ログ](../work_log.md)へ記録する。

<a id="operational-review-recovery-20261115"></a>

## reviewの診断・回復と適用前照合（2026-11-15追補）

基点`a978adf`からの追補。ここでの回復は、失敗した**review公開処理**を照合する操作であり、
運用測定の再実行、completed台帳のreopen、配信許可の変更ではない。
実候補のUTC逆行による保留は維持する。[検証索引](reviews/capital.json#/reviews/20261115-review-recovery-checks.json)に各試行を記録する。

| 状態 | 明示操作・保証 | 停止条件 |
|---|---|---|
| eventとintentが完全でreceiptだけ未作成 | 従来の`--publish-review --resume --expected-head SHA`。正しいlive headと原本を再照合 | 別head、原本変更、複数の未完了公開 |
| intent/receiptが欠落・途中破損、eventは完全 | `--repair-metadata MEMBER`。元fileは残し、`repair-MEMBER.json`をO_EXCL/fsyncで追記。元bytes/SHAと決定的な代替metadataを結合 | receiptは**最新の1件だけ**、registryのcommitted head一致が必須。他の不正receipt、event破損・未知file・競合を拒否 |
| fenceだけが残り、新eventが未作成 | `--recover-orphan`で`on_hold`または`ineligible`の新しい理由付きreviewのみ発行。従来headとinventory/registry全tagのSHAを照合 | `no_adverse_review`への復帰禁止。intentだけが残る、破損eventがある、またはheadが変化した場合は停止 |
| eventやrepair sidecarそのものが破損・不足 | `--inventory`で元file名/bytes/SHAを記録し隔離状態を保つ。独立して保持した正しい原本が必要 | filenameから本文を推定しない。自動削除・上書き・正しいことにして再公開する処置は実装しない |

`--inventory`はローカル読取のみ。`--diagnose`は専用registryも読取り、
`orphan_fence / committed_matched / uninitialized / quarantined_original_required`と比較用SHAを返す。
内部例外・認証情報・raw tag値は表示しない。診断結果自体は修復許可や配信承認ではない。
修復済みmetadataを読むconsumerは、残した元fileのbytes/SHAと代替値を毎回照合する。
元file、sidecar、eventのいずれかが変わると拒否する。SDKの再送はCLI操作内で0、環境変数は終了時に復元する。

隔離fixtureでの操作例（**実候補へは実行しない**）:

```bash
python scripts/runtime_tools.py eligibility-review --candidate-id "$CANDIDATE" --attempt-id "$ATTEMPT" --diagnose
python scripts/runtime_tools.py eligibility-review --candidate-id "$CANDIDATE" --attempt-id "$ATTEMPT" \
  --repair-metadata intent-00000001.json --expected-inventory "$INVENTORY_SHA" \
  --reviewer "$REVIEWER" --reason "$REASON" --source-commit "$SOURCE_COMMIT"
# 修復後も公開がpendingなら、改めて診断した正確なheadで明示resumeする。
```

### 旧source・正式alias・全historyの適用前照合

`eligibility-review --preflight MANIFEST`は、manifestの`expected_source_files`（相対path→SHA）、
`aliases`（alias/target/SHA/bytes）、`canonical_root`を使って読取照合する。
sourceはsrc/scripts/configs/pyproject.toml/requirements.lockに限定し、symlinkをたどらない。
正式入力aliasは、明示したcanonical root内の解決先とbytesを別途確認する。
台帳・plan/report/journal・両消費窓、既存historyの全event/intent/receipt/repairとlive headを照合する。
全履歴を読めない場合やsource/原本/tagが変化した場合は成功扱いしない。

結果の`application_authorized=false`は常に維持する。消費台帳のsymlinkを明示照合できても、
writerのNOFOLLOW境界を緩めず、`writer_path_compatible=false / explicit_plan_required`を返す。
既存historyがそのaliasに依存している場合は、strict readerによる停止を優先する。
旧sourceで候補を再fitしたり、別の正式消費台帳を作ったり、既存aliasを自動変更したりしない。
原本journalのUTC逆行は`utc_reversal_adverse_review_only`であり、採用の前提を満たした意味ではない。

修復metadataのsidecarはローカルの保全記録である。v2 capsuleのevent/receipt本文は変わらず、
独立したregistry管理者による全headの整合した巻戻しは依然として信頼境界外である。
既存T16の4payload archiveは新sidecarを含む全review系列の可搬保存にはならない。別契約の残件として維持する。

### 今回の最終受入と残件

実装`865362d`の[CI43666552694](https://github.com/kenobix/Claude_practice/actions/runs/43666552694)は
全634 unit（249.949表示秒）、全job/step、現Dockerfileの合成system・PostgreSQL・HTTP/Caddy復旧を通過した。
artifact14142284724は234,614 bytes、SHA256 `d4cdcc4ca6cb9bb948274fb370001a9f66445aa8c28a6963ce98ce091d51f94e`。
固定16 memberと内包hash、ZIPだけを受け取る別processの数値検算、各owned resourceのcleanupを照合した。
数値検算は合成1,000要求・暖機50除外・journal1,050応答・p95 28.651527msで、実候補の性能には流用しない。
local全634件/591.268表示秒も成功。保護原本194件、元の運用/消費台帳は不変。

新しい専用projectへ**保存済み実候補のバックアップだけ**を復元した追加結果:

| case | 今回の観測 | 主張の限界 |
|---|---|---|
| 監査書込失敗 | 制約注入でHTTP503、対象監査行0 | コピーDBのみ。実DBの障害ではない |
| 破損artifactのready | コピーskops破損でHTTP503、元の候補/registry/配信は不変 | 実運用のavailability達成ではない |
| monitor CLI負例 | 重複時刻、source hash不一致、reference path欠落を期待理由で拒否。CSV/JSON/marker未作成、DB manifest不変 | missing-label、validation MAE未設定、constant-referenceの別条件まで完了とはしない |
| Caddy切替・停止・復旧 | 同candidate/版1/artifactの2slotで保存応答が完全一致。green停止時502→blue切戻し後ready200 | **異なる二版、承認済み配信、製品promote中断の受入ではない** |
| 旧REAL型fixtureの移行 | コピーの2,514行をREALへ変換して旧状態を再現し、製品initializeでDOUBLE PRECISIONへ。移行前binary32値を全行維持 | 失われた元binary64の精度を復元した意味ではない |
| API停止 | owned containerのRunning=falseとtransport refusalを両方確認 | 過去の停止probeへの遡及適用なし |
| 入口metadata | own Caddy経由でcandidate/artifact一致 | approval_evidence=false / legacy_unverifiableのまま |

v1〜v5の失敗も保持。通常のlocal Dockerfile buildはPyPIの読取timeoutで失敗した。
代替専用imageは固定依存105版と現source18ファイルのSHAを照合し、旧module残存を検出後に
専用レイヤーから除去して余剰0を確認した。通常Dockerfileのlocal成功へ読み替えない。
元UCI/Capitalの8containerは開始時点のExited状態・ID・image・起動時刻を保持し、今回の全project/volume/networkをcleanupした。

旧source98ファイルと正式alias/消費台帳のread-only照合は、停止中の元registryへ接続できなかったため
**保存済みregistry snapshotコピー**に対して行った。元live headの現在性は未確認。
旧sourceには現行consumerがなく、formal consumption symlinkはstrict writerと互換でないため、
実適用前に明示的なsource更新・正式入力の投影/移設契約が必要である。実projectのaliasは変えていない。

最新版の22case一覧はlocal原本`data/reports/20261115-review-recovery/runtime-gap-inventory.json`。
**実観測12領域review待ち・部分5・未実証5**で、正式なcategory attestationは発行していない。
未実証はconstant-reference、適格な異なる二版、製品配信中断復旧、承認operation、timeout段階metadata。
部分caseにもmissing-label/MAE未設定などの不足がある。合成CIや同版routingで埋めない。

警告1,336/234窓の技術reviewは前節のまま。人のリスク受容は未承認であり、
それだけでUTC逆行の不適格証拠を有効化することはできない。
唯一の運用試行は保留で、再測定・新attempt・reopenは禁止されているため、
**今回の制約下でT13bの合格・配信承認まで全P1を完了することはできない**。


<a id="operational-review-input-archive-20261115"></a>

最新の実候補追加確認と停止条件は[2026-11-15 runtime追補](#runtime-followup-20261115)を参照。

## 正式入力の参照・review全系列保存・独立原本回復（2026-11-15追補）

基点`3482f6e`からの製品契約。前節の「移設契約未実装」「event/repair原本回復未実装」「全系列archive未実装」は、
この節の隔離実装・検証状況で補う。実候補への適用、元live registryの最新性、運用保留の解除を意味しない。
[今回の証拠索引](reviews/capital.json#/reviews/20261115-review-archive-checks.json)と[作業ログ](../work_log.md)を参照する。

### 現sourceから同じ正式入力を参照する

`eligibility-review --bind-inputs MANIFEST`は、新checkoutへ**参照設定だけ**をO_EXCL/fsyncで保存する。
設定場所は`data/evaluations/capital-bikeshare/operational-inputs/CANDIDATE-ATTEMPT.json`。
元の台帳・plan・report・journalをコピー/初期化/変更せず、旧checkoutのsymlinkも変更しない。
従来のsymlinkを許可する代わりに、operatorが指定したcanonical rootへ各directory componentをNOFOLLOWで開く。

入力manifestの契約:

- `bindings`：論理相対pathをkeyに、`root`（canonical絶対path）、`target`（data内相対path）、
  `sha256`、`bytes`、`device`、`inode`、`mtime_ns`、`ctime_ns`を指定する。
  正式運用台帳・正式消費台帳・plan・report・journalの**5入力に完全一致**すること。
  この設定はhost固有のprivateファイルであり、Gitや公開archiveへ含めない。
- `expected_source_files`：現在checkoutのsrc/scripts/configsの実行source/configとpyproject.toml/requirements.lockの相対path→SHA。
  consumer・CLI・依存2ファイルを必須にし、余剰/不足sourceやsymlinkも拒否する。bytecode directoryは対象外。
  これは現在のreview readerのbytes照合であり、旧学習sourceの由来を書き換えるものではない。
- `expected_registry_sha256`：明示読取した当該registryの全tag比較値。
  正式入力・両消費窓・全history・live committed headを二度照合し、変化した場合は設定を発行しない。
  historyが未発行ならcontrol tagも無いことが必要で、初回reviewは別の明示操作になる。

以後のStore/共通local consumerは参照設定を読み、同じ正式入力のidentityとbytes、現在sourceを毎回確認する。
元candidate/台帳/versionのlock namespaceを共有し、移設先に別の正式台帳やcandidate lockを作って競合を回避しない。
正式入力の正当な追記もpin不一致として停止するため、実適用の際は別途reviewした移設計画が必要。
sourceや参照設定が処理中に変わる、別inodeへ置換される、rootの親がsymlinkである場合も停止する。
`application_authorized=false / new_attempts=0 / retry_permission=false`を維持する。

現行の`recheck`等にある参照設定以外の旧pathも、実運用への移設時には個別に確認する。
この契約だけで全MLOps commandの自動移設、元live head確認済み、または実候補へ適用済みとは主張しない。

### review全系列を別processへ渡す

```bash
# 隔離fixture専用。実候補への発行・回復を自動実行しない。
DATA_PROFILE_ID=capital-bikeshare .venv/bin/python scripts/runtime_tools.py eligibility-review \
  --candidate-id "$CANDIDATE" --attempt-id "$ATTEMPT" --export-review-archive "$ARCHIVE"
.venv/bin/python -m bike_mlops.operational_validation \
  --verify-review-archive "$ARCHIVE" --expected-sha256 "$SEPARATELY_RETAINED_SHA"
```

ZIPはmanifest、全物理event/intent/receipt/repair、残した途中破損bytes、原本snapshot・全referenceの閉包を持つ。
回復済み系列なら回復controlと保全元も含む。lockは含めない。
単なる全file SHA検査に加え、別processの一時領域で系列の順序・event SHA・snapshot/原本・repair代替値・全reference・headを再検算する。
この一時snapshotは検算用であり、新しい正式消費台帳として利用しない。
結果は`offline_full_series_verified`、`live_freshness_verified=false / application_authorized=false`。
運用数値を採用したり、UTC逆行のraw journalを有効にしたりする結果ではない。

ZIPは最大32MiB、member単体8MiB、650件まで。historyは既存8MiB/501件/100event上限も維持。
member重複・欠落・余剰・traversal・symlink・暗号化・manifest不一致を拒否し、展開先pathをarchiveから直接実行しない。
既知の秘密field/外部URL/絶対pathを拒否し、UTF-8以外の保全原本は公開可能と推定しない。
任意の秘密を完全自動判定できる保証はないため、原本の公開reviewは別途必要。
既存出力を上書きせず、書込中断のpartial出力には成功receipt/SHAを発行しない。

### 独立原本からevent・repairを明示回復する

`--restore-review-archive ARCHIVE`は、破損**前**に独立保管したSHA付きarchive、現在inventory、
現在registry全tag SHA、reviewer/reason/UTC/source commitを必須にする。
「破損後に作ったarchiveの自己申告SHA」では独立保管の真正性を証明できない。保管者/採取経路の確認はoperatorの責任境界に残る。

完全なarchive系列と現在のcommitted live headが一致し、原本・全referenceも同じ場合だけ、
`recovery-00000000.json`へexact archived bytesと保全元descriptorを追記する。
破損event/repair/intent/receiptを削除・上書きせず、欠落fileも元pathへ生成しない。
consumerは保全元が不変であることを確認してから代替bytesを読み、従来の全系列検証を再度通す。
registered tags、gate、配信許可、運用台帳、消費台帳には書込まない。
回復後のadverse追記でもsticky ineligibleを維持し、保全元の変更・欠落先の再生成を拒否する。

古いarchive/別candidate/異なるsource/新しいhistory/未知member/変化したlive head/競合/future reviewを拒否する。
一つの回復controlに限定し、control自身の破損・回復controlを含むarchiveからの再回復は明示的に停止する。
その場合は独立原本を保持したまま、新しい保全先と履歴継承を別途reviewする。自動resetや成功までretryしない。
独立registry管理者による全体の整合した巻戻しは、このローカルhash契約で検出できる保証を持たない。

### runtime観測12領域のreviewと停止理由

保存済み実candidate snapshotの12観測について、candidate/artifact、HTTP結果・監査行、復元volume、
旧REALの保持精度、monitor母数/警告数、cleanupを保存原本から照合した。
**観測12は技術review済み、部分5・未実証5は維持**。正式category attestationは0件。
一部のsummaryの完了表示だけで、必要な全producer/段階/承認が揃ったと扱わない。

- audit503・破損ready・API停止・旧REAL移行・同版routingは、記録された専用コピー環境の範囲で支持する。
- 実candidateのPSI3特徴のreferenceは非定数。別の合成定数referenceを実candidateに結合し直さない。
- missing-label/MAE未設定の残条件は、frozen CSV/referenceの別の拒否試験では代用しない。
- 異なる適格二版・製品promote中断・承認済みoperation/timeout metadataには、有効な採用前提が必要。
  同版二slotやblocked stagingを承認済み配信の証拠にしない。
- 唯一の運用試行はUTC逆行で保留。再測定・new attempt・reopenは禁止のため、有効な運用証拠を新たに作れない。
  人の警告risk受容だけでこの欠陥は解消しない。前提未達のT13b採否・承認は進めない。

この制約下で全P1をpassedにすることはできない。時計研究を再開せずに後日独立した測定を認めるか、
配信承認を伴わない学習成果の範囲で完了とするかは、証拠と限界を示した上で別途ユーザーが判断する。


今回の隔離実装は`cb05e72`の[CI43685531001](https://github.com/kenobix/Claude_practice/actions/runs/43685531001)で
全653 unit（216.116表示秒）・全job/step、固定16 artifactのSHA/内包hash/ZIP-only数値再検算/cleanupまで受入。
artifact14150975862は234,537 bytes、SHA256 `0c03cc31ce7f43c22074e01a79f74f1a678424e4d3e7f728d36d6cbf2cb93fbe`。
最終local対象50件/99.874表示秒も成功。現source36fileをコピーした別processでcanonical合成5入力の参照（台帳コピー0）とadverse拒否を確認した。
破損event/repair/回復controlを含む13memberの別process検算も成功したが、再回復は未対応のまま。
local全suiteの途中source651件には既存future拒否1 error、measurement全体にはtimeout/未特定FAILがある。
失敗を残し、CI成功をこのPCの時計資格やその失敗原因の解決へ流用しない。
実候補の運用保留、live未確認、partial5/missing5、category未発行と別承認待ちは継続する。

<a id="runtime-followup-20261115"></a>

最新の適用範囲・承認順序の改訂案は[22ケースの受入設計](#acceptance-scope-proposal-20261115)を参照。以下の実績・v2の未達判定は改訂案によって変更しない。

## 実候補の監視負例・復元カテゴリ・実適用の前提（基点b8644fa）

[検証索引](reviews/capital.json#/reviews/20261115-runtime-followup-checks.json)。製品source、固定policy、評価窓、元の台帳・registryを変更していない。
再fit・再測定・new operational attempt・reopenは0件。以下の専用コピー試験は新しい運用測定attemptではない。

### 今回完了した範囲

- 同candidate/artifactの保存済みsnapshotを、新しい専用PostgreSQL/MLflowへ復元した。CLIは現source、MLflow serverは保存済み固定依存imageを使用した。API/Caddy/既存8サービスは起動・停止していない。
- コピー内のcandidate報告書のvalidation MAEだけを`null`にした場合、実monitor CLIは`candidate report SHA-256 mismatch`でexit2。コピーregistryの報告書SHAだけも再結合した場合は`candidate, manifest, data, or artifact identity differs`でexit2。CSV/JSON/complete marker未生成、コピー監査DB全manifest不変。元candidate ID、raw skops、登録artifact、CSV、正式台帳は不変。
- この2件は**凍結済み候補へのMAE欠損注入を拒否した証拠**。別candidate identityを作り直した「正規のMAE未設定候補」の受入、欠測ラベル、constant-reference検知を実証したことにはしない。
- 所有projectのcontainer/volume/networkを削除し、元8containerのID/image/開始時刻/状態が不変であることを確認した。194保護原本のbytes/SHAも不変。
- 復元カテゴリ4ケースは、以前の実snapshot producerのdump/全artifact tree/復元前後DB manifest/別volume/保存予測/監査assertion/移行assertionを照合した。元producer sourceと結果のhashは過去の検証索引へ結合し、candidate identityを再計算した。今回再実行せず、`restore-prerequisite.json`を作成した。別processの既存consumerで受入、必須case欠落・異なるartifact・case本文hash破損の3負例を拒否。
- 移行はコピー内2,514行を意図的にREALへ落としてからDOUBLE PRECISIONへ移した試験であり、**既存binary32値の保持**だけを証明する。失われた精度の復元ではない。復元報告の時刻は元producerの開始/完了端点で、精密校正や連続した運用計測を主張しない。
- `restore`のみ確定。registryへ報告/tagを発行しておらず、全bundleの採用・運用gate・配信許可・T13b採否は保留。従来22caseの集計は**復元4件category確認・観測8領域・部分5・未実証5**。古い12/5/5の索引は当時の記録として保持する。

### 部分5・未実証5の依存関係

| case | 現時点で不足するもの・停止理由 |
|---|---|
| delayed_missing_duplicate_labels | 遅延と重複CSV拒否は既存証拠あり。CLIはラベルを固定CSVから読むため、DB観測を消しても欠測ラベル試験にはならない。CSV/manifestやcandidate identityを作り直したfixtureは実候補の同一性を保たない。欠測を含む別producer入力を認める契約のreviewが必要。 |
| insufficient_labels_and_unset_reference | 少数ラベルの明示と参照path欠落拒否に加え、今回MAE欠損の二段階拒否を追加。正規のMAE未設定報告へ変更するとcandidate identityが変わるため、その分岐の同候補実証とはしない。 |
| caddy_switch_and_rollback | 同版・blocked二slotの切替/切戻しは実証済み。異なる適格二版と正当な配信操作の前提が無い。 |
| entry_runtime_database_identity | candidate/artifactの一致はあるが承認operationの結合は無い。legacy_unverifiableを承認済みに読み替えない。 |
| unloadable_version | 破損artifactのreadiness503はある。承認された配信経路から不正版を除外する全体試験は未達。 |
| psi_v2_constant_shift | 実候補の凍結reference3特徴は非定数。定数referenceを捏造せず、汎用機能の合成検証と実候補caseの適用条件を別途reviewする。 |
| version_switch_preserves_saved_prediction | 異なる適格二版が無い。単純な版番号変更はartifact directory SHAも変える。同版routingやhash緩和で代用しない。 |
| interrupted_deployment | 承認された実候補promoteを開始できない。実配信の中断は行わず、合成CIの結果も流用しない。 |
| approval_operation_metadata | 全bundle・有効な運用証拠・別承認が無い。承認tagを手作業で足して試験を成立させない。 |
| timeout_stage_outcome | 実候補の正当な配信operationを開始する前提が無い。開始前gate拒否を処理途中timeoutへ読み替えない。 |

上記には、候補固有の受入と汎用機能の受入の適用範囲、初回配信前に承認済みoperationを要求する順序のreviewが必要なものがある。
現policyを自動変更したり、該当caseを無断で免除したりしない。新しい独立データ・追加候補・試験専用承認を認める場合も、既存の消費窓・閾値・candidate証拠とは分けた計画と承認が必要。

### 実適用・live head照合を再開するための具体案（未実行）

正式5入力のcanonical path/hash/bytes/device/inode/mtime/ctimeと現36sourceを読取確認済み。参照設定は発行していない。
元PostgreSQL/MLflow/APIはExited255で、停止済みregistryへ同じ接続失敗を繰り返していない。
snapshotコピーのhead一致では元registryの最新性を証明できない。

起動案のための限定inspectでは、PGは`b0962998d3ff` / image `sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3`、
MLflowは`f543c219ad1f` / image `sha256:888539f1e5ffd2b8831e8346414cb44367feb1540e48172a1a5ee8c41218505c`だった。
元volumeは`capital-acceptance-20261114_postgres-data`と`capital-acceptance-20261114_mlflow-artifacts`、
公開先はlocalhost55434/5001。両方停止中であり、別の新projectを元liveの代用にはしない。
これは採取時点のidentityで、許可後に同一性と空きportを再確認する。

1. ユーザー判断が必要なのは、元の専用`capital-acceptance-20261114-postgres-staging-1`と`capital-acceptance-20261114-mlflow-1`の**起動/終了を限定許可するか**。API/UCI/Caddyには触れない。これは現状の「既存サービスを起動・停止しない」制約の変更に当たる。
2. 許可後も、最初にcontainer ID・image・volume・ポート55434/5001・既存設定を読取照合する。新規build/recreate/up全体起動はせず、対象の既存2containerだけを扱う。イメージ/volume相違、ポート競合、現在状態の変化があれば停止する。
3. 起動はDB recovery/WAL/ログやMLflow startup migrationなどの書込みを伴い得る。従って**単なる読取許可では実施不可**。起動前backupとschema差分確認、許容書込み範囲を別途合意する。GET照合に限定してもこの起動影響は消えない。
4. 起動後はretryを増やさず、短いdeadline内でversion/run/candidate/artifact・全tag SHA・committed head/fence・全history・正式5入力・両消費窓を照合する。変更検知・future拒否・UTC異常時は設定発行を中止する。
5. 前提が揃った場合に限り、現checkoutへprivate参照設定を新規発行する別の明示操作へ進む。元台帳の複製・初期化はしない。旧path前提が残る他CLIも個別に確認する。参照設定発行だけでadverse review、gate/tag、配信許可を発行しない。
6. 終了時は許可された2containerを元の停止状態へ戻す案とするが、DB/ログへの起動書込みは巻き戻せない。失敗時にvolume削除や原本restoreを自動実行しない。参照設定の失敗原本も削除しない。

### 保存ログから確定できたローカル失敗の範囲

前回のFAIL後にも複数testが完了しているため、FAILそのものがprocessを停止させたわけではない。
exit124はsuite外側の240秒timeoutで、monotonic差は240.136817945秒。
個別testの時間・実行stack・失敗assertionのtracebackは保存されておらず、最後に表示されたsymlink testのdeadlockとも、T03とも断定できない。
独立したfull-suiteのfuture拒否や後日のCI653件成功は、その原因の証明にならない。

原因は未特定として残す。次に新しい理由で該当testを実行する際は、`addFailure/addError`直後のtraceback flush、cleanup前の所有fixture保存、test開始/終了のmonotonic、timeout前のfaulthandler stackを採取する。
今回それだけを理由に受入済みsuiteを再実行したり、timeoutを延ばしたりしていない。

<a id="acceptance-scope-proposal-20261115"></a>

## 22ケースの受入設計と承認順序の改訂案（基点77438f4・未適用）

この節は**ユーザー判断用の設計案**。現行`capital-deployment-evidence-v2`、製品consumer、registry、正式入力参照は変更していない。既存の集計は復元category確認4・観測8・部分5・未実証5のまま。試験を適用外とする判断や新policyの有効化は未承認である。

### コードから確認した不整合

- `deployment.CASES`は22ケースすべてに同じ候補・登録版・artifact等の8項目identityを要求する。`validate_prerequisite`はcategory内の全case名・固定outcome・hash・identityを要求するが、`observations`の中身は非空dictまでの検査。**機械validatorの成功だけではproducerの観測内容を証明できない**ため、技術reviewが必要。
- `record_operational_review(passed)`と`record_deployment_approval`は全bundleを検証する。一方、通常loaderは`require_deployment_eligible`により、この承認を取得済みの版しか読み込まない。従って「同candidateの承認済みoperationを事前のmetadata categoryへ入れる」という受入解釈には循環がある。観測に架空の承認を書くことは解決にならない。
- `command_monitor`はラベルを固定CSVから生成し、Capitalでは予測targetがCSVに無い場合も拒否する。DBの観測削除をラベル欠測として扱う実装ではない。既存の「ラベル停止」合成試験はavailabilityを69時間ずらす方式で、欠落ラベルを持つ実CLI入力の証明ではない。
- `candidate_identity`はvalidationとmonitor_referenceを含む。実候補のMAEを未設定にしたり非定数referenceを定数へ置換したりすればidentityが変わる。一般機能の分岐を固定候補に無理に成立させることはできない。
- 現T13b writerのdecisionは`passed/rejected`だけ。`rejected`も`_validate_registered_version`で`capital_operational_evidence_status=recorded`等を要求する。したがって**「この候補なら今すぐ正式な不採用を書ける」わけではない**。保留は現状のblocked状態と文書に保持され、正式な保留判断の専用契約はまだ無い。

### 全ケースの適用先と必要証拠

G=汎用機能、C=同候補の隔離観測、P=人の配信承認後の実操作。Gの合成証拠はC/Pの代用にしない。下表の「変更案」は未適用。全行に共通して、source・依存lock・入力・rawログ・producer・結果のSHA、専用環境identity、終了値、cleanupが必要。Cはcandidate/data/protocol/reference/raw model/登録artifact/versionを固定し、評価窓を再消費しない。Pはさらに有効な運用証拠と別承認を要する。

| category / case | 適用先と開始条件 | 検証・証拠、固定候補への制約／変更案 |
|---|---|---|
| monitoring / training_reference_bound | C・凍結CSV/manifest/候補/reference/登録版一致 | training行・特徴・binsの再構築とSHA照合、別reference拒否。実候補の非定数3特徴を維持。 |
| monitoring / psi_v2_constant_shift | G・専用定数fixture、C・reference mode確認 | 定数の同値/上下shift・件数・PSIをGで検証。Cで全対象modeが非定数なら定数分岐は適用外の根拠を保存し、非定数binsとPSI実計算を別途必須とする。自己申告N/Aは禁止。 |
| monitoring / distribution_error_separation | G＋C・固定bins/閾値/予測/ラベル | 分布だけ/誤差だけ/双方/不足をGで区別。Cでは保存CSV/JSON/DBの母数と1,336/234警告を照合。警告を合格や因果特定に読み替えない。 |
| monitoring / delayed_missing_duplicate_labels | G＋C・入力経路ごとのラベル契約 | Gは真の欠落・遅延・重複を区別。Cでは固定3時間の到着制約、重複CSV拒否、CSV外target拒否を確認。別のライブ到着ラベルfeedは現CLIの対象外で、対応済みと主張しない。 |
| monitoring / insufficient_labels_and_unset_reference | G＋C・固定MAE/reference | Cは24件未満の保留、reference/報告改変拒否。Gは正規のMAE未設定で`performance_reference_unset`。CでMAEが設定済みなら未設定分岐は非適用の根拠を固定し、欠損注入の拒否を必須にする。 |
| monitoring / monitor_persistence_reconciliation | C・同candidateのmonitor CLI出力 | 窓/母数/指標/identityをCSV/JSON/complete marker/DBで照合し、不一致・途中公開を拒否。offline診断だけで代用しない。 |
| recovery / concurrent_request_retry | G＋C・専用API/DB | 同一requestを並行送信し、同じ保存結果と監査1件。順次retryだけを並行成功にしない。 |
| recovery / changed_input_conflict | C・保存済みrequest ID | 同IDの入力差に409、元予測・監査不変。 |
| recovery / version_switch_preserves_saved_prediction | G＋C試験コピー、Pは別 | 異なる2登録版・各artifact SHAをpin。旧IDは旧版の保存結果、新IDは新しい版。原モデル同一の場合は版切替/冪等性に限定し性能改善を主張しない。現候補へ試験許可を与える契約が必要。 |
| recovery / audit_write_failure | C・コピーDBへの限定fault | 実audit書込み失敗に503、成功予測として返さない。故障を除去した後の状態も保存。 |
| recovery / bad_artifact_and_ready | C・コピーartifactへの限定破損 | hash/読込拒否とready503、元artifact不変。 |
| recovery / api_stop | C・所有APIだけ停止 | 接続失敗の検出と監査への影響。元サービスを停止しない。 |
| recovery / caddy_switch_and_rollback | G＋C試験コピー、Pは別 | 2登録版と専用Caddyで切替/502/旧版復旧、entry/API/DB一致。現在の同版2slot観測は限定証拠として保持。 |
| recovery / interrupted_deployment | G＋C試験コピー、Pは別 | registry/artifact/準備/route切替の段階で中断、操作ID・未確定state・再起動後の明示recover・旧route整合。開始前gate拒否では代用しない。 |
| restore / database_artifact_snapshot_binding | C・停止/整合snapshot | 同時点のDB manifest・dump・artifact tree、candidate/版の対応。既存確認済み4ケースの一つ。 |
| restore / separate_volume_restore | C・別volume/所有project | 元volumeを上書きせずDB/artifactを復元、専用resource cleanup。 |
| restore / restored_version_prediction_audit | C・復元後の同candidate/API/DB | ready/version/保存retry/新規単発予測と監査。運用1,000要求の再測定をしない。 |
| restore / legacy_real_double_migration | G＋Cコピー・旧schema明示 | コピー2,514行のbinary32値保持を検証。DOUBLE化で失われた精度が戻るとはしない。 |
| metadata / entry_runtime_database_identity | G＋C、P | Cはcandidate/version/artifactとblocked状態、試験操作は試験許可IDを一致。Pでは本物の承認ID/operation/state/台帳一致を追加。legacy_unverifiableを承認済みにしない。 |
| metadata / approval_operation_metadata | G＋試験C、P | G/試験Cは試験許可と操作の結合。Pは実承認者・理由・承認SHAと操作IDを照合。Pの結果を初回承認の前提にせず、承認後の稼働確認にする案。 |
| metadata / unloadable_version | G＋C、P | 候補履歴を残し破損版を選択/rollbackから除外、正しい旧版へ戻る。Cのready503だけと区別する。 |
| metadata / timeout_stage_outcome | G＋試験C、P | registry/artifactを含む全体deadline、段階/elapsed/未確定outcomeとchild cleanup。試験Cは試験許可、Pは実承認に結合。遅延完了で成功へ自動変更しない。 |

### 提案A：受入契約v3と初回承認の順序（承認待ち）

予定IDは`capital-deployment-evidence-v3`。現v2とは別のschema/policy hashとして扱い、現時点で有効なhashや承認は発行しない。

1. **Gの製品機能証拠**をsource/依存lock/試験入力/producerへ結合する。各caseの必須observations schema・型・期待値を定義する。単なる`completed=true`や非空dictを証明にしない。実候補identityを合成fixtureへ貼らない。
2. **Cの候補証拠**は同一candidateに結合する。条件付き分岐はvalidatorが元reference/候補内容から適用性を再計算する。非適用でもG証拠と、Cでの代替必須検証が欠ければ拒否。未知mode/欠落field/identity不一致はN/Aにせず拒否する。
3. **試験専用二版の許可**は実承認と独立した`isolated-runtime-test-authorization-v1`案。許可manifestはsource、対象2版と各artifact/raw model SHA、所有project/DB/volume/API/registry、保護対象一覧、操作範囲、承認者/理由、deadlineへ結合する。原モデル同一の登録2版なら、その限定目的も固定する。登録metadataの差によるartifact SHA差は正当に記録し、hashを緩めない。
4. 試験用経路は専用harness/adapterに閉じる。通常loader・promote・evidence consumerへ`allow_test`等の環境変数やfallbackを追加しない。通常の`eligible`/承認tagを捏造せず、API/state/台帳に`test_only`の識別を保存する。正規orchestrationの共有部分を試験する場合も、実際に通過した経路と注入した境界を明示する。試験許可の改変/期限切れ/所有者違い/別volume/実registryへの誤接続を拒否し、実consumerが試験証拠を採用根拠として受け取らない負例を必須にする。
5. 初回の実配信承認は、G＋C＋有効な運用証拠＋警告review＋別人承認の順。**現在のUTC逆行attemptはここを満たさない**。試験許可だけでは次段へ進めない。
6. Pの本物の承認operationとの照合は承認後の確認とする。承認直後は「配信許可あり・稼働未確認」であり稼働完了としない。P確認に失敗/timeoutならrouteを公開せず、既にrouteを動かした場合は事前確認済み旧routeへ明示復旧する。未知状態は未確定のまま保全する。新しい版の公開を初回確認より先に恒久化しない設計・負例を必要とする。

**移行:** v2原本・旧索引・head・policy SHAを保持し、v3へ自動変換/再hashしない。新しい評価manifestに旧証拠の元hashと使用できる限定主張を記録し、足りないv3観測は新規に検証する。source変更後の正式入力参照は36file等の実際のclosureが変わるため別途照合・明示発行が必要。v2 consumerはv3を拒否、v3 consumerはpolicy/schemaを明示選択し、unknown policy・v2をv3と偽る入力・test-onlyの本番利用・古いlive headを拒否する。改訂後も再fit/窓再利用/精度閾値変更は許可しない。

**影響:** candidate評価の合否を変えず、配信前後の証拠の適用先を変更する。旧v2全22case合格という表示にはしない。実際のv3 consumer/harnessの実装とunit/CI/同commit artifact・cleanup検証はユーザーの方針承認後に行う。

### 提案B：保留・不採用の正式な決定（承認待ち）

`capital-candidate-disposition-v1`という別の追記専用判断記録を提案する。既存の運用review/approval tagsとは別で、候補ID・登録版/registry identity（未照合ならその状態）・評価報告SHA・attempt/raw journal/report/正式台帳SHA・UTC逆行位置・未達case・警告review・判断者/理由・前decision hashに結合する。

- `held`は条件未達・判断保留、`rejected`は今回候補の採用を見送る明示判断。どちらも配信許可を与えない。署名/IAMが無ければ人名は自己申告である限界を記す。
- 保留記録は「運用gate passed」も「数値gate failed」も意味しない。将来の判断は前decisionを参照した新しい記録とし、元attempt/台帳/headを書き換えない。
- 原本とcopyの違い、live head未確認を必須fieldにする。未照合での文書判断は可能でも、正式入力移設の完了やregistryでの決定済み状態を主張しない。元registry書込みは別許可。
- `adopted`はこの軽量な判断経路に追加しない。採用には通常の全受入・有効な運用証拠・別承認を要求する。現在候補の推奨は**精度合格・配信保留**。

将来配信を希望する場合だけ、元attemptを残す別運用試行の計画（新しい試行IDと前attempt参照、同candidate/固定plan、回数/資源/停止条件、記録の更新先）を作り、測定前に別許可を得る。既存completedをreopenせず、自動retryしない。時計研究再開は含めない。

### 提案C：元PG/MLflow限定起動・live照合の実行計画（承認待ち）

対象identity/image/volume/portは[前回限定inspect](#runtime-followup-20261115)に記録済み。起動直前に再照合する。許可対象を次のC1/C2に分け、PG/MLflow以外の既存サービスには触れない。

**C1・元2サービスの起動/終了と起動時書込み:**

1. 対象8containerのID/image/開始/状態、元2volume/port/commandの安全なfingerprintを保存する。秘密を含むexpanded env/commandは非公開0600保存・公開はSHAだけ。元2containerが停止中、port5001/55434が未使用、固定image/volume一致が開始条件。状態変化・未知利用者・容量不足なら停止。
2. 停止状態を保ったまま、固定imageの新しい一時helperから元volumeを**read-only mount**し、PGとMLflow artifactを別のprivate cold archiveへ保存する。helperはnetworkなし、元volumeへの書込み不可、原本を削除しない。PG_VERSION・image major・全member SHA/型/owner/mode、PGのtablespace等が別mountを指す場合は対象範囲を確定するまで停止。2archiveの取得前後に元container停止を再確認する。
3. archiveを新しいvolumeだけへ復元し、元と同imageの一時PGでcrash recoveryを検証する。DB/schema versionとMLflow alembic head、audit schema、全artifact manifestを確認する。このcopy試験は元liveの代用ではなく、backupが利用可能かの検査。予定MLflow imageのmigration headとDB head不一致、未知migration、復元不成立は元の起動前に停止する。
4. 同一性が保たれた元PGを既存container ID指定で起動し、最大60秒のreadiness期限。その後元MLflowだけをID指定で起動し最大90秒。全作業deadline15分（backup/dry restoreは別の準備作業上限30分）。失敗時に自動再起動しない。HTTP/SQL個別readは5秒、HTTP retry=0、SQL read-only transaction/statement timeoutを指定する。
5. 版1・run・candidate/artifact/report/全review系列・publishing fence・live headを現在のconsumerで照合する。事前/事後registry tagsと原本SHAを比較し、同じformal rootの既存locksを使う。head不一致・fence・原本変化・future/UTC逆行・timeoutなら発行へ進まない。新しいhistoryが見つかれば古いheadへ戻さない。
6. 許可された2containerだけをMLflow→PG順に正常停止（各30秒上限）。間に状態・所有者が変わった場合は第三者の操作へ干渉せず停止理由を記録する。kill・volume削除・自動backup restoreは許可に含めない。期限内停止できなければ状態を保全して追加判断へ戻る。元8containerの保護状態と原本再照合を記録する。

PG起動ではWAL/recovery/統計、MLflowではログ・初期化・migration等の書込みが起こり得る。**readonly queryだけを許可しても起動はできない**。想定外schema/data変更は検知・停止し、巻戻しは別許可。正常停止してもWAL/起動ログ等のbytesを元に戻す保証はなく、cold backupがあることを自動切戻し許可と解釈しない。現段階ではbackup未取得、dry restore未実施、元2サービス未起動である。

**C2・正式入力参照の発行:** C1のlive照合と現在source closure/5入力canonical identity/両窓消費済み/全history整合が通った場合だけ、現行`bind_inputs`の新しいprivate参照controlを明示発行する。元台帳の複製/初期化、review/承認tag変更は含めない。A/Bのsource変更後は新sourceを先に固定してから行う。C2を許可しない場合はC1は診断だけで終える。

### 残件と判断順

1. A（適用範囲/試験専用二版/順序）とB（非採用の追記判断）をreviewし、方針承認後に実装・負例・CIを行う。仕様承認はリスク受容や実配信承認を意味しない。
2. 現行契約で確認できる独立した拒否条件は隔離fixtureで補う。今回の追加検査の結果とhashは検証索引へ記録し、v2全case合格にはしない。
3. C1/C2の許可後、backup・復元検査・限定起動・live照合・入力参照の順に進める。未許可の起動はしない。
4. G/Cの製品受入と候補の保留判断を完成させる。警告の技術reviewは維持する。実配信の採否は有効な運用証拠と人の判断が揃った場合だけ再開する。

新しい運用測定・new attempt・reopen・再fit・時計調査はこの提案の承認だけでは開始しない。過去の原因未特定FAIL/timeoutは未解決として保持し、必要な新規試験では失敗直後のtraceback・fixture記述・timeout前stackを保存する。

<a id="product-acceptance-contracts-20261116"></a>

## 承認済みA/B/Cの実施記録（2026-11-16）

ユーザーは2026-11-16のチャットで案A/BとC1/C2を明示承認した。従って上の「承認待ち」は提案時点の記録であり、再度の同じ許可は不要。運用再測定・new attempt・reopen・再fit・実配信承認は依然として対象外。

Aの実装は、誤って通常配信bundleと扱われないよう、IDを`capital-product-acceptance-v3`とした。`product_scope`は元candidate ID・monitor referenceファイルSHA・training reference契約から22ケースの必要証拠を導出する。定数かどうかやMAE未設定を自己申告N/Aで指定できない。結果はrequirements planで、全caseは`not_evaluated`。**この計画の生成成功を22ケース合格とは呼ばない**。現v2の配信consumer・UTC逆行拒否・全bundle受入は維持する。

`validate_isolated_test_authorization`は人がpinしたSHAと、harnessが実環境から読んだproject/source/候補/2登録版のartifact/専用registry/DB/volume/保護対象を照合する。許可期限は最長1時間、操作は版切替・retry・rollback・中断・timeout・metadata・読込不可の試験に限定。実MLflow5000/5001や保護volume、同版・同artifactの2版偽装を拒否する。これは署名/IAMではない。通常loader/承認経路はこの許可を利用せず、試験結果も`blocked/approval_evidence=false`である。harnessへの接続・実2版観測が揃うまでは二版受入を完了扱いにしない。

Bの`CandidateDispositionStore`は別のlocal判断系列に`held/rejected`だけを記録する。採用・配信許可・registry tag・運用台帳を書かない。candidate報告・final・運用plan/report/journal・両台帳・警告review・受入reviewの9原本をNOFOLLOWで読み、bytes/SHAを固定。candidate identityを照合し、期待する前head・連番・UTC順序・排他lock・O_EXCL/fsyncで追記する。途中書込み/破損/未知file/古いhead/競合/消失/将来時刻は拒否し、残った原本を自動削除しない。外部に保持したheadが無ければ整合した全系列巻戻しを防げず、人名も署名ではない。**localな非採用判断であり、remote registryへ保留が発行されたことを意味しない**。

操作入口は`python -m scripts.runtime_tools product-acceptance`。`scope`には`--candidate-report/--reference/--candidate-id`、`read-disposition`には`--root/--candidate-id/--expected-head`を指定する。初回headは明示的に`none`、2件目以降は外部保管SHAが必要。`append-disposition --request`はdecision/reason/reviewer/recorded_at_utc/evidence_files/registry_observation/limitationsを含むprivate JSONを使用する。公開ログへ入力の絶対pathや秘密値を返さない。正式原本のコピーを要求する仕様ではなく、同じroot配下のcanonical原本を参照する。

C1のcold backupは元PG85,688,320bytes・artifact1,433,600bytesをprivate tarへ保存し、PGの別volume復元が成功した。元imageのMLflowは**3.17.1**（表示の推測ではなく固定image内から取得）、DBとimageのmigration headはともに`dc11669786a5`。元8container不変・一時resource回収まで確認した。これは起動前準備であり、live照合/参照発行は別の結果を必要とする。

中断時点（後述の再開で解消）の[検証索引](reviews/capital.json#/reviews/20261116-product-acceptance-checks.json)：新契約14件は成功、既存deployment回帰は300秒timeout。C2参照controlは発行済みで、正式5入力・現source closure・194保護原本はoffline照合済み。C1/C2終了時に診断helperの短縮/完全ID比較が失敗し、結果JSON未保存。再起動による結果回収は自動承認レビューに拒否されたため、元liveの再確認・現在の停止状態をこの索引の確認済み項目に含めない。参照controlの再発行は行わない。製品sourceは未commit/CI未検証、試験専用二版harnessと実保留判断はまだ未完了。

<a id="project-closure-20261116"></a>

## 製品の完了範囲と候補の不採用判断（2026-11-16）

ユーザーから最善の終了判断を委任されたため、候補e2df579…を**配信不採用**と決定した。精度合格は変更しない。有効な運用受入が存在せず、新attempt/再測定を行わない条件では、配信採用の必要条件を満たせない。警告1,336/234窓は重なる窓の診断であり独立事故数ではない。リスク受容は行わず、不採用の判断材料として保存した。

local追記専用dispositionのheadは`ece3b490a552de4ff140660a59c3b6d27d83b6dde8fd2c6c35a44c1179f83b21`。9原本のhash/bytesを結合し、readbackで同headを確認した。これはremote registryの採否tagではない。通常配信は既存v2 consumerが引き続きblockedとする。T13bの「配信採用」は中止し、別契約の非採用判断として閉じる。旧T13b writerの条件を緩めて不正な証拠を受け入れない。

元PG/MLflowの限定再起動は今回明示的に再許可された。保存済みcold backupを検算し、live headと発行済み参照controlの整合、5正式入力/36source、両DB論理manifestの不変を確認した。control再発行0、tag更新0。元2サービスを再停止、他6サービス不変。起動に伴うWAL/logは巻き戻していない。

22ケースは[受入設計表](#acceptance-scope-proposal-20261115)の適用範囲を維持する。旧v2の復元4確認/観測8/部分5/未実証5は過去の事実であり、今回全passedにはしない。不採用候補に対する採用前の追加case、承認後の実配信、中断した承認済み配信の復旧、実配信承認metadataは**採用中止に伴い実施を中止**する。汎用機能はunit/隔離CIの証拠、候補コピーの二登録版/Caddy試験は試験専用証拠として保存し、実候補採用へ代用しない。

時計の原因究明、独立時計資格、新candidate/新運用試行は本製品を採用判断まで完結するためには不要となったため、今回の作業から取り下げる。時計問題は未解決、資格未合格を保持する。後から配信を目指す場合は別プロジェクトとして、新しい承認・未消費評価窓・運用証拠が必要。

全回帰・実装commitのCI/artifact/cleanupは証拠索引の最終状態で判断する。外側timeoutの失敗・環境差は保存し、自然再発がないことを原因解決へ読み替えない。

隔離二版試験（two-versions-v6）は版1→2、保存要求の版1保持、新要求の版2、切替先停止502→明示rollback→ready200を確認した。登録skops本体は両方SHA `bd5764e19f97674366495c24cf12d348ee5f8a9aaf8f8e0078c042679f18fb82`。登録artifact hashは1が13dbe8…、2が3e5ce7…で、差は登録metadata。元8container不変、所有container/volume/network回収済み。操作前に専用leaseを検証した。APIの承認operationはlegacy_unverifiableのままで、通常配信許可ではない。

最終受入：実装`0efc7913fe64832467ad4ce0b293d701c2ba293b`の[CI43853213533](https://github.com/kenobix/Claude_practice/actions/runs/43853213533)両job成功。ローカル全667件（790.464表示秒）成功、CI全667件成功。artifact14221415361は234,870bytes、SHA `2c4cb4811a209014afa11d54669abde9132d174d782118f00c63b075b0bc7d14`。16member、内包hash、ZIPのみ別process再計算、暖機50除外/1,000要求、Docker/PG/HTTP/Caddy/TERMのcleanupを検算した。CIの合成p95は実候補の運用証拠へ流用しない。今回の範囲の残作業は0で、上記中止項目と歴史的原因未確定はそのまま保持する。
