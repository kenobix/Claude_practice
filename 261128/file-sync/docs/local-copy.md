# Phase 1のローカルコピーと復旧

`file-sync scan` は通常ファイルの相対パス、サイズ、SHA-256をパスのバイト順で返す。`copy` は同じボリュームのAからBへ、試験ドライバが指定した対象版（revision）でファイルを反映する。Aは読み取り専用で扱い、Bにしかないファイルは保持する。ハブ経由の反映でも同じ処理を使う。登録済み、または登録途中のルートを `copy` と `recover` の反映先に指定すると拒否する。 [Phase 2の同期](hub-sync.md)

## 利用条件

- LinuxまたはWSL2のLinuxファイルシステムを使う。実装はext系、XFS、Btrfsのファイルシステム種別を受け付ける。Phase 1のローカル確認はWSL2のext4で行った。コピーの開始から終了まで、復旧時も含め両ルートの外部編集を止める。
- `-volume` に信頼できるディレクトリを絶対パスで指定し、`-source`、`-destination`、`-root` はそこからの相対パスで指定する。ルートは実行ユーザーが所有し、AとBは重複しない。ユーザーパスの規則は[設計書](design.md#パスとファイルの境界)に従う。
- ボリュームを開いた後は `openat2` の `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV` を使う。通常ファイルかつリンク数1とFDで確認してから、検証済みのprocfs経由で同じFDの実体を開く。作成・置換・一時ファイルの除去は親ディレクトリのFDを使う。必須のAPIが使えなければエラーで停止する。
- ルートと管理ディレクトリ、管理用ロックファイルには `flock` を使う。別の実行が排他を持っている場合は停止する。排他を取らない外部プロセスが同時に更新すると、最後の照合からrenameまでの間にも状態が変わり得る。
- 管理領域は所有者、同一マウント、ディレクトリの0700、ファイルの0600とリンク数、名前・種類を検査してから走査の集計対象から外す。初期化には `init` が必要。既存の管理領域や状態DBの破損、消失を検出したら、データを保持して停止する。`init` が途中で止まって管理領域が残った場合も上書きを拒否する。

## ビルドと人工データでの実行

[固定した環境](toolchain.md#goと依存の再現)のGo 1.27.4をPATHに置き、`261128/file-sync/` で実行する。以下ではコピー先を新しい一時ディレクトリに作る。

```bash
export CGO_ENABLED=1 GOTOOLCHAIN=local GOWORK=off
go mod download all
go mod verify
go vet -mod=readonly ./...
go test -mod=readonly -v -count=1 -timeout=4m ./...
go test -mod=readonly -race -count=1 -timeout=4m ./...
test -z "$(gofmt -l cmd internal)"

demo=$(mktemp -d)
mkdir "$demo/A" "$demo/B"
printf abc > "$demo/A/notes.txt"
go build -mod=readonly -o "$demo/file-sync" ./cmd/file-sync
"$demo/file-sync" scan -volume "$demo" -root A
"$demo/file-sync" init -volume "$demo" -root B
"$demo/file-sync" copy -volume "$demo" -source A -destination B -revision 1
"$demo/file-sync" scan -volume "$demo" -root B
"$demo/file-sync" status -volume "$demo" -root B
```

`copy -path notes.txt` は選んだ1件を反映する。指定を省くとAにある全ファイルを対象にする。どちらの場合もAとBを全走査し、入力の不備、構造衝突、ファイル数やディレクトリ数の上限超過があれば新しい反映を始めない。Bで未知の本文や基準版から変わった本文を見つけた場合は、そのパスの置換を保留する。初回追加は対象版1を指定し、それ以降は基準版より大きい版を指定する。指定した対象版とA・Bの本文が保存済みの基準に一致すれば、Bを変更せずに終了する。

`recover -volume "$demo" -source A -destination B` は、同じコピー元を使って保存済みの予定だけを照合する。`copy` も新しい予定を作る前に同じ復旧を試みる。`status` はBの基準版と予定をJSONで返す。正常終了は終了コード0。本文不一致やI/Oエラーは1、競合保留は3。構造衝突は通常の反映を始める前に診断を返す。

## 反映予定と復旧

SQLiteにパス、コピー元、変更前の本文とサイズ、基準版、対象版と本文、予定IDを保存する。作業名と予約は予定から求める。候補は `<予定ID>.incoming`、元の本文の退避は `<予定ID>.before`、競合で観測したBの本文は `<予定ID>.local`。いずれもBの `.file-sync/stage/` に置く。基準版と完了済み・拒否済み・保留中を含む予定は保持する。予定は4,096件、管理コピーは予約込みで4,096件・1,920 MiB、DBなどの管理枠は128 MiBが上限。

1. 排他を確保し、AとBを走査して対象を決める。候補と必要な退避の容量を予約し、予定をSQLiteでコミットする。
2. 検証済みのAのFDから候補を一時ファイルに書き、実際の本文のサイズとSHA-256を照合する。次にBの現行ファイルを確認する。
3. Bの本文を置き換える場合は、変更前の内容と一致した本文を退避して同期する。候補を同期して閉じ、置換直前にBの本文とFDの識別情報を確認する。
4. 一致したときだけ、同一マウント内でrenameする。新規設置は `RENAME_NOREPLACE` を使う。新設した親の名前、移動先の親、作業ファイルの親をそれぞれ同期する。
5. 同じSQLiteトランザクションで基準版の更新と予定の完了を確定する。完了を確認した後で退避を照合し、回収する。

| 再開時の状態 | 次の処理 |
| --- | --- |
| Bが変更前の内容 | 完成済み候補を再利用する。不完全な候補は、Aから対象と同じ内容を取得でき、途中までのバイトがその先頭と一致するときだけ全量を書き直す。 |
| Bが対象の内容 | 必要な退避を照合し、Bと親ディレクトリを再同期して基準版と予定を確定する。 |
| Bの本文や有無が両方の想定から外れた | Bを置き換えずに競合として記録する。確保できた候補を保持し、観測したBの本文があれば別の管理コピーに残す。競合中の自動反映は停止する。 |
| hash不一致、読み取り不能、結果不明のI/Oエラー | 元パスと保存済みデータを保持して停止する。結果不明の予定は再起動後の照合まで保留する。書き込み済み候補のhash不一致で拒否した予定は、候補とともに残す。 |
| 予定が完了済みで退避が残存 | 退避のサイズとhashを照合し、予定に記録した本文と一致した場合だけ回収する。 |

Phase 0の反映手順では、通常置換に退避の規定がなかった。Phase 1では、rename後からSQLite確定までにI/Oエラーが起きても旧本文を確保するため、置換時の退避を追加した。追加で予約する容量は旧本文1件分で、その枠が不足した場合は置換前に停止する。P1-02ではrename後と基準版の確定直後も停止点に加え、退避の残存と復旧後の回収を確認する。P1-03では置換直前の外部変更で、確認済みの旧本文と競合候補が残ることも確かめる。現在の[設計書](design.md#クライアント側の反映と削除)にこの順序を反映した。

SQLiteの単一接続とWALの設定は[技術選定と再現手順](toolchain.md#sqliteの設定確認)を参照。SQLiteが必要とするDBパスには排他中の0700のディレクトリFDを起点として、検証済みの固定名を渡す。同期ファイル名はSQLiteのDSNに含めない。

## OS試料の再現条件

通常試験では端末上の一時領域に人工データとUnixソケットを作る。デバイスとbind mountは通常のCIでスキップする。別の非特権ユーザー名前空間と非共有のマウント名前空間を作れるLinuxで、次の追加試験を実行できる。デバイス作成やbind mountがカーネルに拒否された試行はスキップとして記録する。

```bash
trial=$(mktemp -d)
CGO_ENABLED=1 go test -mod=readonly -c -o "$trial/file-sync.test" ./internal/local
(
  cd internal/local
  unshare --user --map-root-user --mount --propagation private \
    env FILE_SYNC_ISOLATED_TESTS=1 "$trial/file-sync.test" \
    -test.v -test.run '^TestP1_05_IsolatedMountAndDevice$'
)
```

SIGKILL試験の判定範囲はLinuxプロセスの停止と再起動まで。実機電源断と、SQLite管理枠の最大負荷における検証は[受入条件](acceptance.md)で別に扱う。
