# 技術選定と再現手順

Phase 0の確認環境は Ubuntu 24.04.4 LTS、WSL2 Linux 6.6.87.2、x86-64、ext4、Git 2.43.0、GCC 13.3.0、Python 3.12.3。着手時はGoとSQLite CLIがPATHになかった。Pythonの `sqlite3` は3.45.1。選定したGoはアーカイブから一時領域へ展開し、Go・SQLite・既存のシェル設定のシステム更新は行っていない。

| 使用箇所 | 固定バージョンと選定理由 |
| --- | --- |
| CLI、HTTP、SHA-256 | Go **1.27.4**。標準の `net/http`、`crypto/sha256`、`database/sql` を使う。LinuxとWSLの利用条件で動作を確認し、`go.mod` と実行手順で版を固定する。 [Goリリース履歴](https://go.dev/doc/devel/release#go1.27.0)・[Goアーカイブ](https://go.dev/dl/#go1.27.4) |
| メタデータ | `github.com/mattn/go-sqlite3` **v1.14.52**、同梱SQLite **3.53.4**。Cコンパイラが必要。 `libsqlite3` ビルドタグでシステム版に切り替えず、SQLiteの版差を減らす。SQLite WAL-reset修正を含む版を使用する。 [固定タグ](https://github.com/mattn/go-sqlite3/tree/v1.14.52)・[同梱ヘッダー](https://raw.githubusercontent.com/mattn/go-sqlite3/v1.14.52/sqlite3-binding.h)・[SQLite WAL](https://sqlite.org/wal.html#the_wal_reset_bug) |
| ルート制限付きファイル操作 | `golang.org/x/sys` **v0.45.0** の `unix`。 `Openat2`、`Fstat`、`Renameat2`、`Unlinkat`、`Flock` を使い、CgoはSQLiteのビルドに限定する。 `os.Root` のルート内symlinkとマウント越えの扱いを確認し、全リンク拒否にはLinux側の制約を採用する。 [固定タグのLinux API](https://github.com/golang/sys/blob/v0.45.0/unix/syscall_linux.go)・[Goのos.Root](https://raw.githubusercontent.com/golang/go/go1.27.4/src/os/root.go) |

## Goと依存の再現

下記はリポジトリ直下からLinux x86-64で実行する。既存Goが1.27.4ならダウンロードを省いてその実行ファイルを指定できる。利用できるCコンパイラがない環境では、Cgoを有効にするために別途コンパイラの準備が要る。SQLite CLIは不要。リポジトリの `go.sum` と公開Goモジュールチェックサムデータベースを使う。

```bash
(
set -e
tool_dir=$(mktemp -d)
curl -fL https://go.dev/dl/go1.27.4.linux-amd64.tar.gz -o "$tool_dir/go.tar.gz"
printf '%s  %s\n' 7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840 "$tool_dir/go.tar.gz" | sha256sum -c -
tar -C "$tool_dir" -xzf "$tool_dir/go.tar.gz"
cd 261128/file-sync
export GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=1
export GOCACHE="$tool_dir/build" GOMODCACHE="$tool_dir/modules"
"$tool_dir/go/bin/go" version
"$tool_dir/go/bin/go" mod download all
"$tool_dir/go/bin/go" mod verify
)
```

SHA-256の照合に失敗したら展開前に中止する。Goの表示は `go version go1.27.4 linux/amd64`。同じGoとCgoの設定で [Phase 1のビルドと試験](local-copy.md#ビルドと人工データでの実行) を行う。

## SQLiteの設定確認

Phase 1の端末DBはDSNの `_synchronous=FULL&_foreign_keys=on` と、単一物理接続上のPRAGMAで [設計の設定](design.md#容量と満杯時の扱い) を適用する。Phase 0では、ハブDBと端末DBのDSNに `_journal_mode=WAL` を指定する方針だった。Phase 1の端末DBでは、ページ長4,096を設定してから、初期化時に限りPRAGMAでWALへ移行する。既存DBを開くときはWALを含む全設定を読み返し、モードの不一致を検出したら停止する。Phase 1のSQLite設定試験でバージョン・設定・チェックポイントを照合した。

`SELECT sqlite_version()` が `3.53.4`、 `PRAGMA journal_mode` が `wal`、 `PRAGMA synchronous` が `2`、 `PRAGMA foreign_keys` が `1` を返すことを確認する。 `PRAGMA integrity_check` は `ok`、 `PRAGMA foreign_key_check` は0行が成功条件。 `PRAGMA wal_checkpoint(TRUNCATE)` のbusyが0で、書込み前のWALが0バイトであることも確認する。PRAGMAの設定に成功したかは、設定文の終了だけでなく実際の値を読み返して判断する。 [ドライバの設定実装](https://raw.githubusercontent.com/mattn/go-sqlite3/v1.14.52/sqlite3.go)・[SQLite PRAGMA](https://sqlite.org/pragma.html)

Phase 0では一時プログラムをコンパイルし、実際に開いたSQLiteで上記のバージョン・主要PRAGMAを確認した。WALのチェックポイントではbusy=0とWAL長0を観測した。同じ環境でLinuxのFDを使ったsymlink拒否（ELOOP）、hard linkとFIFOの識別、renameat2と親のfsyncも確認した。停止点、容量上限時のSQLiteの振る舞い、電源断はこの確認の範囲外。

## 人工入力の作成

[固定した人工データ](../fixtures/expected.json) からA・Bの初期フォルダを新しい一時ディレクトリへ作る。 `261128/file-sync/` で実行すると、Aに3バイトの `notes.txt` と0バイトの `empty.txt` が入り、Bにはファイルがない。追加の編集候補Q・Rと境界値X・M・Nは、同じJSONのhex列を指定回数繰り返して作れる。

```bash
python3 - <<'PY'
import hashlib
import json
import tempfile
from pathlib import Path

fixture = json.loads(Path("fixtures/expected.json").read_text(encoding="utf-8"))
roots = Path(tempfile.mkdtemp(prefix="file-sync-demo-"))
for client, files in fixture["demo"].items():
    directory = roots / client
    directory.mkdir()
    for name, content_id in files.items():
        content = fixture["contents"][content_id]
        data = bytes.fromhex(content["unit_hex"]) * content["repeat"]
        assert len(data) == content["size"]
        assert hashlib.sha256(data).hexdigest() == content["sha256"]
        (directory / name).write_bytes(data)
print(roots)
PY
```
