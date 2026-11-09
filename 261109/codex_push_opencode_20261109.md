# Codexへの指示：OpenCode × Ollama実験記録のGitHub反映

## 目的

WSL Ubuntuの`~/projects/Claude_practice`で作業してください。公開リポジトリは`https://github.com/kenobix/Claude_practice`、反映先は`origin/main`。

利用者が`261109/`へ配置した以下の新規2ファイルを確認し、既存の実験履歴と整合させて**安全にcommit・push**してください。

- `261109/opencode_local_llm_test_20261109.md`（実験記録）
- `261109/codex_push_opencode_20261109.md`（本指示書）

## 実施手順

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

## 公開前の安全検査

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

## 完了報告

日本語で「変更ファイル・テスト実行有無と結果・8B/14Bの確認内容・コミットID・push成功/失敗・作業ツリーの状態・次の作業（Go契約・接続・同条件比較）」を簡潔に報告。できなかった場合は理由を正確に報告し、成功したと偽らない。
