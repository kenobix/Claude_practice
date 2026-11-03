# 配信処理の進み具合・timeout・旧stateの読取診断（T17b）

`deploy promote`と`deploy recover`は、配信の成否とは別に、現在の処理段階をstderrへJSON Linesで表示する。通常の最終結果JSONはstdoutに残る。
同じ診断行は、対象profileのruntime directoryにある`deployment-progress.jsonl`へ追記する。これは承認証拠や配信台帳の代用品ではない。

表示する段階はregistry照合、Compose build/起動、候補のready待ち、artifact取得と予測比較、proxy操作、recover。
開始・15秒ごとの処理中・段階終了・中断/失敗を記録し、UTCとprocess内のMONOTONIC経過秒を分ける。
UTCが逆行しても経過時間はUTC差から算出しない。段階終了は、その段階の関数が返ったという意味で、配信承認やAPIの正常稼働全体を示さない。
prepared後のbuild/ready/probeにはdeployment IDを付ける。registry事前確認やrecoverの外枠ではIDが未確定のためnullになり、process IDとlog内の順序で区別する。
コマンド本文、環境変数、承認理由、接続文字列、Dockerのnative出力は診断行へ入れない。logは新規作成時0600で、symlinkや通常file以外への追記を拒否する。

## timeoutの意味

通常CLIのCompose build/起動は600秒、Caddy validate/reload等は30秒を上限にする。
この上限はDocker clientを待つ時間であり、daemon側のbuildや起動が取り消されたことを保証しない。
promote中のDocker timeoutは、台帳を`in_progress / recovery_required`のまま保存し、切戻し成功や配信失敗の確定を自動申告しない。
まず対象profileで`deploy inspect`を実行し、診断logと入口・runtime・台帳を確認してから、手動で`deploy recover`を判断する。
通常のready失敗には既存の補償と旧版確認を適用し、補償未確認なら同じく未確定を残す。

**2026-11-02時点の制限（後述2026-11-03節でCLI全体deadlineを実装）:** MLflow registry/artifactダウンロード自体を別processで打ち切るend-to-end timeoutは未実装。
その処理中のheartbeatを追加しただけで、全配信が一定時間内に終わるとは主張しない。Dockerのtimeout値を運用p95や本番SLOに流用しない。

## 読取専用の旧state診断

```bash
cd /home/kenshin/work/claude_practice/261021
./scripts/bikectl deploy inspect
# Capitalでは、使っているprofileを明示する。実DBへの実行は運用者が判断する。
DATA_PROFILE_ID=capital-bikeshare ./scripts/bikectl deploy inspect
```

このコマンドはDBを初期化せず、stateを書換えず、registryや配信へ接続しない。
SQLiteは`mode=ro`と`query_only`、PostgreSQLはread-only repeatable-read transactionとquery/lock/connect timeoutを使う。
DB profileを確認してから、未完了行とstateの`last_deployment_id`に対応する行を読む。UTC順で最新の承認を推定しない。
stateは通常file・64KiB以下・重複JSON keyなしを要求し、読取前後でfile identityとbytesを確認する。

| 状態 | 終了値 | 読み方 |
|---|---|---|
| `matched` | 0 | 現stateと対応するcompleted行の版・slot・操作ID・承認者/理由が一致。配信承認やモデル真正性の証明ではない |
| `metadata_mismatch` / profile不一致 | 1 | 差異のある項目名を表示する。旧承認情報を上書きしない |
| `legacy_unverifiable` | 2 | bootstrap等の対応行がない。欠けた履歴を作らない |
| `incomplete_deployment_requires_reconciliation` | 2 | 未完了操作がある。処理の継続・遅延完了を調査する |
| `unavailable` / `state_changed_during_inspection` | 2 | 読めない・形式不正・読取中に変更。正常と確定しない |

既存stateの自動移行、APIへの操作metadata提示と厳密照合、registry/artifactのprocess全体timeoutは後続条件。
T17b全体をこの部分実装だけで完了扱いにしない。

## 2026-11-03 T17b: 操作の照合とCLI全体の待機上限

以降の新規promoteはschema3 contextをprepared行へ保存し、`DEPLOYMENT_ID`と`DEPLOYMENT_SLOT`を候補APIの環境へ渡す。APIは起動時にこの組を固定し、`/version`と`/ready`で`deployment_operation`を提示する。DB profile確認後、その操作IDの行だけをread-only transactionで読む。モデル版やUTCによる最新行検索は行わない。

照合対象はprofile/target・操作ID・to_slot/to_version・準備時に固定したartifact SHA・実ロードSHA・申告された操作者/理由。stateにも同じ`deployment_operation`を保存する。台帳が`in_progress`なら`matched_pending`、completedなら`matched_completed`と表示し、readyだけで操作完了とはしない。`approval_evidence:false`を維持する。名前・理由は操作者の申告で、本人認証や独立した審査の証明ではない。URI/credential形の申告はAPIへ表示せず`unsafe_declaration`にする。

| APIの状態 | 意味 |
|---|---|
| `matched_pending` / `matched_completed` | 指定した1行と稼働モデルのidentityが一致。`/ready`は200になり得る |
| `legacy_unverifiable` | 起動時に操作IDなし。旧API/bootstrapとの互換用で、過去の承認を補作しない |
| `operation_missing` / `invalid_operation_binding` | 指定行なし、または操作ID/slot不正 |
| `database_profile_mismatch` / `ledger_profile_mismatch` | DBまたは対象行のprofile/target違い |
| `runtime_identity_mismatch` / `artifact_identity_mismatch` / `operation_metadata_mismatch` | 版/slot、実hash、固定された操作metadataの不一致 |
| `operation_not_active` / `unavailable` | failed行、読取失敗等。照合成功としない |

legacy以外の不一致では`/ready`は503、`/version`は診断状態だけを返し、native SQL/URI/例外本文を出さない。新規候補の配信はmatchedな操作bindingを要求する。旧APIへのrollbackは現ソースをbuildして旧モデルをロードする方式を維持し、古いバイナリ自体の履歴照合を保証しない。contextなし/schema1/schema2のrecover互換経路は保持し、schema3へ自動昇格しない。

通常CLIの`deploy promote`は全体900秒、`deploy recover`は300秒のMONOTONIC deadlineを持つ専用process groupで動く。registry/artifact取得・hash照合・deserialize・probe・proxy・state/台帳確定まで含む。モデルobject/pickleを親へ返さず、有限値のJSONだけを返す。Docker/HTTPの個別上限は全体残時間を超えない。段階logに`remaining_seconds`を加え、モデル取得の各段階・probe HTTP・state/台帳確定を区別する。

期限/TERM/INT時は所有PIDの開始identityとPGIDを照合してTERM、最大5秒後にKILL、leader終了待ち最大2秒と子孫停止確認最大2秒を設ける。これはOSスケジューリングを含む厳密なリアルタイム保証ではない。cleanupが確認できなければ非0で未確定を報告する。prepared後の中断はin_progress/recovery_requiredを残し、強制KILLで更新できなくてもprepared等の未完了行が残る。registry事前確認だけで失敗した場合は配信行を作らない。

Docker daemonや送信済みHTTPの完了はprocess停止で取消保証できない。固定probe request ID `deploy-probe:<deployment_id>`と台帳・入口・stateを調査し、必要なら手動recoverする。自動retryは行わない。completed commit後の応答喪失でも完了行を未完了へ戻さず、結果はinspectで確認する。失敗時の補償も同じ全体予算内であり、補償時間だけを無制限に延長しない。

この上限はCLI配信操作の上限で、API起動自身のloader時間や予測応答SLOを変更するものではない。合成試験の時間を実Capital性能・時計資格として使わない。受入原本・失敗・未達ケースは[今回の索引](reviews/20261103-t17b-t07a-checks.json)に追記する。

Composeへ必要なDB設定3項目（`APP_DB_PASSWORD`、`MLFLOW_DB_PASSWORD`、`POSTGRES_ADMIN_PASSWORD`）と`APP_DB_HOST_PORT`も子processの明示環境へ渡す。環境変数だけの構成とport上書きを維持し、これらの値をargv/診断logへ表示しない。その他の無関係な環境変数を一括継承しない。

最終確認は実装4a4474eの[CI索引](reviews/20261103-t17b-t07a-ci-checks.json)。全447件・artifact/cleanupまで確認済み。冒頭の部分実装に関する記述は2026-11-02時点の履歴であり、現在のCLI契約は2026-11-03節を使う。
