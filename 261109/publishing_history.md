# 公開手順・整理履歴

2026-11-09に使用したCodexへの依頼文と、2026-11-14の文書整理の対応表を保存する。
[全体の索引](README.md)へ戻る。現在の共通ルールは[ルートAGENTS.md](../AGENTS.md)を参照する。

以下の依頼文は**過去の作業の資料**であり、今回の実行指示ではない。文中の旧ファイル名・git add例・未完了状態は当時のまま残している。
旧パスを使用したコマンドをそのまま再実行せず、下の対応表と現在のファイル構成を確認する。

## 2026-11-14の整理

整理前のローカルは`f43ec6a`、GitHub側は`c1a42cb`まで3コミット先行していた。作業ツリーはcleanで、先行変更は`261014/`・`261021/`内だったためfast-forwardで同期した。
Markdown 10本を索引1本＋種類別5本に統合。本文・実測・未確認事項を引き継ぎ、READMEの操作・確認履歴も該当カテゴリへ移した。
画像、動画、ワークフロー、manifest、Pythonサンプルは変更しない。統合前のファイルはGit履歴にも残る。

| 統合前 | 統合先 |
|---|---|
| `desktop_ai_setup_report.md` | [environment.md](environment.md#environment) |
| `opencode_local_llm_test_20261109.md` | [coding_tools.md](coding_tools.md#local) |
| `opencode_go_evaluation_20261109.md` | [coding_tools.md](coding_tools.md#go) |
| `codex_opencode_go_setup.md` | [coding_tools.md](coding_tools.md#codex-go) |
| `comfyui_image_generation_evaluation_20261111.md` | [image_experiments.md](image_experiments.md#flux) |
| `comfyui_qwen_image_2_1_evaluation_20261112.md` | [image_experiments.md](image_experiments.md#qwen) |
| `comfyui_video_generation_evaluation_20261111.md` | [video_experiments.md](video_experiments.md#wan) |
| `codex_push_prompt.md` | [publishing_history.md](publishing_history.md#publish-environment) |
| `codex_push_opencode_20261109.md` | [publishing_history.md](publishing_history.md#publish-opencode) |
| `README.md`の詳細な確認・操作履歴 | 環境構築・開発AIツールの各末尾へ移動。READMEは索引と現方針へ整理 |

- [環境構築記録の公開依頼](#publish-environment)
- [OpenCode実験の公開依頼](#publish-opencode)

<a id="publish-environment"></a>

## Codexへの指示：デスクトップPC環境構築記録をGitHubへ安全に反映

あなたはWSL2 Ubuntu 24.04上で動いているCodexです。作業場所は `/home/kenshin/projects/Claude_practice`、公開リポジトリは `https://github.com/kenobix/Claude_practice`、ブランチは `main` です。

<a id="publish-environment-目的"></a>

### 目的

このフォルダーの `261109/desktop_ai_setup_report.md`（作業履歴）と `261109/codex_push_prompt.md`（本指示）を検査し、**安全性・正確性を確認したうえで**、必要な最小限の変更をコミットして `origin/main` にpushしてください。

`/home/kenshin/projects/ai-lab/test_ollama.py` が存在する場合、内容を読み取り、トークン・個人情報・絶対パス・不要な巨大データ・依存関係の有無を確認してください。安全で再利用価値がある場合のみ、`261109/scripts/test_ollama.py` として**コピー**して追加してください。原本は変更しないでください。対象がなければコピーを省略し、状況を報告してください。

<a id="publish-environment-必ず守る制約"></a>

### 必ず守る制約

1. まずリポジトリ直下の `AGENTS.md` を読み、既存の運用規約に従ってください。`README.md` に新しい `261109/` の項目が未掲載なら、既存形式で1行程度を追記してください。それ以外の既存成果物は変更しないでください。
2. 最初に `pwd`, `git status --short`, `git branch --show-current`, `git remote -v`, `git log -1 --oneline` を実行して状態を確認してください。作業途中の他者の変更や未コミット差分を勝手に取り込んだり上書きしたりしないでください。
3. `git fetch origin` でリモート状態を確認してください。ローカル変更の状況を見て安全なら `git pull --ff-only origin main` で同期してください。fast-forwardできない、競合・意図しない差分がある場合は**そこで停止して報告**してください。`reset --hard`、強制push、rebase、履歴改変は禁止です。
4. **リポジトリはPublicです。** APIキー、アクセストークン、認証情報（GitHub CLI/PAT/SSH/Ollama以外のクラウドキーなど）、パスワード、製品キー、回復キー、個人情報の漏えいを必ず検査してください。`git diff`、`git diff --cached`、`git status` と追加ファイルの全内容を確認してください。問題があれば公開前に停止してください。
5. ログやドキュメントに書かれた「完了」「未完了」を取り違えないでください。特に **Qwen3 14Bは最終観測時にダウンロード中**であり、実測結果がない限り完了扱いにしないでください。既存ファイルやモデル環境を推測で改変しないでください。
6. `~/projects/ai-lab/.venv/`、Pythonのキャッシュ、Ollamaのモデル本体、WSLイメージ、Dockerイメージ、個人ファイルはコミット禁止です。全体を再帰的に `git add .` や `git add -A` しないでください。
7. 記録文書の文章・構成は意味を変えない範囲で必要最小限の訂正のみ可。インストール、OS/WSL設定変更、モデルのダウンロード、ベンチマーク実行は今回の作業に含めません。

<a id="publish-environment-推奨実行手順"></a>

### 推奨実行手順

1. 対象のMarkdown 2ファイルを読み、リンクとコマンド・バージョン・ステータスを検査。
2. `AGENTS.md` と `README.md` を読み、必要なら `README.md` に `261109/` の説明を追加。
3. `test_ollama.py` は実在・内容確認の後、適切であれば `261109/scripts/` にコピー。秘密情報・実行時のローカル依存や不要なパスが含まれないか検査。
4. `git status --short`、`git diff --check`、`git diff -- README.md`、追加ファイル本文を確認。
5. **対象ファイルをパス指定でのみステージ**してください。例：
   `git add 261109/desktop_ai_setup_report.md 261109/codex_push_prompt.md`
   そして実際に必要で安全な場合のみ `git add README.md 261109/scripts/test_ollama.py`。
6. `git diff --cached --check` と `git diff --cached --stat`、`git diff --cached` を確認し、意図したもの以外が含まれていないこと、秘密情報がないことを再点検。
7. 問題がなければ次のメッセージでコミット：
   `git commit -m "docs: record desktop WSL AI environment setup"`
8. push直前にリモートが進んでいないか `git fetch origin` と `git status -sb` などで確認。差異がある場合は**無理にpushせず停止**。安全なら `git push origin main`。
9. 最後に `git status -sb`、`git log -1 --oneline` を表示し、push結果・変更ファイル・コミットID・未完了課題を短く日本語で報告。

<a id="publish-environment-成功条件"></a>

### 成功条件

- `261109/desktop_ai_setup_report.md` と `261109/codex_push_prompt.md` が `origin/main` に反映されている。
- 追加されたファイルは公開可能な内容のみである。
- 必要に応じて `README.md` の一覧に `261109/` が追加されている。
- 予期しない変更・秘密情報のpush・強制的なGit履歴変更がない。
- `git status` がclean、GitHub上の反映先URL・コミットIDを報告できる。

競合、権限不足、認証失敗、安全上の疑義があれば、作業を中断してユーザーへ確認してください。エラーを隠したり、成功したと偽って報告したりしないでください。

<a id="publish-opencode"></a>

## Codexへの指示：OpenCode × Ollama実験記録のGitHub反映

<a id="publish-opencode-目的"></a>

### 目的

WSL Ubuntuの`~/projects/Claude_practice`で作業してください。公開リポジトリは`https://github.com/kenobix/Claude_practice`、反映先は`origin/main`。

利用者が`261109/`へ配置した以下の新規2ファイルを確認し、既存の実験履歴と整合させて**安全にcommit・push**してください。

- `261109/opencode_local_llm_test_20261109.md`（実験記録）
- `261109/codex_push_opencode_20261109.md`（本指示書）

<a id="publish-opencode-実施手順"></a>

### 実施手順

1. **まずルート`AGENTS.md`を読む**。`pwd`、`git status --short`、`git branch --show-current`、`git remote -v`、`git log -1 --oneline`で状態を確認。
2. `git fetch origin`を実施。未コミット変更・分岐・競合を確認し、安全な場合のみ`git pull --ff-only origin main`で同期。`reset --hard`、rebase、履歴改変、強制pushは禁止。別作業の変更があれば勝手に処理しない。
3. `261109/README.md`、`261109/desktop_ai_setup_report.md`、`261109/scripts/benchmark_ollama.py`を読み、今回の新規実験記録が過去の測定と矛盾しないことを確認。**当時の未完了状態を記録した文章は歴史的記録として残す**。
4. `261109/README.md`に新規実験記録へのリンクと一文の要約を追加。ルート`README.md`は既に`261109/`が掲載済みなら変更しない。
5. 次のローカル実験ファイルが存在する場合、内容を調べる。原本は絶対に変更しない。
   - `~/projects/opencode-test/calculator.py`
   - `~/projects/opencode-test/test_calculator.py`
   - `~/projects/opencode-test-14b/calculator.py`
   - `~/projects/opencode-test-14b/test_calculator.py`
   公開可能で再現価値がある場合のみ`261109/opencode_samples/qwen3-8b/`と`261109/opencode_samples/qwen3-14b/`へ明示的にコピーし、READMEに実行方法を記録。存在しなければ省略して報告。
6. テストを新たに実行するなら`python3 -m unittest discover -v`を使い、**今回の再測定**と過去のユーザー共有ログを区別する。8Bの初回失敗・追加指示1回、14Bの初回成功・追加指示0回という履歴を変更しない。14BのUbuntu端末からの検証に成功した場合、その事実と日時を追記。
7. 無料クラウドモデルは今回未実行。OpenCode Goも**契約予定・未契約**なので、導入・接続・使用済みと書かない。性能値や料金の実測を捏造しない。今回のタスクでGo契約やクラウドAPIアクセスを実施しない。

<a id="publish-opencode-公開前の安全検査"></a>

### 公開前の安全検査

リポジトリは**Public**。APIキー、OAuth/PAT、パスワード、秘密鍵、GitHub CLI認証ファイル、個人情報、`.env`を一切公開しない。`.venv/`、`__pycache__/`、`.git/`、モデル本体、Docker・WSLイメージを追加しない。`git add .`と`git add -A`は禁止。不要な外部通信やパッケージ導入・追加の環境変更を行わない。

`git diff --check`と追加対象の全内容を確認し、**ファイルを明示指定して**ステージ。例：

```bash
git add 261109/opencode_local_llm_test_20261109.md \
        261109/codex_push_opencode_20261109.md \
        261109/README.md
```

必要に応じて検査済みの`261109/opencode_samples/`配下のファイルだけを追加。`git diff --cached --check`、`git diff --cached --stat`、`git diff --cached`を確認し、秘密情報・無関係な差分がない場合のみcommit：

```bash
git commit -m "docs: record OpenCode Ollama agent comparison"
```

push直前に`git fetch origin`で他端末からの更新がないか確認し、競合・意図しない変更がなければ`git push origin main`。最後に`git status -sb`、`git log -1 --oneline`で成功を確認。

<a id="publish-opencode-完了報告"></a>

### 完了報告

日本語で「変更ファイル・テスト実行有無と結果・8B/14Bの確認内容・コミットID・push成功/失敗・作業ツリーの状態・次の作業（Go契約・接続・同条件比較）」を簡潔に報告。できなかった場合は理由を正確に報告し、成功したと偽らない。
