# 自作言語とVM

整数と真偽値を持つ小さな言語を作ります。同じプログラムを構文木（AST）の直接評価とスタック型VMで実行し、標準出力と診断を比較します。 [元計画](../261014/plan_language_vm.md)

Phase 0で文法、実行規則、資源上限、診断、例の期待値を確定しました。現在の成果物は仕様と入力例です。実行用CLIはPhase 1以降に段階的に実装します。

- [言語仕様と処理系の構成](docs/language_spec.md)
- [実行例の読み方と受入条件](docs/acceptance.md)
- [入力例](examples/)と[期待値](examples/expected.json)
- [計画の確認と作業ログ](work_log.md)

Phase 1ではPython 3.12の標準ライブラリを使い、位置付きの字句解析・構文解析と、`--tokens`・`--ast` による表示を実装します。
