# 失敗事例

文書の差分を読むときや、CI通過後に仕様を確認するとき、修正後に再レビューするときに使います。PCAP AnalyzerとCI Runnerの文書では、日本語検査と製品CIが成功したあとに説明から条件が抜けていると分かりました。 [日本語技術文書の書き方](japanese-writing.md#標準手順)

## PCAP Analyzerの適用条件の欠落

PR #7では完全に捕捉したUDPペイロードを調べるという文にまとめた結果、適用条件であるフレーム全体の捕捉と時刻の正常性が抜けました。実装は、送信元か宛先のポートが53である非フラグメントIPv4 UDPを候補にし、ヘッダー・記録長・時刻が正常で、フレーム全体を捕捉できた場合にDNS構造を検査します。UDPのペイロードが完全でもフレーム捕捉不足や時刻不正であれば検査対象外です。

修正では候補の条件と検査する条件を分けて記載しました。処理の前提が複数ある場合は、条件を一つずつ並べ、候補に入っても実行されない例を確認します。この条件は当時のclassic pcap解析器のDNS構造検査に適用されます。 [仕様](../../261119/pcap_analyzer/docs/spec.md)・[実装](../../261119/pcap_analyzer/src/pcap_analyzer/packet.py)・[PR #7](https://github.com/kenobix/Claude_practice/pull/7)・[修正コミット f75d341](https://github.com/kenobix/Claude_practice/commit/f75d341234aa5635670553e3b80db677096d563b)

## CI Runnerの処理条件の過度な一般化

PR #8では、Workerが待機中のrunを取得する説明で、初回だけに当てはまるattempt作成をすべての取得に広げました。実装では初回取得のattempt番号は1です。再試行では前回のattempt番号に1を足し、新しいattemptを作ります。どちらの場合も実行権のtokenとleaseを発行します。

修正では初回と再試行それぞれの動作を記載し、共通する動作を別の文にしました。複数の処理段階がある場合は、状態ごとの分岐を先に書き出し、共通条件を特定してから文章をまとめます。ここでの番号の付け方はCI Runnerの契約です。 [設計](../../261117/ci_runner/docs/design.md)・[実装](../../261117/ci_runner/src/ci_runner/worker.py)・[PR #8](https://github.com/kenobix/Claude_practice/pull/8)・[修正コミット 08bc237](https://github.com/kenobix/Claude_practice/commit/08bc237b1eda05f72aa24cc2b67f7a6e1f87f144)

追加修正後は両PRのCIが成功し、mainへの通常マージとPagesの公開を確認しました。公開側でも両READMEの修正文を照合しました。 [PR #7のCI](https://github.com/kenobix/Claude_practice/actions/runs/45518213026)・[PR #8のCI](https://github.com/kenobix/Claude_practice/actions/runs/45518261040)・[最終Pagesデプロイ](https://github.com/kenobix/Claude_practice/actions/runs/45518548052)

## 共同編集アプリの修正後に残った下書き復元の不具合

独立レビュー時点のCIは成功していましたが、CRLFを含む更新を受理すると編集位置がずれ、切断通知がない場合は接続中の表示が残りました。この修正で旧版データを隔離して書き出せるようにしたあと、再レビューで差分が正式本文に依存する場合に書き出しが空になると分かりました。修正の再確認では、元の改行と切断の再現条件に加え、正式本文がCRLFかLFか、端末に状態と差分のどちらが残るか、すでに隔離したか、権限が失効したかを確認しました。 [PR #11](https://github.com/kenobix/Claude_practice/pull/11)・[レビュー時点のCI](https://github.com/kenobix/Claude_practice/actions/runs/45780928870)・[改行と切断の修正](https://github.com/kenobix/Claude_practice/commit/4fcd9e76f47385a72f07fa3ffe329af91b7c895d)・[復元の修正](https://github.com/kenobix/Claude_practice/commit/a2c41fbc95b2da55237ef966b0646e7e00b0ee43)

文書と付与版の分離を確認するCIは、書き出し待ちで時間切れになりました。試験コードが文書の作成後に古い接続表示で先に進む可能性があり、下書きの隔離完了も待っていませんでした。修正後は対象文書の見出し、既存文書とは異なるID、本文と順番号、隔離状態、保存された依存先の状態を確かめてから書き出します。非同期UI試験の待機条件には、処理対象のIDと処理が完了したことを示す状態を含めます。変更したのは試験コードで、最終独立再レビューでも下書き復元の不具合が2つの再現条件で解消したことを確認しました。 [失敗したCI](https://github.com/kenobix/Claude_practice/actions/runs/46034983069)・[試験コードの修正](https://github.com/kenobix/Claude_practice/commit/ea3248afee7d2a19701ded32963f63a23d860b04)・[最終独立再レビュー](../../261125/docs/acceptance.md#最終独立再レビュー)

## 自作VMで見落とした停止命令の条件

固定例、差分比較、CIが成功した時点でも、非空のバイト範囲を持つ単独の `HALT` は検査を通りました。正常なコンパイル結果だけでは見つからないため、独立レビューでは不正な命令を組み立てて再現しました。修正後は元の入力をVM実行と逆アセンブルの両方で拒否し、正しいEOFの空範囲と命令数の上限前後も再確認しました。命令列検査とコンパイラのどちらが条件を保証するかを仕様に明記しました。 [PR #10](https://github.com/kenobix/Claude_practice/pull/10)・[修正前のCI](https://github.com/kenobix/Claude_practice/actions/runs/45753002851)・[修正コミット](https://github.com/kenobix/Claude_practice/commit/cc04b91062208555e8c5967d941760a8f1f2ab70)・[修正後のCI](https://github.com/kenobix/Claude_practice/actions/runs/45799915237)・[受入記録](../../261126/docs/acceptance.md#独立レビュー指摘への対応)
