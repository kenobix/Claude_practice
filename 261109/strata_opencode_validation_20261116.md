# Strata × OpenCode 実験記録（2026-11-16）

## 目的と環境

Windows 11、RTX 5070 Ti 16GB、RAM 32GB、WSL2 Ubuntu 24.04の構成で、Strata上の`qwen3.8-flash-next-coder-iq1_m`をOpenCode経由で使用し、短いコード生成、リポジトリの読み取り、実ファイル作成・pytest自動実行を検証する。

- Strata TerminalからローカルAPI `http://127.0.0.1:8080` を公開し、認証キーを有効化。
- WSL2の`.wslconfig`で`networkingMode=mirrored`を設定。WSLから`curl http://127.0.0.1:8080/health`の成功を確認。
- APIの確認値：`status: ok`、`max_context: 32768`、`loaded: true`、`images: false`、`service: strata`。
- WSLのOpenCodeはv2.2.6。モデル指定は`strata/qwen3.8-flash-next-coder-iq1_m`。OpenCode Go設定は残し、ローカル用プロバイダーを別に追加した。

## 検証1：読み取り専用・短い生成

- `AGENTS.md`の読み取りを指示すると、OpenCodeは`Read .`および`Read AGENTS.md`を呼び出し、内容を要約した。
- `1+1`を計算するPythonコード `print(1 + 1)` を生成した。
- 読み取り専用の指示ではファイル変更なしと報告。

## 検証2：ファイル生成 → pytest → エラー修正

作業場所：`~/projects/strata-smoketest`（Gitリポジトリの外）

### 実施された手順

1. AIが`is_prime.py`と`test_is_prime.py`を新規作成した。6k±1法による素数判定で、実行時間のオーダーは`O(√n)`。
2. システムPython 3.12.3にはpytestがなかった。エージェントが作業ディレクトリに`.venv`を作成し、pytest 9.2.0を導入した。
3. **初回は失敗**：pytestの収集時、`test_is_prime.py`に`import pytest`がなく、`NameError: name 'pytest' is not defined`が発生した。
4. エージェントが誤りを特定し、テストファイルを修正した。
5. 再実行で`29 passed in 38.48s`。内訳は正常値9、境界値8、異常値12の計29ケース。
6. 別の計時表示`real 0m36.163s`とpytest報告`38.48s`は一致せず、定量比較時には再計測が必要。

**評価**：「一発でテストに合格」とは言えない。**ファイル作成・環境準備・失敗検出・修正・再テストまで自動実行した**ことが成果。

### 計測上の注意

- `2**61-1`（素数）の検査は試し割りで重い。平方根は約15億、6刻みのループだけでも約2.5億反復のオーダー。エージェント自身の反復回数説明は不正確だった。
- pytest時間はコードの計算時間であり、LLMのtoken/s性能ではない。
- Strataモニターの今回の観測値は平均**84.0 tokens/s**、VRAM **15.6/16 GB**、システムRAM **30.9/31 GB**。どちらも限界に近く、ComfyUIなどの同時GPU利用は避ける。

## 公開するもの／しないもの

**この文書は、ユーザーが提示した実行ログの概要のみをGitHubに保存したもの。** ソース本体`is_prime.py`と`test_is_prime.py`はユーザーのWSL内にあり、このコミットには含まない。別途ユーザーの環境からコピーして差分確認の上コミットする。

以下は絶対に公開しない：実APIキー、`~/.config/opencode/opencode.json`（キーが含まれる可能性）、`strata-api-key`、Windowsユーザー固有の認証情報、58GBのモデル本体、`.venv/`、`.pytest_cache/`。

## まとめと次の作業

- **P1完了**：StrataとOpenCodeの接続、ツール操作、実ファイル作成・pytest成功。
- **P2次**：CodexとStrataで小規模なバグ1〜2件を、独立の受入テスト・手戻り回数・作業時間で比較し、時間を区切る。
- **P3本命**：Python/FastAPIからComfyUIの保存済みワークフローを呼び、Qwen画像からLTX/Wan動画への単一ジョブを完成させる。

設計判断と却下・保留した案の一覧は[今後の優先順位・判断根拠](ai_learning_roadmap_20261116.md)に記録した。なお、既存の[261021/README](../261021/README.md)にあるMLOpsの配信受入不採用は、今回のStrataテストでは解消していない。

## 出典

利用者の2026-11-16実行ログ、Strataモニター、[AGENTS.md](../AGENTS.md)、[261109/README.md](README.md)、[261021/README.md](../261021/README.md)。ソース全文自体の第三者による独立監査は未実施。
