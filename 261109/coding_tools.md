# 開発AIツールの検証・連携記録

OpenCode＋Ollama、無料・Goモデル、Codex CLIとの連携を、2026-11-09の実施順でまとめる。
種類別の整理日は2026-11-14。[全体の索引](README.md)へ戻る。

ローカル8B/14Bの計算アプリは各5テスト成功。GLM無料モデルは8件、Kimi Goは5件成功。
後続のCodex/Go連携まで完了した履歴があり、前半の「未契約・未接続」は過去の状態を表す。
現在の運用判断はCodex＋OpenCode Goを維持し、Claude Proは追加しない方針。料金・設定・バージョンは当時の観測で、今回再確認していない。

- [ローカル8B/14Bの実験](#local)
- [無料モデル・Goの追加検証](#go)
- [Codex CLIとGoの連携](#codex-go)
- [公開時の再確認・サンプル操作メモ](#coding-readme)

各部の節番号と「今回」は、その元記録内の番号・実施時点を指す。

<a id="local"></a>

## OpenCode × Ollama ローカルLLM開発エージェント検証記録

- 記録日：2026-11-09
- 環境：Windows 11 / WSL2 Ubuntu 24.04、RTX 5070 Ti 16GB、OpenCode 2.2.6、Ollama
- モデル：`qwen3:8b` と `qwen3:14b`
- 関連記録：[`desktop_ai_setup_report.md`](environment.md#environment) および [`scripts/benchmark_ollama.py`](scripts/benchmark_ollama.py)
- 情報源：利用者が提示したOpenCode画面とUbuntuターミナル出力。未実施の作業を完了扱いしない。

本書はGo契約前の8B/14B実験と、その保存時点の履歴を保持する。Go契約後のGLM・Kimi追加実験、課金・プライバシー設定の観測、最新の運用方針は[追加検証記録](coding_tools.md#go)を参照。

<a id="local-1-検証の目的と現在地"></a>

### 1. 検証の目的と現在地

WSL上のOpenCodeからOllamaのローカルモデルを選択し、コードの作成、テスト実行、エラーからの復帰までを行えるか比較した。実験は既存GitHubリポジトリ外の独立したフォルダーで実施した。

| 項目 | 状態 |
| --- | --- |
| OpenCodeの起動 | 成功。画面表示バージョンは **2.2.6** |
| Ollamaモデル認識 | `/models` で`qwen3:8b`・`qwen3:14b`を確認 |
| 8Bでファイル作成とテスト | 成功。ただし追加指示が**1回**必要 |
| 14Bでファイル作成とテスト | OpenCode画面では**追加指示0回**で成功 |
| OpenCode Go | **未契約・未接続**。この後に契約予定 |
| OpenCode提供の無料クラウドモデル | 一覧表示のみ。**未実行・未評価** |

<a id="local-2-既存のollama単独ベンチマーク参考"></a>

### 2. 既存のOllama単独ベンチマーク（参考）

| モデル | 平均生成速度 | 平均API総時間 | GPU割当（別ログで確認） |
| --- | ---: | ---: | --- |
| Qwen3 8B | 131.0 tokens/s | 1.99秒 | 100% GPU確認済み |
| Qwen3 14B | 78.9 tokens/s | 3.28秒 | 100% GPU確認済み |

この測定はOllama APIの生成速度であり、OpenCodeのファイル操作・コマンド実行を含むタスク全体の速度ではない。生成速度だけでコーディング性能の優劣を決めない。GPU割当は既存の環境構築記録にある別の`ollama ps`ログを根拠とし、各ベンチマーク実行時や今回のOpenCode実験中の割当・VRAMピークを測定した値ではない。

<a id="local-3-テスト条件"></a>

### 3. テスト条件

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

<a id="local-4-qwen3-8bの結果"></a>

### 4. Qwen3 8Bの結果

<a id="local-読み取り専用テスト"></a>

#### 読み取り専用テスト

作業ディレクトリとファイル一覧を実ツールで確認するよう指示。複数回の`read`系ツール呼び出しは見られたが、`.git/`の探索に偏り、期待したディレクトリ一覧を明瞭に報告せず、追加質問で終了。**ツール呼び出しは動作したがタスク完了は不十分。**

<a id="local-コード生成とテスト"></a>

#### コード生成とテスト

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

<a id="local-5-qwen3-14bの結果"></a>

### 5. Qwen3 14Bの結果

- 8Bと同じプロンプトを別フォルダーで実行。
- `calculator.py`と`test_calculator.py`を作成。
- 最初から`python3 test_calculator.py`を実行。
- OpenCode画面で`Ran 5 tests in 0.000s`、`OK`を確認。
- **人間の追加指示0回**でテスト成功。
- 最終メッセージは次の操作候補を質問する形式で、要求した**変更ファイル一覧を含む完全な最終報告は未達成**。
- **外部Ubuntuターミナルでの14Bのテスト再実行は、本記録時点では未確認**。画面上の成功結果と区別する。

<a id="local-6-比較と判断"></a>

### 6. 比較と判断

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

<a id="local-7-無料クラウドモデルの扱い"></a>

### 7. 無料クラウドモデルの扱い

OpenCodeの選択画面には`GLM-5.3-Flash Free`、`DeepSeek V4.1 Flash Free`、`MiMo-V2.6-Flash Free`なども表示。**今回は実行していないため実力は未評価**。無料モデルはクラウド経由であり、ローカルOllamaと違って入力内容が外部に送信される。データ保存・学習条件はモデルごとに異なり、提供モデルや無料枠も変更される。後日試す場合は公開してよい小さなサンプルだけを使い、1～2モデルに絞る。

- OpenCode公式モデル一覧：https://opencode.ai/docs/console/models/
- OpenCode Go料金・制限：https://opencode.ai/console/go

<a id="local-8-次の作業未完了のみ"></a>

### 8. 次の作業（未完了のみ）

初期記録時点では14Bの外部Ubuntu端末での再確認が未完了だったが、第10節の再実行で確認済みとなった。残る作業は次のとおり。

1. **OpenCode Goの契約・接続**。価格、利用上限、課金条件を契約画面で確認。APIキーはGitHubに保存しない。
2. Goで新規フォルダーを使い、同じ計算アプリのファイル生成・テストを実行。
3. 公開可能な実コードを使い、複数の小タスクでGo・14B・必要なら無料モデルを比較。指標：初回成功、テスト合格、追加指示回数、人間の修正時間、利用制限による停止時間、支出。
4. Goで埋まらない用途が判明した場合にのみClaude Pro追加を判断。
5. OpenCodeの評価後、ComfyUIによる画像生成・編集、約5秒動画を最小実験。Strataはその後に要件を確認して検討。
6. 今後の検証結果もGitHubに追記する。

外部バックアップは本AI開発チェックリストの対象外とする。バックアップが不要という意味ではない。

<a id="local-9-保存方針"></a>

### 9. 保存方針

本ファイルを`261109/opencode_local_llm_test_20261109.md`に保存。必要に応じて、各実験フォルダーの`calculator.py`と`test_calculator.py`を**元ファイルを変更せず**モデル別にコピーして再現可能にする。`__pycache__/`、`.venv/`、Git管理用`.git/`、APIキー、モデル本体は公開しない。過去の環境構築レポートの記述を上書きせず、後続実験として追加する。

<a id="local-10-github保存時のコード確認と再実行追記"></a>

### 10. GitHub保存時のコード確認と再実行（追記）

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

<a id="go"></a>

## OpenCode Go・無料モデルの追加検証記録

- 実験日：2026-11-09
- 対象：GLM-5.3-Flash Free、Kimi K2.7 Code（OpenCode Go）
- 関連履歴：[ローカル8B/14B実験](coding_tools.md#local)、[環境構築・Ollamaベンチマーク](environment.md#environment)
- 情報源：今回利用者が共有した実験経過・Console/Activity表示・設定の観測と、Codexによる元コードの確認・テスト再実行。契約画面や認証設定には今回アクセスしていない。

<a id="go-1-実験の経過"></a>

### 1. 実験の経過

過去の記録ではGoは未契約、無料モデルは一覧表示のみだった。その履歴を残し、契約・実行後の結果を本書に追加する。課題は四則演算とゼロ除算の例外、標準`unittest`による正常系・異常系の確認で、[以前の計算アプリ課題](coding_tools.md#local-3-テスト条件)を比較の基準とする。

<a id="go-glm-53-flash-free"></a>

#### GLM-5.3-Flash Free

- OpenCode ConsoleのPersonalワークスペースに接続し、無料クラウドモデルを利用。
- 初回指示で`calculator.py`と`test_calculator.py`を生成し、8テストすべて成功。
- 人間の追加指示は0回。変更ファイルとテスト結果を含む最終報告あり。
- OpenCode Activityでは5リクエスト、$0.00の記録を確認したとの利用者報告。
- 元実験フォルダーは`~/projects/opencode-test-go/`。フォルダー名に`go`を含むが、この実験で使ったモデルは無料のGLM。

<a id="go-kimi-k27-codeopencode-go"></a>

#### Kimi K2.7 Code（OpenCode Go）

- 月額$10のGo契約後に`calculator.py`と`test_calculator.py`を生成。
- 初回は`python`コマンドが存在せず失敗したが、追加指示なしで`python3`へ切り替え、5テストすべて成功。
- 人間の追加指示は0回。最終報告あり。利用者はUbuntuターミナルからの独立した再実行でも5件成功を確認済み。
- ConsoleではKimiへの7リクエスト、利用額換算で約$0.02と表示されたとの利用者報告。月額契約とは別の追加請求とは断定しない。
- 元実験フォルダーは`~/projects/opencode-test-go-kimi/`。

<a id="go-2-既存のローカル実験との比較"></a>

### 2. 既存のローカル実験との比較

生成時の経過は利用者共有の履歴。ローカル8B/14Bのテスト結果は以前の保存時に再確認しており、今回その2モデルは再実行していない。

| 項目 | Qwen3 8B（ローカル） | Qwen3 14B（ローカル） | GLM-5.3-Flash Free | Kimi K2.7 Code（Go） |
|---|---|---|---|---|
| コード・テスト生成 | 成功 | 成功 | 成功 | 成功 |
| 初回テストと復帰 | `python`不在で失敗、追加指示で再実行 | 最初から`python3`で成功 | 初回指示で成功 | `python`不在で失敗、自律的に`python3`へ切り替え成功 |
| 人間の追加指示 | 1回 | 0回 | 0回 | 0回 |
| 最終テスト成功 | 5/5 | 5/5 | 8/8 | 5/5 |
| 最終報告 | 変更ファイルを含む完全な報告は未達成 | 変更ファイルを含む完全な報告は未達成 | 変更ファイル・テスト結果の報告あり | 最終報告あり |
| Activity/Consoleのリクエスト表示 | 本記録では未確認 | 本記録では未確認 | 5リクエスト | 7リクエスト |
| 利用額表示 | 本記録では未計測 | 本記録では未計測 | $0.00 | 約$0.02（利用額換算） |

Kimiは初回のコマンド実行では失敗しており、「初回テスト成功」とは記録しない。一方で、人間の追加指示を受けずに復帰できた。GLMの8テストとKimiの5テストは生成されたテスト構成の違いで、モデル性能の直接的な優劣を示さない。Kimiも1つのテストメソッド内で複数の入力を検証している。

この小規模な計算アプリ実験だけでは、一般的なコード品質、複雑な修正への対応、長時間の安定性は判断できない。OpenCodeセッション全体の所要時間や、同条件での総費用・品質差も実測していない。既存の131.0/78.9 tokens/sはOllama単独の生成速度であり、今回のクラウドモデルの速度とは比較しない。

<a id="go-3-ソース確認と今回のテスト再実行"></a>

### 3. ソース確認と今回のテスト再実行

両実装は`add`、`subtract`、`multiply`、`divide`を備え、除数が0なら`ZeroDivisionError`を発生させる。実装は組み込み演算のみ、テストは標準ライブラリの`unittest`と同じフォルダーの`calculator`を使用する。追加依存、認証情報、個人情報、絶対パス、外部通信、ファイル書き込みは含まれない。

| 対象 | テストメソッド数 | 確認内容 |
|---|---:|---|
| GLM | 8 | 四則演算、負数、小数加算、1/3の近似比較、ゼロ除算の例外・メッセージ、0/0の例外 |
| Kimi | 5 | 四則演算ごとの複数入力（負数・0・小数結果を含む）、ゼロ除算の例外 |

2026-11-09 21:50 JST、CodexからWSL Ubuntuの各元フォルダーで以下を実行した。ファイルの内容は変更せず、`PYTHONDONTWRITEBYTECODE=1`でPythonキャッシュの新規生成も抑止した。

```bash
cd ~/projects/opencode-test-go
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ~/projects/opencode-test-go-kimi
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

| 対象 | 今回の実行結果 | 終了コード |
|---|---|---:|
| GLM | `Ran 8 tests in 0.000s`、`OK` | 0 |
| Kimi | `Ran 5 tests in 0.000s`、`OK` | 0 |

これは生成済みPythonコードの独立した再検証であり、OpenCodeでの生成やクラウドモデルへの再問い合わせではない。0.000秒はテストランナーの丸め表示で、モデルの応答時間やタスク所要時間ではない。

<a id="go-4-契約課金プライバシー設定の観測"></a>

### 4. 契約・課金・プライバシー設定の観測

以下は2026-11-09の利用者共有内容であり、今回CodexがConsoleの現在値を再確認した結果ではない。将来の料金や設定を保証するものではなく、税額・追加請求額も計算していない。

| 項目 | 共有された観測 |
|---|---|
| Go契約 | 月額$10＋適用税 |
| 次回更新日 | 2026-12-09 |
| Extra Usage | OFF |
| Auto-recharge | OFF |
| 追加クレジット残高 | $0 |
| 有料モデルの学習利用許可 | OFF |
| 地域設定 | Europe & United States |
| 無料モデルの学習利用許可 | スクリーンショットでONを確認したとの利用者報告。現在値は再確認できておらず、OFFへ変更済みとは扱わない |

GLMの$0.00とKimiの約$0.02はActivity/Consoleの表示値として記録する。Goの月額料金とは別の追加請求が発生した証拠として扱わない。クレジットカード番号、請求先住所、アカウント認証情報、画面の個人情報は保存しない。

<a id="go-5-公開ファイルと再実行方法"></a>

### 5. 公開ファイルと再実行方法

原本を変更せず、次の4ファイルだけを内容が一致する形でコピーした。`.git/`、`__pycache__/`、`.venv/`、モデル本体、認証設定はコピーしていない。

- GLM：[calculator.py](opencode_samples/glm-5.3-flash-free/calculator.py)、[test_calculator.py](opencode_samples/glm-5.3-flash-free/test_calculator.py)
- Kimi：[calculator.py](opencode_samples/kimi-k2.7-code-go/calculator.py)、[test_calculator.py](opencode_samples/kimi-k2.7-code-go/test_calculator.py)

コピーしたコードのテストは次の手順で実行できる。Python標準ライブラリだけで完結し、OpenCode・Ollama・APIキーは不要。

```bash
cd ~/projects/Claude_practice/261109/opencode_samples/glm-5.3-flash-free
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ../kimi-k2.7-code-go
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

<a id="go-6-codexとopencode-goの運用方針"></a>

### 6. CodexとOpenCode Goの運用方針

- 現在のChatGPT Plus経由のCodexは維持する。
- OpenCode Goは独立して使える状態を維持する。
- 将来CodexからGoモデルを利用する際は、依頼で挙げられた`--keep-provider`等の公式オプションについて、提供元・対応バージョン・既存設定を保持する挙動を調査し、設定を上書きせず追加する。オプションの有無・適用先・具体的なコマンドは今回は未検証。
- CodexとGoを接続する作業自体は今回は実施しない。
- 実タスク用の専用ベンチマークの優先度を下げ、通常の開発中に成功と手戻りを記録する。タスク・使用モデル・テスト結果・追加指示回数・修正内容を残し、複数タスクで判断する。

<a id="go-7-残作業"></a>

### 7. 残作業

1. 無料モデルの学習利用許可の現在値を利用者が再確認する。今回設定は変更していない。
2. 通常の開発でCodex・独立したGo・必要に応じてローカルモデルを使い、成功と手戻りを継続記録する。
3. CodexからGoを使う必要が生じた時点で公式の接続手段と設定保持方法を調査する。接続作業は別タスクとする。

今回の保存作業では環境設定の変更、追加課金、モデルのダウンロード、新たなクラウドAPI利用は行っていない。

<a id="codex-go"></a>

## Codex CLIとOpenCode Goの連携記録

- 作業日：2026-11-09
- 環境：WSL2 Ubuntu 24.04（実機は24.04.5 LTS）
- 目的：従来のChatGPT経由のCodexを維持し、Go用の起動経路を追加する。
- 関連履歴：[Go・無料モデルの検証](coding_tools.md#go)、[ローカル8B/14B実験](coding_tools.md#local)
- 情報源：利用者が完了を報告した導入・接続手順、実機のバージョンと設定構造、公式資料。今回の記録作業でセットアップやモデルへの問い合わせは再実行していない。

<a id="codex-go-1-導入結果"></a>

### 1. 導入結果

| 項目 | 2026-11-09の結果 | 今回の確認方法 |
|---|---|---|
| nvm | 0.41.0 | `nvm --version` |
| Node.js | 24.22.2 | nvm配下の`node --version` |
| npm / npx | いずれも11.19.1 | nvm配下をPATHに指定して各`--version` |
| Codex CLI | 0.167.0 | nvm配下をPATHに指定して`codex --version` |
| OpenCode Console | Personalワークスペースへ接続済み | 利用者の完了報告 |
| 通常起動 | `codex`でGPT-6 Luna | 利用者の動作報告と、既定モデル`gpt-6-luna`の設定確認 |
| Go起動 | `codex-go`でKimi K2.7 Code | 利用者の動作報告と、Go用プロファイル・起動スクリプトの構造確認 |

nvm 0.41.0を導入後、Node.js 24.22.2を用意し、npm経由でCodex CLI 0.167.0を導入した。以下は対応する導入・確認用のコマンド例であり、保存された逐語的な実行ログではない。今回これらのインストール操作は行っていない。

```bash
# nvm導入済みのシェルでの導入例
nvm install 24.22.2
nvm use 24.22.2
npm install -g @openai/codex@0.167.0

# バージョンの確認
nvm --version
node --version
npm --version
npx --version
codex --version
```

今回の非対話シェルではnvmと`~/.local/bin`がPATHへ反映されていなかったため、確認時にはnvmのスクリプトとインストール済みの実行パスを明示した。利用者の通常のUbuntu端末での起動経路とは区別する。

<a id="codex-go-2-console接続と従来プロバイダーの維持"></a>

### 2. Console接続と従来プロバイダーの維持

利用者は次のセットアップ方式でConsoleのPersonalワークスペースに接続し、従来のCodexのサインインとモデルを維持した。

```bash
npx @opencode/console setup codex --keep-provider
```

`--keep-provider`は`@opencode/console`のセットアップオプションであり、`codex login`のオプションではない。ローカルにキャッシュされた公式パッケージ`@opencode/console` 0.1.7のREADME・実装で確認した。READMEでは既存のサインイン・モデルを維持しつつ、ワークスペースのMCP・skills・instructions・policiesを適用する仕組みと説明されている。セットアップは設定やhooksを書き換える操作なので、今回再実行していない。

同READMEでは、ワークスペースの方針によってはモデル名・トークン数をhooks経由で報告すると説明されている。従来モデルの維持を、Consoleとの通信が一切なくなる意味に解釈しない。実機のベース設定にはhooksが存在することだけを確認し、認証状態や送信履歴は調べていない。

<a id="codex-go-3-ベース設定とgo専用プロファイル"></a>

### 3. ベース設定とGo専用プロファイル

実機では`~/.codex/config.toml`のトップレベルが`model = "gpt-6-luna"`、`model_reasoning_effort = "high"`で、既定の`model_provider`を上書きしていなかった。Goの定義を追加し、起動時のプロファイルで切り替える。

モデル選択に関係する項目だけを抜粋した構造例を示す。実ファイルのコピーや、単独でセットアップを完結させるための設定一式ではない。

```toml
# ~/.codex/config.toml の構造例
model = "gpt-6-luna"
model_reasoning_effort = "high"

[model_providers.opencode-go]
name = "OpenCode Go"
base_url = "https://opencode.ai/inference/go/openai/v1"
wire_api = "responses"
```

Go専用ファイルには切り替えに必要な項目だけを置く。

```toml
# ~/.codex/go.config.toml
model = "kimi-k2.7-code"
model_provider = "opencode-go"
```

Codex CLI 0.167.0では`codex --profile go`がベース設定の上に`go.config.toml`を重ねる。`[profiles.go]`をベースファイルに作る旧方式は使用しない。実機にも旧`profiles`テーブルはなかった。この読み込み方式は[OpenAI公式のAdvanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles)で確認した。

Goのエンドポイントと`wire_api = "responses"`は[OpenCode公式Codexガイド](https://opencode.ai/docs/console/harnesses/codex/)と一致した。設定の各項目は[OpenAI公式Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)も参照。

<a id="codex-go-4-go専用の起動スクリプト"></a>

### 4. Go専用の起動スクリプト

`~/.local/bin/codex-go`にGo専用の起動スクリプトを用意した。モデル選択を行う最終行の抜粋は次のとおり。

```bash
exec codex --profile go "$@"
```

実機のスクリプトは実行可能で、`--profile go`と引数の引き継ぎを確認した。`exec`でCodexに置き換わる。今回スクリプト自体は実行していない。

<a id="codex-go-5-bashrcと起動の使い分け"></a>

### 5. .bashrcと起動の使い分け

実機の`.bashrc`ではnvmの読み込みと次のPATH設定を確認した。公開例は関連部分のみ。

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.local/bin:$PATH"
```

通常のUbuntu端末では、新しいシェルで設定を反映して起動する。

| コマンド | 選ぶ設定・モデル | 利用枠・接続先 |
|---|---|---|
| `codex` | ベース設定、GPT-6 Luna | 従来のChatGPT経由のCodexを維持（利用者報告） |
| `codex-go` | `go.config.toml`、Kimi K2.7 Code | PersonalワークスペースのOpenCode Go |

Go用プロファイルはCodex CLIの通常モデルを置き換えずに選択できる。今回の確認は設定とバージョンまでで、両モデルへの新たな推論実行や性能測定はしていない。

<a id="codex-go-6-goの契約枠と追加従量課金の防止"></a>

### 6. Goの契約枠と追加従量課金の防止

同日の[既存の契約・設定観測](coding_tools.md#go)は、Goが月額$10＋適用税、次回更新2026-12-09、Extra Usage OFF、Auto-recharge OFF、追加クレジット$0。今回Consoleへアクセスして現在値を再確認したものではない。

[OpenCode公式Go資料](https://opencode.ai/docs/console/go/)を2026-11-09に参照したところ、GoのKimi K2.7 Codeの月間利用枠は利用額換算$60で、5時間枠は月間の20%、週間枠は50%と説明されていた。この金額は契約料金や追加クレジット残高とは別の利用量の計数枠で、無制限の利用を意味しない。将来の枠はConsoleと公式資料で確認する。

同資料ではExtra Usageを有効にすると、Goの上限到達後に従量課金残高へ移行する。従量利用への切り替えを避けるためExtra Usage OFF、残高の自動補充を避けるためAuto-recharge OFFを維持する方針。月額契約の更新まで止まる設定ではない。以前のKimiの約$0.02という表示も、別途の追加請求とは断定していない。

<a id="codex-go-7-今回の確認範囲と残作業"></a>

### 7. 今回の確認範囲と残作業

バージョン、モデル・プロバイダー・プロファイルの構造、起動スクリプトのモデル選択、PATH設定を読み取り中心で確認した。既存のユーザー設定は変更せず、セットアップ、課金設定変更、モデルのダウンロード、API呼び出しは実施していない。

残作業は通常の開発中の成功・手戻りの記録。無料モデルの学習利用許可の現在値も、従来どおり未確認の課題として保持する。従来のCodexと独立したGoを維持し、専用ベンチマークの優先度は下げる。

<a id="coding-readme"></a>

## 公開時の確認・操作メモ（旧READMEから統合）

以下は2026-11-09に追記した確認履歴。記載日時・測定条件と、その時点の未完了状態を保持する。

<a id="coding-readme-1-追記opencodeのコーディング実験"></a>

### 追記：OpenCodeのコーディング実験

本節はGo契約前の履歴。契約後の追加実験は次節と[Go・無料モデルの記録](coding_tools.md#go)を参照。

OpenCode画面の履歴では、8Bは初回の`python`不在で失敗し、追加指示1回で5テスト成功。14Bは最初から`python3`を使い、追加指示0回で5テスト成功した。両モデルとも変更ファイルを含む完全な最終報告は未達成。詳細と限界は[実験記録](coding_tools.md#local)に保存した。

公開用コードは[8Bのcalculator.py](opencode_samples/qwen3-8b/calculator.py)・[テスト](opencode_samples/qwen3-8b/test_calculator.py)、[14Bのcalculator.py](opencode_samples/qwen3-14b/calculator.py)・[テスト](opencode_samples/qwen3-14b/test_calculator.py)。元の実験フォルダーから4ファイルだけを内容を変えずにコピーし、`.git/`やキャッシュは含めていない。Python標準ライブラリだけで実行でき、OpenCode・Ollama・モデルの起動は不要。

```bash
cd ~/projects/Claude_practice/261109/opencode_samples/qwen3-8b
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ../qwen3-14b
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

2026-11-09 21:12 JSTにCodexから両方の元実験フォルダーで既存テストを再実行し、それぞれ5件成功。これはOpenCodeの実行履歴とは別の再確認で、生成タスクや速度ベンチマークは再実行していない。原本は変更せず、Pythonキャッシュの生成も抑止した。今回の文書に付随していた`Zone.Identifier` 2件はダウンロード属性のみのため削除し、公開対象から除外した。

無料クラウドモデルは未実行・未評価、OpenCode Goは未契約・未接続のまま。今回の作業で契約・導入・クラウドAPI接続は行っていない。

<a id="coding-readme-2-追記opencode-go無料モデルの追加検証"></a>

### 追記：OpenCode Go・無料モデルの追加検証

本節はCodexからGoへ接続する前の検証履歴。後続の連携完了は次節と[連携手順](coding_tools.md#codex-go)を参照。

利用者の2026-11-09の追加実験では、GLM-5.3-Flash Freeは初回指示で生成と8テストに成功し、最終報告あり。Go契約後のKimi K2.7 Codeは`python`不在での失敗から追加指示なしで`python3`へ切り替え、5テストに成功し、最終報告あり。両方とも人間の追加指示は0回。8B/14Bの過去の履歴とは別の後続実験として保存する。

公開コピーはGLMの[calculator.py](opencode_samples/glm-5.3-flash-free/calculator.py)・[test_calculator.py](opencode_samples/glm-5.3-flash-free/test_calculator.py)、Kimiの[calculator.py](opencode_samples/kimi-k2.7-code-go/calculator.py)・[test_calculator.py](opencode_samples/kimi-k2.7-code-go/test_calculator.py)。4ファイルだけを原本から内容を変えずにコピーした。標準Pythonだけで再検証でき、クラウドAPI接続や認証情報は不要。

```bash
cd ~/projects/Claude_practice/261109/opencode_samples/glm-5.3-flash-free
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ../kimi-k2.7-code-go
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

今回の保存作業では2026-11-09 21:50 JSTに元実験フォルダーでGLM 8件・Kimi 5件の成功を再確認した。原本は変更していない。契約・利用額・設定は利用者共有の観測として[追加検証記録](coding_tools.md#go)に記載し、今回Consoleの設定確認・変更、追加課金、モデル取得、CodexからGoへの接続は行っていない。

<a id="coding-readme-3-追記codex-cliとopencode-goの連携"></a>

### 追記：Codex CLIとOpenCode Goの連携

2026-11-09、利用者からCodex CLIとPersonalワークスペースのGo連携完了が報告された。実機でnvm 0.41.0、Node.js 24.22.2、npm/npx 11.19.1、Codex CLI 0.167.0を確認。`--keep-provider`はOpenCode Consoleのセットアップ側のオプションで、通常モデルを維持する方式。

`config.toml`の既定モデルは`gpt-6-luna`のまま、`go.config.toml`で`kimi-k2.7-code`と`opencode-go`を選ぶ構造を確認した。`~/.local/bin/codex-go`は`codex --profile go`を起動する。公開範囲をモデル選択・起動経路に絞った構造例を[連携手順](coding_tools.md#codex-go)に保存した。今回、設定変更やモデルへの問い合わせは行っていない。
