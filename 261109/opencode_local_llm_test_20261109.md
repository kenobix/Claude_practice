# OpenCode × Ollama ローカルLLM開発エージェント検証記録

- 記録日：2026-11-09
- 環境：Windows 11 / WSL2 Ubuntu 24.04、RTX 5070 Ti 16GB、OpenCode 2.2.6、Ollama
- モデル：`qwen3:8b` と `qwen3:14b`
- 関連記録：[`desktop_ai_setup_report.md`](desktop_ai_setup_report.md) および [`scripts/benchmark_ollama.py`](scripts/benchmark_ollama.py)
- 情報源：利用者が提示したOpenCode画面とUbuntuターミナル出力。未実施の作業を完了扱いしない。

本書はGo契約前の8B/14B実験と、その保存時点の履歴を保持する。Go契約後のGLM・Kimi追加実験、課金・プライバシー設定の観測、最新の運用方針は[追加検証記録](opencode_go_evaluation_20261109.md)を参照。

## 1. 検証の目的と現在地

WSL上のOpenCodeからOllamaのローカルモデルを選択し、コードの作成、テスト実行、エラーからの復帰までを行えるか比較した。実験は既存GitHubリポジトリ外の独立したフォルダーで実施した。

| 項目 | 状態 |
| --- | --- |
| OpenCodeの起動 | 成功。画面表示バージョンは **2.2.6** |
| Ollamaモデル認識 | `/models` で`qwen3:8b`・`qwen3:14b`を確認 |
| 8Bでファイル作成とテスト | 成功。ただし追加指示が**1回**必要 |
| 14Bでファイル作成とテスト | OpenCode画面では**追加指示0回**で成功 |
| OpenCode Go | **未契約・未接続**。この後に契約予定 |
| OpenCode提供の無料クラウドモデル | 一覧表示のみ。**未実行・未評価** |

## 2. 既存のOllama単独ベンチマーク（参考）

| モデル | 平均生成速度 | 平均API総時間 | GPU割当（別ログで確認） |
| --- | ---: | ---: | --- |
| Qwen3 8B | 131.0 tokens/s | 1.99秒 | 100% GPU確認済み |
| Qwen3 14B | 78.9 tokens/s | 3.28秒 | 100% GPU確認済み |

この測定はOllama APIの生成速度であり、OpenCodeのファイル操作・コマンド実行を含むタスク全体の速度ではない。生成速度だけでコーディング性能の優劣を決めない。GPU割当は既存の環境構築記録にある別の`ollama ps`ログを根拠とし、各ベンチマーク実行時や今回のOpenCode実験中の割当・VRAMピークを測定した値ではない。

## 3. テスト条件

実験フォルダー：

- 8B：`~/projects/opencode-test`
- 14B：`~/projects/opencode-test-14b`

それぞれ別フォルダーで`git init`してOpenCodeを起動。既存の`Claude_practice`を直接編集しない構成。

両モデルには**次の同一プロンプト**を入力した。

> Python標準ライブラリのみを使って、小さな計算アプリを実装してください。
>
> 要件：
> - `calculator.py` に加算・減算・乗算・除算の関数を実装する
> - ゼロ除算の場合は `ZeroDivisionError` を発生させる
> - `test_calculator.py` に `unittest` のテストを作成する
> - 正常系と異常系を含むテストを実際に実行する
> - テストの実行結果と変更ファイルを最後に報告する
>
> このフォルダー以外のファイルを変更しないでください。外部への通信、追加パッケージのインストール、Gitのcommit・pushは禁止します。

## 4. Qwen3 8Bの結果

### 読み取り専用テスト

作業ディレクトリとファイル一覧を実ツールで確認するよう指示。複数回の`read`系ツール呼び出しは見られたが、`.git/`の探索に偏り、期待したディレクトリ一覧を明瞭に報告せず、追加質問で終了。**ツール呼び出しは動作したがタスク完了は不十分。**

### コード生成とテスト

- `calculator.py`と`test_calculator.py`を作成。
- 加算・減算・乗算・通常の除算・ゼロ除算について計5テストを作成。
- 初回に`python test_calculator.py`を実行し、次のエラーで失敗。

```text
/bin/bash: line 1: python: command not found
Exited with code 127
```

- `which python || which python3`により`/usr/bin/python3`を発見。しかし、自律的にはテストを再実行せず、ユーザーに質問して終了。
- 利用者が**1回追加指示**し、`python3 -m unittest discover -v`の実行を依頼。OpenCode内で全5件成功。
- 利用者が外部Ubuntuターミナルでも再確認して全5件成功。

```text
test_add ... ok
test_divide_normal ... ok
test_divide_zero ... ok
test_multiply ... ok
test_subtract ... ok

Ran 5 tests in 0.000s
OK
```

`git status --short`には`calculator.py`、`test_calculator.py`、`__pycache__/`が未追跡として表示された。`__pycache__/`は公開保存対象外。**コードの間違いではなく、実行環境への適応と失敗後の自律再試行に課題があった。**

## 5. Qwen3 14Bの結果

- 8Bと同じプロンプトを別フォルダーで実行。
- `calculator.py`と`test_calculator.py`を作成。
- 最初から`python3 test_calculator.py`を実行。
- OpenCode画面で`Ran 5 tests in 0.000s`、`OK`を確認。
- **人間の追加指示0回**でテスト成功。
- 最終メッセージは次の操作候補を質問する形式で、要求した**変更ファイル一覧を含む完全な最終報告は未達成**。
- **外部Ubuntuターミナルでの14Bのテスト再実行は、本記録時点では未確認**。画面上の成功結果と区別する。

## 6. 比較と判断

| 項目 | Qwen3 8B | Qwen3 14B |
| --- | --- | --- |
| コードファイル作成 | 成功 | 成功 |
| テストファイル作成 | 成功 | 成功 |
| 初回テスト | `python`不在で失敗 | 5件成功 |
| 環境コマンドの判断 | 失敗後に`python3`を発見 | 最初から`python3`を使用 |
| 人間の追加指示 | **1回** | **0回** |
| 最終テスト | **5/5成功**（外部端末でも確認） | **5/5成功**（OpenCode画面） |
| 変更ファイルを含む完了報告 | 不十分 | 不十分 |

**暫定評価：** この簡単な1件の課題では14Bの方が少ない介入で完了。8Bも追加指示後には全テスト成功。ただし、1タスクだけでは一般的なコーディング性能は判断できず、実際の開発タスクを複数件比較する必要がある。OpenCodeセッション全体の正確な所要時間・コスト・VRAMピークは未計測。

## 7. 無料クラウドモデルの扱い

OpenCodeの選択画面には`GLM-5.3-Flash Free`、`DeepSeek V4.1 Flash Free`、`MiMo-V2.6-Flash Free`なども表示。**今回は実行していないため実力は未評価**。無料モデルはクラウド経由であり、ローカルOllamaと違って入力内容が外部に送信される。データ保存・学習条件はモデルごとに異なり、提供モデルや無料枠も変更される。後日試す場合は公開してよい小さなサンプルだけを使い、1～2モデルに絞る。

- OpenCode公式モデル一覧：https://opencode.ai/docs/console/models/
- OpenCode Go料金・制限：https://opencode.ai/console/go

## 8. 次の作業（未完了のみ）

初期記録時点では14Bの外部Ubuntu端末での再確認が未完了だったが、第10節の再実行で確認済みとなった。残る作業は次のとおり。

1. **OpenCode Goの契約・接続**。価格、利用上限、課金条件を契約画面で確認。APIキーはGitHubに保存しない。
2. Goで新規フォルダーを使い、同じ計算アプリのファイル生成・テストを実行。
3. 公開可能な実コードを使い、複数の小タスクでGo・14B・必要なら無料モデルを比較。指標：初回成功、テスト合格、追加指示回数、人間の修正時間、利用制限による停止時間、支出。
4. Goで埋まらない用途が判明した場合にのみClaude Pro追加を判断。
5. OpenCodeの評価後、ComfyUIによる画像生成・編集、約5秒動画を最小実験。Strataはその後に要件を確認して検討。
6. 今後の検証結果もGitHubに追記する。

外部バックアップは本AI開発チェックリストの対象外とする。バックアップが不要という意味ではない。

## 9. 保存方針

本ファイルを`261109/opencode_local_llm_test_20261109.md`に保存。必要に応じて、各実験フォルダーの`calculator.py`と`test_calculator.py`を**元ファイルを変更せず**モデル別にコピーして再現可能にする。`__pycache__/`、`.venv/`、Git管理用`.git/`、APIキー、モデル本体は公開しない。過去の環境構築レポートの記述を上書きせず、後続実験として追加する。

## 10. GitHub保存時のコード確認と再実行（追記）

2026-11-09 21:12 JST、CodexからWSL Ubuntu上の元実験フォルダーで次の既存テストを再実行した。OpenCode画面や利用者端末での過去の結果とは別の確認であり、コード生成・OpenCodeセッション・Ollamaベンチマークの再実行は行っていない。

```bash
cd ~/projects/opencode-test
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ~/projects/opencode-test-14b
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

| 元実験フォルダー | 今回の結果 | 終了コード |
|---|---|---:|
| `~/projects/opencode-test`（8B） | `Ran 5 tests in 0.000s`、`OK` | 0 |
| `~/projects/opencode-test-14b`（14B） | `Ran 5 tests in 0.000s`、`OK` | 0 |

両方とも`test_add`、`test_subtract`、`test_multiply`、`test_divide_normal`、`test_divide_zero`が成功した。14BのUbuntu側での独立した再確認はこれにより完了。表示された0.000秒はテストランナーの丸め表示であり、OpenCodeのタスク所要時間ではない。8Bの追加指示1回、14Bの追加指示0回という生成時の履歴は変更しない。

ソースを確認したところ、四則演算とゼロ除算の例外、および標準`unittest`の5テストのみで、認証情報・個人情報・絶対パス・外部通信・ファイル書き込みは含まれていなかった。原本を変更せず、次の4ファイルを内容が一致する形でコピーした。

- [8B calculator.py](opencode_samples/qwen3-8b/calculator.py)
- [8B test_calculator.py](opencode_samples/qwen3-8b/test_calculator.py)
- [14B calculator.py](opencode_samples/qwen3-14b/calculator.py)
- [14B test_calculator.py](opencode_samples/qwen3-14b/test_calculator.py)

公開コピーの実行方法は[README](README.md)に記録した。`.git/`、`.venv/`、`__pycache__/`、モデル本体、OpenCodeの個人設定はコピーしていない。Goは未契約・未接続、無料クラウドモデルは未実行のままで、今回の保存作業でも契約・導入・クラウドAPIアクセスは行っていない。
