# 作業ログ: GitHubへのpush設定（2026-03-31）

## 概要

ローカルのフォルダをGitHubリポジトリにpushするまでの一連の作業記録。

---

## 手順と発生したエラー

### 1. フォルダ名の変更

`claude` フォルダを `claude_practice` にリネーム。

```bash
mv /home/<ユーザー名>/work/claude /home/<ユーザー名>/work/claude_practice
```

---

### 2. GitHubでリポジトリを作成

- GitHubにログインし、新しいリポジトリを作成
- 公開設定: Public
- **ミス**: LICENSEファイルを含めて作成してしまった（後でpush時にエラーの原因になる）

---

### 3. ローカルのgit初期化

```bash
cd /home/<ユーザー名>/work/claude_practice
git init
git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
```

**発生した状況**: `git init` のデフォルトブランチ名が `master` になった。  
GitHubのデフォルトは `main` のため、ブランチ名を統一する必要があった。

```bash
git branch -m master main
```

---

### 4. gitユーザー情報の設定

```bash
git config --global user.name "<GitHubユーザー名>"
git config --global user.email "<メールアドレス>"
```

---

### 5. コミット

```bash
git add .
git commit -m "Initial commit"
```

---

### 6. push → エラー1: fetch first

```
! [rejected] main -> main (fetch first)
error: failed to push some refs to '...'
hint: Updates were rejected because the remote contains work that you do not have locally.
```

**原因**: GitHub側にLICENSEファイルを含む初期コミットが存在しており、ローカルと履歴が異なっていた。

**対処**: リモートの変更をpullしてからpush。

```bash
git pull origin main --allow-unrelated-histories --no-rebase
git push -u origin main
```

---

### 7. push → エラー2: メールアドレスのプライバシー制限

```
remote: error: GH007: Your push would publish a private email address.
remote: You can make your email public or disable this protection by visiting:
remote: https://github.com/settings/emails
```

**原因**: GitHubでメールアドレスがプライベート設定になっており、コミットにそのメールが含まれるとpushを拒否される。

**対処**: 
1. GitHub Settings → Emails にアクセス
2. 「All web-based Git operations will be linked to `<ID>+<ユーザー名>@users.noreply.github.com`」のnoreplyアドレスを確認
3. gitのメール設定をnoreplyアドレスに変更

```bash
git config --global user.email "<ID>+<ユーザー名>@users.noreply.github.com"
```

4. 既存コミットのメールアドレスを書き換え

```bash
git commit --amend --reset-author --no-edit

# 全コミットを一括で書き換える場合
FILTER_BRANCH_SQUELCH_WARNING=1 \
GIT_COMMITTER_EMAIL="<noreplyアドレス>" \
git filter-branch -f --env-filter \
  'export GIT_AUTHOR_EMAIL="<noreplyアドレス>"' \
  -- --all
```

5. 履歴を書き換えたので `--force` でpush

```bash
git push https://<ユーザー名>:<PAT>@github.com/<ユーザー名>/<リポジトリ名>.git main --force
```

---

### 8. push成功

GitHubリポジトリにローカルのファイルが反映された。

---

## 認証について

- `gh` コマンド（GitHub CLI）はインストールされていなかった
- SSH認証も未設定
- **HTTPS + Personal Access Token (PAT)** で認証
  - GitHub Settings → Developer settings → Personal access tokens → Generate new token
  - スコープ: `repo` にチェック
- VSCode × GitHub連携のサインインも活用
- **注意**: PATはチャットやコードに貼り付けない。使い終わったトークンはすぐに削除する

---

## 今日の教訓

| # | 教訓 |
|---|------|
| 1 | GitHubでリポジトリ作成時はREADMEやLICENSEを含めず「空」で作るとpush時のコンフリクトを避けられる |
| 2 | `git init` のデフォルトブランチは `master` のため、GitHubに合わせて `main` にリネームする |
| 3 | GitHubのメールプライバシー設定が有効な場合、noreplyアドレスをgitに設定する必要がある |
| 4 | 履歴を書き換えた後のpushには `--force` が必要 |
| 5 | PATは秘密情報として扱い、チャットやコードに直接書かない |
