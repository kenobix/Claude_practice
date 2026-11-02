# 証拠の入口と5分の説明手順（T14・初版）

現在の優先度は[計画§11.6](../../261014/plan_mlops.md#remaining-work-20261030)、コマンド・失敗・仮説の履歴は[作業log](../work_log.md)を正規の入口とする。
レビューJSONはその時点のsnapshotなので、後続のCI確認JSONと区別する。記録だけの後続commitを、先行CIで試験した実装commitとは呼ばない。

| 対象 | 実装commit / CI | 確認できる証拠 | 限界 |
|---|---|---|---|
| T07a/T17a | `f341e94` / [41298859518](https://github.com/kenobix/Claude_practice/actions/runs/41298859518) | [local受入](reviews/20261101-t07-t17-checks.json)・[CI索引](reviews/20261101-t07-t17-ci-checks.json)。実Caddy切替/SIGKILLと台帳復旧はlocal、既存system/PGはCI | local復旧harnessのmodel/registryはdouble。新harness自体のCI接続は未了 |
| T13a | `f1cc9ff` / [41300378098](https://github.com/kenobix/Claude_practice/actions/runs/41300378098) | [HTTP障害CI](reviews/20261101-t13-http-ci-checks.json)。部分保存、commit後の応答喪失、同値retry、競合拒否、cleanup | 使い捨て実MLflow HTTPに対する合成決定。実人間の採否ではない |
| T16 | `2492abc` / [41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156) | [Sol review](reviews/20261102-t16-sol-review.json)・[CI索引](reviews/20261102-t16-ci-checks.json)。4原本/manifest/hash、archive-only別process再計算、cleanup | 1050応答からwarmup50を除く1000件。合成計測で実性能・時計資格・承認を証明しない |
| T07b/T17b部分 | 基点`69c3a5a`からの今回変更 | [復元契約](deployment-recovery.md)・[CLI診断](deployment-observability.md)。最新の検証索引/CI確定状態は計画と作業log参照 | T07実producer、T17b API metadata/全体timeoutは未完了 |

artifactの公開保管は14日で失効し得る。索引のSHAは取得時のbytesを示し、期限切れartifactを再取得できる保証ではない。
raw logや一時dumpをcommitしたと主張しない。手元の`data/reports/`に残す原本は公開索引の相対path・bytes・SHAで照合する。
T16のarchiveだけが手元にある場合の再計算手順は[計測証拠アーカイブ](measurement-evidence-archive.md)を使う。
CI artifact以外の実DB原本、`.env`、認証値、実候補データを説明用にコピーしない。

## 5分で説明する順番

1. **0〜1分:** [README](../README.md)で、需要予測モデルと、それを安全に登録・監査・切替する基盤の目的を説明する。UCIの実験履歴と合成Capital試験の区別を先に伝える。
2. **1〜2分:** MLflowは実験・登録モデルの管理画面。`training-report.json`のMAE/RMSEは予測誤差で、APIの応答時間ではない。学習・評価済み期間を次候補の選択へ再利用しない。
3. **2〜3分:** T16のCI索引で実装commit・run・artifact SHA・1000 measured・p95・warmup除外を示す。p95は応答時間であり、合成PC試験から本番SLOを合格としない。
4. **3〜4分:** 配信復旧文書で、同一request IDの再送は元の予測を返すこと、台帳とartifactを対応させて復元すること、timeoutでは未確定を残すことを示す。
5. **4〜5分:** 計画書で、時計資格、独立した新データ、実候補の精度/監視/復旧/承認がまだ必要と説明する。合成成功を実採否へ格上げしない。

説明のためにlive環境を変更する必要はない。CLIの確認や新しい障害試験は、[runbook](runbook.md)に従い別途実行する。
用語は[用語集](glossary.md)、時計の限界は[中断と時計診断](interruption-clock-diagnostics.md)を参照する。
