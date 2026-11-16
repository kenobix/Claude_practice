# 検証記録の索引

80件の検証記録を用途別5ファイルにまとめています。元の判定、失敗、制約は変更していません。
旧ファイル名が分かる場合は、末尾の対応表から収録先を探せます。
JSON（JavaScript Object Notation）は、項目名と値を組にして保存する形式です。

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

| 項目 | 保存している内容 |
|---|---|
| `reviews[旧ファイル名]` | 元の記録をJSONとして解析した本文 |
| `originals[旧ファイル名]` | 元の保存先、サイズ、ハッシュ、文字列として保存した原文（`utf8`） |
| `source_commit` | 整理直前のGitコミット。元の保存先と組み合わせれば当時のファイルも確認できる |

SHA-256（Secure Hash Algorithm 256-bit）は、内容の一致を調べるハッシュ値です。
原本を照合するときは、`originals`の原文をUTF-8（Unicode Transformation Format 8-bit）へ戻して計算します。
`reviews`の本文を再整形して計算すると、空白や改行が変わり元のハッシュと一致しないことがあります。
原文を別に収録している分、合計サイズは増えています。

文書からのリンクの`#/reviews/旧名`は、JSON内の位置を表すJSON Pointerです。
ブラウザーがその位置へ移動しない場合は、旧名で検索するか、下のPython例で取り出してください。
記録内部の過去の保存先、コマンド、ハッシュは当時のままです。別の旧JSON名が出てきた場合も対応表で収録先を確認できます。

保存形式を整理しただけなので、正式入力の参照、台帳、配信承認を再発行したり書き換えたりはしていません。

リポジトリのルートで読み取る例です。

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
| `20261028-evidence.json` | `foundation.json` |
| `20261028-staging-checkpoint-evidence.json` | `foundation.json` |
| `20261028-synthetic-staging-benchmark.json` | `foundation.json` |
| `20261030-checkpoint-evidence.json` | `foundation.json` |
| `20261030-p0-evidence-checks.json` | `foundation.json` |
| `20261030-t03-checks.json` | `foundation.json` |
| `20261030-t04-checks.json` | `foundation.json` |
| `20261030-t05-checks.json` | `foundation.json` |
| `20261030-t06-design.json` | `foundation.json` |
| `20261030-t06a-checks.json` | `foundation.json` |
| `20261030-t06b-checks.json` | `foundation.json` |
| `20261101-admin-owner-checks.json` | `clock-history.json` |
| `20261101-chrony-control-checks.json` | `clock-history.json` |
| `20261101-clock-origin-checks.json` | `clock-history.json` |
| `20261101-clock-owner-checks.json` | `clock-history.json` |
| `20261101-t03-t13-diagnostics.json` | `clock-history.json` |
| `20261101-t07-t17-checks.json` | `deployment-ci.json` |
| `20261101-t07-t17-ci-checks.json` | `deployment-ci.json` |
| `20261101-t08-checks.json` | `deployment-ci.json` |
| `20261101-t13-http-checks.json` | `deployment-ci.json` |
| `20261101-t13-http-ci-checks.json` | `deployment-ci.json` |
| `20261101-t15-checks.json` | `deployment-ci.json` |
| `20261102-t07b-ci-checks.json` | `deployment-ci.json` |
| `20261102-t07b-t17b-checks.json` | `deployment-ci.json` |
| `20261102-t16-checks.json` | `deployment-ci.json` |
| `20261102-t16-ci-checks.json` | `deployment-ci.json` |
| `20261102-t16-sol-review.json` | `deployment-ci.json` |
| `20261103-chrony-runtime-checks.json` | `clock-validation.json` |
| `20261103-chrony-runtime-ci-checks.json` | `clock-validation.json` |
| `20261103-clock-extra-checks.json` | `clock-history.json` |
| `20261103-clock-followup-checks.json` | `clock-history.json` |
| `20261103-orm-compatibility-checks.json` | `deployment-ci.json` |
| `20261103-t17b-t07a-checks.json` | `deployment-ci.json` |
| `20261103-t17b-t07a-ci-checks.json` | `deployment-ci.json` |
| `20261103-upload-action-checks.json` | `deployment-ci.json` |
| `20261103-upload-action-ci-checks.json` | `deployment-ci.json` |
| `20261104-backend-boundary-checks.json` | `clock-history.json` |
| `20261104-clock-rate-checks.json` | `clock-validation.json` |
| `20261104-clock-rate-ci-checks.json` | `clock-validation.json` |
| `20261104-clock-readiness-checks.json` | `clock-history.json` |
| `20261104-followup-checks.json` | `clock-history.json` |
| `20261104-qualification-design-checks.json` | `clock-validation.json` |
| `20261105-clock-acquisition-checks.json` | `clock-validation.json` |
| `20261105-clock-acquisition-ci-checks.json` | `clock-validation.json` |
| `20261105-clock-acquisition-startup-checks.json` | `clock-validation.json` |
| `20261105-clock-campaign-checks.json` | `clock-validation.json` |
| `20261105-clock-campaign-ci-checks.json` | `clock-validation.json` |
| `20261105-clock-campaign-missing-checks.json` | `clock-validation.json` |
| `20261106-clock-scheduler-checks.json` | `clock-validation.json` |
| `20261106-clock-scheduler-ci-checks.json` | `clock-validation.json` |
| `20261106-t09-checks.json` | `foundation.json` |
| `20261106-t10-grouping-checks.json` | `foundation.json` |
| `20261106-t10-grouping-ci-checks.json` | `foundation.json` |
| `20261106-t10-query-checks.json` | `foundation.json` |
| `20261108-clock-bundle-budget-checks.json` | `clock-validation.json` |
| `20261108-clock-bundle-checks.json` | `clock-validation.json` |
| `20261108-clock-bundle-ci-checks.json` | `clock-validation.json` |
| `20261108-t10-snapshot-checks.json` | `foundation.json` |
| `20261109-admin-resources-checks.json` | `clock-history.json` |
| `20261109-clock-bundle-input-checks.json` | `clock-validation.json` |
| `20261109-clock-bundle-input-ci-checks.json` | `clock-validation.json` |
| `20261109-host-admin-checks.json` | `clock-history.json` |
| `20261109-host-cause-checks.json` | `clock-history.json` |
| `20261109-nict-method-checks.json` | `clock-history.json` |
| `20261109-prerequisite-review-checks.json` | `clock-history.json` |
| `20261109-resync-result-checks.json` | `clock-history.json` |
| `20261109-task-source-checks.json` | `clock-history.json` |
| `20261114-capital-evaluation-checks.json` | `capital.json` |
| `20261114-capital-monitor-checks.json` | `capital.json` |
| `20261114-capital-operational-restore-checks.json` | `capital.json` |
| `20261114-capital-runtime-checks.json` | `capital.json` |
| `20261114-capital-runtime-docs-ci-checks.json` | `capital.json` |
| `20261114-product-resume-checks.json` | `capital.json` |
| `20261115-file-consolidation-checks.json` | `capital.json` |
| `20261115-operational-consumer-checks.json` | `capital.json` |
| `20261115-operational-review-checks.json` | `capital.json` |
| `20261115-review-archive-checks.json` | `capital.json` |
| `20261115-review-recovery-checks.json` | `capital.json` |
| `20261115-runtime-followup-checks.json` | `capital.json` |
| `20261116-product-acceptance-checks.json` | `capital.json` |
