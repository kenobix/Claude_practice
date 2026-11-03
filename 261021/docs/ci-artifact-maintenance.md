# CI artifact actionの単独保守（2026-11-03）

時計・原本公開待ちから独立したP2作業。基点`7174a1f`。
workflowの`actions/upload-artifact@v4`を`v7.0.2`へ固定し、`archive: true`を明示する。
Node.js20 actionの強制移行警告を対象とし、Python/MLflow依存lockは変更しない。

[公式release](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)と
[固定版action定義](https://github.com/actions/upload-artifact/blob/v7.0.2/action.yml)を確認した。
実行runtimeはNode24。v7で追加された単一fileの非ZIP uploadは使わず、従来の複数file ZIPを維持する。
name、16pathの明示allowlist、14日保存、失敗時のlog回収条件、hidden-file既定除外を保つ。
最初のlocal検証でYAML parse、全10run blockの`bash -n`、正規化差分がtagとarchive指定だけであることを確認した。

受入には実装commitのCI全job/step成功、16member ZIPの実download、GitHub digestとのSHA一致、
内側archiveのmember hashと別process再計算、T07a normal/TERM・T07b/既存PGのcleanup確認が必要。
local構文検証だけでは完了にしない。初期記録は[local索引](reviews/20261103-upload-action-checks.json)。

このCIは合成検証で、実Capital採否・時計資格・本番SLOの証拠ではない。
他の非推奨警告が残る場合は発生箇所と別taskを記録し、warningを消すための広範な依存更新は行わない。
