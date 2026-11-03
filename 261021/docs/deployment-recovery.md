# 配信復旧の状態・承認metadataと隔離受入（T07a/T17a）

2026-11-02追記: 以下のT07a/T17aは当時の境界を保持する。追加した実MLflow/skops対応復元は本書末尾、通常CLIの部分的な段階表示・読取診断は[配信処理の診断](deployment-observability.md)を参照。

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

## 2026-11-01 CI追跡

実装f341e94の[CI検証索引](reviews/20261101-t07-t17-ci-checks.json)で、全369件と既存合成system/PostgreSQL/cleanup成功・artifact hashを確認した。本書の新復旧harnessの実Caddy受入自体はlocal実行で、CI接続は残る。local全suiteの既存UTC拒否も取り消さない。

## T07b: 合成Capitalの実MLflow・skops・DB対応復元

`capital_system_harness.py --run-synthetic-system --verify-paired-restore`で、現Dockerfileからbuildした独自imageを使う。
既存のblocked candidate登録・API・監査・計測が終わった後、専用APIとMLflowを止め、registry DBと予測監査DBを`pg_dump -Fc`で採取し、MLflow artifact volumeの全通常fileを対応させる。
専用fixtureには他writerが存在しない。DB内容のcanonical hash・全public table件数・column定義・sequence値を採取前後で比較する。稼働中の一般システムに対する無停止backup手順ではない。

復元先は別UUIDのCompose project、新しい3 volume、別のランダムloopbackポートを使う。
同一の現Dockerfile image IDを指定して、空のDBへ復元し、MLflowを起動する前にDB全内容・column/sequence・artifact相対path/byte数/hashを比較する。
MLflowへ登録済みのversion・candidate・report/protocol hash・blocked tagを保持し、staging APIの通常loaderでhash確認後にskopsモデルを読む。
保存済み要求への再送が同じ版・予測を返すことと、新しいrequest IDでも同じ予測を生成することを別々に確認する。
復元したskops fileだけを破損させた負例では、通常loaderがSHA不一致で拒否し、保存済み原本へ戻した後にAPIを起動する。
旧stateのPostgreSQL読取診断も、対応行なし/別profileを合成fixtureで確認する。

artifactは通常fileとdirectoryだけを受け入れる。欠落・余剰・同件数の値変更・symlinkは照合を拒否する。
sourceとrestore両projectのlabelを確認して回収し、現配信のcontainer ID不変を確認する。
native dump・artifact原本は一時directoryだけに置き、CIへアップロードしない。CIの既存`synthetic-system-ci.log`へ、相対file hash、table/column/sequenceの照合値、版、拒否結果、別volume/cleanup結果を保存する。
T16のarchive3本のallowlistは増やさない。実装commitのCI確認までは、この追加部分のCI受入を完了としない。

合成Capitalで実MLflow/skopsを動かす試験であり、実候補の復旧producer、実承認、時計資格、実環境の災害復旧証拠ではない。
T07aの実Caddy/SIGKILLによる切替復旧harness自体のCI接続と、実candidateに結合したproducer証拠は引き続き残る。

### T07bの実装CI確認（2026-11-02）

実装`2414b20`の[CI41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218)は419 tests/54.813秒・両job全stepが成功。[照合索引](reviews/20261102-t07b-ci-checks.json)に保存した。
artifact13150206991の外側ZIP SHAは`4c010bcb5591152645c6004a38d99ffac21d61137fcefb2e3ddfb62b4461f802`、全13 memberのhashを照合した。
registry65table・audit5table（744観測/1052予測）・column/sequence・8 artifactの対応、別3volume、破損モデル拒否、同ID再送/新ID推論を確認した。
restoreの残存container/network/volume/imageがすべて0、workspace回収済み、source project cleanupと保護container ID不変も確認できた。
T16の内側ZIPは`c6ca847386fe751a8d559a6b1ffc2e41e2b90e3eaada4581c22ce5fd82cab66d`で、archiveだけを渡した別processの再計算も一致した。
T07bの合成対応復元を完了とし、T07a復旧harnessのCIと実producerは別の未完了条件として維持する。

## 2026-11-03 T07a更新: 現DockerfileとTERM cleanup

上記のcached image/tzdata複製は旧試行の記録。現在のharnessはallowlistで複製したworkspaceの現Dockerfileを固有tag・UUID labelでbuildし、image ID/labelを確認してAPI/worker/復元APIへ渡す。既存stackのtagを上書きしない。全resource回収後はimageも同じ所有確認をして削除し、残存0と保護container ID不変を確認する。

`--term-after-startup`は実PG/API/Caddyの起動後にharness自身へTERMを送り、非0終了・signal記録・cleanupを確認する専用負例。通常runの3停止点SIGKILL/recoverでは、新しいAPI操作binding・state・同じ台帳行も照合する。復元APIの元要求再送はlegacy起動であり、新しい配信操作の承認を作らない。T07bの実MLflow/skopsとは引き続き別の合成受入である。

CIにはnormal/TERMの2 runと明示したlog/summary3 fileだけを追加する。dump、環境file、raw HTTP、model資料は公開allowlistに加えない。CI実装commitのartifact/member hashと両runのcleanupが確認されるまではCI受入を完了扱いにしない。build client自体のtimeoutでdaemonに作業が残る場合の取消は保証せず、残存またはcleanup未確認は成功にしない。
