# 簿記2級 図解カード: AI画像生成 → 自作SVGへの移行

## 経緯

`sets/bookkeeping-2kyu/` の図解画像29枚はすべてGemini/GPTのテキスト画像生成で
作成したが、QAの結果、以下のような構造的欠陥が繰り返し見つかった。

- 5種類の「テンプレートパネル」(連結の相殺消去図、圧縮記帳の償却曲線、
  仕損・減損の度外視法比較、部門別配賦のA/B/C部門フロー、製造間接費の配賦差異
  バーグラフ)が無関係なテーマの画像に混入し、同じ数値・ラベルが出てくる
- 画像内に表示された数値どうしが矛盾する(内訳の合計が一致しないなど)
- 日本語の文字化け、プロンプトの指示文自体がキャプションとして描画される

これはテキスト画像生成(拡散モデル)が正確な文字・数値・矢印関係を要する
技術図を苦手とするという構造的限界であり、プロンプトの工夫では解決しない。
そのため、この種の「構造が命」の図はAI画像生成をやめ、ダッシュボードの
忘却曲線グラフ(`js/chart.js`)と同じ方式——`createElementNS`によるインライン
SVGをコードで直接記述する——に切り替える。数値・ラベルはコードで指定した
通りにしか出ないため、構造的に正しさを保証できる。

対象はQAで再生成が必要と判定された20枚(本番稼働中の7枚＋未配線の13枚)。
残り9枚は実害が軽微なため今回は対象外とし、明示的に先送りする(下表参照)。

## アーキテクチャ

- カードJSONに新フィールド `diagram`(ビルダー関数を指すレジストリキー)を
  `image`と並列に追加。1枚のカードが両方を持つことはない。
- `js/diagrams/`にトピック別のビルダーファイルを配置。各ビルダーは
  detachedな`<svg>`要素を返す純粋関数で、`js/diagrams/index.js`の
  `buildDiagram(key, container, altText)`経由で`review.js`から呼ばれる。
- 共通プリミティブ(箱・矢印・T字勘定・表・複数行ラベル)は`js/diagrams/helpers.js`
  に集約し、色は`js/config.js`の`COLORS`(CSSトークン由来)のみを使う。
- 目視QAは`tools/diagram-gallery.html`+`tools/screenshot-diagrams.mjs`
  (ヘッドレスChromiumでスクリーンショット)で行う。詳細は各ファイルのコメント参照。

## フェーズ

1. **Phase 1(基盤、完了)**: スキーマ配線、`helpers.js`、QAハーネス、
   お手本として`bookkeeping-cycle-diagram`を実装・配線・実機確認。
2. **Phase 2(パイロット、未着手)**: 本番稼働中で実害のある残り6枚
   (`double-entry-basics`, `financial-statements-structure`,
   `tegata-flow-diagram`, `wip-valuation-spoilage`, `cost-variance-analysis`,
   `cvp-breakeven-chart`)をサブエージェント2並列で実装。
3. **Phase 3(展開、未着手)**: 未配線13枚を新規配線としてサブエージェント3並列で実装。
4. **Phase 4(先送り)**: 残り9枚はPNGのまま維持(下表)。

## 移行状況

### 移行対象20枚

| 旧PNG | 新diagramキー | ステータス |
|---|---|---|
| `bookkeeping-cycle-diagram.png` | `bookkeeping-cycle-diagram` | ✅ 完了(お手本・配線済み) |
| `double-entry-basics.png` | `double-entry-basics` | 未着手(Phase 2) |
| `financial-statements-structure.png` | `financial-statements-structure` | 未着手(Phase 2) |
| `tegata-flow-diagram.png` | `tegata-flow-diagram` | 未着手(Phase 2) |
| `wip-valuation-spoilage.png` | `wip-valuation-spoilage` | 未着手(Phase 2) |
| `cost-variance-analysis.png` | `cost-variance-analysis` | 未着手(Phase 2) |
| `cvp-breakeven-chart.png` | `cvp-breakeven-chart` | 未着手(Phase 2) |
| `bank-reconciliation-diagram.png`(未配線) | `bank-reconciliation-diagram` | 未着手(Phase 3) |
| `other-securities-valuation.png`(未配線) | `other-securities-valuation` | 未着手(Phase 3) |
| `depreciation-methods-compare.png`(未配線) | `depreciation-methods-compare` | 未着手(Phase 3) |
| `lease-classification-compare.png`(未配線) | `lease-classification-compare` | 未着手(Phase 3) |
| `merger-goodwill-structure.png`(未配線) | `merger-goodwill-structure` | 未着手(Phase 3) |
| `capital-consolidation-timetable.png`(未配線) | `capital-consolidation-timetable` | 未着手(Phase 3) |
| `downstream-upstream-compare.png`(未配線) | `downstream-upstream-compare` | 未着手(Phase 3) |
| `manufacturing-overhead-variance.png`(未配線) | `manufacturing-overhead-variance` | 未着手(Phase 3) |
| `absorption-vs-direct-costing.png`(未配線) | `absorption-vs-direct-costing` | 未着手(Phase 3) |
| `high-low-point-method.png`(未配線) | `high-low-point-method` | 未着手(Phase 3) |
| `standard-operating-volume-types.png`(未配線) | `standard-operating-volume-types` | 未着手(Phase 3) |
| `grade-costing-equivalence.png`(未配線) | `grade-costing-equivalence` | 未着手(Phase 3) |
| `process-costing-carryover.png`(未配線) | `process-costing-carryover` | 未着手(Phase 3) |

### 先送り9枚(PNGのまま維持)

`adjusting-entries-flow.png`, `honten-shiten-kanjo.png`, `securities-classification.png`,
`tax-effect-consolidation.png`, `cost-accounting-flow.png`, `department-cost-allocation.png`,
`kobetsu-vs-sogo.png`, `sogo-genka-types.png`(以上、本番稼働中で実害軽微)、
`assekiritono-flow.png`(未配線、QAで問題なしと判定済み)。

## Phase 1 の実装・QA記録

- `js/diagrams/helpers.js`: `createSvg`/`drawBox`/`drawLines`/`ensureArrowMarker`+
  `drawArrow`/`drawTAccount`/`drawTable`/`yen`を実装。フォントは各要素に埋め込まず、
  `css/review.css`の`.flashcard-diagram text`で一括指定(chart.jsと同じ方針)。
- `js/diagrams/bookkeeping-basics.js`: `buildBookkeepingCycleDiagram`を実装
  (取引→三伝票→仕訳帳→総勘定元帳の帳簿組織フロー図)。
- `js/review.js`を3分岐(`card.diagram` → `imagePathFor(card)` → 図なし)に変更、
  `js/store.js`(`makeCard`・初期状態生成・`importSet`)と`js/deck.js`
  (「図あり」バッジ)に`diagram`フィールドを配線。
- QA: `node tools/screenshot-diagrams.mjs bookkeeping-cycle-diagram`で
  ヘッドレスChromiumのスクリーンショットを目視確認 → ラベル・矢印・数値関係(なし)
  ともに仕様通り。さらにPlaywrightで実アプリの復習画面を自動操作し、対象2カード
  (「仕訳帳と総勘定元帳の役割の違いは？」「三伝票制で使われる3種類の伝票は？」)
  を裏返してSVGが正しく表示されること、コンソールエラーがないことを確認済み。
