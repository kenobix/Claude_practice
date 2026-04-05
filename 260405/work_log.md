# 作業ログ: スマホからClaude Codeを操作（2026-04-05）

## 概要

スマホからPC上のClaude Codeを操作する2つの方法（Remote Control・Dispatch）を調査・設定した。
それぞれの特徴と制約を理解し、用途に応じた使い分けを確立した。

---

## 実施内容

### 1. Remote Controlの設定

**目的**: VSCodeのClaude Codeセッションをスマホと共有する

**手順**:
1. バージョン確認（v2.1.51以上が必要）
   ```bash
   claude --version
   # → 2.1.87 (Claude Code) ✓
   ```
2. VSCodeのClaude Codeチャット内で入力
   ```
   /remote-control
   ```
3. スマホのブラウザで `claude.ai/code` を開き、同一アカウントでログイン
4. セッション一覧からPCのセッションを選択 → 接続完了

**結果**: スマホの「コード」タブにセッションが表示され、PCとスマホ双方からメッセージを送信できることを確認。

**注意点**:
- QRコードはVSCodeチャット内には表示されない（ターミナルに表示される）
- ターミナル/VSCodeを閉じるとセッション終了
- 毎回自動で有効化したい場合は `/config` から「Enable Remote Control for all sessions」をオン

---

### 2. Dispatchの設定

**目的**: スマホからPCのClaude Desktopにタスクを非同期で委託する

**手順**:
1. Claude Desktop（Windows）の左サイドバーから「Dispatch」を開く
2. セットアップ画面で以下を設定:
   - **スリープしない**: オン（外出中もDispatchが動き続けるよう）
   - **ClaudeでChromeを使用**: 済（ブラウザ操作タスクに対応）
   - **すべてのコネクタがオン**: 済（Gmail・Notion等のコネクタが使える）
3. 「セットアップを完了」を押す
4. スマホのClaudeアプリ → Dispatchタブからタスクを送信

---

### 3. WSL環境とDispatchの制約確認

**試したこと①**: スマホのDispatchから `~/work/claude_practice/260405/` にファイル作成を指示

**結果**: 失敗

**原因**:
- DispatchはWindows上のClaude Desktopが処理を実行する
- WSLのパス（`~/work/...` = `/home/kenshin/work/...`）はWindows側から直接見えない
- `\\wsl$\Ubuntu\home\kenshin\work\` 形式のUNCパスも試みたが、CoworkはWSLファイルシステムへのアクセスが非対応

**試したこと②**: スマホのDispatchからWindowsパス（`C:\Users\kensh\work\`）にファイル作成を指示

**結果**: 成功

`C:\Users\kensh\work\test.txt` が作成され、以下の内容が書き込まれた:
```
2026-04-05 18:05:50
Dispatchテスト成功
```

**結論**: DispatchはWSLパスには非対応だが、**Windowsのファイルシステム（C:ドライブ等）へのファイル操作は正常に動作する**。

---

## 特徴と使い分けまとめ

| 機能 | Remote Control | Dispatch |
|------|---------------|---------|
| 実行環境 | WSL内のClaude Code | Windows上のClaude Desktop |
| 操作方式 | 同期（セッション共有） | 非同期（タスク委託） |
| WSLファイル操作 | 可能 | 不可 |
| コード作業・ファイル編集 | 最適 | 向かない |
| Webブラウジング | 非対応 | 最適 |
| コネクタ（Gmail等） | 非対応 | 最適 |
| Windowsのファイル操作 | 向かない | 可能（C:\Users\...）|
| PC前を離れた際の利用 | △（セッション維持が必要） | ◎（タスク投げてスマホを閉じてOK）|

### 用途別の選択指針

| やりたいこと | 使うべき機能 |
|------------|------------|
| WSLのコードをスマホから操作・確認 | Remote Control |
| 外出中にメールチェック・Web調査をClaude に依頼 | Dispatch |
| スマホとPCで同じチャットセッションを共有 | Remote Control |
| タスクを投げてスマホを閉じる（非同期） | Dispatch |

---

## 今日の教訓

| # | 教訓 |
|---|------|
| 1 | Remote ControlはWSL内のClaude Codeセッションを共有するため、WSLのファイル操作が可能 |
| 2 | DispatchはWindows上のClaude Desktopが実行主体のため、WSLファイルシステムにはアクセスできない |
| 3 | WSL開発環境のスマホ操作にはRemote Control、それ以外（Web・コネクタ等）にはDispatchと使い分ける |
| 4 | Remote Controlは `/config` から全セッション自動有効化ができる |
| 5 | Dispatchのスリープ防止設定は、外出中にタスクを処理させるなら必ずオンにする |
