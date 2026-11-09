# デスクトップPC 初期設定・WSL2・GPU AI開発環境 構築記録

- **記録日**: 2026-11-09
- **対象PC**: パソコン工房 LEVEL-R7B6-R77-UKX（商品ID 1127867）
- **作業環境**: Windows 11 Home / WSL2 Ubuntu 24.04 LTS
- **記録範囲**: 初回起動から、GitHub連携・PyTorch CUDA計算・Docker・Ollama 8B動作までの初期記録、およびQwen3 14B取得確認・8B/14Bベンチマークの追記
- **初期記録の最終確認時点**: `qwen3:14b` のダウンロード中（当時は完了・GPU動作が未確認）。後続の確認結果は第11節に追記。
- **情報源**: 利用者が共有したコマンド出力・スクリーンショット・作業経過。推定値と未検証項目を区別する。

## 1. 要約

第1〜10節は初期記録として保持する。以下の未完了項目は当時の状態であり、後続の進捗は第11節を参照。

主要ハードウェアの構成確認、Windowsのライセンス認証・更新、AMD/NVIDIAドライバー導入、Norton削除、WSL2とUbuntu 24.04、VS Code/Codex、GitHub CLI、PyTorchのGPU計算、Docker DesktopのWSL連携、OllamaによるQwen3 8BのGPU推論を完了した。

**未完了**: Qwen3 14Bのダウンロード・推論測定、外部媒体へのバックアップ、C:ドライブ暗号化、CPU/GPUの継続負荷テスト。未確認のものを完了扱いしない。

## 2. PC構成と検証結果

| 分類 | 購入・実機情報 | 確認状態 |
|---|---|---|
| CPU | AMD Ryzen 7 7700、8コア/16スレッド | CPU-Z/タスクマネージャーで確認 |
| マザーボード | MSI PRO B650-S（MS-7E26）、B650 | CPU-Zで確認 |
| GPU | Palit NVIDIA GeForce RTX 5070 Ti 16GB GDDR7 | GPU-Z、`nvidia-smi`、PyTorchで確認 |
| RAM | Micron DDR5 16GB × 2 = 32GB | 確認。実動作DDR5-5200 / CL42（OC設定は未変更） |
| SSD | WD Blue SN5100 2TB NVMe PCIe 4.0×4 | CrystalDiskInfoで健康状態100%、26℃（測定当時） |
| 電源 | 850W / 80PLUS GOLD（注文内容） | ソフトウェア上では実機型番未検証 |
| ネットワーク | 有線2.5GbE + TP-Link Archer TX10UB nano（Wi-Fi 6 / Bluetooth 5.3 USB） | TP-Linkドライバー導入とWi-Fi接続完了 |
| OS | Windows 11 Home、バージョン26H2、OSビルド26300.9711（確認当時） | ライセンス認証、更新完了 |

モニターのHDMIはマザーボード側ではなくRTX 5070 Ti側に接続した。

## 3. Windows初期設定・セキュリティ

1. 初回起動・Windowsセットアップ、Microsoftアカウントへのサインイン、TP-Linkドライバー導入によるWi-Fi接続を完了。
2. 本体側面のWindows 11 Homeプロダクトキーを使用してライセンス認証完了。**キーの実値は記録・公開しない。**
3. Windows Updateを実施、`winver` で26H2 / OSビルド26300.9711を確認（2026年10月末時点）。
4. AMD B650チップセットドライバーを公式インストーラーから導入。
5. NVIDIA AppからStudioドライバーを導入。WSLの`nvidia-smi`ではWindows側のKMD Version **617.42** と CUDA UMD Version **13.4** を報告。後者はLinux側にCUDA Toolkitを別途入れた証拠ではない。
6. Norton 360をアンインストール。WindowsセキュリティでMicrosoft Defender有効・主要保護項目が緑色であることを確認。
7. デバイスマネージャーの表示範囲で警告マークなし。
8. C:ドライブの `manage-bde -status C:` は**暗号化率0.0%、保護オフ**。`msinfo32`では自動デバイス暗号化サポートにPCR7/HSTI/モダンスタンバイ/DMA関連の問題が表示された。対応可否は追加調査が必要。開発環境構築のためにBIOSや暗号化設定を無理に変更していない。

### セキュリティ上の注意

- リポジトリ `kenobix/Claude_practice` は **Public**。APIキー、OAuth/PAT、SSH秘密鍵、Windowsのプロダクトキー、BitLocker回復キー、個人情報は絶対にpushしない。
- `~/.config/gh/hosts.yml` や `.env` をGitに加えない。`.gitignore` に記載があっても、追跡済みファイルやログ本文には注意。
- C:ドライブは暗号化されていないため、盗難時のデータ保護には留意する。バックアップと暗号化は別の機能。

## 4. WSL2・Ubuntu 24.04

### インストール履歴

初回の `wsl --install -d Ubuntu-24.04` ではWSL本体と `VirtualMachinePlatform` の設定が完了し、Windowsの再起動が必要になった。再起動後はディストリビューションがまだなかったため、同じコマンドを再実行してUbuntuの導入に成功した。

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --list --verbose
```

観測値:

- WSL Version: **3.0.3.0**（Codexによる調査）
- カーネル: **6.18.54.1-microsoft-standard-WSL2**（同上）
- ディストリビューション: **Ubuntu 24.04.5 LTS (noble)**
- WSL VERSION: **2**
- Ubuntuユーザー: `kenshin`
- プロジェクト: `/home/kenshin/projects/`
- Git: **2.43.0**、Python: **3.12.3**

実施した主要更新:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y gh curl ca-certificates build-essential python3-venv python3-pip
```

直近の更新結果は `0 upgraded, 0 newly installed, 0 to remove`。GitHub CLI は **2.45.0**（Ubuntu提供パッケージ）。

## 5. VS Code・Codex・GitHub

### VS Code連携

- `ms-vscode-remote.remote-wsl` を導入。WSL側のVS Code Server導入成功。
- `code .` によりWSLフォルダーをVS Codeで開いた。
- Codex拡張機能をWSL側にも導入・有効化。Codexから読み取り専用の環境調査に成功。
- VS Codeの `Restricted Mode` / Workspace Trustは、自分が把握しているワークスペースに限って信頼する。

### GitHubリポジトリ

- **URL**: https://github.com/kenobix/Claude_practice
- **公開範囲**: Public
- **ローカル**: `/home/kenshin/projects/Claude_practice`
- **ブランチ**: `main`
- **認証**: `gh auth login` で `kenobix` としてGitHub.com/HTTPS認証済み。WSLからブラウザーを自動で開けなかったが、URLをブラウザーで手動入力して認証完了。
- **Git email**: GitHub提供のnoreplyアドレスを設定。
- **同期済みコミット**: `114af9d`（2026-11-09の観測値）。`git pull --ff-only origin main` によりノートPCで先行していた2コミットをfast-forwardで反映。
- **最終観測**: `git status` が `working tree clean`。これ以降のリモート更新がないことは保証しない。

```bash
cd ~/projects/Claude_practice
git status
git fetch origin
git pull --ff-only origin main
git log -1 --oneline
```

**注意**: このリポジトリには `AGENTS.md` があり、作業単位でのcommit/pushなどのルールが定められている。Codex利用時はそれも参照し、無関係なファイルを変更・ステージしない。

## 6. Python・PyTorch CUDA環境

WSLのUbuntuで、GitHubリポジトリとは分離した仮想環境を作成した。

```bash
mkdir -p ~/projects/ai-lab
cd ~/projects/ai-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cu130
python -m pip install numpy
```

確認したバージョン:

- PyTorch **2.14.1+cu130**
- NumPy **2.5.4**
- Python **3.12.3**

初回は `No module named 'numpy'` 警告が出たが、NumPy導入により解消。PyTorchの `torch.cuda.is_available()` は `True`、GPU名は RTX 5070 Ti。以下のテスト結果は `1024.0`。

```python
import torch

a = torch.ones((1024, 1024), device="cuda")
b = a @ a
torch.cuda.synchronize()
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0))
print(b[0, 0].item())  # 1024.0
```

WSL内にLinux用のNVIDIA GPUドライバーを重複して導入していない。PyTorchの標準的なホイール利用だけならシステム全体へのCUDA Toolkitの導入は必須ではない。

## 7. Docker Desktop + WSL連携

Windows PowerShellで導入:

```powershell
winget install -e --id Docker.DockerDesktop
```

Docker Desktopの「Settings → Resources → WSL Integration」で `Ubuntu-24.04` を有効にし、「Apply & Restart Engine」を実行。初回の `docker run` はソケットの `permission denied` だったが、設定適用・WSL再起動後に解消した。

最終確認:

| 項目 | バージョン・結果 |
|---|---|
| Docker Desktop | 4.98.0 |
| Docker Engine/CLI | 29.9.2 |
| Docker Compose | v5.6.1 |
| `docker run --rm hello-world` | `Hello from Docker!` を表示、成功 |

**運用方針**: Ubuntu内へ別のDocker Engineを不用意に追加しない。Docker Desktopとの二重管理を避ける。

## 8. Ollama・ローカルLLM

### Ollama導入

```bash
sudo apt install -y zstd
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
systemctl is-active ollama
```

最初は展開用の `zstd` がなく失敗した。`zstd` 1.5.5を追加後にインストール成功。

- Ollama **0.40.15**
- systemdサービス: **active**
- APIアドレス: **http://127.0.0.1:11434**（ローカルホスト）
- Python用パッケージ `ollama` **0.6.3** を `~/projects/ai-lab/.venv` に導入

### Qwen3 8B 実行結果

```bash
ollama run qwen3:8b
ollama ps
nvidia-smi
```

| 項目 | 測定・観測値 |
|---|---|
| モデル | `qwen3:8b` |
| ダウンロード | 約5.2GB |
| ロード中サイズ | 約5.6GB（`ollama ps`表示） |
| GPU割当 | **100% GPU** (`ollama ps`) |
| コンテキスト長 | 4,096 tokens（測定時） |
| GPUメモリ使用 | `nvidia-smi`: 6,718 / 16,303 MiB（測定時） |
| 生成速度1 | **126.1 tokens/s**, 884 tokens, 総7.26秒、ロード0.00秒 |
| 生成速度2 | **129.6 tokens/s**, 896 tokens, 総7.06秒、ロード0.00秒 |
| 平均 | **127.85 tokens/s**（2回の算術平均、同一プロンプト、ウォーム状態） |

生成速度の計算方法は `eval_count / (eval_duration / 1e9)`。GPUの利用は `ollama ps` の100% GPUで確認済み。`nvidia-smi` のGPU-Utilが0%なのは生成処理終了後の観測であり、GPU非利用の根拠ではない。

API動作確認済み:

```bash
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3:8b","prompt":"Pythonの特徴を3つ教えてください。","stream":false,"think":false}' \
  | python3 -m json.tool
```

Pythonからも次のライブラリで呼び出しに成功。実際のファイル `~/projects/ai-lab/test_ollama.py` は別に存在する（この文書にソース全文は転載していない）。

```python
from ollama import chat
response = chat(model="qwen3:8b", messages=[{"role": "user", "content": "Pythonのリスト内包表記を説明して"}], think=False)
print(response.message.content)
```

**注意**: PythonコードをそのままBashプロンプトに貼り付けると `Command 'from' not found` などになる。`.py` ファイルに保存して `python test_ollama.py` と実行するか、`python - <<'PY' ... PY` を使用する。

### Qwen3 14B（初期記録時点：進行中・未完了）

```bash
ollama stop qwen3:8b
ollama run qwen3:14b
```

2026-11-09の初期記録の最終ログでは、**9.3GB中47MB（約1%）をダウンロード中**。当時、完了・起動・GPU割当・性能値は**未確認**。後続の実測値は第11節に追記する。

## 9. 初期記録時点で次に実施すること（優先順）

1. `qwen3:14b` のダウンロード完了確認。`ollama list`、`ollama run qwen3:14b`、別ターミナルで `ollama ps` を実行し、`100% GPU` かCPUオフロードか確認。
2. 8Bと14Bの速度・品質比較。同条件・同一プロンプト、warm-up後複数回測定。VRAMが不足する場合はコンテキスト長・量子化・同時常駐モデルを見直す。**14Bが16GBに必ず収まるとは断定しない。**
3. Codexで今回の記録をリポジトリに安全に取り込む。既存 `AGENTS.md` を尊重し、秘密情報を確認してコミット・push。
4. 必要に応じてOpenCode/CursorなどへOllama API接続し、小さなコーディング課題でクラウドモデルとの品質差を検証。
5. 外付けSSD等を準備した後、WSLエクスポート・Windows重要ファイルのバックアップを設定。現在は**外付けSSDなし・WSL環境スナップショット見送り**。GitHubはコード保存先であり、Windows/WSL全体のバックアップではない。
6. デバイス暗号化の課題・CPU/GPU長時間負荷試験は別作業として管理する。

### オプション：14BのGPU割当と速度の記録

```bash
ollama list
ollama ps
nvidia-smi
```

同条件ベンチマークでは、Ollama APIの `prompt_eval_duration`、`eval_count`、`eval_duration`、`load_duration`、`total_duration` を記録する。初回ロード時間とウォーム状態の生成速度を混同しない。

## 10. 再現時の注意と保存方針

- `~/projects/ai-lab/.venv/` は**公開リポジトリにコピーしない**。必要なら依存関係と実行手順をテキスト化する。
- Ollamaモデルの巨大な重みファイルをGitHubにコミットしない。モデル名とバージョンを記録して再取得可能にする。
- すべてのPCに一斉に更新を流さない。ノートPCとデスクトップPCは同じ `main` を編集するため、作業前の `git fetch` / `git pull --ff-only` と競合確認を徹底する。
- 秘密情報を画面で確認・ログ出力しても、公開ドキュメントには転載しない。GitHubに一度pushした情報は削除コミットだけで完全に消えるとは限らない。
- 再現性の目的は「動作確認できた手順・バージョン・課題の記録」であり、PCの全ファイルをGitHubにアップロードすることではない。

## 11. Qwen3 14Bの取得確認と8B/14Bベンチマーク（追記）

2026-11-09に利用者から共有された`python benchmark_ollama.py`の出力と、更新時の読み取り専用確認を記録する。初期の8B測定（平均127.85 tokens/s）は別の測定履歴として保持する。今回、Codexによる推論やベンチマークの再実行は行っていない。

### 取得・応答・GPU割当の確認範囲

更新時の`ollama list`は次のモデルを返した。14Bのモデル登録と取得済みサイズを確認でき、共有ログでも14Bの生成計測が成功しているため、ダウンロード完了・推論実行済みとして追記する。

```text
NAME         ID              SIZE
qwen3:14b    bdbd181c33f2    9.3 GB
qwen3:8b     500a1f067a9f    5.2 GB
```

日本語応答の成功と14Bの100% GPU動作は、今回提示されたベンチマーク出力だけでは確認できない。スクリプトは日本語のプロンプトを送るが、生成本文を表示しない。更新時の`ollama ps`も見出しのみでロード中モデルはなかった。応答本文と、14Bの`PROCESSOR`列が`100% GPU`になっている出力を確認するまで、この2点は確認保留とする。

### 測定条件

公開用にコピーした[benchmark_ollama.py](scripts/benchmark_ollama.py)の設定は以下のとおり。

- API: `http://127.0.0.1:11434/api/generate`、非ストリーミング。
- モデル順: `qwen3:8b`、`qwen3:14b`。同じ日本語プロンプトを使用。
- プロンプト: 「Pythonで二分探索を実装し、計算量と注意点を説明してください。」
- 各モデルで1回ウォームアップ後、3回測定。測定終了後に`ollama stop`で対象モデルをアンロード。
- `think=False`、`num_ctx=4096`、`num_predict=256`、`temperature=0`、`seed=42`、`keep_alive="10m"`。
- 生成速度: `eval_count / (eval_duration / 1e9)`。総時間: API応答の`total_duration / 1e9`。平均は各3回の算術平均。

`num_predict=256`は生成上限の設定であり、実際の生成トークン数は提示ログにない。平均値はスクリプトが丸め前の値から計算して表示した値を採用する。各回の表示値は丸め済みのため、表示値を再平均しても同じ値にならない場合がある。

### 比較表

| 項目 | Qwen3 8B | Qwen3 14B |
|---|---:|---:|
| 測定1の生成速度 | 130.4 tokens/s | 78.5 tokens/s |
| 測定2の生成速度 | 131.0 tokens/s | 78.9 tokens/s |
| 測定3の生成速度 | 131.4 tokens/s | 79.4 tokens/s |
| 平均生成速度（ログ表示値） | **131.0 tokens/s** | **78.9 tokens/s** |
| 測定1の総時間 | 2.01秒 | 3.32秒 |
| 測定2の総時間 | 2.01秒 | 3.26秒 |
| 測定3の総時間 | 1.96秒 | 3.24秒 |
| 平均総時間（ログ表示値） | **1.99秒** | **3.28秒** |

この測定の表示平均値から計算すると、8Bの生成速度は14Bの約1.66倍（131.0 / 78.9）。応答品質、実生成トークン数、各回のロード時間、VRAM使用量、GPU割当の測定値はこのログに含まれず、速度から推定しない。

### 共有された実測ログ

```text
=== qwen3:8b ===
ウォームアップ完了
測定1: 130.4 tokens/s, 2.01秒
測定2: 131.0 tokens/s, 2.01秒
測定3: 131.4 tokens/s, 1.96秒
平均生成速度: 131.0 tokens/s
平均総時間: 1.99秒

=== qwen3:14b ===
ウォームアップ完了
測定1: 78.5 tokens/s, 3.32秒
測定2: 78.9 tokens/s, 3.26秒
測定3: 79.4 tokens/s, 3.24秒
平均生成速度: 78.9 tokens/s
平均総時間: 3.28秒
```

### 今後の確認

- 日本語応答本文と14Bの`100% GPU`を示すログを確認し、確認保留項目を更新する。
- 8B/14Bの応答品質や、別のコンテキスト長・同時常駐条件を比較する場合は別測定として記録する。
- 初期記録のバックアップ、暗号化調査、CPU/GPUの継続負荷試験は未完了として保持する。

---

**関連ファイル**: `codex_push_prompt.md` — Codexにこのレポートを点検させ、既存GitHubリポジトリに安全にcommit/pushする指示。
