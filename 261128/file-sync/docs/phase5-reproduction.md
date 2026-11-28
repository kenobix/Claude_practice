# 実演と復旧の再現

LinuxまたはWSL2のLinuxファイルシステムを使う。 `261128/file-sync` で [固定環境](toolchain.md#goと依存の再現) を用意し、次のコマンドを実行する。Pythonは標準ライブラリだけを使う。 `TMPDIR` を指定する場合は、ローカルのLinuxファイルシステム上に置く。

```bash
export CGO_ENABLED=1 GOTOOLCHAIN=local GOWORK=off
go mod download all
go mod verify
result=$(mktemp -d)
product=4a3ef75e26a19e54b7848cc3528021e27450b043
python3 -B tools/verify_mvp.py demo --product-sha "$product" --output "$result/demo.json"
```

出力先は未作成の名前を指定する。スクリプトは対象SHAに対し `cmd`、`internal`、モジュール2ファイルと固定fixtureの差分を確認し、Goと同梱SQLiteのバージョンを照合する。 `--product-sha` を省略すると現在のHEADの製品ファイルを照合する。実行時は同じユーザー権限で `127.0.0.1` の空きポートを使い、ハブが表示した `listening` を待つ。クライアントA・Bをそれぞれ独立したCLIプロセスとして起動する。試行ごとに新規トークンを子プロセスの環境だけに渡す。出力には操作ID、世代ID、照合した人工データを含み、トークンやPC上の保存先は含めない。

| 実演の段階 | 観測と判定 |
| --- | --- |
| 正常同期 | AのPとEをseedし、Bがpull。A・B・ハブのファイルとSHA-256、A・Bの基準版1、ハブの版1と受理各1件を確認。Eは0バイトの本文とhashを持つ。Bの初回受信前の不存在および未登録パスには本文も行もない。 |
| 編集競合の検出 | 共通基準P/1でAにQ、BにRを書く。Aだけが受理され、Bは終了コード3。ハブQ/2、B元パスR、B基準P/1、両候補の管理コピーQ・Rの本文とSHA-256を確認。Pは旧blobに残る。 `notes.txt` の受理はAの作成・更新の2件で、Eの受理は別に1件。 |
| 手動解決後 | Bの `conflicts` が表示したハブ世代と最新候補の版2を使い、 `resolve --choose local` でRを選ぶ。Aが受信してA・B・ハブはR/3。Bの選択が受理1件となり、競合は解決済みになる。Q・Rの管理コピーとハブの旧blobを保持する。 |
| 削除と復帰 | 新しい試行で全員P/1から開始。Bを同期せず、Aの削除を送ってハブをtombstone/2に進める。この時点のBはP/1。復帰後はA・Bの元パスが不存在、基準版2。Bの `.trash` とハブblobのPを照合する。A・Bの再同期後も本文は復活せず、当該パスの受理はAの2件のまま。 |

本文を読み出す検査、CLIの `status` と `conflicts`、ハブDBの読取専用SQLを併用する。SQLでは `entries` と `receipts` を読み、期待したパス、版、本文hash、結果、操作IDの対応を照合する。初回登録で実行する手入力のコマンドは [ハブの起動と同期](hub-sync.md#人工データでの起動と同期)、解決と削除の操作は [競合・削除の手順](conflicts.md) にある。

## 応答消失と旧世代拒否

同じディレクトリで以下を実行する。Goの試験が使い捨て領域とlocalhostに環境を作り、それぞれ検証結果を出力する。

```bash
go test -mod=readonly -count=1 -v -timeout=2m \
  -run '^(TestP4_02_ChangeSIGKILL|TestP4_04_RepeatOldReceiptAfterNewHead|TestP4_12_RestoreOldGeneration)$' \
  ./internal/hub
```

- P4-02はコミット直前と直後にハブからパイプへの到達通知を受けてSIGKILLする。直後の試行ではAがQを送信待ちに残し、基準P/1のまま。ハブはQ/2を受理済み。Aを再実行すると同じ操作IDでQ/2を確認し、当該操作の受理は1件、BはP/1のまま。
- P4-04は受理結果が不明な間に別の操作でハブをR/3へ進める。同じIDの再送が元のQ/2の受理を返し、取得した最新のmanifestで最終的にR/3に揃うことを確認する。
- P4-12はP/1のバックアップから、新世代で別のハブを起動する。正しく認証した旧世代要求は `409 generation_mismatch`。旧ハブとBにはQ/2が残り、AのRと旧送信待ちは明示再登録時に保全される。新世代の受理は0件。

[停止点と保持データ](phase4-verification.md)・[CLIでの正式なバックアップ・復元](backup-restore.md)

## クリーンな環境と回帰試験

Linux x86-64では次の手順でDraft PR #13の担当ブランチをcloneし、Goとキャッシュを同じ一時領域に用意できる。Goアーカイブ、Goモジュール、リポジトリの取得にはネットワーク接続を使う。Cコンパイラ、Git、curl、tar、sha256sum、Python 3は導入済みとする。すでに固定Goがある場合はアーカイブの取得と展開を省略できる。

```bash
(
set -e
scratch=$(mktemp -d)
git clone --single-branch --branch feat/file-sync \
  https://github.com/kenobix/Claude_practice.git "$scratch/source"
git -C "$scratch/source" rev-parse HEAD
curl -fL https://go.dev/dl/go1.27.4.linux-amd64.tar.gz -o "$scratch/go.tar.gz"
printf '%s  %s\n' 7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840 "$scratch/go.tar.gz" | sha256sum -c -
tar -C "$scratch" -xzf "$scratch/go.tar.gz"
export PATH="$scratch/go/bin:$PATH"
export GOCACHE="$scratch/build" GOMODCACHE="$scratch/modules"
export CGO_ENABLED=1 GOTOOLCHAIN=local GOWORK=off
cd "$scratch/source/261128/file-sync"
go mod download all
go mod verify
result=$(mktemp -d)
python3 -B tools/verify_mvp.py demo --output "$result/demo.json"
)
```

レビューで特定のHEADを再現する場合は、clone後にそのSHAをチェックアウトしてから実行する。通常・raceの試験と任意の隔離・大容量試験の前提条件は別資料に記載した。通常・raceの葉テストの集計は受入記録のコマンドで再現できる。 [Phase 4の再現手順](phase4-verification.md#再現手順)・[集計手順](acceptance.md#葉テストの集計)・[Draft PR #13](https://github.com/kenobix/Claude_practice/pull/13)
