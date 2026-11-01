# 予測保存列の精度移行と復元の検証（T08）

T08は既存の`Database.initialize()`がPostgreSQLの`predictions.predicted`をREALからDOUBLE PRECISIONへ移す処理の検証です。実DBの移行は行っていません。現行の`monitor_windows`のREAL列を移行する作業も含みません。最終結果とsource/log/backupのSHAは[検証記録](reviews/20261101-t08-checks.json)、試行の経緯は[作業ログ](../work_log.md)を参照してください。

REALは約6桁、DOUBLE PRECISIONは約15桁の精度を持ちます。旧REAL保存時に失われた桁は、型を広げても復元できません。T08では`predicted::double precision`で読み出した旧保存値と`struct.pack('!f', value)`のbinary32を照合し、移行後も同じbinary32由来の値を保持することを確認します。新しい予測行についてはbinary64の値を保持できることを別に調べます。[PostgreSQL数値型の仕様](https://www.postgresql.org/docs/17/datatype-numeric.html)を参照。

## API応答の十進表示に関する制約

通常のpsycopg読取では、旧REALの短い十進文字列がPython floatへ変換されます。DOUBLE PRECISION化した後は、保存されていたbinary32の値をより多くの桁で表すため、APIの数値表示が変わる場合があります。合成fixtureで次を観測しました。

| 旧REALの読取/API値 | 移行後の値 | 保存値の照合 |
|---:|---:|---|
| 0.33333334 | 0.3333333432674408 | 同じbinary32 |
| 415.72473 | 415.7247314453125 | 同じbinary32 |
| 15.189926 | 15.189926147460938 | 同じbinary32 |

これは予測モデルを変更した結果ではありません。旧保存値・request ID・入力hash・元model版/metadataは保持され、移行後の同じ入力の再送は同じ保存値を返します。モデルを読み直さなくても、元版7を返すことをAPI routerで確認しています。異入力は409です。ただし**移行前に送ったJSONの十進値との完全一致は保証していません**。完全一致が必要な利用者がいる場合は、過去応答の保全や版付きの応答形式を別途設計してから実DBへ適用してください。T08のために既存予測行を丸め直したり、新しい列や応答policyを追加したりしていません。

## 9件の受入と保存する根拠

| ケース | 検証内容 |
|---|---|
| 保存値・反復初期化 | 4種類の旧数値、全table行、列型/null/default、request/model/artifact/profile/feature metadataを保持。予測列だけdouble、monitor列はREALのまま。再initializeでも保持。 |
| API再送 | 実router+専用DB、モデル未読込/現在版99でも旧保存版7を返す。同一要求の反復と異入力409、十進表示の変化を記録。HTTPサーバーを起動する試験ではなくTestClient経由。 |
| 新予測と競合 | 新行のbinary64保持、同じIDで違う候補計算値を渡しても保存済みの版/値を維持、入力hash変更は拒否。 |
| profile違い | initialize途中でprofile不一致を検知したら、先行した型変更もtransactionでrollback。 |
| 例外・接続切断 | ALTER実行後/commit前に人工例外または実backend切断を発生させ、全行・schemaの復元と再開を確認。 |
| 実SIGKILL | ALTER後commit前でkillすると旧schemaのまま、commit後killでは移行済み状態を保持。どちらも再initializeが一貫する。 |
| 並行初期化 | 別接続の2 initializerが既存advisory lockを通して完了し、値/metadataを保全。 |
| 旧列不足 | candidate/profile列とmetadataのない合成UCI旧DBを、既存の規則に従って補完。既存の予測値/metadataは保持。 |
| backup/restore | 移行前/後をpg_dump customで保存し、第二の専用container・別anonymous volumeへpg_restore。型・全行・metadata・API再送を照合し、旧backupはrestoreだけでは移行されず、明示initialize後に移行することを確認。 |

backupはこの試験内で作った合成DBだけを対象とします。dumpに認証値は含めず、ファイル名は`before.dump`/`after.dump`を固定します。CI公開対象はこの2ファイル、`summary.json`、試験logだけです。dumpには作成時刻等があり、別試行とのbytes一致を前提にせず、各試行のSHAを記録します。[pg_dumpの仕様](https://www.postgresql.org/docs/17/app-pgdump.html)を参照。

## 再現手順

Docker Desktop/WSL統合が動き、`postgres:17.6-bookworm`がlocalにある状態で実行します。imageを自動pullせず、既存のDB URLやCompose projectは利用しません。

```bash
cd /home/kenshin/work/claude_practice/261021
MLFLOW_DISABLE_AGENT_HINT=1 .venv/bin/python scripts/test_prediction_migration_postgres.py \
  --run-disposable --evidence-dir data/reports/t08-manual-001
```

出力先は新規directoryを指定します。既存directoryを再利用すると停止し、前回のbackup/summaryを上書きしません。試験は専用labelとランダムloopback portの2 containerを作り、非superuserのapp ownerで試験します。終了時は所有ID/labelを照合して専用container/volumeを削除し、残存も検査します。途中失敗のlogとsummaryを成功結果へ置き換えません。SIGKILLをharness全体へ送る場合やDocker自体の障害ではfinallyが動かない可能性があり、cleanup成功出力の有無と専用labelを確認してください。

T08は合成旧schemaの受入です。既存UCIを停止・backup・移行・復元した結果や、T07のartifactを含む復旧、T17の配信metadata一致、実Capital監視/承認の証拠を示すものではありません。
