# 失敗事例

技術文書を短くするときや、複数の処理段階をまとめるときに参照します。下の2件は、日本語検査と製品CIが成功したあと、マージ前の差分確認で見つかりました。再発防止の作業手順は[日本語技術文書の書き方](japanese-writing.md#標準手順)を参照してください。

## PCAP Analyzerの適用条件の欠落

PR #7では完全に捕捉したUDPペイロードを調べるという文にまとめた結果、適用条件であるフレーム全体の捕捉と時刻の正常性が抜けました。実装は、送信元か宛先のポートが53である非フラグメントIPv4 UDPを候補にし、ヘッダー・記録長・時刻が正常で、フレーム全体を捕捉できた場合にDNS構造を検査します。UDPのペイロードが完全でもフレーム捕捉不足や時刻不正であれば検査対象外です。

修正では候補の条件と検査する条件を分けて記載しました。処理の前提が複数ある場合は、条件を一つずつ並べ、候補に入っても実行されない例を確認します。この条件は当時のclassic pcap解析器のDNS構造検査に適用されます。 [仕様](../../261119/pcap_analyzer/docs/spec.md)・[実装](../../261119/pcap_analyzer/src/pcap_analyzer/packet.py)・[PR #7](https://github.com/kenobix/Claude_practice/pull/7)・[修正コミット f75d341](https://github.com/kenobix/Claude_practice/commit/f75d341234aa5635670553e3b80db677096d563b)

## CI Runnerの処理条件の過度な一般化

PR #8では、Workerが待機中のrunを取得する説明で、初回だけに当てはまるattempt作成をすべての取得に広げました。実装では初回取得のattempt番号は1です。再試行では前回のattempt番号に1を足し、新しいattemptを作ります。どちらの場合も実行権のtokenとleaseを発行します。

修正では初回と再試行それぞれの動作を記載し、共通する動作を別の文にしました。複数の処理段階がある場合は、状態ごとの分岐を先に書き出し、共通条件を特定してから文章をまとめます。ここでの番号の付け方はCI Runnerの契約です。 [設計](../../261117/ci_runner/docs/design.md)・[実装](../../261117/ci_runner/src/ci_runner/worker.py)・[PR #8](https://github.com/kenobix/Claude_practice/pull/8)・[修正コミット 08bc237](https://github.com/kenobix/Claude_practice/commit/08bc237b1eda05f72aa24cc2b67f7a6e1f87f144)

追加修正後は両PRのCIが成功し、mainへの通常マージとPagesの公開を確認しました。公開側でも両READMEの修正文を照合しました。 [PR #7のCI](https://github.com/kenobix/Claude_practice/actions/runs/45518213026)・[PR #8のCI](https://github.com/kenobix/Claude_practice/actions/runs/45518261040)・[最終Pagesデプロイ](https://github.com/kenobix/Claude_practice/actions/runs/45518548052)
