# 学習スケジュール（再構成版）

作成日: 2026-04-13  
前提: `/home/kenshin/work/claude_practice` の作業ログをもとに済み分を除外して再設計

---

## すでに終わっていること（スキップ対象）

| 項目 | 根拠 |
|------|------|
| Claude Code 101 | 260406でサブエージェント・エージェントチームを実践済み。基礎操作は身についている |
| Claude Code in Action（GitHub連携） | 260331でGit/GitHub push・PAT認証まで完了 |
| Claude Code in Action（MCP接続） | 260401でTickTick・FigmaのMCPサーバーをカスタムコネクタとして接続済み |
| Introduction to subagents | 260406で3並列サブエージェント・エージェントチーム・Exploreエージェントを実践済み |
| Remote Controlチュートリアル | 260405で設定・動作確認まで完了 |
| Dispatchチュートリアル | 260405でWSLとWindowsの制約含め把握済み |

---

## 残り：今週（2026-04-13〜）でやること

### 月（今日）：Introduction to agent skills

**カテゴリ**: Course（Academy）  
**URL**: https://academy.anthropic.com/  
**目安時間**: 30分

**Claudeドキュメント以外でやること**:

1. コースを見ながら実際にSkillを1つ書く
   - 場所: `~/.claude/skills/` に `.md` ファイルを作成する
   - テーマ案: 「作業ログを自動でテンプレート付きで生成するskill」

2. 書いたSkillをClaude Codeで呼び出して動作確認

**参考リンク（外部）**:
- Skills のファイル形式サンプル集（GitHub公式）:  
  https://github.com/anthropics/claude-code/tree/main/skills

---

### 火：Introduction to MCP（MCPサーバーを自作する）

**カテゴリ**: Course（Academy）  
**URL**: https://academy.anthropic.com/  
**目安時間**: 1時間

260401でMCPサーバーに「接続する」ことはした。  
今日は「作る」側に回る。

**Claudeドキュメント以外でやること**:

1. PythonでシンプルなMCPサーバーを1つ動かす  
   テーマ案: 「`~/.claude/` 内のSkillファイル一覧を返すMCPサーバー」

2. FastMCPライブラリを使うと最短で書ける

**参考リンク（外部）**:
- MCP公式仕様・チュートリアル:  
  https://modelcontextprotocol.io/
- FastMCP（Pythonで最速MCPサーバーを書けるライブラリ）:  
  https://github.com/jlowin/fastmcp
- MCP Python SDK（公式）:  
  https://github.com/modelcontextprotocol/python-sdk
- MCPサーバーのサンプル集（公式）:  
  https://github.com/modelcontextprotocol/servers

---

### 水：MCP Advanced Topics

**カテゴリ**: Course（Academy）  
**URL**: https://academy.anthropic.com/  
**目安時間**: 1.1時間

**Claudeドキュメント以外でやること**:

1. コースで学んだ内容を火曜日に作ったMCPサーバーに追加実装する  
   テーマ案: ファイルシステムの読み書き、またはHTTPリクエストを介した外部APIアクセス

2. Claude Codeのカスタムコネクタに自作サーバーを登録して動作確認

**参考リンク（外部）**:
- MCP仕様（Resources・Tools・Promptsの詳細）:  
  https://modelcontextprotocol.io/docs/concepts/resources
- MCPデバッグツール（MCP Inspector）:  
  https://github.com/modelcontextprotocol/inspector

---

### 木（自由課題）：マルチエージェントの状態を可視化する簡易MCPサーバーを自作

**カテゴリ**: なし（独自課題。Use casesの発展形）  
**目安時間**: 自由

月〜水の集大成。コースを経ずに作ろうとしていたものを、今度は土台を持った上で作る。

**具体的にやること**:

1. サブエージェントの実行状態（起動中・完了・エラー）を記録するDBを用意（SQLiteで十分）
2. それをMCPサーバーとして公開し、Claude Codeがツールとして呼べるようにする
3. 260406のダッシュボード（React + Hono）にそのデータを表示するエンドポイントを追加する

260406のdashboardをベースにするとフロント・バックエンドは再利用できる。

**参考リンク（外部）**:
- MCPの `Tool` 定義リファレンス:  
  https://modelcontextprotocol.io/docs/concepts/tools
- better-sqlite3（既に260406で使用済み）:  
  https://github.com/WiseLibs/better-sqlite3

---

## カテゴリ対応表（Anthropicの学習体系）

| カテゴリ名 | 説明 | URL |
|-----------|------|-----|
| **Academy / Course** | 体系的な動画・テキスト学習（時間見積もりあり） | https://academy.anthropic.com/ |
| **Tutorial** | ドキュメント内のステップバイステップガイド | https://docs.anthropic.com/ja/docs/claude-code/tutorials |
| **Use cases** | 活用アイデア集（体系学習ではない） | https://docs.anthropic.com/ja/docs/use-cases |
| **Academy（広義）** | CourseとTutorialを含む全学習コンテンツ | https://academy.anthropic.com/ |

今週のすべてのコース（月〜水）は **Academy / Course** カテゴリ。  
木曜の自由課題は **Use cases** の発展実践に近い位置づけ。

---

## 補足：「Introduction to agent skills」のTutorialも読む

コース（月）と並行して以下のTutorialを参照するとSkillの理解が深まる。

| Tutorial | 読むタイミング |
|----------|-------------|
| What are skills? | 月曜のコース前に読む |
| How skills compare to other Claude Code features | コース中に読む |

URL: https://docs.anthropic.com/ja/docs/claude-code/skills-overview
