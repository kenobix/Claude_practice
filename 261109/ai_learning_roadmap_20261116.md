# 生成AI開発環境の優先順位と判断根拠（2026-11-16）

> 詳細版はこの作業日のダウンロード用PDF/Markdownとしても作成。ここではGitHubから参照しやすい判断と根拠を記録する。

## 決定

**Strataの基本動作検証は完了。Codexとの実用比較を短時間で終え、ローカルAI Studioの最小実装へ進む。** 新しいモデルの収集自体を目的にしない。

| 優先 | 作業 | 完了条件 |
|---|---|---|
| P1（完了） | Strata × OpenCodeでコード作成とpytestを実行 | 29テスト成功。初回失敗を自己修正 |
| P2（次） | CodexとStrataの実用比較を1〜2件 | 独立テスト・手戻り回数・時間を記録。2時間程度で区切る |
| P3（本命） | FastAPI × ComfyUIのローカルAI Studio MVP | 保存済み画像からLTX/Wan動画生成、進捗・MP4・設定JSONを取得 |
| P4 | 異常系・運用整備 | タイムアウト、失敗記録、GPUの逐次実行、秘密情報除外 |
| P5（必要時） | RAG / MCP / 新規モデル | MVPに対する具体的な価値があるときだけ採用 |

## 根拠：どこまで検証されたか

- RTX 5070 Ti 16GB、RAM 32GB、Windows 11 + WSL2でStrataとOpenCodeを連携。32Kコンテキスト、WSLからのAPI到達、テキスト生成とファイル読み取りを確認。
- Pythonの `is_prime.py` と `test_is_prime.py` をOpenCodeエージェントが作成。最初のpytest収集では `import pytest` 欠落で失敗。自分で修正し、**29 passed / 0 failed** に到達。したがって「一発成功」と書かない。
- pytest報告38.48秒とシェル実測36.163秒には差がある。速度比較の基準値として無批判に使わない。巨大な整数 `2**61-1` の素数判定は試し割り `O(√n)` の実行時間を支配する。
- Strataモニターで平均84 tok/s、VRAM 15.6/16 GB、RAM 30.9/31 GBを観測。小規模タスクの動作確認と複雑なリポジトリを扱う実用性は別問題。
- [261109の既存記録](README.md)ではQwen画像生成・編集、Wan動画が記録済み。ユーザーの後続操作でLTX-2.3のI2Vも実行確認済み。画像生成を繰り返すより、API経由の統合・異常処理が新しい学習価値を持つ。
- [261014の開発計画](../261014/next_project_plan.md)は再現可能な実装、品質ゲート、受入証拠を重視している。
- [261021のMLOps](../261021/README.md)では667件の単体テストがあってもUTCの時刻逆行2件を理由にCapital候補は**配信不採用**。テスト成功率だけで受入を判定しない。

## P2：比較設計

1. 小規模の未解決バグまたは機能1〜2件を、同じ受入テストで比較する。
2. CodexとStrataは別ブランチまたは別worktreeで作業し、共有作業ツリーの競合を防ぐ。
3. 合格条件、手戻り回数、利用者の介入、経過時間、無関係な変更の有無を記録。
4. 軽量Strataが失敗する用途はCodexへ戻す。「IQ1_Mでコードを出せた」だけで実務代替とはしない。

## P3：AI Studio MVP

1. 保存済みのComfyUIワークフローをPythonクライアントから投入し、生成完了とMP4取得まで確認する。
2. FastAPIに `POST /jobs`、`GET /jobs/{id}`、`GET /jobs/{id}/result` を用意。長時間生成を1回の同期HTTP処理に閉じ込めない。
3. まずは単一のGPUジョブキューでQwen画像→LTX/Wan動画を連結する。モデル、seed、寸法、FPS、処理時間、成果物パス、失敗理由を記録する。
4. 最小UIと異常系を追加。localhost限定、ファイル種別・サイズ・保存先を制限する。
5. StrataとComfyUIの同時GPU常駐は避ける。Strataと既存MLOpsが共に8080番ポートを使うことにも注意し、AI Studioは別ポートを使う。

## 却下・保留した案

| 案 | 判断 | 理由 |
|---|---|---|
| LoRAや画像生成モデルを次々追加 | 当面却下 | 既に基本生成できる。モデル数より統合・品質管理が重要 |
| Wan/LTXの大量再生成 | 後回し | 動作を確認済み。品質統計が必要になる時だけ実施 |
| IQ1_MをCodexの全面代替 | 却下 | メモリほぼ満杯、複雑な受入基準で未検証 |
| 128K以上へ即拡大、巨大量子化モデルへ変更 | 見送り | RAM余裕が乏しくスワップや停止リスクがある |
| RAG・MCP・クラウドGPUを一度に統合 | 却下 | スコープ拡大でMVPの完成基準がぼやける |
| Claude/ChatGPTの契約をネット評価のみで決定 | 保留 | 実タスク完了率・制限到達・料金で比較すべき |
| MLOpsのテスト件数だけで配信可能と断定 | 却下 | 現行READMEに配信不採用と明記 |

## セキュリティ・GitHub運用

- 公開リポジトリのため、Strata APIキー、ローカル設定ファイル、モデル本体、`.venv/`、キャッシュや個人情報をコミットしない。
- ソース原本はユーザーの別ディレクトリ `~/projects/strata-smoketest` に存在。**原本コード2ファイルの公開は別途WSLからコピー・レビュー・pushが必要**。この判断文書はコード本体を代替しない。
- 歴史的な測定記録は書き換えず、更新日を持つ新しい記録として追加する。

## 参照

- [Strata実験の記録](strata_opencode_validation_20261116.md)
- [261109/README](README.md) / [261014/next_project_plan](../261014/next_project_plan.md) / [261021/README](../261021/README.md)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)（長いジョブ管理の設計の参考）
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)（進捗通知の参考）

判断は2026-11-16時点の会話ログと上記ソースに基づく。未実施のCodex比較、ComfyUI API統合、AI Studio本体の完成は**未検証**。
