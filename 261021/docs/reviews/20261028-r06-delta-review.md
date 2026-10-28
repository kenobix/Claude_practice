# 前回確認後の差分レビューとR06の判断

確認日: 2026-10-28。差分の基準は`9131b1ff0a3f2036aab1c5acb4ca0bc641cb2963`、commit時刻は2026-10-28 15:38:10 JST（06:38:10 UTC）。今回のユーザー依頼は「前回以降のログだけを確認し、3点を判断して残作業を更新する」。過去の全モデルや全原本の再監査は行っていない。

## 前回以降の確認結果

| 対象 | 方法 | 結果・証拠の範囲 |
|---|---|---|
| Git | `git status --short`、`git log`、`git diff 9131b1f --stat` | 作業開始時のHEADは基準commitと同じ。追跡ファイルの変更0。以前からある無関係な未追跡ファイルは対象外。 |
| ローカル成果物 | 基準commit時刻より後のmtimeを列挙 | reports 20件、raw ZIP 41件、derived 4件、UCI runtime 5件、Capital SQLiteに新しい更新はなし。candidate bundle/評価台帳はなし、Capital runtime directoryも未作成。mtime確認であり、全原本のhash再監査ではない。 |
| Docker差分ログ | `docker compose logs --since '2026-10-28T15:38:10+09:00' --timestamps --no-color postgres mlflow api-blue api-green caddy` | 取得成功、新しいログ0行。ログが増えていないため、新しい障害報告もなかった。ログ未出力の処理の成功までは証明しない。 |
| コンテナ状態 | `docker compose ps --format json` | UCIの5サービスがrunning、PostgreSQLはhealthy。今回API readyを再probeしておらず、単にrunningで配信可能とは判定しない。 |
| MLflow差分 | experiment一覧から`runs/search`を基準時刻以降でfilter。`model-versions/search`のcreation/last-update時刻を比較 | 新規run 0、新規/更新model version 0。どちらも残りpageなし。 |
| UCI運用DB差分 | `psql`によるSELECT。predictions.created_at、deployments.updated_at、monitor_windows.created_atを基準時刻と比較 | 新規prediction 0、配信作成/更新0、新規monitor window 0。DB内容は変更していない。 |
| 前回最終commitのCI | `gh run view 40645058486 --json headSha,status,conclusion,url,updatedAt,jobs` | HEAD `9131b1f`に一致、success。lock install、pip check、テスト、compileall成功、完了06:39:23 UTC。 |

確認した保存先・MLflow・運用DBには、前回以降の追加学習・評価・登録・配信の証拠がなかった。評価済み期間を再採点して確認する操作はしていない。外部の未記録処理まで含む全活動の不在を保証するものではない。

CI参照: [run 40645058486](https://github.com/kenobix/Claude_practice/actions/runs/40645058486)。公式月次archiveは今回再取得・再照会していないため、前回のHTTP 200/404を現在の公開状態として断定しない。

## 関連する前回文書の修正

1. **学習締切とラベル到着:** 前回案には5月31日23時のラベルが6月1日02時に利用可能とあったが、validationは6月1日00時開始だった。このラベルを含めると予測開始より後の情報になる。`evaluation_protocol.py`の`training_cutoff <= validation_start`に合わせ、締切を00時、最後の学習targetを5月31日21時と定義した。22時・23時を除外する。候補実験前の文書修正で、既存の評価値は変更していない。
2. **timezone再現性の説明:** 前回work_logは`tzdata==2026.5`のinstallだけでOSから独立したと説明していたが、これは誤り。今回の`zoneinfo.TZPATH`はOSの検索pathで、`/usr/share/zoneinfo/America/New_York`が存在する。Python標準ライブラリの`find_tzfile`もTZPATHを先に検索する実装だった。package版は固定されているが、利用providerの版を固定・記録した証拠はない。R04/P0に未完了項目を追加した。既存のDST集計が誤っていたという発見ではない。
3. **運用計測の仕様不足:** 旧案の「入力/応答固定」だけでは、同じrequest IDの再送で保存済み応答を測ってしまう。全要求を新規IDにし、暖機50件、測定1,000件、同時数1、接続再利用、再試行0、timeout10秒、HTTP/schema/識別検証、失敗を含む全標本のp95計算を定義した。
4. **現行状態の説明:** runbookに残っていた「現行の最終評価が再fitする」という説明を、過去の実験とP0修正後の保存済みartifact読み込みに分けて修正した。初回Capitalの経路と今回決めたgateは依然未実装。

日時算術はPythonの`datetime/timedelta`で確認した。validationは1,464時間、後続評価は1,461時間、選択ラベル到着時刻は8月1日02時。対象期間にDST遷移はない。実データ件数は取得・集計後に検証する。

## 3点の判断

ユーザーがCodexへ判断を委任したため、追加の確認待ちにはせず次を採用する。詳細は[評価基準](../capital-bootstrap-gate.md)。今回は文書への仕様確定であり、実装済み・実測合格とは扱わない。

1. **RMSEもgateにする。** validationと後続評価のそれぞれで、同じ行のbaselineに対してMAEを厳密改善、RMSEは以下とする。MAEだけが改善しRMSEが悪化した候補を通さない。個々の最大誤差の改善は保証しない。指標の欠落/非有限、行不一致も拒否する。
2. **ローカル運用目標を採用する。** 暖機後の逐次1,000要求、エラー0、p95 250ms以下。これは事前に選ぶ学習用目標。実測から逆算した閾値や本番SLOではない。profile/model/hashの誤りもエラーに含める。
3. **区間別は診断値にする。** weekday/weekend、朝7〜9時・夕方16〜18時の件数、候補/baseline MAEと差を記録する。業務の優先区間・許容誤差が未定義のため自動gateは設けず、手動採否で偏りを説明する。全体gateの不合格を手動で上書きしない。

## 更新後の残作業

正規の一覧は[計画書11.4](../../../261014/plan_mlops.md#114-r06判断後の残作業の実行順序)。順番はR04 timezone固定→R06 bootstrap/gate実装→原本範囲・buffer確定と取得/監査/protocol凍結→validationと同一artifact後続評価→R05隔離runtimeとR06運用測定→R07監視→R08/R09復旧・運用metadata→R10成果説明とした。

今回、新しい候補fit・評価窓の消費・登録・配信・負荷試験は実施していない。ソース/依存は変更していないため、ローカルでの44テスト再実行は省略して前回CI結果を参照し、変更した文書のリンク・日時/件数算術・差分を検査する。push後のCIはworkflowにより自動実行される。

変更後の検査結果: 追加/変更行と新規レビュー内のローカルリンク11件（anchorを含む）は全て解決し、日時/件数算術と`git diff --check`も成功した。
