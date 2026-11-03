# MLflowのnoload非推奨警告の切分け

2026-11-03、基点`298c6c8`からの独立したP2調査。
[検証索引](reviews/20261103-orm-compatibility-checks.json)を参照。
**警告の発生箇所と更新判断は確認済み。上流の修正は未確認で、依存lockは変更しない。**

## 確認した範囲

現行lock/venvはMLflow・skinny・tracingが3.17.1、SQLAlchemyが2.1.5。
MLflowの`SqlMCPAccessEndpoint.resolved_version_rel`は`viewonly=True`、`lazy="noload"`の単一関係である。
`sqlalchemy_store.py:327`の全mapper設定時にSQLAlchemyが非推奨警告を出す。
このため、そのMCP関係を直接利用していない入口からのimportでも警告が出る。

SQLAlchemyの[公式説明](https://docs.sqlalchemy.org/en/21/orm/relationship_api.html#sqlalchemy.orm.relationship.params.lazy)は、
noloadを2.1から非推奨としており、将来の削除と関連値がNoneとなる問題を示している。
本repoのsource/testsを検索した範囲では、当該MLflow MCP関係を直接参照していない。
これはMLflowの全機能が将来も安全だという保証ではない。

接続を拒否する専用processで通常のimportを行い、同じ警告とmapper設定を確認した。
比較用processでこの警告だけをerrorにすると、mapper設定中のimportを拒否した（意図したexit23）。
このerror化は検証process内だけで、製品のwarning policyやCI設定へ追加していない。
未公開のSQLAlchemyで試験したことや、将来版が必ず同じ例外になることを意味しない。

現行の使い捨てSQLite publisher回帰は6件成功した。
モデル精度・実DB/registryや、MLflowのMCP機能全体を検証したものではない。

## なぜ今lockを変えないか

確認時点の[最新公開release3.17.2](https://github.com/mlflow/mlflow/releases/tag/v3.17.2)と
[固定版のモデル定義](https://github.com/mlflow/mlflow/blob/v3.17.2/mlflow/store/tracking/dbmodels/models.py)を取得した。
ASTで同じ関係の`lazy="noload"`を確認した。
**3.17.1から3.17.2へ上げるだけで、この警告が直る根拠はない。**
警告を消すためのSQLAlchemy downgrade、site-packages直接patch、全warningの抑制、未release版の採用は行わない。

次の再調査条件は、上流の当該loader変更、修正release、SQLAlchemyの削除予定が具体化したとき。
その時点で依存差分を限定し、一時SQLiteの登録/承認/改ざん拒否と、隔離HTTP/PostgreSQL・対応復元・CI artifact/cleanupまで確認する。
実DBのschema移行やMLflow server image更新を、依存の検査だけで勝手に適用しない。
同じ未修正releaseの確認を繰り返しても、修正完了とは扱わない。

## 上流へ報告する場合の下書き

以下は報告案で、外部issueを作成・送信していない。

```text
MLflow 3.17.1 with SQLAlchemy 2.1.5 emits SADeprecationWarning while importing
mlflow.store.tracking.sqlalchemy_store and configuring the shared ORM mappers.
SqlMCPAccessEndpoint.resolved_version_rel uses lazy="noload", viewonly=True.
The same declaration is present in the released v3.17.2 source.
Promoting SADeprecationWarning to an error reproduces an import refusal;
this is a compatibility probe, not an execution on an unreleased SQLAlchemy.
Please review an appropriate loader strategy and direct/alias contains_eager
paths with regression coverage before SQLAlchemy removes noload.
```
