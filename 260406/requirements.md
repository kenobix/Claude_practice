# 個人開発管理ダッシュボード 要件定義書

作成日: 2026-04-06  
更新日: 2026-04-06（試作品フェーズ完了後に実態に合わせ修正）  
作成方法: Claude Code サブエージェント（技術調査・機能要件・非機能要件を並列実施）

---

## 1. システムコンセプト

WSL上で動作し、スマートフォンからもアクセスできる個人開発者向けの生産性管理ツール。  
タスク管理・集中時間の計測を一元化し、どこからでも作業状況を把握・更新できる。

---

## 2. 採用技術スタック

| 技術 | 採否 | 用途・理由 |
|------|------|-----------|
| **SQLite** | 採用 | ローカルDB。個人用途に十分、バックアップが `cp` 一発 |
| **Cloudflare Tunnel** | スキップ（Phase 2） | スマホ外部アクセスに有効だが、試作品は LAN 内アクセスのみ |
| **Firebase Auth** | スキップ（Phase 2） | 試作品フェーズでは認証不要（ローカル専用） |
| **Docker** | 条件付き採用 | `docker compose up -d` 構成は用意済み。WSL環境ではnpmで直接起動 |
| PostgreSQL | スキップ | 個人用途に過剰 |

### 追加採用技術

| 技術 | 用途 |
|------|------|
| Hono (Node.js v20 LTS) | バックエンドAPI（ポート 3001） |
| React + Vite | フロントエンド（ポート 5173） |
| Drizzle ORM | DBスキーマ管理・マイグレーション |
| Tailwind CSS v3 | UIスタイリング |
| React Router v6 | クライアントサイドルーティング |
| better-sqlite3 | 同期SQLiteドライバ |
| tsx | TypeScript直接実行（開発時） |

---

## 3. システム構成図

```
ブラウザ / スマートフォン（同一LAN）
    │  HTTP
    │  :5173（フロントエンド）
    ▼
React + Vite（静的SPA）
    │  REST API calls  /api/*
    │  :3001（バックエンド）
    ▼
Hono / Node.js（REST API）
    │
    ▼
SQLite（./backend/data/dashboard.db）
    └── WAL モード有効
```

> スマホからのLANアクセスはWSL2のNAT制約があるため、Windowsホスト側でポートフォワーディングが必要（詳細はREADME参照）。

---

## 4. 機能要件（実装済み）

### 4.1 ユーザーストーリー（試作品スコープ）

| # | ストーリー |
|---|-----------|
| US-01 | タスクを素早く追加したい |
| US-02 | タスクにラベルを付けてカテゴリ分けしたい |
| US-03 | タスクに優先度とステータスを設定したい |
| US-04 | タスクが完了したらワンクリックで完了済みにしたい |
| US-05 | タスクに集中時間を計測したい（開始・停止） |
| US-06 | 今日の作業時間と完了タスク数をダッシュボードで確認したい |
| US-07 | タスクにメモ（プレーンテキスト）を書きたい |

### 4.2 実装済み機能一覧

#### Phase 1 MVP（実装完了）

| ID | 機能 | 実装状態 |
|----|------|---------|
| F-01 | タスク CRUD | ✅ |
| F-02 | タスクステータス管理（Todo / In Progress / Done） | ✅ |
| F-03 | タスク優先度設定（High / Medium / Low） | ✅ |
| F-04 | タスクラベル（自由テキスト） | ✅ |
| F-05 | タスクメモ（プレーンテキスト） | ✅ |
| F-06 | 完了ボタン（ワンクリック） | ✅ |
| F-07 | タイマー開始・停止 | ✅ |
| F-08 | 時間ログ記録・累計表示 | ✅ |
| F-09 | ダッシュボードホーム（今日のタスク・稼働タイマー・統計） | ✅ |
| F-10 | レスポンシブレイアウト | ✅ |
| F-11 | ヘルスチェックエンドポイント（GET /health） | ✅ |

#### Phase 2（未実装）

| ID | 機能 |
|----|------|
| F-12 | Cloudflare Tunnel によるインターネット公開 |
| F-13 | Firebase Auth による認証 |
| F-14 | 日次・週次レポート |
| F-15 | タスク検索・タグ絞り込み |

### 4.3 画面構成

```
/           ダッシュボード（今日のタスク・稼働タイマー・今日の統計）
└── /tasks  タスク一覧（フィルタ・新規作成）
    └── /tasks/:id  タスク詳細・編集・タイマー操作・メモ
```

### 4.4 データエンティティ（実装済み）

```
Task 1 ── N TimeLog
```

| エンティティ | 主要フィールド |
|------------|--------------|
| Task | id, title, label, description(memo), status, priority, due_date, created_at, completed_at |
| TimeLog | id, task_id, started_at, stopped_at, duration_seconds, memo |

> Project・Note テーブルはスキーマに残っているが、UIには表示しない（Phase 2以降で再検討）。

---

## 5. 非機能要件

### 5.1 パフォーマンス

| 項目 | 目標値 |
|------|--------|
| API応答（読み取り系） | 200ms以内 |
| API応答（書き込み系） | 500ms以内 |
| ページ初期ロード（ローカル） | 1秒以内 |

### 5.2 セキュリティ（Phase 1の範囲）

- ローカルネットワーク内のみ公開（外部公開なし）
- Phase 2 で Firebase Auth + Cloudflare Tunnel を追加予定

### 5.3 運用・起動

| 方法 | コマンド |
|------|---------|
| Docker（推奨） | `docker compose up -d` |
| npm直接起動（WSL Docker未使用時） | `npm run dev`（バックエンド・フロントエンド別々） |

### 5.4 移植性

- Docker 環境なら1コマンドで起動
- Node.js v18+ があれば npm で直接起動可能

---

## 6. 開発フェーズ計画

| フェーズ | 内容 | 状態 |
|---------|------|------|
| **Phase 1 MVP** | SQLite + Hono API + React フロント | ✅ 完了 |
| **Phase 2** | Cloudflare Tunnel + Firebase Auth | 未着手 |
| **Phase 3** | レポート・検索・タグ等の Should 機能 | 未着手 |

---

## 7. 開発アプローチ（Claude Code エージェント活用）

このシステムは Claude Code のエージェント機能を活用して開発した。

```
Phase A: サブエージェント（並列）
├── Agent 1: 技術調査（Cloudflare / SQLite / Firebase / Docker）
├── Agent 2: 機能要件定義
└── Agent 3: 非機能要件定義
       ↓ 結果を集約 → requirements.md 作成

Phase B: エージェントチーム（協調開発）
├── Backend Agent:  Hono API / Drizzle ORM / SQLite
├── Frontend Agent: React / Vite / Tailwind CSS
└── Infrastructure Agent: Docker Compose / Dockerfile / .env

Phase C: サブエージェント（デバッグ）
└── Debug Agent: TypeScript エラー調査・修正提案
```

### エージェントの使い分け

| 用途 | 使用したエージェント | 理由 |
|------|-------------------|------|
| 並列調査・要件定義 | サブエージェント（3並列） | 独立したタスク、結果のみ親に返せばよい |
| フルスタック開発 | エージェントチーム | バックエンド・フロントエンド・インフラの協調が必要 |
| デバッグ・調査 | サブエージェント（Explore） | コードベースの探索に特化、低コスト |
