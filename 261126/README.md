# 自作言語とVM

整数と真偽値を持つ小さな言語を作ります。同じプログラムを構文木（AST）の直接評価とスタック型VMで実行し、標準出力と診断を比較します。 [元計画](../261014/plan_language_vm.md)

Phase 0で文法、実行規則、資源上限、診断、例の期待値を確定しました。Phase 1では位置付きの字句解析と構文解析を実装し、CLIでトークンとASTを確認できるようにしました。

- [言語仕様と処理系の構成](docs/language_spec.md)
- [実行例の読み方と受入条件](docs/acceptance.md)
- [入力例](examples/)と[期待値](examples/expected.json)
- [計画の確認と作業ログ](work_log.md)

## CLIの使い方

リポジトリのルートから `261126/` に移動し、Python 3.12で実行します。追加パッケージは不要です。

```bash
cd 261126
python3 -m tiny_language --tokens examples/arithmetic.tiny
python3 -m tiny_language --ast examples/arithmetic.tiny
python3 -B -m unittest discover -s tests -v
```

`--tokens` はEOFを含むトークンの配列を、`--ast` は構文木をJSONで標準出力に表示します。トークンには元の文字列とソース位置があり、ASTの各ノードには対応するトークンがあります。ASTの整数値は長い整数の桁を保てる10進文字列です。

どちらのオプションも入力エラーと字句エラーを検出し、`--ast` は構文エラーも検出します。該当するエラーでは標準エラー出力にJSONの診断を1件出し、終了コードは2です。診断位置は1始まりの行・列と、0始まりで終端を含まないUTF-8バイト範囲です。 [Phase 1の受入記録](docs/acceptance.md#phase-1の受入記録)

Phase 2では名前解決、型検査、AST評価と `run --engine ast PATH` を実装します。
