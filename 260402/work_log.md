# 作業ログ: Remotionで動画作成（2026-04-02〜03）

## 概要

RemotionとClaude Codeを使い、Claude Codeの紹介動画（28秒・MP4）を作成した。
Node.jsの未インストールやWSL環境特有の依存ライブラリ不足など、複数のエラーを解決しながら完成させた。

---

## 実施内容と発生したエラー

### 1. Node.jsのインストール

Remotionの実行にはNode.jsが必要だが、未インストールだった。

```bash
sudo apt install npm
```

---

### 2. Remotionプロジェクトのセットアップ

Claude Codeが生成した雛形ファイル（`package.json` / `tsconfig.json` / `src/` 等）をもとに依存パッケージをインストール。

```bash
cd ~/work/claude_practice/260402
npm install
```

**警告（動作には影響なし）**:
```
npm WARN deprecated source-map@0.8.0-beta.0
```

---

### 3. Agent Skills のインストール

Claude CodeがRemotionプロジェクト内で正しいコードを生成できるよう、Remotionのベストプラクティス集（Agent Skills）を追加。

```bash
npx skills add remotion-dev/skills
```

**選択内容:**
- Installation scope: **Project**（プロジェクト内に配置、Gitで共有可能）
- Installation method: **Symlink**（推奨）
- `find-skills` も追加（適切なSkillsを自動提案する補助ツール）

インストール先: `260402/.agents/skills/remotion-best-practices/`

---

### 4. スタジオの起動

```bash
npm run dev
# → http://localhost:3000 でプレビュー起動
```

**問題**: TailwindCSSのスタイルが適用されず、背景が透明になっていた。

**原因**: `src/style.css`（Tailwindのディレクティブファイル）が存在せず、`index.ts` からインポートされていなかった。

**対処**:
1. `src/style.css` を作成
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```
2. `src/index.ts` にインポートを追加
```ts
import "./style.css";
```
3. `npm run dev` を再起動 → スタイル適用を確認

---

### 5. レンダリング（MP4書き出し）

スタジオのRenderボタンを押してMP4書き出しを試みたところ、以下のエラーが発生。

**エラー1: Headless Chromeの起動失敗**
```
Error: Failed to launch the browser process!
libnss3.so: cannot open shared object file: No such file or directory
```

**原因**: WSL環境にHeadless Chromeの依存ライブラリが不足していた。

**対処**: 必要なライブラリを一括インストール。

```bash
sudo apt install -y \
  libnss3 \
  libatk1.0-0t64 \
  libatk-bridge2.0-0t64 \
  libcups2t64 \
  libdrm2 \
  libxkbcommon0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libgbm1 \
  libasound2t64
```

> **注意**: Ubuntu 24.04では `libatk1.0-0` → `libatk1.0-0t64`、`libasound2` → `libasound2t64` のようにパッケージ名が変わっている。

**エラー2: `alsa-ucm-conf` の404**
```
E: Failed to fetch .../alsa-ucm-conf_...  404  Not Found
E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
```

**原因**: パッケージリストが古く、一部のURLが無効になっていた。

**対処**:
```bash
sudo apt-get update && sudo apt install -y libnss3 libasound2t64 --fix-missing
```

---

### 6. 日本語フォントの文字化け

レンダリング成功後、MP4を確認すると日本語テキストがすべて「□□□」に文字化けしていた。

**原因**: WSLのHeadless Chromeに日本語フォントがインストールされていなかった。

**対処**: Google製のCJK（日中韓）フォントをインストール。

```bash
sudo apt install -y fonts-noto-cjk
fc-cache -fv   # フォントキャッシュを更新
```

再度 `npm run dev` で再起動 → Renderで再書き出し → 文字化け解消を確認。

---

### 7. 最終レンダリング成功

```
│ Bundled code    ━━━━━━━━━━━━━━━━━━ 6075ms
│ Composition     ClaudeCodeVideo
│ Codec           h264
│ Output          out/ClaudeCodeVideo.mp4
│ Concurrency     8x
│ Rendered frames ━━━━━━━━━━━━━━━━━━ 26070ms
│ Encoded video   ━━━━━━━━━━━━━━━━━━ 674ms
│ +               out/ClaudeCodeVideo.mp4 1.9 MB
╰─ Done in 33289ms.
```

---

## エラーまとめ

| # | エラー | 原因 | 対処 |
|---|--------|------|------|
| 1 | TailwindCSSが適用されない | `style.css` 未作成・未インポート | `style.css` 作成 + `index.ts` にimport追加 |
| 2 | `libnss3.so: cannot open shared object file` | WSLにChrome依存ライブラリが不足 | `sudo apt install libnss3` 等を実行 |
| 3 | `alsa-ucm-conf` 404エラー | aptパッケージリストが古い | `apt-get update --fix-missing` |
| 4 | Ubuntu 24.04でパッケージ名エラー | パッケージ名が `t64` サフィックス付きに変更 | `libatk1.0-0t64` 等の新しい名前を使う |
| 5 | 日本語が □□□ に文字化け | Headless Chromeに日本語フォントなし | `sudo apt install fonts-noto-cjk` |

---

## 今日の教訓

| # | 教訓 |
|---|------|
| 1 | WSL上でRemotionをレンダリングする場合、Chromeの依存ライブラリと日本語フォントを事前にインストールする |
| 2 | Ubuntu 24.04はパッケージ名が変わっているものがある（`t64` サフィックス） |
| 3 | TailwindをRemotionで使う場合は `style.css` の作成と `index.ts` からのimportが必要 |
| 4 | `npx skills add remotion-dev/skills` でAgent Skillsを追加するとClaude Codeの精度が上がる |
