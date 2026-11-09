# デスクトップPCのWSL AI開発環境

2026-11-09の環境構築記録と、公開前に実施した読み取り中心の検証を保存する。

## 保存ファイル

- [環境構築レポート](desktop_ai_setup_report.md): 導入手順、当時の観測値、未完了課題。
- [Codexへの反映指示](codex_push_prompt.md): 検査対象と安全なGit操作の手順。
- [Ollama Pythonサンプル](scripts/test_ollama.py): `~/projects/ai-lab/test_ollama.py` から内容を変更せずコピー。原本は変更していない。
- [8B/14Bベンチマーク](scripts/benchmark_ollama.py): `~/projects/ai-lab/benchmark_ollama.py` から内容を変更せずコピー。原本は変更していない。測定結果は[レポートの追記](desktop_ai_setup_report.md#11-qwen3-14bの取得確認と8b14bベンチマーク追記)を参照。

## 初回公開前の検証結果（当時の状態）

以下は初回公開前の観測を保持したもの。後続の14B取得確認と測定結果は「追記」を参照。

| 確認対象 | 2026-11-09の再確認結果 |
|---|---|
| Windows | `DisplayVersion` は26H2、OSビルド26300.9711 |
| WSL / Ubuntu | WSL 3.0.3.0、Ubuntu-24.04はWSL2で稼働。Ubuntu 24.04.5 LTS、カーネル6.18.54.1-microsoft-standard-WSL2 |
| 開発ツール | Git 2.43.0、Python 3.12.3、GitHub CLI 2.45.0、zstd 1.5.5 |
| GPU | `nvidia-smi` でRTX 5070 Ti、16,303 MiBを認識。KMD 617.42、CUDA UMD 13.4 |
| ai-labの仮想環境 | インストール済みメタデータはPyTorch 2.14.1+cu130、NumPy 2.5.4、ollama 0.6.3 |
| Docker | Desktop 4.98.0、Engine/CLI 29.9.2、Compose v5.6.1。デーモンへの照会成功 |
| Ollama | 0.40.15、systemdサービスactive。モデル一覧に`qwen3:8b`あり。`ollama ps`ではロード中モデルなし |
| Qwen3 14B | モデル一覧に`qwen3:14b`なし。ダウンロード完了・起動・GPU割当・速度は未確認のまま |
| Git | 作業開始時は`main`の`114af9d`。`git fetch origin`と`git pull --ff-only origin main`で同期済みを確認 |

再確認したバージョンはレポートと一致した。レポートのPyTorch CUDA計算、Docker hello-world、Qwen3 8BのGPU推論・速度は過去の共有結果であり、今回は再実行していない。生成速度の平均値127.85 tokens/sは記載された2値の算術平均と一致する。CPU・メモリ・SSD等の実機構成、Windowsの認証・保護・暗号化状態、初期設定の履歴も今回は再検証していない。

通常のサンドボックス内ではGPUアクセスや名前解決が制限されるため、WSL・GPU・稼働サービスとGitのネットワーク操作は許可されたサンドボックス外で確認した。インストール、設定変更、モデル取得、推論、ベンチマークは実施していない。

## サンプルの使い方

必要なものは、Python、Python用`ollama`パッケージ（元環境は0.6.3）、起動済みのローカルOllama、および取得済みの`qwen3:8b`。元環境では`~/projects/ai-lab/.venv`を使用した。

```bash
cd ~/projects/Claude_practice
~/projects/ai-lab/.venv/bin/python 261109/scripts/test_ollama.py
```

このコマンドはQwen3 8Bをロードして推論するため、今回の公開前検証では実行していない。サンプル自体は`ollama.chat`による問い合わせと回答表示のみで、認証情報、個人情報、絶対パス、ファイル書き込みを含まない。接続先を変更する環境変数等は設定せず、ローカルのOllamaで利用する。

仮想環境、モデル本体、キャッシュは保存しない。文書に付随していた`Zone.Identifier` 2件はWindowsのダウンロード属性のみで、成果物ではないため削除した。

## 追記：Qwen3 14Bとベンチマーク

2026-11-09に共有された実測ログで、8B/14Bそれぞれウォームアップ後3回の計測が正常に終了している。平均生成速度は8Bが131.0 tokens/s、14Bが78.9 tokens/s、平均総時間はそれぞれ1.99秒、3.28秒。初回8B測定（127.85 tokens/s）とは別の測定として保存する。

更新時の`ollama list`では`qwen3:14b`（ID `bdbd181c33f2`、9.3 GB）を確認し、取得済みと記録した。`ollama ps`にはロード中モデルがなく、提示されたベンチマーク出力にも応答本文・GPU割当はないため、日本語応答の成功と14Bの100% GPU動作は確認保留。

ベンチマークはPython標準ライブラリのみを使い、`http://127.0.0.1:11434/api/generate`へ問い合わせる。Python用`ollama`パッケージは不要だが、起動済みOllama、取得済みの両モデル、PATH上の`ollama`コマンドが必要。各モデルの測定後に`ollama stop`でそのモデルをアンロードする。

```bash
cd ~/projects/Claude_practice
python3 261109/scripts/benchmark_ollama.py
```

更新作業ではスクリプトを実行せず、内容と構文、コピー元との一致を確認した。新たな推論・モデル取得・ベンチマークは実施していない。

## 残作業

- 日本語応答本文と14Bの`100% GPU`を示すログの確認。品質比較、コンテキスト長や同時常駐条件を変えた測定。
- 外部媒体へのWindows/WSLバックアップ、デバイス暗号化の調査、CPU/GPUの継続負荷試験。
