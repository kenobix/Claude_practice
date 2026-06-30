# 作業ログ: AI箱庭（生成エージェント×実在地図 購買行動シミュレーション）（2026-06-30〜）

## 概要

複数のAIペルソナを実在の地図（渋谷駅周辺）上で行動させ、自社製品をどうすれば買ってもらえるかを分析する「AI箱庭」のv1を構築した。[README.md](./README.md)に技術詳細はまとめてあり、本ファイルでは構想〜意思決定の経緯を記録する。

---

## 構想のきっかけ

「AI箱庭の中でGoogle Mapと連携し、ペルソナの動きと自社製品の購入可能性を分析したい」という相談から開始。

---

## 実装方針の比較検討

最初にClaude側で3案（A: 可視化重視の軽量実装／B: Places・Routes API連動のリアルデータ重視／C: バッチシミュレーション＋分析ダッシュボード）を提示。

その後、別の生成AIが提示した3案（案1: Pythonベース軽量ターン制シミュレータ／案2: マルチエージェントフレームワーク＋記憶モジュール／案3: ゲームエンジンによる3D可視化）と比較を実施。

**比較の結論:**

| 観点 | 採用した判断 |
|------|------|
| 全体構成 | 相手案の「案1（軽量ターン制）」をベースに採用 |
| 分析機能 | 相手案の「Chain of ThoughtのJSON化」「A/Bテストの自動化」を取り込み（私の最初の案にはこの観点が欠けていた） |
| 可視化 | バッチ実行→ログ保存→後から地図に再生という方式（私の案Cの考え方）を採用。リアルタイム表示は見送り |
| 見送った要素 | 相手案2（ペルソナ間の会話・記憶・口コミ伝播）、相手案3（Unity/Unreal等の3D可視化）はv1ではオーバースペックと判断し除外 |

開発規模はこれまでの260xxxシリーズの段階的な進め方（単一HTML→学習しながら拡張）に合わせ、いきなりフル機能を狙わない方針とした。

---

## スコープ確定（ユーザーへの確認結果）

| 項目 | 決定内容 |
|------|---------|
| 周辺施設データ | Google Places APIを実際に使用 |
| 行動決定LLM | Gemini API（260521・260525と同じ環境変数の仕組みを流用） |
| 第一弾の規模 | ペルソナ3〜5体、1日分のシミュレーション |

---

## 実装内容

| ファイル | 役割 |
|---------|------|
| [personas.json](./personas.json) | ペルソナ3体（会社員・経営者・大学生）。性格・予算・ニーズが異なる設定 |
| [scenario.json](./scenario.json) | 渋谷駅周辺、架空のコーヒーショップ「Kenshin Coffee」、実施時間帯(8:00〜21:00、1時間刻み) |
| [places_client.py](./places_client.py) | Google Places API（Nearby Search legacy）のラッパー。座標を丸めてキャッシュし、API呼び出し回数を抑制 |
| [llm_agent.py](./llm_agent.py) | Gemini APIへ「現在地・時刻・周辺候補」を渡し、Chain of Thought形式のJSONで次の行動を決定させる |
| [simulate.py](./simulate.py) | ターン制シミュレーション本体。自社店舗（実在しない）を常に候補に追加する処理を含む |
| [analyze.py](./analyze.py) | 検討率・来店率・購入率の集計、購入に至らなかった理由の一覧化。`--compare`でA/Bテスト比較に対応 |
| [map_view.html](./map_view.html) | Leaflet + OpenStreetMap（無料・APIキー不要）でログを地図上に再生。各ペルソナの思考をサイドパネルに表示 |

構文チェック（`python3 -m py_compile`）は実施済み。実APIキーを持たないため、実際の動作確認はユーザー側で実施予定。

---

## Google Maps APIキー取得手順・料金の調査

ユーザーから「無料枠の料金を念のため確認してほしい」との依頼を受け、WebSearchで最新情報を確認した。

**判明した誤り**: README初稿に「月$200の無料クレジット」と記載していたが、これは**2025年3月に廃止済み**で不正確だった。正しくは、SKU（APIの種類）ごとに毎月の無料リクエスト数が設定される方式（Essentials SKUは月10,000件まで無料）に変更されている。READMEを修正し、出典（Google公式ドキュメント）へのリンクを追記した。

参考: [Places API Usage and Billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing)、[Google Maps Platform core services pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)

---

## Google Cloud ConsoleでのPlaces API選択（進行中の論点）

ユーザーがAPIキー取得中に、Google Cloud Consoleの「APIライブラリ」で「Places API」と「Places API (New)」の2つが表示され、どちらを選ぶべきか・なぜGCP経由が必要なのかという疑問が生じた。

- `places_client.py`は**legacy（旧）のNearby Search エンドポイント**（`maps.googleapis.com/maps/api/place/nearbysearch/json`）を使う実装になっているため、**「Places API」（New表記が付いていない方）を有効化する必要がある**。
- Google Maps PlatformのAPI群（Places, Maps JavaScript, Geocoding等）は、Gemini APIのようなAI Studio経由の単独発行ではなく、**すべてGoogle Cloud Consoleのプロジェクト・課金設定を介して発行する仕組み**になっている。これはGoogle Maps Platform全体の仕様であり、今回のAI箱庭固有の要件ではない。
- 「箱庭を作るのにGCPが必要か」という問いに対しては、Google Maps系のAPI（実在施設データの取得）を使う限りはGCPの利用が前提になる、と回答予定。GCPを避けたい場合は、施設データを手動定義する方式（実装当初の選択肢の一つ）に切り替えることも可能。

---

## Google Cloud ConsoleでのAPIキー取得完了（確認結果）

ユーザーがスクリーンショット（APIライブラリ検索結果・製品詳細ページ・料金タブ・お支払い情報確認・APIキー制限設定・プロジェクト概要）を共有し、手順の妥当性を確認した。

**確認結果: すべて正しい手順で完了している。**

| 確認項目 | 結果 |
|---------|------|
| 選択したAPI | 「Places API」（"(New)"が付いていない legacy 版）を正しく選択・有効化 |
| 料金タブ | SKUごとに無料枠＋従量課金（例: 0〜5,000件は無料、5,000件以降は$17/1,000件）という構造を画面上で確認 |
| 請求先アカウント | 無料トライアル（$300クレジット、90日間）に登録、カード情報を設定（画面に氏名・カード末尾が写っていたため、共有時の取り扱いに注意するようユーザーに伝達。本ログには記載しない） |
| APIキーの制限 | 「APIの制限」で「Places API」のみにチェックを入れ、他のMaps系APIは許可しない設定にできている（漏洩時の被害範囲を限定する正しい設定） |
| プロジェクト | 「My First Project」が作成され、Google Maps Platformが有効化された状態を確認 |

これでPlaces APIキーの取得は完了。残るは`GOOGLE_API_KEY`（Gemini、既に260521・260525で取得済みのはず）と合わせて環境変数を設定し、`simulate.py`を実際に実行する動作確認のみ。

---

## 今後の予定

- ユーザーがAPIキーを取得・設定し、`simulate.py`の動作確認
- 動作確認後、結果を本ログまたは別ログに追記
- v1の有用性を確認した上で、ペルソナ間相互作用や可視化強化（案2・案3の要素）の追加要否を検討
