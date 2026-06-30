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

---

## 初回実行と429エラーの対応

`python3 simulate.py`を初回実行したところ、3ターン分（[08:00]〜[10:00]の一部）は正常に動作したが、その後`429 RESOURCE_EXHAUSTED`エラーで停止した。

```
google.genai.errors.ClientError: 429 RESOURCE_EXHAUSTED.
Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests
```

**原因**: コードのバグではなく、Gemini API無料枠の**レート制限（1分あたりのリクエスト数上限）**に達したため。`simulate.py`がペルソナごとに間隔を空けずに連続でAPIを呼んでいたことが直接の原因。Google AI Studioの「使用状況」画面でも`429 TooManyRequests`エラーが記録されていることを確認した。

**対応（[llm_agent.py](./llm_agent.py)を修正）**:

1. リクエスト間隔の確保: `PersonaAgent`に`_throttle()`を追加し、前回の呼び出しから`MIN_REQUEST_INTERVAL_SEC`(6.5秒)以上空けてから次のリクエストを送るようにした
2. 429エラー時の自動リトライ: `_call_with_retry()`を追加し、429が発生した場合はエラーメッセージ中の`retry in X秒`を読み取って待機し、最大5回まで自動リトライするようにした
3. 副次的な修正: ログを見るとロクシタンなど自社店舗以外の場所でも`action_type="purchase"`が出力されていた（`analyze.py`の集計は自社店舗かどうかで正しくフィルタしているため分析結果に影響はないが、紛らわしいため）。プロンプトに「`purchase`は自社店舗を選んだ場合のみ使うこと」という制約を追記した

修正後のコードは構文チェック済み。再実行による動作確認はユーザー側で実施予定。

これでPlaces APIキーの取得は完了。残るは`GOOGLE_API_KEY`（Gemini、既に260521・260525で取得済みのはず）と合わせて環境変数を設定し、`simulate.py`を実際に実行する動作確認のみ。

---

## ログの保存場所とシステム構成の質問への回答

ユーザーから「Google Maps APIはどう使われているか」「ペルソナの位置・移動履歴はどこに保存されているか」「システム構成図はあるか」という質問を受け、以下を回答・対応した。

- Google Maps API（Places API）は`places_client.py`経由の周辺施設取得のみに使用。地図の**描画**にはAPIキー不要のLeaflet+OpenStreetMapを使っており、Maps APIとは無関係
- 位置・移動履歴はDB等ではなく、`simulate.py`実行ごとに`logs/sim_YYYYMMDD_HHMMSS.json`へ全ターン分が保存される方式
- システム構成図は当初未作成だったため、[README.md](./README.md)に**Mermaidのflowchart**を追加（設定ファイル→シミュレーション本体→外部API→ログ→分析/可視化、の流れを図示）

## 実際のGoogle Map上での再生機能を追加

「ログを実際のGoogle Mapで表示できるか」との質問を受け、新規ファイル[map_view_google.html](./map_view_google.html)を作成した。

- 既存の`map_view.html`（Leaflet+OpenStreetMap、無料・APIキー不要）とは別に、**Google Maps JavaScript API**を使う版を追加
- Maps JavaScript APIはPlaces APIとは別物のため、Cloud Consoleで個別に有効化が必要。専用のAPIキー（「アプリケーションの制限」をウェブサイト+HTTPリファラー、「APIの制限」をMaps JavaScript APIのみに絞る）を発行する運用を推奨する形でREADMEに手順を追記
- 実装は`gemini.html`等と同じ「APIキーをUI入力→localStorageに保存」のパターンを踏襲。Google Maps JS APIを動的に`<script>`タグでロードし、`google.maps.Marker`でペルソナの位置をプロットする

---

## 今後の予定

- ユーザーがレート制限対策後の`simulate.py`を再実行し、動作確認
- `map_view_google.html`の動作確認（Maps JavaScript APIキーの発行が必要）
- 動作確認後、結果を本ログまたは別ログに追記
- v1の有用性を確認した上で、ペルソナ間相互作用や可視化強化（案2・案3の要素）の追加要否を検討

---

## 429エラーの再発と原因の訂正（1分あたり→1日あたり）

レート制限対策（リクエスト間隔6.5秒）を入れた後も再実行で429エラーが発生した。エラー詳細を見直したところ、`quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'`と明記されており、**実際は「1分あたり」ではなく「1日あたり」のリクエスト数上限（このプロジェクトでは20回/日）**だったことが判明した。前回の実行（約17回消費）と今回の数回で、その日の上限を使い切っていた。

リクエスト間隔のスロットリングは1分単位の制限には有効だが、1日単位の制限には無効であり、最初の見立てが誤りだった。

**対応:**

1. [llm_agent.py](./llm_agent.py): エラーメッセージに`PerDay`が含まれる場合は、待ってもリトライしても解消しないため、即座に分かりやすいエラーメッセージで失敗するよう変更（無駄な待機時間をなくす）
2. [scenario.json](./scenario.json): ユーザーと相談の上、**「シナリオを縮小して無料枠内に収める」方針**を選択。`turn_minutes`を60分→180分（3時間刻み）、シミュレーション時間を9:00〜21:00（5ターン）に変更し、3ペルソナ×5ターン＝最大15回/日に削減（無料枠20回/日に対して余裕を持たせた）
3. [README.md](./README.md)のコスト目安セクションを実態に合わせて更新。「1日の無料枠の正確な値はGoogle AI Studioの「レート制限」ページで確認できる」「より大規模に動かしたい場合は課金を有効化する」という案内を追記

**教訓**: Gemini APIのエラーメッセージの`retryDelay`（数十秒程度）は、実際の制限が「1日あたり」であっても短い値が返ってくることがあり、これだけでは制限の種類（分単位か日単位か）を判断できない。`quotaId`の文字列（`PerMinute`/`PerDay`等）を確認する必要がある。

---

## 初回完走の確認

ユーザーが無料枠のGemini APIキーをもう1つ発行し、`python3 simulate.py`を再実行した結果、**エラーなく完走**した。

```
[09:00] 佐藤 美咲: purchase -> Shibuya Excel Hotel Tokyu (自社製品検討=True, 関心度=9)
[09:00] 田中 健一: purchase -> ロクシタン SHIBUYA TOKYO (自社製品検討=True, 関心度=10)
[09:00] 鈴木 葵: purchase -> 渋谷センター街入口 (自社製品検討=True, 関心度=9)
...(中略、5ターン×3ペルソナ=15件すべて正常終了)...
[21:00] 鈴木 葵: purchase -> 渋谷センター街入口 (自社製品検討=True, 関心度=9)

ログを保存しました: logs/sim_20260630_180426.json
```

3ペルソナ×5ターン＝15回のGemini API呼び出しが、無料枠（1日20回）の範囲内で完走することを確認した。

**地図再生も両方式で動作確認済み**:
- `map_view.html`（Leaflet+OpenStreetMap）: `file://`で直接開いて正常に動作。渋谷の実際の道路網上にペルソナのマーカーと自社店舗ピンが表示され、サイドパネルに思考ログが表示された
- `map_view_google.html`（Google Maps JavaScript API）: GitHub Pages（`https://kenobix.github.io/Claude_practice/260701/map_view_google.html`）にデプロイ後、HTTPリファラー制限をかけたAPIキーで正常に動作。実際のGoogle Map上（渋谷駅周辺の建物・施設名表示あり）でペルソナの移動と思考ログを確認できた

## action_type="purchase"の誤判定を修正（コード側で強制）

ログを見ると、自社店舗以外（ロクシタン、渋谷センター街入口など）でも`action_type="purchase"`と出力される問題が、プロンプトに制約を追記した後も解消していなかった（LLMがプロンプトの指示を完全には守らない）。

[simulate.py](./simulate.py)の`run_simulation()`内で、LLMの出力をそのまま信用せず、**コード側で強制的に補正**するよう修正した。選んだ候補が自社店舗（`is_our_store`）でないにもかかわらず`action_type=="purchase"`だった場合、`"enter_and_browse"`に書き換える。プロンプトでの指示はLLMへのヒントとしては有効だが、データの整合性を保証するには不十分であり、決定的なルールはコード側で担保すべき、という教訓。

なお`analyze.py`の購入判定は元々「`location_after`が自社店舗名と一致し、かつ`action_type=="purchase"`」という条件でフィルタしていたため、この不具合があっても**分析結果（購入率の集計）自体には影響していなかった**。コンソールログの表示が紛らわしかっただけ。

## Google CloudのAPI費用上限について

ユーザーから「APIの請求が無料の範囲でできるように予算を設定したい」との相談があり、以下を回答した。

- GCPの「予算とアラート」は**通知のみ**で、上限に達してもAPIの利用は自動停止されない（誤解しやすいポイント）
- 確実に無料枠を超えないようにするには、「IAMと管理」→「割り当てとシステムの上限」（Quotas）で、Places APIの1日あたりリクエスト数を無料枠より低い値に手動設定することを推奨（これは実際にAPIを停止させる強制力がある）
- 予算アラートは早期警告として併用（金額をテストの¥1ではなく、たとえば¥500程度に設定）するのが望ましい
