"""
Claude Skills MCP Server
========================
~/.claude/skills/ にあるSkillファイルをClaude Codeがツールとして使えるようにするMCPサーバー。

MCPの基本構造：
  - Tool   : Claude Codeが呼び出せる「関数」
  - Resource: Claude Codeが読める「データ」（今回は使わない）
  - Prompt  : 再利用できるプロンプトテンプレート（今回は使わない）
"""

from pathlib import Path
from mcp.server.fastmcp import FastMCP

# MCPサーバーのインスタンスを作成（引数はサーバーの名前）
mcp = FastMCP("claude-skills-server")

SKILLS_DIR = Path.home() / ".claude" / "skills"


@mcp.tool()
def list_skills() -> str:
    """
    ~/.claude/skills/ にインストール済みのSkillの一覧を返す。
    各Skillのディレクトリ名と、その中に含まれるファイル名を表示する。
    """
    if not SKILLS_DIR.exists():
        return "skills ディレクトリが見つかりません: ~/.claude/skills/"

    skills = sorted(SKILLS_DIR.iterdir())
    if not skills:
        return "インストール済みのSkillはありません。"

    lines = ["インストール済みのSkill一覧:\n"]
    for skill_path in skills:
        if skill_path.is_dir():
            files = [f.name for f in sorted(skill_path.iterdir())]
            lines.append(f"  [{skill_path.name}]")
            for f in files:
                lines.append(f"    - {f}")
        else:
            lines.append(f"  {skill_path.name}")

    return "\n".join(lines)


@mcp.tool()
def read_skill(skill_name: str, file_name: str = "") -> str:
    """
    指定したSkillのファイル内容を読んで返す。

    Args:
        skill_name: Skillのディレクトリ名（例: "find-skills"）
        file_name:  読むファイル名（省略時はディレクトリ内の最初の.mdファイルを読む）
    """
    skill_path = SKILLS_DIR / skill_name

    if not skill_path.exists():
        return f"Skill '{skill_name}' が見つかりません。list_skills() で一覧を確認してください。"

    # ファイル名が指定されていない場合、最初の.mdファイルを使う
    if not file_name:
        md_files = sorted(skill_path.glob("*.md"))
        if not md_files:
            return f"'{skill_name}' に .md ファイルが見つかりません。"
        target = md_files[0]
    else:
        target = skill_path / file_name

    if not target.exists():
        return f"ファイル '{file_name}' が '{skill_name}' に見つかりません。"

    return target.read_text(encoding="utf-8")


if __name__ == "__main__":
    # stdio モード：Claude Code の MCP 設定から呼び出される標準的な起動方法
    mcp.run(transport="stdio")
