# Phase 4: 固定規模のオフラインCLI計測

2026-11-17実測。[全9試行の生データ](benchmark.result.json)。
Phase 4の計測部分のみ完了。READMEの新規環境での通し再現と3分動画は未完了。
製品コード、依存、Schema、安全上限は変更していない。

## 再現

プロジェクトの固定依存を導入した既存の専用venvから実行する。

```bash
cd ~/work/Claude_practice-261119/261119/pcap_analyzer
.venv/bin/python scripts/benchmark.py > /tmp/pcap-benchmark.json
```

Linuxの`os.wait4`が必要。既定で100/1000/3000サイクルを各3回、各CLI試行60秒上限で実行する。
`--repeats`は1〜5、`--timeout`は0より大きく120秒以下。規模は意図的にCLIから拡大できない。
PCAPとCLI出力は一時ディレクトリに作成・削除し、送信・捕捉・逆引きは行わない。
成功時のみ結果JSONと終了0。不正設定・失敗・stderr出力・timeout・不整合・非決定的出力は終了2。
timeoutは子をkillしてwaitし、放置しない。既存の記録JSONを再計測結果で自動上書きしない。

## 入力レシピと独立した期待値

Scapy/製品のencoderを使わずstructでclassic pcapを構築する。little endian ns、snaplen65535、linktype1。
固定MAC、文書用192.0.2.1:40000 ↔ 198.51.100.53:9000、IPv4/UDP、checksum0（検査対象外）。
時刻は1700000000秒からファイル順に1ns増加。サイクルあたり以下の5レコードを繰り返す。

| 順 | 内容 | caplen | wirelen | 分類 | フロー |
|---|---|---:|---:|---|---|
| 1 | UDP headerのみ、payloadなし | 42 | 42 | parsed | a→b |
| 2 | 同じ両端点の逆方向UDP | 42 | 42 | parsed | b→a |
| 3 | EtherType ARPの14-byte header（ARP本体未検査） | 14 | 14 | unsupported | 未帰属 |
| 4 | UDP長12/IP長32だがpayload4 bytes捕捉なし | 42 | 46 | truncated | a→b（L4 headerは完全） |
| 5 | 1の後へ1 byte追加しcaplen>wirelen | 43 | 42 | malformed | 未帰属 |

CサイクルでN=5C、captured=183C、original=186C、ファイル長=24+(16×5+183)C=24+263C。
状態はparsed=2C、残る各状態=C、割合40%/20%/20%/20%。
単一フローa→bはpackets=2C/captured=84C/original=88C、b→aはC/42C/42C。
未帰属は2C/57C/56C。capturedは84C+42C+57C=183C、originalは88C+42C+56C=186C。
診断3Cは最大規模でも9000件で既定10000件以内。全入力は既定のサイズ・件数上限以内。
DNS対象0。長さ不正の記録を含むoriginal値を実回線の転送量と解釈しない。

各試行で製品JSON Schemaと上の手計算、方向別bytesと未帰属bytesの和を検査する。
同一規模の3試行で出力SHA-256一致も要求する。入力hash、出力hash/bytes、個別計測値を生データに保存する。
小規模の根拠番号・分類、生成上限、失敗・timeout、改変や非決定性の検出は自動テストで確認する。

## 計測境界と環境

- 実コマンドは専用venvの`python -m pcap_analyzer <temporary-input> --format json`。
- wall timeはPopen直前からwait4による終了確認直後までのperf_counter_ns差。
  Python起動・import・入力hash・解析・フロー/診断/参照集計・JSON整形・一時ファイルへの書込みを含む。
  入力生成・出力の読込み/hash・Schema/期待値検査は含まない。fsyncは行わずOS cacheへの書込みまで。
  10ms間隔の終了確認による待ちを含む。ns単位の記録は計測精度がnsという意味ではない。
- 最大RSSはwait4が返す**そのCLI子プロセス**のru_maxrss（LinuxではKiB）。
  親の累積RUSAGE_CHILDRENは使わない。起動時も含むプロセスのresident setであり、Python heapのみでも、
  親ハーネスやOS page cacheを含む全体使用量でもない。
- Linux/WSL2 x86_64、kernel6.6.87.2-microsoft-standard-WSL2、Python3.12.3、Scapy2.6.1、tool0.3.0。
  固定requirements.lockを使用。生データに実行時刻・環境・ハーネスSHA-256を記録。
- 小規模から順に逐次実行、専用warmupなし。cache flushやCPU固定、他プロセスの制御は行わない。
  機能試行は回帰試験と重なったため記録に採用せず、保存値は回帰試験終了後に計測した。
  ホスト全体の無負荷は保証しない。サンプル3回の環境依存実測で、SLA/性能保証/最大値推定ではない。

## 実測

| パケット数 | 入力bytes | wall time中央値 秒 | 最小〜最大 秒 | 最大RSSの試行最大 KiB | 解析成功率 |
|---:|---:|---:|---:|---:|---:|
| 500 | 26,324 | 0.728613 | 0.657221〜0.963401 | 54,516 | 40% |
| 5,000 | 263,024 | 1.622286 | 1.605360〜1.651119 | 57,292 | 40% |
| 15,000 | 789,024 | 3.943958 | 3.647912〜4.095124 | 65,260 | 40% |

全9試行で終了0・stderr空・complete=true、手計算/Schema/bytes一致、各規模の出力hash一致。
最大規模のcaptured549000/original558000 bytes、a→b252000/264000、b→a126000/126000、
未帰属171000/168000 bytes。捕捉長と元長それぞれの分割が一致する。

## 適用範囲と残課題

診断が多い単一UDPフローの限定的な基準。多数フロー・DNSキー/イベント負荷、TCP負荷、
100MiB/100万件の上限付近、他OS/ハードウェア/Pythonの性能は未実測。
規模間の傾向から線形時間・一定メモリ・上限での処理時間を保証しない。今回、性能最適化は行わない。
CIでは小さな実CLIの計測経路と異常系を検査し、速度閾値やこの実測値との一致を要求しない。
通常CI成功を9試行の性能再計測やTShark実体比較の成功と混同しない。

次はPhase 4のREADMEによる新規専用環境での通し再現（正常例・解析限界）、続いて3分デモ/動画。
この計測だけでPhase 4/MVP完成とはしない。全受入後に独立レビューと完成判定を提案する。
