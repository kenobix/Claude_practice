# 日本語技術文書の書き方

日本語のREADME、設計書、作業ログ、説明資料、UI文言を新規作成・修正するときに使います。変更箇所の日本語を点検し、各文の適用条件と処理の順序を仕様や実装と照合します。

## スキルの利用

[yomiyasu v3.1.0](https://github.com/nanaism/yomiyasu/tree/db78b9bc5e868549b517a037df7a4d1c93edac23/skills/yomiyasu) を公式タグのコミット `db78b9bc5e868549b517a037df7a4d1c93edac23` から取得し、スキル一式を[リポジトリ内](../../.agents/skills/yomiyasu/SKILL.md)に収録しています。MITの[ライセンス](../../.agents/skills/yomiyasu/LICENSE)と[Unicode由来データの許諾通知](../../.agents/skills/yomiyasu/UNICODE-LICENSE.txt)を同梱しています。公式リポジトリの紹介用企業ロゴはスキルの実行・参照に使われないため収録対象から外しています。

- Codexはリポジトリの `.agents/skills/` を参照します。手動で指定する場合は依頼に `$yomiyasu` と対象を含めます。 [Codexのスキル配置](https://learn.chatgpt.com/docs/build-skills)
- Claude Codeは `.claude/skills/yomiyasu` の相対シンボリックリンクから同じスキルを参照し、`CLAUDE.md` の `@AGENTS.md` から共通指示を読みます。手動で指定する場合は `/yomiyasu` に対象を添えます。 [Claude Codeのスキル配置](https://code.claude.com/docs/en/skills)・[指示の読み込み](https://code.claude.com/docs/en/memory#import-additional-files)

新しいworktreeやcloneでは、Gitがシンボリックリンクを展開できる環境でリポジトリをチェックアウトし、そのworktreeを作業場所としてエージェントを起動します。検査スクリプトにはPython 3.9以降を使用します。Pythonの標準ライブラリだけで動き、パッケージの追加やHookの有効化は不要です。Python 3.8は上流資料でも動作未確認です。Windowsでは `python3` の代わりに `python` または `py -3` を使えます。 [v3.1.0の実行環境とコマンド](https://github.com/nanaism/yomiyasu/blob/v3.1.0/docs/tools.md)

## 標準手順

1. 対象範囲、読み手、関連仕様を確認します。推敲では変更前の文章を、新規作成では依頼と根拠資料を控えます。
2. 推敲する原文や新規の下書きをyomiyasuで検査し、見直す候補を拾います。短い変更では対象の段落と必要な前後の文を使います。
3. 候補を読み、直訳調、曖昧な主語、冗長な表現を必要な分だけ直します。専門用語は読み手に合わせ、判断の根拠がある場合に説明を補います。
4. 修正前後の主張、主語と対象、断定の強さ、時制を読み比べます。新規作成では根拠資料と照合します。
5. 技術的な文は該当する仕様、実装の分岐、境界条件を扱うテストと照合します。原文の誤りを訂正した場合は、通常の推敲と区別して根拠と理由を記録します。
6. 適用条件と例外、否定、処理順序、保証範囲、数値・単位、未検証事項を一つずつ確認します。条件の論理積・論理和や、初回と再試行のような状態別の違いも照合します。 [条件と状態を落とした事例](lessons-learned.md)
7. Markdownの構造、リンク、アンカー、コマンド、識別子、引用したエラー原文、測定値を確認します。HTMLのUI文言は表示上の文を抜き出して検査し、反映後の表示と属性も確かめます。
8. 最終稿に対して `run` を実行し、提出前に `verify` で記録と照合します。原稿を変えた場合は、変更後の原稿を改めて検査します。
9. 変更に必要なCIを確認します。広い範囲や技術仕様に関わる推敲では、必要に応じて実装との照合を独立レビューで確かめます。
10. 変更箇所、検査結果、残した指摘の理由、未確認事項を報告します。Pythonや検査を実行できなかった場合は理由を示します。

| 確認項目 | 判定に使う根拠 |
| --- | --- |
| 日本語として自然か | 読み手を想定した通読とyomiyasuの候補 |
| 原文の意味を保ったか | 条件・例外・順序・確度ごとの前後比較 |
| 現在の実装と一致するか | 対応する仕様、実装の分岐とテスト。成功応答が保証する範囲や、条件を判定する処理も照合する |
| CIが成功したか | 対象SHAの実行結果とジョブの検査範囲 |

yomiyasuの指摘件数は修正の参考に使います。`verify` は最終稿と検査時の記録を照合します。各項目の判定には表に示した根拠が必要です。 [既存の文書編集方針](../../261021/docs/README.md#文書の編集方針)・[PR #6の推敲と確認](https://github.com/kenobix/Claude_practice/pull/6)

## コマンド例

この例では、リポジトリのルートから、配下にあるUTF-8の原文と最終稿を検査します。ファイル名は例です。検査対象を一時ディレクトリに抜き出した場合は、そのディレクトリを作業場所にしてスクリプトを絶対パスで指定します。検査記録には原稿の写しが含まれるため、一時領域に保存してコミット対象から外します。

```bash
skill=.agents/skills/yomiyasu/scripts
review_state=$(mktemp -d)
python3 "$skill/yomiyasu_lint.py" original.md
python3 "$skill/yomiyasu_check.py" run \
  --source original.md --final revised.md --state-dir "$review_state"
python3 "$skill/yomiyasu_check.py" verify \
  --final revised.md --state-dir "$review_state"
```

新規作成では `--source original.md` の代わりに `--material notes.md` で根拠資料を渡します。推敲でも根拠資料を追加できます。スキルは作業ディレクトリ外やドットで始まるパスの対象ファイルを直接読み込みません。設定などの中から必要な公開可能な文章だけを抜き出す場合も、一時領域で検査できます。 [v3.1.0の引数と検査記録](https://github.com/nanaism/yomiyasu/blob/v3.1.0/docs/tools.md)
