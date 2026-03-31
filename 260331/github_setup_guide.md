# GitHubへのpush完全ガイド（HTTPS認証）

エラーなしでローカルフォルダをGitHubにpushするための手順。

---

## 事前準備

### 1. Personal Access Token（PAT）を作成

1. GitHubにログイン
2. 右上アイコン → Settings → Developer settings → Personal access tokens → **Tokens (classic)**
3. "Generate new token (classic)" をクリック
4. 設定:
   - **Note**: 任意の名前（例: `my-token`）
   - **Expiration**: 任意（例: 90 days）
   - **Scopes**: `repo` にチェック
5. "Generate token" をクリック
6. 表示されたトークン（`ghp_xxxx...`）を安全な場所にコピー（**この画面を閉じると二度と見られない**）

---

### 2. noreplyメールアドレスを確認

GitHubではメールアドレスのプライバシー保護のため、コミットにはnoreplyアドレスを使う。

1. GitHub → Settings → Emails
2. 「All web-based Git operations will be linked to `<ID>+<ユーザー名>@users.noreply.github.com`」の部分を確認・コピー

---

### 3. GitHubでリポジトリを作成（**空で作成**）

1. GitHub右上の「+」→ New repository
2. Repository name を入力
3. Public / Private を選択
4. **「Add a README file」「Add .gitignore」「Choose a license」はすべてチェックしない**（空のリポジトリを作成）
5. "Create repository" をクリック

> **ポイント**: 空で作成することで、後のpush時にコンフリクトが発生しない。

---

## 手順

### Step 1: gitユーザー情報を設定

```bash
git config --global user.name "<GitHubユーザー名>"
git config --global user.email "<ID>+<ユーザー名>@users.noreply.github.com"
```

noreplyアドレスを使うことで、メールプライバシーエラーを回避できる。

---

### Step 2: ローカルリポジトリを初期化

```bash
cd /path/to/your/folder
git init
git branch -m master main
```

> `git branch -m master main` でブランチ名をGitHubのデフォルト（`main`）に統一する。

---

### Step 3: リモートを登録

```bash
git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
```

---

### Step 4: コミット

```bash
git add .
git commit -m "Initial commit"
```

---

### Step 5: push

```bash
git push https://<ユーザー名>:<PAT>@github.com/<ユーザー名>/<リポジトリ名>.git main
```

`<PAT>` の部分に事前準備で作成したトークンを入力する。

---

### Step 6: 認証情報を保存（次回以降の省略）

```bash
git config --global credential.helper store
```

設定後、次回からは `git push origin main` だけでpushできる。

---

## まとめ（チェックリスト）

- [ ] PATを作成した
- [ ] noreplyメールアドレスを確認した
- [ ] GitHubにリポジトリを**空で**作成した
- [ ] `git config --global user.email` にnoreplyアドレスを設定した
- [ ] `git branch -m master main` でブランチ名を `main` に変更した
- [ ] `git remote add origin <URL>` でリモートを登録した
- [ ] `git add .` → `git commit` → `git push` でpush完了

---

## 注意事項

- PATはパスワードと同様に扱い、チャット・コード・公開リポジトリに書かない
- PATが漏洩した場合は即座に [https://github.com/settings/tokens](https://github.com/settings/tokens) で削除する
- PATに有効期限を設定し、定期的に更新する
