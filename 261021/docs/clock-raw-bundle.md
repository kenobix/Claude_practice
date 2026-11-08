# 合成時計raw原本の可搬bundle

2026-11-08、基点`99f14cd`。[合成採取](clock-fake-acquisition.md)の4原本を、1個のZIPだけで別processへ渡す。
[実装](../src/bike_mlops/clock_raw_bundle.py)は保存codeを実行せず、現在のoffline verifierで再計算する。
これは実時計のcollector/参照源/認証器ではない。数値が全て良好でも時計資格は`incomplete`を維持する。

## 使用方法と終了値

以下は`261021`からの使用例。`--expected-plan-sha256`には、採取時に別に保存したplan SHAを渡す。
archive内の値を期待値として自動コピーする機能はない。合成planの保存は実採取の事前review真正性を保証しない。
既存rawと同じsource revisionを持つverifier環境を用意し、出力には未使用の名前を選ぶ。

```bash
.venv/bin/python scripts/clock_raw_bundle.py \
  --export-dir data/reports/fake-scheduled-example-001 \
  --output data/reports/fake-clock-example-001.zip \
  --expected-plan-sha256 "$PLAN_SHA"

# 別process・別作業directoryでも、元raw directoryは不要。
.venv/bin/python scripts/clock_raw_bundle.py \
  --review-archive data/reports/fake-clock-example-001.zip \
  --output data/reports/fake-clock-review-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
```

| 操作/状態 | 終了値 | 保存されるもの |
|---|---:|---|
| exportが完成 | 0 | ZIPのみ。資格/数値の合格ではない。既知の数値違反がある完全rawも保管できる |
| reviewで明確な違反 | 1 | member/hash・campaign SHA・全offline reviewを含むreport |
| reviewで数値passedまたは不足 | 2 | 同report。時計資格は`incomplete` |
| 不正schema/ZIP、IO拒否、中断 | 2 | 完成reportは作らない。stderrは入力内容・絶対pathを出さない固定文 |

export stdoutは保存の受領票を返し、時計資格欄は常に`incomplete`を示す。既知違反を含む数値の合否はreview reportで確認する。

CLIではTERM/INTを例外へ変換してprivate stagingを回収し、元のsignal handlerへ戻す。
bundle処理自体は子processを起動せず、archive-onlyの検証試験では別processの終了値も確認する。
atomic publish直後のsignalでは完成fileが存在して非0になり得る。終了値を0へ補正しない。
SIGKILL/電源断時の回収は保証せず、不完全rawにcomplete markerを生成する機能もない。

## 固定memberと結合

ZIPは`manifest.json`と`raw/{plan.json,sources.json,journal.jsonl,complete.json}`の5通常fileのみ。
manifestのkindは`synthetic-clock-raw-bundle-v1`、外から渡すplan SHAと各rawのbytes/SHAを保存する。
ZIP byte SHAもreportへ記録し、raw→chain/nonce/sequence→source manifest/identity→全campaign→offline reviewを再計算する。
失敗/欠測は元の数値を保持し、completeはraw採取/reader回収の完了であって時計資格ではない。

rawは元のbytesを保つ。export前後のfile identity/stat/内容とmember集合を照合し、見える変更を拒否する。
exportは保存source bytesが現在installされた固定6 module/script（scheduled時7件）と完全一致する場合だけ許可する。
再hashした任意codeや`.env`等の余剰fileをZIPへ取り込まない。source bindingは現在のraw契約の範囲であり、
bundle verifier自身/全import/interpreterの実行真正性を認証しない。それらのrevision/hashは別の検証記録で管理する。

ZIP_STORED、固定順序/日時/通常file属性の表現だけを受け付ける。
重複/欠落/余剰、絶対path、traversal、symlink/directory、圧縮・暗号化memberを拒否し、`extract*()`を使わない。
読取は最大64MiB+64KiB、raw総量は64MiB、manifestは16KiBを上限とする。
再構築ZIPとのbyte一致でcomment/extra field・前置/末尾の隠しbyteも拒否する。
単一FD/nofollow/nonblockのstable byte snapshotを検証し、snapshot SHAを保存する。
読取後に外側pathが変化しても、後で置かれたfileを検証したとは主張しない。

## 境界と共有範囲

別process試験は、元rawを持たないcwdとPATHだけの環境から、ZIP・期待plan・install済みverifierで再計算する。
archiveはcodeの自己完結した実行環境ではなく、対応するverifierが必要である。保存sourceは実行しない。
真正性、源の独立性/不確かさ、実hostのphase deadline、OS修復、保守、実時計資格/実候補採否は未受入のまま。
自己申告やhash一致で実時計の前提を満たすconsumerは追加しない。

代表raw/ZIP/reportはignoredな`data/reports/20261108-clock-bundle/`へ保存し、公開Gitや既存16CI artifactには追加しない。
任意の秘密情報を検出する汎用scannerではない。合成schema/許可sourceでも、公開前には内容と共有範囲を別途reviewする。
既存の実T10 snapshot/protocol/ledgerは変更しない。次の実入力は[必要資料一覧](clock-input-request.md)を参照する。

修正後067d3ceの[CI42449911296/最終索引](reviews/20261108-clock-bundle-ci-checks.json)で全588件（専用18件）と全system、既存16 artifact/hash/archive-only/cleanupを確認した。
初期のZIP末尾byte拒否漏れと、その後のraw/manifest予算混同は、修正前FAILと修正後成功を別索引に保持する。
別processの可搬性は受入済みだが、合成raw ZIPをCI artifactへ公開したものではない。[用語集](glossary.md#時計源の提出資料と原本bundleで使う用語)も参照できる。
