# 配信処理の進み具合・timeout・旧stateの読取診断（T17bの一部）

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

**残る制限:** MLflow registry/artifactダウンロード自体を別processで打ち切るend-to-end timeoutは未実装。
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
