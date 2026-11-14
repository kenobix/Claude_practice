# デスクトップAI環境・実験記録

2026-11-09〜12の環境構築、開発AIツール、ComfyUIの画像・動画実験を種類別にまとめた索引。
2026-11-14にMarkdownを10本から6本へ整理した。各記録の実測値・実験条件・当時の未完了状態は保持し、過去の文書と今回の測定を混同しない。

## 種類別の記録

| 種類 | ファイル | 内容 |
|---|---|---|
| 環境構築・ローカルLLM | [environment.md](environment.md) | Windows・WSL・RTX 5070 Ti・PyTorch・Docker・Ollama、Qwen3 8B/14Bの測定、サンプルの実行方法 |
| 開発AIツール | [coding_tools.md](coding_tools.md) | OpenCode＋Ollama、無料GLM・GoのKimi、Codex CLIとGoの連携、コードの再検証・操作メモ |
| 画像生成・編集 | [image_experiments.md](image_experiments.md) | FLUX.1 Schnell・FLUX.2 Klein、Qwen Image 2.1 Turboの英語・日本語・参照画像編集と比較 |
| 動画生成 | [video_experiments.md](video_experiments.md) | Wan 2.2 TI2V-5Bの約3秒・約5秒の生成、課題と再現手順 |
| 公開手順の履歴 | [publishing_history.md](publishing_history.md) | 当時のCodexへの依頼文、旧ファイル名と統合先の対応表、今回の整理履歴 |

## 主な結果

| 対象 | 記録済みの結果 |
|---|---|
| Qwen3 8B / 14B | ウォーム後各3回の平均131.0 / 78.9 tokens/s、API総時間1.99 / 3.28秒。14Bの日本語応答・100% GPUは別ログで確認 |
| OpenCodeの計算アプリ | 8B・14B・Kimi各5テスト、GLM 8テスト成功。追加指示は8Bが1回、他は0回 |
| FLUX | Schnellの日英固定シード比較、Kleinの新規生成と色変更を保存。編集43.92秒 |
| Qwen Image 2.1 Turbo | 英語736×736が17.16秒、英語1024×1024が3.13秒、日本語生成22.85秒、編集25.00秒 |
| Wan 2.2 | 49 / 49 / 81フレーム、16fps。生成時間42.15 / 22.18 / 34.27秒 |

生成時間は各実験時の利用者報告。今回の整理では再測定していない。異なるモデル・条件の時間やテスト数から性能の順位を決めない。
日本語画像生成では窓からあふれる花の表現が不完全。画像編集・動画生成でも細部や背景の完全固定は達成したと評価しない。

## 成果物

| 保存先 | 内容 |
|---|---|
| [scripts/](scripts/) | OllamaのPython呼び出しと8B/14Bベンチマーク。実行方法は[環境構築の操作メモ](environment.md#environment-readme) |
| [opencode_samples/](opencode_samples/) | 8B・14B・GLM・Kimi別の計算アプリとテスト。実行方法は[開発AIツールの操作メモ](coding_tools.md#coding-readme) |
| [comfyui/images/](comfyui/images/) | FLUX・Qwenの確認済みPNG。編集元は重複コピーせず共用 |
| [comfyui/videos/](comfyui/videos/) | WanのMP4 3本 |
| [comfyui/workflows/](comfyui/workflows/) | 保存済みJSONのコピーと、編集PNGから抽出したJSON。由来は各記録・manifestで区別 |
| [FLUX manifest](comfyui/artifact_manifest.json) | PNG・JSONのサイズ、SHA256、対応関係 |
| [Qwen manifest](comfyui/qwen_image_2_1_artifact_manifest_20261112.json) | 英語・日本語・編集のPNG・JSONと、抽出した編集ワークフローの由来 |
| [Wan manifest](comfyui/video_artifact_manifest_20261111.json) | 動画の寸法、フレーム数、設定、SHA256 |

モデル本体、仮想環境、キャッシュ、認証情報は保存しない。今回の整理は文書とリンクだけを対象にし、成果物や元のWindows/WSL環境は変更していない。

## 今後のモデル運用方針

- 画像生成・編集は利用者の好みに合うQwen Image 2.1 Turboを主力とし、FLUX.2 Kleinも用途に応じて使い分ける。客観的な画質・速度の優劣は断定しない。
- 動画はWan 2.2を基準とし、LTX系などを追加検証候補とする。
- Hugging Faceの派生・量子化モデル、LoRA、Strataによるローカル大規模LLMは今後の候補。導入可否や性能は未検証。
- 現時点でClaude Proは追加せず、Codex＋OpenCode Goを維持する。

## 残作業

- 同じ入力・固定シードで画像編集や動画の車体・背景維持を確認し、生成時間とVRAMを記録する。Wanの約3秒・約5秒生成自体は実施済み。
- 通常の開発中にモデルごとの成功と手戻りを記録する。専用ベンチマークの優先度は下げる。
- 無料モデルの学習利用許可の現在値を再確認する。
- 従来のCodexとGoを併用し、既存設定を保持する。
- 外部媒体へのWindows/WSLバックアップ、デバイス暗号化の調査、CPU/GPUの継続負荷試験。
