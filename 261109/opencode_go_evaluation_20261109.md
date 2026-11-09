# OpenCode Go・無料モデルの追加検証記録

- 実験日：2026-11-09
- 対象：GLM-5.3-Flash Free、Kimi K2.7 Code（OpenCode Go）
- 関連履歴：[ローカル8B/14B実験](opencode_local_llm_test_20261109.md)、[環境構築・Ollamaベンチマーク](desktop_ai_setup_report.md)
- 情報源：今回利用者が共有した実験経過・Console/Activity表示・設定の観測と、Codexによる元コードの確認・テスト再実行。契約画面や認証設定には今回アクセスしていない。

## 1. 実験の経過

過去の記録ではGoは未契約、無料モデルは一覧表示のみだった。その履歴を残し、契約・実行後の結果を本書に追加する。課題は四則演算とゼロ除算の例外、標準`unittest`による正常系・異常系の確認で、[以前の計算アプリ課題](opencode_local_llm_test_20261109.md#3-テスト条件)を比較の基準とする。

### GLM-5.3-Flash Free

- OpenCode ConsoleのPersonalワークスペースに接続し、無料クラウドモデルを利用。
- 初回指示で`calculator.py`と`test_calculator.py`を生成し、8テストすべて成功。
- 人間の追加指示は0回。変更ファイルとテスト結果を含む最終報告あり。
- OpenCode Activityでは5リクエスト、$0.00の記録を確認したとの利用者報告。
- 元実験フォルダーは`~/projects/opencode-test-go/`。フォルダー名に`go`を含むが、この実験で使ったモデルは無料のGLM。

### Kimi K2.7 Code（OpenCode Go）

- 月額$10のGo契約後に`calculator.py`と`test_calculator.py`を生成。
- 初回は`python`コマンドが存在せず失敗したが、追加指示なしで`python3`へ切り替え、5テストすべて成功。
- 人間の追加指示は0回。最終報告あり。利用者はUbuntuターミナルからの独立した再実行でも5件成功を確認済み。
- ConsoleではKimiへの7リクエスト、利用額換算で約$0.02と表示されたとの利用者報告。月額契約とは別の追加請求とは断定しない。
- 元実験フォルダーは`~/projects/opencode-test-go-kimi/`。

## 2. 既存のローカル実験との比較

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

## 3. ソース確認と今回のテスト再実行

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

## 4. 契約・課金・プライバシー設定の観測

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

## 5. 公開ファイルと再実行方法

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

## 6. CodexとOpenCode Goの運用方針

- 現在のChatGPT Plus経由のCodexは維持する。
- OpenCode Goは独立して使える状態を維持する。
- 将来CodexからGoモデルを利用する際は、依頼で挙げられた`--keep-provider`等の公式オプションについて、提供元・対応バージョン・既存設定を保持する挙動を調査し、設定を上書きせず追加する。オプションの有無・適用先・具体的なコマンドは今回は未検証。
- CodexとGoを接続する作業自体は今回は実施しない。
- 実タスク用の専用ベンチマークの優先度を下げ、通常の開発中に成功と手戻りを記録する。タスク・使用モデル・テスト結果・追加指示回数・修正内容を残し、複数タスクで判断する。

## 7. 残作業

1. 無料モデルの学習利用許可の現在値を利用者が再確認する。今回設定は変更していない。
2. 通常の開発でCodex・独立したGo・必要に応じてローカルモデルを使い、成功と手戻りを継続記録する。
3. CodexからGoを使う必要が生じた時点で公式の接続手段と設定保持方法を調査する。接続作業は別タスクとする。

今回の保存作業では環境設定の変更、追加課金、モデルのダウンロード、新たなクラウドAPI利用は行っていない。
