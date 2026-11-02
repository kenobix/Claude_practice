# T16: 合成計測証拠の保存と再計算

`synthetic-system` CI jobでは、T16を有効にした実行で、使い捨てCapital fixtureの合成計測原本4件を`synthetic-measurement-evidence.zip`へ保存します。対象は計画、計測report、operational attempt ledger、要求journalです。manifestには各原本の相対パス・byte数・SHA-256、candidate/attempt ID、source commitとdirty flagを含めます。ZIP外の`archive-sha256.json`はZIP全体のSHA-256を記録し、`recalculation.json`は別processの検算結果です。

## ローカル実行

この追加保存は明示的に`--evidence-dir`を指定したsynthetic-system harnessだけで有効です。Docker Desktopが起動し、対象ユーザーからDocker daemonを利用できるWSL/Linux環境で、プロジェクトディレクトリから実行します。

```bash
cd 261021
. .venv/bin/activate
mkdir -p data/reports
set -o pipefail
timeout --signal=TERM --kill-after=10 1200 \
  python scripts/capital_system_harness.py --run-synthetic-system \
    --evidence-dir data/reports/t16-local 2>&1 \
  | tee data/reports/t16-local-harness.log
```

指定する出力directoryは新規でなければなりません。既存directoryは上書きしません。harnessは成功した1 attemptだけを明示選択し、出力archiveと再計算結果を生成してから既存のfinally cleanupを実行します。失敗した実行を成功archiveとして出力しません。成功後は次を確認できます。

- `data/reports/t16-local/synthetic-measurement-evidence.zip`
- `data/reports/t16-local/archive-sha256.json`
- `data/reports/t16-local/recalculation.json`
- `data/reports/t16-local-harness.log`の`cleanup_complete`

別processで手動再検算する場合は、検証processにarchive以外の計画・台帳・環境情報を渡さず、次を実行します。

```bash
env -i PATH="$PATH" .venv/bin/python -m bike_mlops.capital_measurement_evidence \
  --verify-archive data/reports/t16-local/synthetic-measurement-evidence.zip
```

検証processは明示memberだけをメモリ/一時directoryへ読み、`extractall`は使いません。DB、MLflow、Dockerへ接続しません。

## 再計算条件と限界

検証はmanifestから4原本のbyte数/hashを検証し、台帳→candidate/attempt→plan/report/journalの結合を確認します。journalとreportの1,050要求順・HTTP 200・latencyをrequest IDごとに照合し、warmup 50件を除いた1,000件からnearest-rank (`ceil(0.95*n)-1`) でp95を再計算します。要求ID digest、エラー0件、reportに記録したp95も比較します。

これは保存済み合成行の数値・結合を独立processで再計算する検証です。実データの精度、実運用の応答時間、実DB監査の完全性、OS時計の適格性、モデルの承認・配信許可を証明しません。`operational_gate=not_evaluated`と`deployment_permission=blocked`を維持します。機密フィールド名、認証付きURI、外部URI、絶対パス、symlink、既存出力、内容改変、欠落/余分/重複memberを検出した場合は閉じて失敗します。原本を削除・変更してarchive hashを合わせることはしません。

GitHub Actions artifactは固定allowlistでZIP、外側digest JSON、再計算JSONだけを追加します。`data/reports/**`の再帰公開はしません。ローカル実行だけではT16完了ではなく、実装commitのCI artifact検査、cleanup確認をSol review後に確認します。

## Sol reviewでの補強（2026-11-02）

export時に加え、archiveだけを検証する経路でも台帳とjournal全行を含む4原本の秘密情報検査を行います。manifestのdescriptor項目を固定し、report・台帳・journalの元パスを対象attemptへ結びます。workspace自体と途中directoryのsymlinkを拒否し、原本の読取はdirectory/fileを`O_NOFOLLOW`で開いて変更を検知します。

export失敗と部分出力削除の失敗が重なった場合は両方を記録して非0で終了します。resource cleanup失敗時のarchive削除失敗も握り潰しません。これらの検査は固定された合成出力用であり、任意の文章から全種類の秘密値を発見する汎用検出器ではありません。未知の原本を公開してよいと判断する用途には使いません。

Solの検証結果は[review索引](reviews/20261102-t16-sol-review.json)へ記録します。Luna索引は当時の検証記録として保持します。

## 実装commitのCI確認

実装`2492abc`の[CI41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156)で399 testsと両job全stepが成功しました。T16の3ファイルを含むartifactの全13 memberとhashを照合し、ZIPだけを別processへ渡して1050応答・warmup50除外・1000測定件のp95を再計算しました。同じattemptの報告値と一致し、専用project cleanupも確認したため、T16を完了とします。[CI証拠](reviews/20261102-t16-ci-checks.json)に記録しています。上記のlocal/CI pending記述は受入条件・実施順の説明であり、最新の完了状態はこの節です。
