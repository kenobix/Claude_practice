# 個人開発管理ダッシュボード

タスク管理・集中タイマーを一元化したローカル動作の生産性ツール。  
Claude Code のサブエージェントとエージェントチームを使って開発した試作品（Phase 1 MVP）。

---

## システム構成図

```
┌─────────────────────────────────────────────────────────┐
│                   ブラウザ / スマートフォン              │
└────────────────────┬────────────────────────────────────┘
                     │ HTTP  :5173
                     ▼
┌─────────────────────────────────────────────────────────┐
│              フロントエンド（React + Vite）               │
│                                                         │
│  ページ構成:                                             │
│    /            ダッシュボード（タイマー・統計・今日のTasks）│
│    /tasks       タスク一覧（フィルタ・新規作成）           │
│    /tasks/:id   タスク詳細（編集・メモ・タイマー）         │
│                                                         │
│  主要ライブラリ:                                          │
│    React 18 / React Router v6 / Tailwind CSS v3        │
│    axios（APIクライアント）                              │
└────────────────────┬────────────────────────────────────┘
                     │ REST API  /api/*  :3001
                     ▼
┌─────────────────────────────────────────────────────────┐
│              バックエンド（Hono / Node.js）               │
│                                                         │
│  エンドポイント:                                          │
│    GET    /health                 ヘルスチェック          │
│    GET    /api/tasks              タスク一覧（フィルタ可）  │
│    POST   /api/tasks              タスク作成              │
│    PUT    /api/tasks/:id          タスク更新              │
│    DELETE /api/tasks/:id          タスク削除              │
│    POST   /api/tasks/:id/timer/start  タイマー開始        │
│    POST   /api/tasks/:id/timer/stop   タイマー停止        │
│    GET    /api/tasks/:id/timelogs タイムログ一覧          │
│    GET    /api/dashboard          ダッシュボードデータ     │
│                                                         │
│  制約: タイマーはシステム全体で同時に1つのみ起動可能         │
└────────────────────┬────────────────────────────────────┘
                     │ Drizzle ORM（better-sqlite3）
                     ▼
┌─────────────────────────────────────────────────────────┐
│              データベース（SQLite）                        │
│              ./backend/data/dashboard.db               │
│                                                         │
│  テーブル:                                               │
│    tasks      id / title / label / description          │
│               status / priority / due_date              │
│               created_at / completed_at                 │
│                                                         │
│    time_logs  id / task_id / started_at / stopped_at   │
│               duration_seconds / memo                   │
│                                                         │
│  設定: WAL モード・外部キー制約有効                         │
└─────────────────────────────────────────────────────────┘
```

### データフロー例（タイマー起動）

```
① ユーザーが「▶ Start Timer」をクリック
      ↓
② React → POST /api/tasks/:id/timer/start
      ↓
③ Hono: 既存の実行中タイマーを確認（time_logs.stopped_at IS NULL）
         → 他のタスクのタイマーが動いていれば 400 を返す
         → なければ time_logs に新規レコードを INSERT
         → タスクの status が todo なら in_progress に自動更新
      ↓
④ フロント: GET /api/dashboard で active_timer を取得
      ↓
⑤ useTimer フック: setInterval で 1秒ごとに elapsed++ し画面を更新
```

---

## Claude Code エージェント構成図

このシステムは3段階のエージェント戦略で開発した。

```
┌─────────────────────────────────────────────────────────┐
│  Phase A: サブエージェント（並列実行）                      │
│                                                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │  Agent 1     │ │  Agent 2     │ │  Agent 3     │   │
│  │  技術調査     │ │  機能要件    │ │  非機能要件   │   │
│  │  （Web検索）  │ │  （分析）    │ │  （分析）    │   │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘   │
│         └────────────────┴────────────────┘            │
│                          ↓ 結果を集約                   │
│                   requirements.md 作成                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Phase B: エージェントチーム（協調開発）                   │
│                                                         │
│  ┌──────────────────────┐                              │
│  │   オーケストレーター   │  ← 全体方針・タスク分配        │
│  └──────────┬───────────┘                              │
│    ┌─────────┴──────────────────────────┐              │
│    ▼                    ▼               ▼              │
│  ┌──────────┐   ┌──────────────┐  ┌──────────────┐   │
│  │ Backend  │   │  Frontend    │  │Infrastructure│   │
│  │  Agent   │   │   Agent      │  │   Agent      │   │
│  │          │   │              │  │              │   │
│  │ Hono API │   │ React/Vite   │  │ Docker       │   │
│  │ Drizzle  │◄──┤ Tailwind CSS │  │ Compose      │   │
│  │ SQLite   │   │ React Router │  │ Dockerfile   │   │
│  └──────────┘   └──────────────┘  └──────────────┘   │
│                                                         │
│  ※ エージェントチームは実験的機能（CLAUDE_CODE_          │
│     EXPERIMENTAL_AGENT_TEAMS=1 が必要）                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Phase C: サブエージェント（デバッグ）                     │
│                                                         │
│  ┌───────────────────────────────────────────┐         │
│  │  Debug Agent（Explore モード）              │         │
│  │                                           │         │
│  │  調査: drizzle-orm 内部型エラーの原因特定    │         │
│  │  調査: body.memo の Union 型エラーの特定    │         │
│  │  提案: skipLibCheck / 型アサーションで修正   │         │
│  └───────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────┘
```

### サブエージェントとエージェントチームの使い分け

| 特性 | サブエージェント | エージェントチーム |
|------|---------------|----------------|
| 実行方式 | 並列・独立 | 協調・相互通信 |
| 結果の受け渡し | 親エージェントへの返答のみ | エージェント間で直接やり取り |
| コスト | 低い | 高い（複数インスタンス） |
| 向いているタスク | 独立した調査・分析・デバッグ | 複数領域が絡む統合開発 |
| 安定性 | 安定（デフォルト機能） | 実験的（環境変数が必要） |

---

## 起動方法

### npm で直接起動（WSL / Docker 未使用時）

```bash
# ターミナル1: バックエンド
cd ~/work/claude_practice/260406/dashboard/backend
npm install          # 初回のみ
npm run dev          # → http://localhost:3001

# ターミナル2: フロントエンド
cd ~/work/claude_practice/260406/dashboard/frontend
npm install          # 初回のみ
npm run dev -- --host  # → http://localhost:5173（--host でLAN公開）
```

### Docker で起動（Docker Desktop が動いている場合）

```bash
cd ~/work/claude_practice/260406/dashboard
cp .env.example .env
docker compose up -d

# 停止
docker compose down

# ログ確認
docker compose logs -f
```

---

## アクセス先

| サービス | URL |
|---------|-----|
| フロントエンド | http://localhost:5173 |
| バックエンドAPI | http://localhost:3001 |
| ヘルスチェック | http://localhost:3001/health |

### スマートフォンからのアクセス（同一LAN）

WSL2 はNATを使うため、Viteが表示するNetwork IPではスマホからアクセスできない。  
Windows PowerShell（管理者）でポートフォワーディングを設定する必要がある：

```powershell
# WSLのIPを確認
wsl hostname -I

# ポートフォワーディング設定（<WSL_IP> を実際の値に置き換え）
netsh interface portproxy add v4tov4 listenport=5173 listenaddress=0.0.0.0 connectport=5173 connectaddress=<WSL_IP>
netsh interface portproxy add v4tov4 listenport=3001 listenaddress=0.0.0.0 connectport=3001 connectaddress=<WSL_IP>

# 設定後、スマホから以下でアクセス（Windows側のIPを使用）
# http://192.168.x.x:5173/
```

設定を削除したい場合：
```powershell
netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=3001 listenaddress=0.0.0.0
```

---

## データベース

SQLite ファイルの保存先：
```
./backend/data/dashboard.db
```

バックアップ：
```bash
cp ./backend/data/dashboard.db ./backend/data/dashboard.db.backup-$(date +%Y%m%d%H%M%S)
```

マイグレーション（スキーマ変更時）：
```bash
cd backend
npx drizzle-kit generate  # マイグレーションSQLを生成
# → バックエンド再起動時に自動適用される
```
