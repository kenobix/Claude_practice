# T17b残件とT07a CI接続のSol向け引継ぎ

実装基点`2414b2031dd21cdf425761b1cd29d43c7a355f66`。後続の記録commitも読み、[計画§11.6](../../261014/plan_mlops.md#remaining-work-20261030)を正規の優先順位とする。
段階表示・Docker client timeout・read-only旧state診断の現状は[配信処理の診断](deployment-observability.md)、対応復元の境界は[配信復旧](deployment-recovery.md)を確認する。

## 1. API・state・台帳の操作metadata

現APIはmodel/version/hashを提示するが、配信操作のID・承認者/理由を台帳へ結び付けて提示する経路はない。
新候補を起動する前にprepared行の操作IDを固定し、APIの起動環境からその1行だけを読む方式等を検討する。
「同じ版の最新行」をUTC順で選んではいけない。再配信やrollbackで同じ版が再登場するため、版だけの検索も承認metadataの根拠にしない。

受入条件:

- DB profile/target、操作ID、to_slot/to_version、preparedの固定artifact identityと、実際にロードしたモデルhashが一致すること。
- 表示する承認者/理由はユーザーが記録した申告情報で、認証済みidentityや独立した人の審査とは呼ばない。
- API起動時は台帳がprepared/in_progressでもよいが、表示は未確定であること。API readyだけでcompleted/承認済みとしない。
- normal completion・proxy switch直後停止・state書込直後停止・recover後に、API/state/同じ台帳行のmetadataを照合する。
- 不一致、欠落、別profile、別artifact、別操作ID、legacy/bootstrap行なしは区別する。旧承認情報の自動捏造・上書きを行わない。
- 古いAPI/旧stateへの互換方針を先に明示する。旧版へ切戻せることと、その版の過去操作metadataを検証できることを混同しない。
- APIにDB初期化以外の新しい書込経路を増やさず、秘密情報・native SQL/URIをresponseへ出さない。

## 2. registry/artifact・予測probeの全体deadline

現行600秒/30秒はDocker clientを待つ上限だけである。registry/artifactはheartbeatがあっても長時間ブロックし得る。
別processへの隔離とMONOTONIC deadlineを検討し、取得済みモデルをpickleで親へ渡さない。親が必要なversion/hash/gate/finite prediction等を検証可能なJSONとして返す方式を優先する。
argv・stdout・診断logへcredentialを出さず、最小の明示環境を使う。signal/killは自分が作ったprocess groupと開始identityを照合して行う。

受入条件:

- registry待機、artifact取得、hash照合、deserialize、probe HTTP、proxy/state/台帳確定の各段階を区別する。
- operation全体のdeadline・各段階の残時間・補償/cleanupの上限と例外を先に固定する。threadの待機終了だけをchild取消成功としない。
- HTTP/daemonがtimeout後に完了する負例を用意し、既に送った要求を未送信と決めず、固定request ID・台帳を調べられること。
- prepared後のtimeout/中断はpending/recovery_requiredを残す。registry確認前の失敗と、送信後の未確定を同一状態へ潰さない。
- 正常完了、TERM、KILL、timeout、応答喪失、子process未終了、cleanup失敗、UTC逆行の合成ケースを別attempt/logへ保存する。
- future拒否緩和・時刻clamp・成功するまでの自動retryで回避しない。過去T03の自然停止を未再発だけで解決扱いにしない。

## 3. T07aの実Caddy切替復旧harnessをCIへ

`test_deployment_recovery_system.py`はlocalで実Caddy/API/PGを通しているが、現状はcached `bike-mlops-api-blue` imageを要求し、新harness自体はCI未接続。
使い捨てworkspaceから現Dockerfileで固有tag/label付きimageを作り、元stackのtagを上書きしない。
worker/復元APIにもimage identityを引継ぎ、TERMを含む失敗時に所有を確認してcontainer/network/volume/imageを回収する。
CI接続前に、同ID並行/409、3停止点、rollback/recover、5table/モデル資料復元、timeout終了値伝播を確認する。
公開artifactは明示allowlistとし、raw credentials・環境file・実DBを含めない。CI実装commitのlog/hash/cleanupまで確認して完了にする。
T07aのmodel/registry doubleと、T07bの実MLflow/skopsは異なる受入である。実候補producerとして使うには別の同一candidate結合が必要。

## 共通の停止境界と報告

OS・時計設定・実DB・実registry・現在の配信は変更しない。実candidateのfit/評価はT09/T10等の前提を確認してから。
schema/writer・承認の信頼境界を変更する必要があれば、Solが仕様と負例を先に整理する。Lunaへ未定義の判断を委ねない。
独立して進められる実装・負例・文書整備を可能な限り多く行い、全試行・失敗・コマンド・結果・hash・限界を保存する。
確認済みの節目ごとに対象pathだけcommit/pushし、CIを接続した場合は同じ実装commitのartifactとcleanupまで確認する。
最後に完了項目・残件・次のSol/Lunaへの依頼文を提示する。

## 2026-11-03実装後の追跡

本書の「未実装」は基点の状態である。今回のAPI操作binding/schema3・CLI process全体deadlineと現image/TERM cleanup付きT07a CI接続の判定は[最新索引](reviews/20261103-t17b-t07a-checks.json)、残件の正規状態は計画§11.6の2026-11-03追記を参照する。CI artifactを確認するまで受入完了へ昇格しない。T02の実producer、時計資格、T09以降の実候補評価は別条件である。

2026-11-03最終確認: 実装`4a4474e`の[CI41559833990](https://github.com/kenobix/Claude_practice/actions/runs/41559833990)で全447件・Caddy normal/TERM・対応復元・artifact16 member/hash/cleanupが成功。[最終CI索引](reviews/20261103-t17b-t07a-ci-checks.json)を確認する。合成受入とSQLite OS補足は完了し、実producer・時計資格・新原本からの採否は計画§11.6の残件に従う。
