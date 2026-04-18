# 作業ログ: MCPサーバーを自作（2026-04-13）

## 概要

Pythonで自作のMCPサーバーを作成し、Claude Codeのカスタムツールとして登録した。
`~/.claude/skills/` のSkillファイルを一覧・参照できる `claude-skills-server` を実装した。

---

## learning modeについて

Claude Codeに「learning mode」という公式機能は存在しない。
代替として、Claudeに「説明しながら進めて」と指示することで同等の効果が得られる。

---

## 実施内容

### 1. uvのインストール（pipの代替）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**uvとは**: Pythonの新しいパッケージマネージャー。`pip` + `venv` を一体化したもの。
sudoなしでインストールでき、速度も速い。

---

### 2. MCPサーバーのプロジェクト作成

```bash
cd ~/work/claude_practice/260413/mcp_server
uv init --no-readme
uv add "mcp[cli]"
```

`mcp[cli]` には FastMCP が含まれており、別途インストール不要。

---

### 3. サーバー実装（server.py）

**MCPの3要素**:

| 要素 | 概要 | 今回の使用 |
|------|------|-----------|
| Tool | Claude Codeが呼び出す「関数」 | ✅ 使用（list_skills, read_skill） |
| Resource | Claude Codeが読む「データ」 | 未使用 |
| Prompt | 再利用可能なプロンプトテンプレート | 未使用 |

**実装したTool**:

| Tool名 | 役割 | 引数 |
|--------|------|------|
| `list_skills` | ~/.claude/skills/ の一覧表示 | なし |
| `read_skill` | 指定Skillのファイル内容を返す | skill_name（必須）, file_name（省略可） |

**FastMCPの書き方**:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("server-name")

@mcp.tool()
def my_tool(arg: str) -> str:
    """docstringがToolの説明になる"""
    return f"結果: {arg}"

if __name__ == "__main__":
    mcp.run(transport="stdio")  # stdioモードで起動
```

Pythonの型アノテーションから `inputSchema`（JSON Schema）を自動生成する。

---

### 4. MCPのstdioプロトコルを理解する

MCPはJSON-RPC 2.0をstdin/stdout経由でやり取りする。

**通信の順序**:
1. クライアント → `initialize` → サーバー（Capabilitiesを交換）
2. クライアント → `tools/list` → サーバー（ツール一覧を取得）
3. クライアント → `tools/call` → サーバー（ツールを実行）

**動作確認コマンド**:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_skills","arguments":{}}}\n' \
  | uv run python server.py
```

---

### 5. Claude Codeへの登録

**MCP設定ファイルの場所**:

| ファイル | スコープ |
|---------|---------|
| `~/.claude/mcp.json` | ユーザーレベル（全プロジェクト共通） |
| `{project}/.mcp.json` | プロジェクトレベル |

**settings.json ではない**。`mcpServers` は `settings.json` のスキーマに存在しない。

`~/.claude/mcp.json` に登録:

```json
{
  "mcpServers": {
    "claude-skills-server": {
      "command": "/home/kenshin/.local/bin/uv",
      "args": [
        "run",
        "--project", "/home/kenshin/work/claude_practice/260413/mcp_server",
        "python",
        "/home/kenshin/work/claude_practice/260413/mcp_server/server.py"
      ]
    }
  }
}
```

**反映タイミング**: Claude Codeの再起動後に有効になる。

---

## 動作確認結果

```json
{
  "result": {
    "content": [{"type": "text", "text": "インストール済みのSkill一覧:\n\n  [find-skills]\n    - SKILL.md"}],
    "isError": false
  }
}
```

`list_skills` が `find-skills` を正常に返すことを確認。

---

## 今日の教訓

| # | 教訓 |
|---|------|
| 1 | MCPサーバーはJSON-RPC 2.0 over stdioで動く。`initialize` → `tools/list` → `tools/call` の順序が必須 |
| 2 | FastMCPはPythonの型アノテントとdocstringから自動でスキーマを生成する |
| 3 | Claude CodeのMCPサーバー設定は `~/.claude/mcp.json` に書く（settings.jsonではない） |
| 4 | `mcp[cli]` をインストールすればFastMCPが使える。FastMCPを別途インストールする必要はない |
| 5 | uvはpip+venvの代替。sudoなしでインストールでき、プロジェクトごとに独立した環境を管理できる |
