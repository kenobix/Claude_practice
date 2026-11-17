# Phase 2 受入確認

確認日: 2026-11-17（Asia/Tokyo）。対象: schema 0.4.0 / tool 0.3.0。
元計画: [Phase 2: フローとDNS照合](../../../261014/plan_pcap_analyzer.md)。
判定: **Phase 2完了**。この文書の人工入力・対応範囲で受入条件を確認した。
TSharkによる独立比較はPhase 3、性能/メモリ計測・デモはPhase 4であり、MVP全体は未完成。

## 受入条件と証拠

| 条件 | 実施した検証 | 根拠 |
|---|---|---|
| 双方向通信量・TCP flags | baseline 10件/680 bytes/4 flows、SYN+ACKをS/A両方へ計数。混在例でも方向別・未帰属の件数/捕捉長/元長が一致 | [flow tests](../tests/test_flows.py)、[CLI受入試験](../tests/test_phase2_acceptance.py) |
| 同じDNS IDの混同を防ぐ | ID=7の6問い合わせを同時保持。port、名前、QTYPE、QCLASS、bytesラベルを分け、応答を逆順で捕捉。ASCII大文字/小文字は対応させる | CLI受入試験のcollision例、[key tests](../tests/test_dns_keys.py) |
| 応答順序・再利用を成功と混同しない | 問い合わせ前の応答は孤立、5秒窓外の応答で過去を修復しない。期限後の同一キーは別問い合わせとして照合 | CLI受入試験の混在例、[window tests](../tests/test_dns_windows.py) |
| 終端・曖昧さ・追加応答 | baseline query6は未応答観測、query7,8は曖昧。窓端の前/ちょうど/1ns後、完了後query再利用、追加応答リンク、RCODE/TC | [report tests](../tests/test_dns_report.py)、window tests |
| fragment・truncated・malformedを成功応答にしない | 正しい応答に似た3種類のパケットを混在。fragmentはフローから除外、payload切詰め/不正DNSは確認済みフローへ帰属しDNS照合から除外 | CLI受入試験の混在例、[packet tests](../tests/test_dns_packets.py) |
| 時刻逆行・整数精度 | backward fixtureは対象件数を維持して時間結果を無効化。窓閉鎖後に逆行しても既出の結果を破棄。ns/usの元精度と整数nsを維持 | 14 fixtureのCLI確認、report tests、[Phase 1 tests](../tests/test_phase1.py) |
| 上限・途中結果・根拠 | フロー/DNSキー/件数/診断/入力上限、破損末尾、読込エラーで明示停止。計数済みレコードを分配し、DNS上限queryは未登録番号へ保持 | report tests、flow tests、Phase 1 tests |
| 出力契約・決定性・オフライン | Schema、根拠の一意分配、追加応答リンク、JSON再実行一致、制御bytesの端末エスケープ、socket禁止下の生成/解析 | CLI受入試験、report tests、[Phase 0 tests](../tests/test_phase0.py)、Phase 1 tests |

## 既存14 fixtureの横断確認

[手定義Phase 2期待値](../expected/phase2.fixtures.json)を新規追加。
既存の[fixture設計](fixtures.md)・生成レシピから定義し、解析結果から自動生成しない。
既存テストが検査する入力・分類・時刻に加え、CLIからのフロー数・未帰属長・DNS件数と無効化を検査する。
元の14 fixtureや旧期待値は変更しない。

| fixture | Phase 2で確認した結果 |
|---|---|
| baseline | 4 flows、未帰属0、query4/response2、25ms/未応答観測/曖昧判定が[独立期待値](../expected/baseline.dns.json)と一致 |
| header_only | 0 flows、0 query/response、有効な空イベント配列 |
| empty | E_FORMAT、終了2、JSONなし |
| capture_truncated | 0 flows、未帰属1件40/54 bytes、DNS対象0 |
| record_cutoff / header_cutoff | 0 flows、0 DNS、partial、E_RECORD_DATA / E_RECORD_HEADER、終了2 |
| malformed_length | 0 flows、未帰属1件55/54 bytes、DNS対象0 |
| unsupported_arp | 0 flows、未帰属1件42/42 bytes、DNS対象0 |
| fragment | 0 flows、未帰属1件50/50 bytes、DNS対象0 |
| nano / big_endian | 各1 flow、未帰属0、DNS対象0（元精度の検査も既存テストで維持） |
| backward | 1 flow、query1/response1を維持、events=[]/event_counts=null、時刻逆行無効化 |
| unsupported_linktype / unsupported_pcapng | E_LINKTYPE / E_FORMAT_UNSUPPORTED、終了2、JSONなし |

## 追加の混在人工入力

PCAPはテスト内の固定文書用IP・固定時刻から一時ディレクトリへ合成する。
DNS bytesはstructで組み立て、Scapyや解析器のDNSキー生成から期待値を作らない。
IP/TCP/UDPの共通wire helperは既存の手組みfixture helperを再利用する。
ns間隔は整数変換を検証するための人工値であり、実ネットワークの測定値ではない。

同一ID例は6 queryと逆順6 response。query1→response12からquery6→response7までを
11/9/7/5/3/1nsで一意照合する。12件738 bytes、2 UDP flows、方向別308/308と61/61 bytes。
制御文字入りラベルもキーのhexを保ち、端末ではESCを直接出さずエスケープする。

混在例は次の10レコード。

| 番号 | 入力 | 期待 |
|---|---|---|
| 1 | 応答だけ | unmatched_response |
| 2 | 問い合わせ | 後の窓進行でno_response_observed |
| 3 | 応答と同じDNSを持つIPv4 MF fragment | unsupported、未帰属、DNS除外 |
| 4 | 応答末尾を1 byte捕捉不足 | truncated、フロー帰属、DNS除外 |
| 5 | DNSヘッダーのない完全UDP | malformed、フロー帰属、DNS除外 |
| 6 | 6秒時点のTCP SYN | 問い合わせ2の窓を閉じる |
| 7 | 古いキーへの遅い応答 | unmatched_response。問い合わせ2を成功に変更しない |
| 8 / 9 | 同じキーの新queryと1ns後の応答 | matched、根拠8→9 |
| 10 | 逆方向TCP SYN+ACK | 同じTCP flowへ帰属しS/A両計数 |

結果は10件、captured=576 / original=577 bytes、parsed7/unsupported1/truncated1/malformed1。
TCP 108 bytes + UDP 407 bytes + 未帰属61 bytes = 全捕捉576 bytes。
元長はTCP108 + UDP408 + 未帰属61 = 577 bytes。DNS対象はquery2/response3で、除外3件を応答成功へ混入しない。

## 実行と完了判定の範囲

worktreeルートから:

```bash
261119/pcap_analyzer/.venv/bin/python -m pytest -q 261119/pcap_analyzer/tests/test_phase2_acceptance.py
261119/pcap_analyzer/.venv/bin/python -m pytest -q 261119/pcap_analyzer/tests
```

受入試験16件成功（既存14 fixture + 混在2例）。全体結果はREADME/work_logに記録する。
新たな解析機能・仕様緩和・依存追加はない。既存の安全上限、逐次読込、時刻精度、JSON契約は維持する。
この検証は全実通信の正しさや未知RDATAの意味を保証せず、観測不足を障害/攻撃と断定しない。

## 次の単位: Phase 3

TSharkは現在の環境で未導入（command -vで確認）。今回は導入も実行も行っていない。
次はOS/他プロジェクトを変更しない実行環境の選択から始め、版・設定・フィルター・実行コマンドを記録する。
比較対象は同一人工fixtureのframe番号、captured/original長、元精度に応じた整数時刻、IPv4端点、
protocol、port、TCP flags等の共通に定義できる項目とする。
fragment/切詰め/不正入力では比較できる範囲と拒否の違いを説明し、独自DNS窓状態まで同じとは仮定しない。
独立比較・性能測定・デモは、このPhase 2完了判定とは別の未完了事項である。
