# 検証記録の索引

2026-11-16に80個のJSONを用途別5冊へ統合しました。判定・失敗・制約を含め、元の記録の内容を保持しています。
元の個別ファイルは削除し、関連文書のリンクを更新しました。

| 集約ファイル | 内容 | 記録数 |
|---|---|---:|
| [foundation.json](foundation.json) | 基礎・監視・データ評価の検証 | 16 |
| [deployment-ci.json](deployment-ci.json) | 配信・復旧・CIの検証 | 16 |
| [clock-history.json](clock-history.json) | 時計の原因調査・管理者資料のreview | 17 |
| [clock-validation.json](clock-validation.json) | 時計のoffline・合成部品の検証 | 18 |
| [capital.json](capital.json) | Capital実候補・証拠保全・製品の終了判断 | 13 |

## 最新の結果

[製品の終了判断・最終検証](capital.json#/reviews/20261116-product-acceptance-checks.json)は、
`capital.json`の`reviews["20261116-product-acceptance-checks.json"]`にあります。
候補は精度合格・配信不採用、時計問題は未解決という終了判断を維持しています。

## 読み方と原本の照合

- `reviews[旧ファイル名]`: 元JSONをそのまま解析した内容です。
- `originals[旧ファイル名]`: 元path、サイズ、SHA256、UTF-8原文（`utf8`）です。
- `source_commit`: 整理直前のcommitです。原本はこのcommitの元pathでも確認できます。
- `#/reviews/旧ファイル名`はJSON Pointerです。ブラウザーが該当箇所へ移動しない場合は旧名で検索するか、以下のPython例で取り出してください。
- 内部の過去path・コマンド・hashは当時の記録として保持しています。別の旧JSON名が登場したら、下の対応表から現在の収録先を確認してください。
- `reviews`だけを再整形して計算したhashは元ファイルのhashと一致しません。原本の照合には`originals`のUTF-8原文を使います。独立した照合のため内容と原文を両方収録しており、合計サイズは増えています。
- この整理は文書の保存形式の変更です。既存の正式入力参照・台帳・配信承認の再発行や書換えは行いません。

リポジトリのルートで実行する例（読取のみ）：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
import json

collection = json.loads(Path("261021/docs/reviews/capital.json").read_text(encoding="utf-8"))
name = "20261116-product-acceptance-checks.json"
original = collection["originals"][name]
raw = original["utf8"].encode("utf-8")
assert len(raw) == original["size_bytes"]
assert hashlib.sha256(raw).hexdigest() == original["sha256"]
assert json.loads(raw) == collection["reviews"][name]
print(json.dumps(collection["reviews"][name], ensure_ascii=False, indent=2))
PY
```

## 旧ファイル名から探す

| 旧ファイル名 | 収録先 |
|---|---|
| `20261028-evidence.json` | [foundation.json](foundation.json#/reviews/20261028-evidence.json) |
| `20261028-staging-checkpoint-evidence.json` | [foundation.json](foundation.json#/reviews/20261028-staging-checkpoint-evidence.json) |
| `20261028-synthetic-staging-benchmark.json` | [foundation.json](foundation.json#/reviews/20261028-synthetic-staging-benchmark.json) |
| `20261030-checkpoint-evidence.json` | [foundation.json](foundation.json#/reviews/20261030-checkpoint-evidence.json) |
| `20261030-p0-evidence-checks.json` | [foundation.json](foundation.json#/reviews/20261030-p0-evidence-checks.json) |
| `20261030-t03-checks.json` | [foundation.json](foundation.json#/reviews/20261030-t03-checks.json) |
| `20261030-t04-checks.json` | [foundation.json](foundation.json#/reviews/20261030-t04-checks.json) |
| `20261030-t05-checks.json` | [foundation.json](foundation.json#/reviews/20261030-t05-checks.json) |
| `20261030-t06-design.json` | [foundation.json](foundation.json#/reviews/20261030-t06-design.json) |
| `20261030-t06a-checks.json` | [foundation.json](foundation.json#/reviews/20261030-t06a-checks.json) |
| `20261030-t06b-checks.json` | [foundation.json](foundation.json#/reviews/20261030-t06b-checks.json) |
| `20261101-admin-owner-checks.json` | [clock-history.json](clock-history.json#/reviews/20261101-admin-owner-checks.json) |
| `20261101-chrony-control-checks.json` | [clock-history.json](clock-history.json#/reviews/20261101-chrony-control-checks.json) |
| `20261101-clock-origin-checks.json` | [clock-history.json](clock-history.json#/reviews/20261101-clock-origin-checks.json) |
| `20261101-clock-owner-checks.json` | [clock-history.json](clock-history.json#/reviews/20261101-clock-owner-checks.json) |
| `20261101-t03-t13-diagnostics.json` | [clock-history.json](clock-history.json#/reviews/20261101-t03-t13-diagnostics.json) |
| `20261101-t07-t17-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t07-t17-checks.json) |
| `20261101-t07-t17-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t07-t17-ci-checks.json) |
| `20261101-t08-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t08-checks.json) |
| `20261101-t13-http-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t13-http-checks.json) |
| `20261101-t13-http-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t13-http-ci-checks.json) |
| `20261101-t15-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261101-t15-checks.json) |
| `20261102-t07b-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261102-t07b-ci-checks.json) |
| `20261102-t07b-t17b-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261102-t07b-t17b-checks.json) |
| `20261102-t16-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261102-t16-checks.json) |
| `20261102-t16-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261102-t16-ci-checks.json) |
| `20261102-t16-sol-review.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261102-t16-sol-review.json) |
| `20261103-chrony-runtime-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261103-chrony-runtime-checks.json) |
| `20261103-chrony-runtime-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261103-chrony-runtime-ci-checks.json) |
| `20261103-clock-extra-checks.json` | [clock-history.json](clock-history.json#/reviews/20261103-clock-extra-checks.json) |
| `20261103-clock-followup-checks.json` | [clock-history.json](clock-history.json#/reviews/20261103-clock-followup-checks.json) |
| `20261103-orm-compatibility-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261103-orm-compatibility-checks.json) |
| `20261103-t17b-t07a-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261103-t17b-t07a-checks.json) |
| `20261103-t17b-t07a-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261103-t17b-t07a-ci-checks.json) |
| `20261103-upload-action-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261103-upload-action-checks.json) |
| `20261103-upload-action-ci-checks.json` | [deployment-ci.json](deployment-ci.json#/reviews/20261103-upload-action-ci-checks.json) |
| `20261104-backend-boundary-checks.json` | [clock-history.json](clock-history.json#/reviews/20261104-backend-boundary-checks.json) |
| `20261104-clock-rate-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261104-clock-rate-checks.json) |
| `20261104-clock-rate-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261104-clock-rate-ci-checks.json) |
| `20261104-clock-readiness-checks.json` | [clock-history.json](clock-history.json#/reviews/20261104-clock-readiness-checks.json) |
| `20261104-followup-checks.json` | [clock-history.json](clock-history.json#/reviews/20261104-followup-checks.json) |
| `20261104-qualification-design-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261104-qualification-design-checks.json) |
| `20261105-clock-acquisition-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-acquisition-checks.json) |
| `20261105-clock-acquisition-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-acquisition-ci-checks.json) |
| `20261105-clock-acquisition-startup-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-acquisition-startup-checks.json) |
| `20261105-clock-campaign-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-campaign-checks.json) |
| `20261105-clock-campaign-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-campaign-ci-checks.json) |
| `20261105-clock-campaign-missing-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261105-clock-campaign-missing-checks.json) |
| `20261106-clock-scheduler-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261106-clock-scheduler-checks.json) |
| `20261106-clock-scheduler-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261106-clock-scheduler-ci-checks.json) |
| `20261106-t09-checks.json` | [foundation.json](foundation.json#/reviews/20261106-t09-checks.json) |
| `20261106-t10-grouping-checks.json` | [foundation.json](foundation.json#/reviews/20261106-t10-grouping-checks.json) |
| `20261106-t10-grouping-ci-checks.json` | [foundation.json](foundation.json#/reviews/20261106-t10-grouping-ci-checks.json) |
| `20261106-t10-query-checks.json` | [foundation.json](foundation.json#/reviews/20261106-t10-query-checks.json) |
| `20261108-clock-bundle-budget-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261108-clock-bundle-budget-checks.json) |
| `20261108-clock-bundle-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261108-clock-bundle-checks.json) |
| `20261108-clock-bundle-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261108-clock-bundle-ci-checks.json) |
| `20261108-t10-snapshot-checks.json` | [foundation.json](foundation.json#/reviews/20261108-t10-snapshot-checks.json) |
| `20261109-admin-resources-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-admin-resources-checks.json) |
| `20261109-clock-bundle-input-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261109-clock-bundle-input-checks.json) |
| `20261109-clock-bundle-input-ci-checks.json` | [clock-validation.json](clock-validation.json#/reviews/20261109-clock-bundle-input-ci-checks.json) |
| `20261109-host-admin-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-host-admin-checks.json) |
| `20261109-host-cause-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-host-cause-checks.json) |
| `20261109-nict-method-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-nict-method-checks.json) |
| `20261109-prerequisite-review-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-prerequisite-review-checks.json) |
| `20261109-resync-result-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-resync-result-checks.json) |
| `20261109-task-source-checks.json` | [clock-history.json](clock-history.json#/reviews/20261109-task-source-checks.json) |
| `20261114-capital-evaluation-checks.json` | [capital.json](capital.json#/reviews/20261114-capital-evaluation-checks.json) |
| `20261114-capital-monitor-checks.json` | [capital.json](capital.json#/reviews/20261114-capital-monitor-checks.json) |
| `20261114-capital-operational-restore-checks.json` | [capital.json](capital.json#/reviews/20261114-capital-operational-restore-checks.json) |
| `20261114-capital-runtime-checks.json` | [capital.json](capital.json#/reviews/20261114-capital-runtime-checks.json) |
| `20261114-capital-runtime-docs-ci-checks.json` | [capital.json](capital.json#/reviews/20261114-capital-runtime-docs-ci-checks.json) |
| `20261114-product-resume-checks.json` | [capital.json](capital.json#/reviews/20261114-product-resume-checks.json) |
| `20261115-file-consolidation-checks.json` | [capital.json](capital.json#/reviews/20261115-file-consolidation-checks.json) |
| `20261115-operational-consumer-checks.json` | [capital.json](capital.json#/reviews/20261115-operational-consumer-checks.json) |
| `20261115-operational-review-checks.json` | [capital.json](capital.json#/reviews/20261115-operational-review-checks.json) |
| `20261115-review-archive-checks.json` | [capital.json](capital.json#/reviews/20261115-review-archive-checks.json) |
| `20261115-review-recovery-checks.json` | [capital.json](capital.json#/reviews/20261115-review-recovery-checks.json) |
| `20261115-runtime-followup-checks.json` | [capital.json](capital.json#/reviews/20261115-runtime-followup-checks.json) |
| `20261116-product-acceptance-checks.json` | [capital.json](capital.json#/reviews/20261116-product-acceptance-checks.json) |
