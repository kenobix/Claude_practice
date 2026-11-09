# Codexへの指示：デスクトップPC環境構築記録をGitHubへ安全に反映

あなたはWSL2 Ubuntu 24.04上で動いているCodexです。作業場所は `/home/kenshin/projects/Claude_practice`、公開リポジトリは `https://github.com/kenobix/Claude_practice`、ブランチは `main` です。

## 目的

このフォルダーの `261109/desktop_ai_setup_report.md`（作業履歴）と `261109/codex_push_prompt.md`（本指示）を検査し、**安全性・正確性を確認したうえで**、必要な最小限の変更をコミットして `origin/main` にpushしてください。

`/home/kenshin/projects/ai-lab/test_ollama.py` が存在する場合、内容を読み取り、トークン・個人情報・絶対パス・不要な巨大データ・依存関係の有無を確認してください。安全で再利用価値がある場合のみ、`261109/scripts/test_ollama.py` として**コピー**して追加してください。原本は変更しないでください。対象がなければコピーを省略し、状況を報告してください。

## 必ず守る制約

1. まずリポジトリ直下の `AGENTS.md` を読み、既存の運用規約に従ってください。`README.md` に新しい `261109/` の項目が未掲載なら、既存形式で1行程度を追記してください。それ以外の既存成果物は変更しないでください。
2. 最初に `pwd`, `git status --short`, `git branch --show-current`, `git remote -v`, `git log -1 --oneline` を実行して状態を確認してください。作業途中の他者の変更や未コミット差分を勝手に取り込んだり上書きしたりしないでください。
3. `git fetch origin` でリモート状態を確認してください。ローカル変更の状況を見て安全なら `git pull --ff-only origin main` で同期してください。fast-forwardできない、競合・意図しない差分がある場合は**そこで停止して報告**してください。`reset --hard`、強制push、rebase、履歴改変は禁止です。
4. **リポジトリはPublicです。** APIキー、アクセストークン、認証情報（GitHub CLI/PAT/SSH/Ollama以外のクラウドキーなど）、パスワード、製品キー、回復キー、個人情報の漏えいを必ず検査してください。`git diff`、`git diff --cached`、`git status` と追加ファイルの全内容を確認してください。問題があれば公開前に停止してください。
5. ログやドキュメントに書かれた「完了」「未完了」を取り違えないでください。特に **Qwen3 14Bは最終観測時にダウンロード中**であり、実測結果がない限り完了扱いにしないでください。既存ファイルやモデル環境を推測で改変しないでください。
6. `~/projects/ai-lab/.venv/`、Pythonのキャッシュ、Ollamaのモデル本体、WSLイメージ、Dockerイメージ、個人ファイルはコミット禁止です。全体を再帰的に `git add .` や `git add -A` しないでください。
7. 記録文書の文章・構成は意味を変えない範囲で必要最小限の訂正のみ可。インストール、OS/WSL設定変更、モデルのダウンロード、ベンチマーク実行は今回の作業に含めません。

## 推奨実行手順

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

## 成功条件

- `261109/desktop_ai_setup_report.md` と `261109/codex_push_prompt.md` が `origin/main` に反映されている。
- 追加されたファイルは公開可能な内容のみである。
- 必要に応じて `README.md` の一覧に `261109/` が追加されている。
- 予期しない変更・秘密情報のpush・強制的なGit履歴変更がない。
- `git status` がclean、GitHub上の反映先URL・コミットIDを報告できる。

競合、権限不足、認証失敗、安全上の疑義があれば、作業を中断してユーザーへ確認してください。エラーを隠したり、成功したと偽って報告したりしないでください。
