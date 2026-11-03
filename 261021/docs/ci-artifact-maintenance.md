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

実装`5a687a7`のunit jobは463件64.677秒成功。次の警告はaction更新とは別に扱う。

- MLflowの`sqlalchemy_store.py:327`からSQLAlchemyの`noload`非推奨警告。現行lockでの試験は成功したが、将来の依存互換は個別に確認する。今回警告の抑制・lock更新・上流fileの直接patchはしない。
- ZIP重複memberのUserWarningは、重複を拒否する負例を作ったときのもの。重複archiveを受理した証拠でも通常処理の破損でもない。

全system job・artifact・cleanupの受入は[CI索引](reviews/20261103-upload-action-ci-checks.json)で確認済み。
実装`5a687a7a58ead84240bfd72bfcbe4cfbe31f3559`の[CI41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089)は全job/step成功。
実行時のaction解決SHAは`cf430e030ddbb5b0abf93d22962f4752f3646cd9`、`archive: true`も実logで確認した。
artifact13214080832の233,299 bytes/16member ZIP SHA
`f02bdd44b3082142f0f5cc38ef7b384cf52b9b744c02d1ddf482a278874a8789`はGitHub digestと一致。
内側archiveの4原本hashとarchive-only別process・独立nearest-rank再計算、T07a normal/TERM・T07b対応復元・PG cleanupを確認した。
system logにNode20強制移行/punycode/url.parse警告は見られなかった。すべての依存警告がなくなったとは呼ばない。
合成p95の19.503491msは保存値の検算で、実運用性能・時計資格へ流用しない。
初期local索引のCI_PENDINGは当時のsnapshotとして保持する。
