# 自作言語とVM

整数と真偽値を持つ小さな言語を作ります。同じプログラムを構文木（AST）の直接評価とスタック型VMで実行し、標準出力と診断を比較します。 [元計画](../261014/plan_language_vm.md)

Phase 0で言語仕様と期待値を定め、Phase 1で位置付きの字句解析と構文解析を実装しました。Phase 2で名前解決、静的型検査、AST評価器を追加し、Phase 3でコンパイラとスタック型VMを実装しました。

- [言語仕様と処理系の構成](docs/language_spec.md)
- [バイトコード命令仕様と命令数の計算](docs/bytecode.md)
- [実行例の読み方と受入条件](docs/acceptance.md)
- [入力例](examples/)と[期待値](examples/expected.json)
- [計画の確認と作業ログ](work_log.md)

## CLIの使い方

リポジトリのルートから `261126/` に移動し、Python 3.12で実行します。追加パッケージは不要です。

```bash
cd 261126
python3 -m tiny_language --tokens examples/arithmetic.tiny
python3 -m tiny_language --ast examples/arithmetic.tiny
python3 -m tiny_language run --engine ast examples/sum_loop.tiny
python3 -m tiny_language run --engine vm examples/sum_loop.tiny
python3 -m tiny_language --disassemble examples/sum_loop.tiny
python3 -m tiny_language run --engine vm --trace examples/sum_loop.tiny
python3 -B -m unittest discover -s tests -v
```

`--tokens` はEOFを含むトークンの配列を、`--ast` は構文木をJSONで標準出力に表示します。トークンには元の文字列とソース位置があり、ASTの各ノードには対応するトークンがあります。ASTの整数値は長い整数の桁を保てる10進文字列です。

どちらのオプションも入力エラーと字句エラーを検出し、`--ast` は構文エラーも検出します。該当するエラーでは標準エラー出力にJSONの診断を1件出し、終了コードは2です。診断位置は1始まりの行・列と、0始まりで終端を含まないUTF-8バイト範囲です。 [Phase 1の受入記録](docs/acceptance.md#phase-1の受入記録)

`run --engine ast PATH` と `run --engine vm PATH` はプログラム全体の名前解決と型検査を終えてから実行します。上の合計例の標準出力は、どちらも `15` と `6` の2行です。

診断は標準エラー出力に書きます。実行前にソースの誤りを検出した場合は標準出力が空で、終了コードは2です。実行時エラーでは成功済みの出力が残り、終了コードは3です。処理系内部の不具合では `E_INTERNAL` と終了コード4を返します。 [Phase 3の受入記録](docs/acceptance.md#phase-3の受入記録)

`--disassemble PATH` は命令番号、命令名、オペランド、元ソースの位置をJSON配列で標準出力に表示します。`run --engine vm --trace PATH` は命令が成功するたびに、命令と実行後のスタック、変数スロットへの書き込みをJSONで1行ずつ標準エラー出力に書きます。`print` の出力先は標準出力です。 [逆アセンブルとトレース](docs/bytecode.md#逆アセンブルとトレース)

次のPhase 4では有界の生成プログラムで両処理系の一致を調べ、処理段階ごとの時間を測定します。
