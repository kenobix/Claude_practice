"""Game content: chapters, lessons, and quizzes."""
from typing import List, Dict, Any

CHAPTERS = [
    {
        "id": "prologue",
        "title": "プロローグ: Claude Codeの世界へ",
        "emoji": "🌟",
        "xp_reward": 50,
        "description": "Claude Codeとは何か？旅の始まりに世界を知ろう。",
        "lessons": [
            {
                "title": "Claude Code とは？",
                "content": """[bold cyan]Claude Code[/bold cyan] は Anthropic が開発した [bold]AIを使ったCLI（コマンドラインインターフェース）ツール[/bold]です。

ターミナル上で動作し、AIの力を借りてソフトウェアエンジニアリングのタスクを
こなすことができます。

[bold yellow]主な特徴:[/bold yellow]
  • コードの読み書き・編集・バグ修正
  • ターミナルコマンドの実行
  • ファイルシステムの操作
  • GitやGitHubとの連携
  • テストの実行とデバッグ

[bold yellow]使いどころ:[/bold yellow]
  • 複雑なリファクタリング
  • コードベースの調査・理解
  • バグの特定と修正
  • ドキュメント生成

Claude Codeはあなたのターミナルに住む[bold magenta]AIペアプログラマー[/bold magenta]です！""",
            },
            {
                "title": "インストールと起動",
                "content": """[bold yellow]インストール方法:[/bold yellow]

  [bold green]$ npm install -g @anthropic-ai/claude-code[/bold green]

Node.js が必要です（v18以上推奨）。

[bold yellow]APIキーの設定:[/bold yellow]

  [bold green]$ export ANTHROPIC_API_KEY=sk-ant-...[/bold green]

または [cyan].bashrc[/cyan] / [cyan].zshrc[/cyan] に記載しておくと便利です。

[bold yellow]起動方法:[/bold yellow]

  [bold green]$ claude[/bold green]           # インタラクティブモード
  [bold green]$ claude "質問や指示"[/bold green]  # 直接指示モード
  [bold green]$ claude --help[/bold green]    # ヘルプを表示

起動すると [bold cyan]>[/bold cyan] プロンプトが表示され、
日本語でも自然に話しかけられます！""",
            },
            {
                "title": "基本的な使い方",
                "content": """[bold yellow]インタラクティブモードでの対話:[/bold yellow]

Claude Codeを起動したら、自然言語で指示を出すだけです。

[dim]例:[/dim]
  [cyan]> このコードのバグを修正して[/cyan]
  [cyan]> README.mdを日本語で書いて[/cyan]
  [cyan]> テストを実行して結果を教えて[/cyan]

[bold yellow]終了方法:[/bold yellow]
  • [bold]Ctrl+C[/bold] を押す
  • [bold]/exit[/bold] と入力する

[bold yellow]入力のヒント:[/bold yellow]
  • [bold]Enter[/bold] で送信
  • [bold]Shift+Enter[/bold] で改行（複数行入力）
  • [bold]↑↓[/bold] キーで入力履歴を辿れる
  • [bold]Ctrl+L[/bold] で画面をクリア""",
            },
        ],
        "quiz": [
            {
                "id": "q_prologue_1",
                "question": "Claude Code はどのように動作するツールですか？",
                "choices": [
                    "ブラウザ上で動作するWebアプリ",
                    "ターミナル（CLI）で動作するAIツール",
                    "デスクトップアプリ（GUI）",
                    "スマートフォン向けアプリ",
                ],
                "answer": 1,
                "explanation": "Claude Code はターミナルで動作するCLIツールです。ブラウザ版（claude.ai/code）やIDE拡張もありますが、基本はCLIです。",
                "xp": 20,
            },
            {
                "id": "q_prologue_2",
                "question": "Claude Code のインストールコマンドはどれですか？",
                "choices": [
                    "pip install claude-code",
                    "brew install claude",
                    "npm install -g @anthropic-ai/claude-code",
                    "apt install claude-code",
                ],
                "answer": 2,
                "explanation": "npm install -g @anthropic-ai/claude-code が正解です。Node.jsのパッケージマネージャーnpmでグローバルインストールします。",
                "xp": 20,
            },
            {
                "id": "q_prologue_3",
                "question": "Claude Code で複数行の入力をするには？",
                "choices": [
                    "Alt+Enter",
                    "Ctrl+Enter",
                    "Shift+Enter",
                    "Tab+Enter",
                ],
                "answer": 2,
                "explanation": "Shift+Enter で改行して複数行の指示を入力できます。Enterだけだと送信されてしまいます。",
                "xp": 20,
            },
        ],
    },
    {
        "id": "chapter1",
        "title": "第1章: スラッシュコマンドの秘密",
        "emoji": "⚔️",
        "xp_reward": 80,
        "description": "/で始まるコマンドを使いこなして効率アップ！",
        "lessons": [
            {
                "title": "スラッシュコマンドとは",
                "content": """[bold cyan]スラッシュコマンド[/bold cyan] は [bold]/[/bold] で始まる特殊なコマンドで、
Claude Code の動作を制御したり、便利な機能を呼び出したりできます。

[bold yellow]基本コマンド一覧:[/bold yellow]

  [bold green]/help[/bold green]       ヘルプを表示
  [bold green]/clear[/bold green]      会話履歴をクリア（コンテキストをリセット）
  [bold green]/compact[/bold green]    会話を要約してコンテキストを節約
  [bold green]/exit[/bold green]       Claude Codeを終了
  [bold green]/status[/bold green]     現在の状態を表示
  [bold green]/cost[/bold green]       現在のセッションのAPIコスト確認

[dim]ヒント: /help を実行すると全コマンドリストが見られます[/dim]""",
            },
            {
                "title": "会話管理コマンド",
                "content": """[bold yellow]/clear と /compact の違い:[/bold yellow]

[bold cyan]/clear[/bold cyan]
  • 会話履歴を[bold red]完全に削除[/bold red]
  • コンテキストをゼロからリセット
  • 新しいタスクを始めるときに便利
  • ⚠️ 元に戻せない

[bold cyan]/compact[/bold cyan]
  • 会話を[bold green]要約・圧縮[/bold green]して保持
  • 重要な情報は残しつつトークン節約
  • 長い作業セッションで役立つ
  • コンテキストウィンドウが埋まりそうなときに使用

[bold yellow]コンテキストとは？[/bold yellow]
Claude が一度に処理できる「記憶」の量です。
長い会話はトークンを消費するので、/compact で節約できます。""",
            },
            {
                "title": "コード専用コマンド",
                "content": """[bold yellow]開発に役立つスラッシュコマンド:[/bold yellow]

[bold cyan]/commit[/bold cyan]
  Gitコミットを自動作成します。
  変更内容を分析して適切なコミットメッセージを提案・実行。

[bold cyan]/review-pr[/bold cyan]
  プルリクエストをレビューします。
  GitHub PR URLやPR番号を指定して使用。

[bold cyan]/bug[/bold cyan]
  バグ報告書を作成します。
  現在の状態をキャプチャして詳細なバグレポートを生成。

[bold cyan]/init[/bold cyan]
  プロジェクトに [bold]CLAUDE.md[/bold] ファイルを作成します。
  Claude が覚えておくべきプロジェクト情報を記録。

[dim]例: /commit を使うと git add, git diff, git log を自動で分析して
適切なコミットメッセージを提案してくれます！[/dim]""",
            },
            {
                "title": "カスタムスラッシュコマンド",
                "content": """[bold yellow]自分だけのコマンドを作れる！[/bold yellow]

[bold cyan]~/.claude/commands/[/bold cyan] ディレクトリに
Markdownファイルを置くと [bold]カスタムスラッシュコマンド[/bold] になります。

[bold yellow]作り方:[/bold yellow]

  [dim]ファイル: ~/.claude/commands/deploy.md[/dim]
  [green]---
  description: 本番環境にデプロイする
  ---

  以下の手順でデプロイしてください：
  1. テストを全て実行
  2. ビルドを作成
  3. デプロイスクリプトを実行[/green]

このファイルを作ると [bold]/deploy[/bold] コマンドが使えるようになります！

[bold yellow]プロジェクト専用コマンド:[/bold yellow]
[cyan].claude/commands/[/cyan] に置くと、そのプロジェクト内だけで使えるコマンドになります。""",
            },
        ],
        "quiz": [
            {
                "id": "q_ch1_1",
                "question": "会話履歴を完全に削除してリセットするコマンドは？",
                "choices": ["/compact", "/clear", "/reset", "/delete"],
                "answer": 1,
                "explanation": "/clear で会話履歴を完全削除できます。/compact は要約して保持します。",
                "xp": 25,
            },
            {
                "id": "q_ch1_2",
                "question": "/compact コマンドの主な目的は？",
                "choices": [
                    "コードを圧縮してファイルサイズを小さくする",
                    "会話を要約してトークン（コンテキスト）を節約する",
                    "画像を圧縮する",
                    "コミットメッセージを短くする",
                ],
                "answer": 1,
                "explanation": "/compact は長い会話を要約し、コンテキストウィンドウのトークン消費を抑えます。",
                "xp": 25,
            },
            {
                "id": "q_ch1_3",
                "question": "カスタムスラッシュコマンドのファイルをどこに置く？",
                "choices": [
                    "/usr/local/bin/",
                    "~/.bashrc の中に記述",
                    "~/.claude/commands/ ディレクトリ",
                    "~/Documents/claude/",
                ],
                "answer": 2,
                "explanation": "~/.claude/commands/ にMarkdownファイルを置くとグローバルなカスタムコマンドになります。プロジェクト内は .claude/commands/ です。",
                "xp": 25,
            },
            {
                "id": "q_ch1_4",
                "question": "/commit コマンドは何をしてくれる？",
                "choices": [
                    "ファイルをコミット形式で圧縮する",
                    "変更内容を分析して適切なGitコミットを自動作成する",
                    "コードをコミットメントする（誓約する）",
                    "GitHubにPRを作成する",
                ],
                "answer": 1,
                "explanation": "/commit はgit diff/statusを分析して適切なコミットメッセージを提案・実行します。",
                "xp": 25,
            },
        ],
    },
    {
        "id": "chapter2",
        "title": "第2章: ツールという名の武器",
        "emoji": "🛠️",
        "xp_reward": 100,
        "description": "Claude Codeが使うツールを理解して、仕組みを学ぼう。",
        "lessons": [
            {
                "title": "ツールシステムとは",
                "content": """[bold cyan]ツール（Tools）[/bold cyan] は Claude Code が実際に行動するための能力です。

Claude はあなたの指示を理解し、適切なツールを選んで実行します。

[bold yellow]主要なツール:[/bold yellow]

  [bold green]Read[/bold green]      ファイルを読む
  [bold green]Write[/bold green]     ファイルを作成・上書き
  [bold green]Edit[/bold green]      ファイルの一部を編集
  [bold green]Bash[/bold green]      シェルコマンドを実行
  [bold green]Glob[/bold green]      ファイルをパターン検索
  [bold green]Grep[/bold green]      ファイル内容を検索
  [bold green]WebSearch[/bold green] Webを検索（モデルによる）
  [bold green]Agent[/bold green]     サブエージェントを起動

[dim]ツールの使用はユーザーの承認が必要な場合があります。
実行前に「[y/n?]」と聞いてくることもあります。[/dim]""",
            },
            {
                "title": "ファイル操作ツール",
                "content": """[bold yellow]Read ツール[/bold yellow]
ファイルの内容を読み込みます。
  • 行番号付きで表示
  • PDFや画像も読める
  • Jupyterノートブックも対応

[bold yellow]Edit ツール[/bold yellow]
ファイルの特定の部分を置換します。
  • 差分（diff）のみ送信するので効率的
  • old_string → new_string の形式
  • ユニークな文字列にマッチして置換

[bold yellow]Write ツール[/bold yellow]
ファイルを新規作成または完全上書きします。
  • 既存ファイルへは先にReadが必要
  • 大きな変更向け

[dim]⚠️ ヒント: Editの方がWriteより効率的（差分のみ転送）。
既存ファイルの修正にはEditを優先して使います。[/dim]""",
            },
            {
                "title": "検索・実行ツール",
                "content": """[bold yellow]Glob ツール[/bold yellow]
ファイルをパターンで検索します。
  [dim]例: **/*.py → 全Pythonファイル[/dim]
  [dim]例: src/**/*.ts → srcフォルダ以下の全TSファイル[/dim]

[bold yellow]Grep ツール[/bold yellow]
ファイルの中身をリポジトリ全体で検索します。
  • 正規表現対応
  • ファイルタイプ指定可能
  • マッチした行のコンテキスト表示

[bold yellow]Bash ツール[/bold yellow]
シェルコマンドを実行します。
  • npm test / pytest など
  • git コマンド
  • ビルドスクリプト
  • 任意のCLIツール

[dim]⚠️ 注意: Bash は強力ですが、危険なコマンドは
実行前に確認を求めます。[/dim]""",
            },
            {
                "title": "パーミッションモード",
                "content": """[bold yellow]Claude Code の安全機能: パーミッション[/bold yellow]

ファイルの削除や危険なコマンドは実行前に確認を求めます。

[bold cyan]承認モード:[/bold cyan]
  [bold green]Auto[/bold green]    安全な操作は自動承認
  [bold]Manual[/bold]  全ての操作に確認が必要
  [bold red]YOLO[/bold red]    全て自動承認（⚠️危険）

[bold cyan]よく出る確認例:[/bold cyan]
  • ファイルの削除
  • git push
  • データベース操作
  • 外部APIへのリクエスト

[bold yellow]--dangerously-skip-permissions フラグ:[/bold yellow]
CI/CD環境などで全自動実行したい場合に使用。
[bold red]通常の開発では使用しない[/bold red]こと！

[dim]Enterで承認、nで拒否できます[/dim]""",
            },
        ],
        "quiz": [
            {
                "id": "q_ch2_1",
                "question": "ファイルを検索するパターンマッチングツールは？",
                "choices": ["Find", "Grep", "Glob", "Search"],
                "answer": 2,
                "explanation": "Glob ツールがファイルパスのパターンマッチング（**/*.js など）を担当します。Grep はファイルの内容を検索します。",
                "xp": 30,
            },
            {
                "id": "q_ch2_2",
                "question": "Edit ツールが Write ツールより効率的な理由は？",
                "choices": [
                    "Editの方が処理が速い",
                    "Editは差分のみ送信するのでトークンを節約できる",
                    "Editはファイルを圧縮してくれる",
                    "Editは複数ファイルを同時編集できる",
                ],
                "answer": 1,
                "explanation": "EditはOld文字列→New文字列の差分のみ送信するため、ファイル全体を送るWriteよりトークン効率が良いです。",
                "xp": 30,
            },
            {
                "id": "q_ch2_3",
                "question": "シェルコマンドを実行するツールは？",
                "choices": ["Shell", "Terminal", "Bash", "Execute"],
                "answer": 2,
                "explanation": "Bash ツールがシェルコマンドの実行を担当します。テスト実行、gitコマンド、ビルドなど何でもできます。",
                "xp": 30,
            },
        ],
    },
    {
        "id": "chapter3",
        "title": "第3章: CLAUDE.mdという魔法書",
        "emoji": "📖",
        "xp_reward": 120,
        "description": "プロジェクトの記憶をCLAUDE.mdに刻もう。",
        "lessons": [
            {
                "title": "CLAUDE.md とは",
                "content": """[bold cyan]CLAUDE.md[/bold cyan] は Claude Code がプロジェクトについて
覚えておくべき情報を記録するための特別なファイルです。

[bold yellow]どこに置く？[/bold yellow]
  • [bold]プロジェクトルート[/bold] → そのプロジェクトに関する情報
  • [bold]~/ (ホーム)[/bold] → 全プロジェクト共通の情報
  • [bold]サブディレクトリ[/bold] → その配下のコードに関する情報

[bold yellow]何を書く？[/bold yellow]
  • プロジェクトの概要と目的
  • よく使うコマンド（ビルド、テスト方法）
  • コーディング規約
  • 注意点や既知の問題
  • アーキテクチャの説明

[dim]CLAUDE.mdは会話のたびに自動で読み込まれるので、
毎回同じことを説明しなくて済みます！[/dim]""",
            },
            {
                "title": "CLAUDE.md の書き方",
                "content": """[bold yellow]効果的な CLAUDE.md の例:[/bold yellow]

[green]# My Project

## 概要
ECサイトのバックエンドAPI（FastAPI + PostgreSQL）

## 開発環境セットアップ
```bash
pip install -r requirements.txt
cp .env.example .env
docker-compose up -d  # DBを起動
uvicorn main:app --reload
```

## テスト
```bash
pytest tests/ -v
pytest tests/ --cov=app  # カバレッジ付き
```

## コーディング規約
- 型アノテーション必須
- 関数には docstring を書く
- コミットは conventional commits 形式

## 注意事項
- DB マイグレーションは alembic を使う
- 本番DBに直接接続しないこと
```[/green]

[dim]/init コマンドを使うと自動生成してくれます[/dim]""",
            },
            {
                "title": "設定ファイル settings.json",
                "content": """[bold cyan]settings.json[/bold cyan] で Claude Code の動作をカスタマイズできます。

[bold yellow]場所:[/bold yellow]
  • [bold]~/.claude/settings.json[/bold] → グローバル設定
  • [bold].claude/settings.json[/bold] → プロジェクト設定

[bold yellow]設定できること:[/bold yellow]
  [green]{
    "model": "claude-opus-4-6",
    "permissions": {
      "allow": ["Bash(git *)", "Read"],
      "deny": ["Bash(rm -rf *)"]
    },
    "hooks": {
      "PreToolUse": [{...}],
      "PostToolUse": [{...}]
    }
  }[/green]

[bold yellow]よく使う設定:[/bold yellow]
  • [bold]model[/bold] → 使用するClaudeモデル
  • [bold]permissions[/bold] → ツールの許可・拒否ルール
  • [bold]hooks[/bold] → ツール実行前後の自動処理""",
            },
            {
                "title": "Hooks（フック）システム",
                "content": """[bold cyan]Hooks[/bold cyan] はツールの実行前後に自動でシェルコマンドを走らせる機能です。

[bold yellow]フックの種類:[/bold yellow]
  [bold green]PreToolUse[/bold green]   ツール実行前に走る
  [bold green]PostToolUse[/bold green]  ツール実行後に走る
  [bold green]Stop[/bold green]         Claude が応答を終了したとき
  [bold green]SubagentStop[/bold green] サブエージェント終了時

[bold yellow]設定例:[/bold yellow]
  [green]{
    "hooks": {
      "PostToolUse": [{
        "matcher": "Edit|Write",
        "hooks": [{
          "type": "command",
          "command": "prettier --write $CLAUDE_TOOL_INPUT_FILE_PATH"
        }]
      }]
    }
  }[/green]

[dim]→ ファイルを編集するたびに自動でフォーマットが走る！[/dim]

[bold yellow]使いどころ:[/bold yellow]
  • Linter/Formatter の自動実行
  • テストの自動実行
  • 通知（Slack, デスクトップ通知）
  • ログ記録""",
            },
        ],
        "quiz": [
            {
                "id": "q_ch3_1",
                "question": "CLAUDE.md をホームディレクトリに置くと？",
                "choices": [
                    "そのプロジェクトだけで有効",
                    "全プロジェクト共通で有効",
                    "エラーになる",
                    "読み込まれない",
                ],
                "answer": 1,
                "explanation": "~/CLAUDE.md（ホームディレクトリ）に置くと全プロジェクト共通の設定になります。プロジェクトルートに置くとそのプロジェクト専用です。",
                "xp": 35,
            },
            {
                "id": "q_ch3_2",
                "question": "CLAUDE.md を自動生成してくれるコマンドは？",
                "choices": ["/create", "/setup", "/init", "/generate"],
                "answer": 2,
                "explanation": "/init コマンドがプロジェクトを分析してCLAUDE.mdを自動生成します。",
                "xp": 35,
            },
            {
                "id": "q_ch3_3",
                "question": "ファイル編集のたびに自動でフォーマッターを走らせるには？",
                "choices": [
                    "毎回手動でコマンドを実行する",
                    "PostToolUse フックに設定する",
                    "CLAUDE.mdに書く",
                    "/format コマンドを使う",
                ],
                "answer": 1,
                "explanation": "PostToolUse フックをsettings.jsonに設定すると、ツール実行後（例：Edit後）に自動でコマンドが走ります。",
                "xp": 35,
            },
        ],
    },
    {
        "id": "chapter4",
        "title": "第4章: MCPという拡張術",
        "emoji": "🔌",
        "xp_reward": 150,
        "description": "MCPサーバーでClaude Codeの能力を無限に拡張せよ！",
        "lessons": [
            {
                "title": "MCPとは何か",
                "content": """[bold cyan]MCP（Model Context Protocol）[/bold cyan] は
Claude が外部ツールやサービスと通信するための[bold]オープンプロトコル[/bold]です。

[bold yellow]MCPの役割:[/bold yellow]
  Claude Code ←→ MCPサーバー ←→ 外部サービス

[bold yellow]MCPで何ができる？[/bold yellow]
  • データベースに直接接続してSQLを実行
  • Slack/GitHubにメッセージ送信
  • Google Driveのファイルを操作
  • Docker コンテナを管理
  • Figmaデザインを読み込む
  • カスタムAPIを呼び出す

[bold yellow]公式・人気のMCPサーバー:[/bold yellow]
  [green]github[/green]    GitHubリポジトリ操作
  [green]postgres[/green]  PostgreSQL操作
  [green]sqlite[/green]    SQLiteデータベース
  [green]slack[/green]     Slackメッセージ
  [green]filesystem[/green] 強化されたファイル操作

[dim]MCPはオープン標準なので、誰でもサーバーを作れます！[/dim]""",
            },
            {
                "title": "MCPサーバーの設定",
                "content": """[bold yellow]settings.json への追加:[/bold yellow]

  [green]{
    "mcpServers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "your_token"
        }
      },
      "postgres": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres",
                 "postgresql://localhost/mydb"]
      }
    }
  }[/green]

[bold yellow]MCPの追加コマンド:[/bold yellow]

  [bold green]$ claude mcp add <name> <command>[/bold green]
  [bold green]$ claude mcp list[/bold green]
  [bold green]$ claude mcp remove <name>[/bold green]

[dim]追加したMCPサーバーは再起動後から使えるようになります[/dim]""",
            },
            {
                "title": "Claude Code を API から使う",
                "content": """[bold yellow]Claude Code SDK[/bold yellow]

Claude Code をプログラムから制御できる公式SDKもあります。

[bold cyan]TypeScript/JavaScript:[/bold cyan]
  [green]import ClaudeCode from '@anthropic-ai/claude-code';

  const sdk = new ClaudeCode();

  for await (const message of sdk.run("テストを実行して")) {
    console.log(message);
  }[/green]

[bold cyan]Python:[/bold cyan]
  [green]import claude_code_sdk

  async for event in claude_code_sdk.run("バグを修正して"):
      print(event)[/green]

[bold yellow]使いどころ:[/bold yellow]
  • CI/CDパイプラインへの組み込み
  • 自動コードレビューシステム
  • カスタムAI開発ツールの構築
  • バッチ処理での大量コード生成""",
            },
        ],
        "quiz": [
            {
                "id": "q_ch4_1",
                "question": "MCPはどういう意味ですか？",
                "choices": [
                    "Memory Control Protocol",
                    "Model Context Protocol",
                    "Multi-Chat Platform",
                    "Managed Code Process",
                ],
                "answer": 1,
                "explanation": "MCPはModel Context Protocolの略で、AIモデルと外部ツールを繋ぐオープンプロトコルです。",
                "xp": 40,
            },
            {
                "id": "q_ch4_2",
                "question": "MCPサーバーを追加するCLIコマンドは？",
                "choices": [
                    "claude plugin add",
                    "claude mcp add",
                    "claude extend add",
                    "claude server add",
                ],
                "answer": 1,
                "explanation": "claude mcp add <name> <command> でMCPサーバーを追加できます。claude mcp list で一覧、claude mcp remove で削除。",
                "xp": 40,
            },
            {
                "id": "q_ch4_3",
                "question": "Claude Code SDKの主な用途は？",
                "choices": [
                    "UIデザインのカスタマイズ",
                    "Claude Codeをプログラムから制御してAIツールを構築する",
                    "APIキーの管理",
                    "MCPサーバーの作成",
                ],
                "answer": 1,
                "explanation": "Claude Code SDKはClaudeをプログラムから制御する手段で、CI/CD、自動レビュー、カスタムツール構築に使えます。",
                "xp": 40,
            },
        ],
    },
    {
        "id": "chapter5",
        "title": "第5章: 達人の技・実践テクニック",
        "emoji": "🏆",
        "xp_reward": 200,
        "description": "上級テクニックをマスターして真のClaude Code使いへ！",
        "lessons": [
            {
                "title": "効果的な指示の出し方",
                "content": """[bold yellow]Claude Codeを上手く使うコツ:[/bold yellow]

[bold cyan]1. 具体的に指示する[/bold cyan]
  [red]✗ 悪い:[/red] 「バグを直して」
  [green]✓ 良い:[/green] 「tests/test_auth.py の line 42 のKeyErrorを修正して。
         Noneチェックを追加すれば直ると思う」

[bold cyan]2. コンテキストを提供する[/bold cyan]
  [green]「このプロジェクトはFastAPI + SQLAlchemyを使っています。
  ユーザー認証のエンドポイントを追加してください。
  既存のパターンに合わせてください」[/green]

[bold cyan]3. 段階的に進める[/bold cyan]
  大きなタスクは小さく分割。
  まず「調査して」→ 確認 → 「修正して」

[bold cyan]4. フィードバックを伝える[/bold cyan]
  「いい感じ、でもエラーハンドリングを追加して」
  「それは違う、別のアプローチで」""",
            },
            {
                "title": "マルチエージェントとサブエージェント",
                "content": """[bold cyan]Agent ツール[/bold cyan] で Claude が Claude を呼び出せます！

[bold yellow]仕組み:[/bold yellow]
  メインのClaude → サブエージェント（別のClaude）を起動
  複数のタスクを並列処理可能！

[bold yellow]使いどころ:[/bold yellow]
  • 複数ファイルの同時調査
  • 独立したタスクの並列実行
  • メインのコンテキストを守りたい調査

[bold yellow]エージェントの種類（Claude Code内蔵）:[/bold yellow]
  [green]Explore[/green]   コードベース探索専門
  [green]Plan[/green]      設計・実装計画専門
  [green]general-purpose[/green] 汎用タスク

[bold yellow]注意点:[/bold yellow]
  • サブエージェントはコストがかかる
  • 必要なときだけ使う
  • 複雑な依存関係がある場合は直列処理が安全""",
            },
            {
                "title": "バッチモードとCI/CD活用",
                "content": """[bold yellow]非インタラクティブモード（バッチ処理）:[/bold yellow]

  [bold green]$ claude --print "テストを実行して結果を教えて"[/bold green]
  [bold green]$ echo "コードレビューして" | claude --print[/bold green]

[bold yellow]CI/CDへの組み込み例（GitHub Actions）:[/bold yellow]

  [green]- name: AI Code Review
    run: |
      claude --print "PR差分をレビューして問題点を報告して" \\
        --dangerously-skip-permissions[/green]

[bold yellow]便利なフラグ:[/bold yellow]
  [bold]--print[/bold] (-p)    結果をstdoutに出力（非インタラクティブ）
  [bold]--model[/bold]         使用するモデルを指定
  [bold]--max-turns[/bold]     最大ターン数を制限
  [bold]--output-format[/bold] json/text/stream-json

[dim]⚠️ CI/CD環境では --dangerously-skip-permissions が
必要なことが多いですが、セキュリティに注意！[/dim]""",
            },
            {
                "title": "コスト管理とモデル選択",
                "content": """[bold yellow]Claude のモデル比較:[/bold yellow]

  [bold cyan]claude-opus-4-6[/bold cyan]
    最高性能・最高コスト
    複雑なアーキテクチャ設計、難しいバグ修正に

  [bold cyan]claude-sonnet-4-6[/bold cyan]
    バランス型（デフォルト）
    日常的なコーディングタスクに最適

  [bold cyan]claude-haiku-4-5[/bold cyan]
    高速・低コスト
    シンプルなタスク、大量バッチ処理に

[bold yellow]コスト削減テクニック:[/bold yellow]
  • [bold]/compact[/bold] でコンテキストを節約
  • [bold]/clear[/bold] で新タスク開始時にリセット
  • 単純なタスクはHaikuモデルを指定
  • [bold]/cost[/bold] でセッションコストを確認

[dim]モデル指定: claude --model claude-haiku-4-5-20251001[/dim]""",
            },
        ],
        "quiz": [
            {
                "id": "q_ch5_1",
                "question": "Claude Codeを非インタラクティブ（バッチ）で実行するフラグは？",
                "choices": ["--batch", "--print", "--silent", "--auto"],
                "answer": 1,
                "explanation": "--print (-p) フラグで非インタラクティブモードになり、結果をstdoutに出力します。CI/CDで使われます。",
                "xp": 50,
            },
            {
                "id": "q_ch5_2",
                "question": "最もコスト効率が良い（低コスト）のモデルは？",
                "choices": [
                    "claude-opus-4-6",
                    "claude-sonnet-4-6",
                    "claude-haiku-4-5",
                    "claude-mini",
                ],
                "answer": 2,
                "explanation": "claude-haiku-4-5が最も軽量・高速・低コストです。シンプルなタスクや大量バッチ処理に向いています。",
                "xp": 50,
            },
            {
                "id": "q_ch5_3",
                "question": "現在のセッションのAPIコストを確認するコマンドは？",
                "choices": ["/price", "/billing", "/cost", "/token"],
                "answer": 2,
                "explanation": "/cost コマンドで現在のセッションで消費したAPIトークンとコストを確認できます。",
                "xp": 50,
            },
            {
                "id": "q_ch5_4",
                "question": "大きなタスクへの効果的なアプローチは？",
                "choices": [
                    "全部一度に指示する",
                    "段階的に分割して進める（調査→確認→実装）",
                    "毎回/clearしてから指示する",
                    "Opusモデルだけを使う",
                ],
                "answer": 1,
                "explanation": "大きなタスクは「まず調査して」→確認→「次に実装して」と段階的に進める方が、精度が上がりミスが減ります。",
                "xp": 50,
            },
        ],
    },
]

ACHIEVEMENTS = {
    "first_quiz": {
        "name": "初陣",
        "description": "最初のクイズに答えた",
        "emoji": "⚔️",
        "xp": 10,
    },
    "perfect_chapter": {
        "name": "完璧主義者",
        "description": "1章のクイズを全問正解",
        "emoji": "💯",
        "xp": 50,
    },
    "streak_5": {
        "name": "連続正解マスター",
        "description": "5問連続正解",
        "emoji": "🔥",
        "xp": 30,
    },
    "all_chapters": {
        "name": "Claude Code 卒業生",
        "description": "全章をクリア",
        "emoji": "🎓",
        "xp": 200,
    },
    "speed_demon": {
        "name": "スピードスター",
        "description": "クイズに即答（3秒以内）",
        "emoji": "⚡",
        "xp": 15,
    },
}
