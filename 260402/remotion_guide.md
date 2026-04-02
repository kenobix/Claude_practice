# RemotionとClaude Codeで動画を作成するガイド

## 目次

1. [Remotionとは](#remotionとは)
2. [仕組みの概要](#仕組みの概要)
3. [システム構成図](#システム構成図)
4. [動画生成のワークフロー](#動画生成のワークフロー)
5. [環境セットアップ](#環境セットアップ)
6. [プロジェクト構成](#プロジェクト構成)
7. [Claude Codeとの連携](#claude-codeとの連携)
8. [よくあるエラーと対処法（WSL環境）](#よくあるエラーと対処法wsl環境)

---

## Remotionとは

RemotionはReact（TypeScript）でプログラムとして動画を作るフレームワーク。

| 従来の動画編集 | Remotion |
|--------------|----------|
| After Effects / Premiere などのGUIツール | Reactコンポーネントとしてコードで記述 |
| タイムラインをマウスで操作 | `<Sequence>` で時間を管理 |
| アニメーションはGUIで設定 | `spring()` / `interpolate()` で数式的に定義 |
| バージョン管理が難しい | Git で管理できる |
| AIによる自動生成が困難 | Claude Codeに「動画を作って」と指示できる |

---

## 仕組みの概要

```
Reactコンポーネント
    ↓  フレーム番号（useCurrentFrame）を受け取る
各フレームをHTMLとして描画
    ↓  Headless Chrome がスクリーンショット
フレーム画像の連続（例: 840枚 = 28秒 × 30fps）
    ↓  FFmpeg がエンコード
MP4動画ファイル
```

**ポイント**: 動画は「フレーム番号に応じてUIが変わるReactアプリ」をコマ撮りしたものです。

---

## システム構成図

### コンポーネント構成

```
src/
├── index.ts              # エントリーポイント（registerRoot）
├── style.css             # TailwindCSSのディレクティブ
├── Root.tsx              # Composition定義（サイズ・fps・尺）
└── ClaudeCodeVideo.tsx   # メイン動画（シーンを並べる）
    ├── <Sequence from=0>   → TitleScene.tsx
    ├── <Sequence from=90>  → OverviewScene.tsx
    ├── <Sequence from=240> → ConnectorsScene.tsx
    ├── <Sequence from=480> → DemoScene.tsx
    └── <Sequence from=660> → SummaryScene.tsx
```

### ビルド・レンダリング構成

```
┌─────────────────────────────────────────────────────────┐
│                     開発環境                             │
│                                                         │
│  ┌──────────┐    Webpack    ┌──────────────────────┐   │
│  │ React/TS │ ────────────→ │  Remotion Studio      │   │
│  │ ソース    │              │  localhost:3000        │   │
│  └──────────┘              │  （ブラウザプレビュー）  │   │
│        ↑                   └──────────────────────┘   │
│  Claude Codeが編集                                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   レンダリング                            │
│                                                         │
│  React/TSソース                                          │
│       ↓ Webpack バンドル                                 │
│  ┌──────────────┐                                       │
│  │ Headless     │ ← フレーム番号を渡してHTMLを描画        │
│  │ Chrome       │                                       │
│  └──────┬───────┘                                       │
│         │ 各フレームをキャプチャ（PNG）                    │
│         ↓                                               │
│  ┌──────────────┐                                       │
│  │    FFmpeg    │ ← PNGを結合・エンコード                 │
│  └──────┬───────┘                                       │
│         ↓                                               │
│  out/ClaudeCodeVideo.mp4                                │
└─────────────────────────────────────────────────────────┘
```

---

## 動画生成のワークフロー

```
1. プロジェクト作成
   npx create-video@latest
   └─ テンプレート選択（Blank）
   └─ TailwindCSS: Yes
   └─ Skills: Yes

        ↓

2. Agent Skills インストール（任意・推奨）
   npx skills add remotion-dev/skills
   └─ Claude Codeがベストプラクティスを理解して正しいコードを生成できるようになる

        ↓

3. コンポーネント実装
   Claude Codeへの指示例:
   「TitleSceneコンポーネントを作って。黒背景にタイトルがフェードインする」

        ↓

4. プレビューで確認
   npm run dev
   └─ localhost:3000 でリアルタイムプレビュー
   └─ フレーム単位で確認・修正

        ↓

5. レンダリング（MP4書き出し）
   Studioの「Render」ボタン または
   npx remotion render src/index.ts ClaudeCodeVideo out/video.mp4

        ↓

6. 完成
   out/ClaudeCodeVideo.mp4
```

---

## 環境セットアップ

### 前提条件

| ツール | バージョン | インストール方法 |
|--------|-----------|----------------|
| Node.js | v18以上 | `sudo apt install npm` |
| npm | v9以上 | Node.jsに同梱 |

### WSL環境で追加で必要なもの

Headless Chrome（レンダリングエンジン）の依存ライブラリ:

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

日本語フォント（日本語テキストを使う場合は必須）:

```bash
sudo apt install -y fonts-noto-cjk
fc-cache -fv
```

### プロジェクト初期化

```bash
npx create-video@latest    # プロジェクト作成
cd <プロジェクト名>
npm install                # 依存パッケージインストール
npx skills add remotion-dev/skills  # Agent Skills追加（推奨）
npm run dev                # スタジオ起動
```

---

## プロジェクト構成

```
260402/
├── package.json           # 依存パッケージ・スクリプト定義
├── remotion.config.ts     # Remotion設定（TailwindCSS有効化など）
├── tsconfig.json          # TypeScript設定
├── tailwind.config.js     # TailwindCSS設定
├── skills-lock.json       # Skillsバージョンロック
│
├── .agents/               # Agent Skills（Claude Code用ガイド）
│   └── skills/
│       └── remotion-best-practices/
│           └── rules/     # アニメーション・フォント等のベストプラクティス集
│
├── src/
│   ├── index.ts           # エントリーポイント
│   ├── style.css          # TailwindCSSディレクティブ（必須）
│   ├── Root.tsx           # Composition登録（動画サイズ・fps・尺を定義）
│   ├── ClaudeCodeVideo.tsx # メインコンポーネント（シーンのタイムライン）
│   └── scenes/            # シーンごとのコンポーネント
│       ├── TitleScene.tsx
│       ├── OverviewScene.tsx
│       ├── ConnectorsScene.tsx
│       ├── DemoScene.tsx
│       └── SummaryScene.tsx
│
└── out/
    └── ClaudeCodeVideo.mp4  # レンダリングされた動画
```

---

## Remotionの主要API

### 時間の管理

```tsx
import { useCurrentFrame, useVideoConfig } from "remotion";

const frame = useCurrentFrame();         // 現在のフレーム番号（0〜）
const { fps, durationInFrames } = useVideoConfig();
```

### アニメーション

```tsx
import { interpolate, spring } from "remotion";

// 線形補間: frame 0〜30 の間で opacity が 0→1 に変化
const opacity = interpolate(frame, [0, 30], [0, 1], {
  extrapolateRight: "clamp",
});

// スプリングアニメーション（物理ベースの自然な動き）
const scale = spring({ frame, fps, config: { damping: 12 } });
```

### シーンの分割

```tsx
import { Sequence } from "remotion";

// from: 開始フレーム, durationInFrames: このシーンの長さ
<Sequence from={90} durationInFrames={150}>
  <OverviewScene />
</Sequence>
```

---

## Claude Codeとの連携

### Agent Skillsとは

`npx skills add remotion-dev/skills` でインストールされるファイル群。
Remotionのベストプラクティスをまとめた45のルールファイルが `.agents/skills/` に配置され、
Claude Codeがプロジェクト内で作業する際に自動的に参照する。

```
.agents/skills/remotion-best-practices/rules/
├── animations.md      # アニメーションの書き方
├── fonts.md           # フォントの読み込み方
├── tailwind.md        # TailwindCSSの使い方
├── transitions.md     # シーン遷移
├── timing.md          # タイミング制御
└── ...（全45ファイル）
```

### Claude Codeへの指示例

```
「ConnectorsSceneに新しいコネクタ "Slack" を追加して」
「タイトルシーンに背景グラデーションを追加して」
「全シーンのフォントをNoto Sans JPに統一して」
「シーン間にフェードトランジションを追加して」
```

---

## よくあるエラーと対処法（WSL環境）

### TailwindCSSが効かない

**症状**: 背景が透明（チェック柄）、スタイルが未適用  
**原因**: `src/style.css` が存在しないか、`index.ts` からインポートされていない  
**対処**:
```css
/* src/style.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
```
```ts
// src/index.ts
import "./style.css";  // ← この行を追加
```

### Chromeが起動しない（レンダリング失敗）

**症状**: `libnss3.so: cannot open shared object file`  
**対処**: 「環境セットアップ」のライブラリを `sudo apt install` する

### 日本語が □□□ になる

**症状**: 動画内の日本語テキストがすべて豆腐（□）になる  
**対処**: `sudo apt install -y fonts-noto-cjk && fc-cache -fv`

### Ubuntu 24.04でパッケージが見つからない

**症状**: `Package 'libatk1.0-0' has no installation candidate`  
**原因**: Ubuntu 24.04からパッケージ名に `t64` サフィックスが付いた  
**対処**: `libatk1.0-0` → `libatk1.0-0t64` のように書き換える
