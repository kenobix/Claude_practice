# Phase 4の障害・容量試験

## 再現手順

`261128/file-sync` で固定のGo 1.27.4、`CGO_ENABLED=1`、SQLite 3.53.4同梱の `mattn/go-sqlite3 v1.14.52`、`golang.org/x/sys v0.45.0` を使う。試験はLinuxまたはWSL2のLinuxファイルシステムとlocalhostを使い、`testing.T.TempDir` に人工データを作る。対象のコミットは実行前に `git rev-parse HEAD` で確認する。 [固定環境とチェックサム](toolchain.md)

```bash
export GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=1
go mod verify
gofmt -l cmd internal
go vet -mod=readonly ./...
go build -mod=readonly -o /tmp/file-sync ./cmd/file-sync
go test -mod=readonly -count=1 -timeout=4m -v ./...
go test -mod=readonly -race -count=1 -timeout=4m ./...
```

ハブの1,920 MiBを実ファイルで埋める3試行だけは別に実行する。保存場所の空きを先に確認し、3 GiB未満では試料を作らず失敗を返す。標準CIは `FILE_SYNC_TEST_CAPACITY` を指定せず、デバイス・マウント用の隔離試験も既定では実行しない。件数上限、SQLiteの大きなトランザクション、再登録の上限、端末の論理長試験は標準CIに含める。子プロセス用テストを単独で呼んだときのスキップは、停止境界を検証する親試験の実行とは別に数える。

```bash
df -B1 "${TMPDIR:-/tmp}"
FILE_SYNC_TEST_CAPACITY=1 go test -mod=readonly -count=1 -v -timeout=4m \
  -run '^TestP4_10_ActualBlobBytes$' ./internal/hub
```

固定長の試験では有効な既存行とblobをまとめて準備した後、再Openで全blobの長さ・hash、DBの整合性と外部キーを照合した。境界の受理・拒否、同時要求、取得と再送には実際のHTTPを使った。試料のP、Q、Rなどの値は製品出力から決めず、[固定fixture](../fixtures/expected.json)を使った。

## 実際に停止させた境界

各子プロセスは処理に到達するとパイプで通知し、親からの入力を待つ。その時点で親がSIGKILLを送る。親は終了シグナルを確認し、停止後のSQLiteとファイルを検査した。通知の待ち時間は試験異常の検出に使い、停止点の推定には使わない。表で版に併記したP、Q、Rは固定の本文・長さ・SHA-256を指す。

### ハブ

| 試験・境界 | 停止直後と再開後の観測 |
| --- | --- |
| P4-01 `blob-bytes-written` | Qの先頭2バイトのみ `.part` に存在。Q行・完成名なし。再Openで一時ファイルを回収し、同じPUTに201。 |
| P4-01 `blob-verified`、`before-blob-sync`、`blob-synced`、`before-blob-close`、`blob-closed`、`before-blob-rename` | Q全5バイトは `.part`、行と完成名はなし。再Openで回収し、再PUTは201。 |
| P4-01 `blob-renamed`、`before-blob-directory-sync`、`blob-directory-synced`、`before-blob-commit` | Q完成名あり、DB行なし。元と先が同じ1つのblobディレクトリなので、その同期を前後で試した。再Openで照合・同期・登録し、再PUTは200。 |
| P4-01 `blob-committed`、`before-blob-response` | Q完成名・行あり。再Open後に同一PUTは200。 |
| P4-02 `before-change-commit` | HTTP応答なし。DBはP/1、追加受理0件。AはQ、基準P/1、送信待ちa2が1件。 |
| P4-02 `change-committed` | HTTP応答なし。DBはQ/2、a2の受理1件。Aの基準と送信待ちは直前試行と同じ。 |
| P4-11 固定ページ上限付近でPOSTコミット前後 | 最大長1,024バイトのパスに対するno-opを使った。前は履歴が増えず、後は1件増えた。いずれも同じ要求の再送で版1・履歴差分1件になった。 |

P4-01の全試行で通常再起動は世代を維持し、P/1、Pのblob、受理1件、Aの元パスQ、BのP/1を保持した。Qは最終的に実体と登録が各1件で、未完成の参照は0件だった。P4-02はAが元の送信待ちを再送し、Q/2、追加受理a2の1件、BのP/1に収束した。停止した要求の応答は失われたため、再送前のクライアントからは結果不明と判定した。 [子プロセス試験](../internal/hub/phase4_stops_test.go)・[SQLite上限と停止](../internal/hub/phase4_sqlite_test.go)

### 受信側

| 試験・境界 | Bの停止直後 |
| --- | --- |
| P4-05 `plan-committed` | P・基準P/1、Q予定が未完了。 |
| P4-05 `incoming-two-bytes`、`incoming-verified` | P・基準P/1。候補は `he` の2バイト、または検査済みQの5バイト。 |
| P4-05 `before-incoming-sync-close`、`incoming-synced-before-close`、`incoming-synced-closed`、`before-rename` | P・基準P/1。置換用の `.before` にP。 |
| P4-05 `renamed`、`before-destination-directory-sync`、`destination-directory-synced`、`before-stage-directory-sync`、`stage-directory-synced`、`before-baseline-commit` | ファイルQ・基準P/1、予定未完了、退避P。置換先とステージの親を個別に確認した。 |
| P4-05 `baseline-committed` | ファイルQ・基準Q/2、予定完了。完了後の整理前のため退避Pもある。 |
| P4-05 `renamed` 後にRを保存 | 復旧でRを元パスに維持し、基準P/1、予定は競合。Q・Rの競合候補と退避Pを照合した。 |
| P4-06 `plan-committed` | P・基準P/1、未完了。退避なし。 |
| P4-06 `before-trash-sync-close`、`trash-synced-before-close`、`trash-synced-closed`、`before-trash-directory-sync`、`trash-directory-synced`、`before-delete-backed-up-commit` | P・基準P/1、退避の実体P、DBの退避済みフラグは未確定。 |
| P4-06 `delete-backed-up`、`before-unlink` | P・基準P/1、退避P・退避済みフラグ確定。 |
| P4-06 `unlinked`、`before-delete-directory-sync`、`delete-directory-synced`、`before-baseline-commit` | ファイル不存在・基準P/1。退避Pあり、予定未完了。 |
| P4-06 `baseline-committed` | ファイル不存在・基準tombstone/2。退避Pを保持し、予定完了。 |
| P4-06 `unlinked` 後に退避欠落・破損 | 再Open後も不存在・基準P/1。退避の照合に失敗して予定未完了。破損した試行は退避の誤った本文も保持した。 |

P4-05の通常試行ではQ/2へ収束し、完了後に `.before` を照合して整理した。P4-06の通常試行では退避Pを照合してtombstone/2へ収束し、退避を残した。Bの通常・保留の各試行で、ハブの世代と対象版、操作ID、履歴2件は維持された。 [端末のSIGKILL試験](../internal/local/phase4_test.go)

### バックアップと復元

P4-12ではバックアップ準備開始 `preparing`、公開前 `before-publish`、公開済み `published` で停止した。復元はコピー済み・世代確定前 `copied-before-generation`、世代確定済み・公開前 `generation-committed`、公開済み `published` で停止した。公開前は正式名がなく、復元途中の保存先ではハブを起動できなかった。公開済みのバックアップは復元でき、公開済みの復元ハブは新世代でOpenできた。停止前の元ハブはP/1を維持した。

別の試行ではP/1のGからQ/2まで進めてAのRを送信待ちにし、GのバックアップをG2へ復元した。新しいトークンで認証した旧世代の要求は、受理済みかどうかと期待版にかかわらず409。変更前のトークンは401。A・Bの旧世代の通常同期は保留となった。明示再登録後はAのR、新世代のP、旧送信待ちと旧DBを保全した。BのQ/2と旧ハブのQ/2はそのまま残り、G2の受理は0だった。以前のrevisionで参照したblob、未参照blob、受理記録を別試行で照合した。DBとblobとmanifestの破損、外部キー不整合、既存の保存先、異なる端末IDを拒否し、元データを維持した。 [復元試験](../internal/hub/phase4_backup_test.go)

## 固定容量とSQLiteの論理長

WSL2のLinux 6.6.87.2、ext4の使い捨て領域で測定した。1,920 MiBを埋める試験の実行前には約965 GBの空きがあった。単位はバイト。blobの物理量はファイルの `st_blocks × 512` の合計で、ディレクトリやファイルシステムの管理領域を含まない。端末の枠は疎ファイルも論理長で計上する。

| Mを追加する時点の残り | 追加候補の予約長 | 追加前のblob論理長 | 追加後のblob論理長 | blobの物理量 | HTTP |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10,485,759 | 10,485,760 | 2,002,780,161 | 2,002,780,161 | 2,002,784,256 | 507 |
| 10,485,760 | 10,485,760 | 2,002,780,160 | 2,013,265,920 | 2,013,270,016 | 201 |
| 10,485,761 | 10,485,760 | 2,002,780,159 | 2,013,265,919 | 2,013,270,016 | 201 |

上限ちょうどのblobと大きなSQLiteを同時に置いた試験で、ハブの論理長の最大観測は2,045,492,432バイトだった。blob枠が残り1件の同時PUTでは、4,095件の既存実体16,379バイトに対し、先行PUTが5バイトを予約した。到達通知時の一時ファイルは2バイトだった。先行接続を切断するとその予約と一時ファイルを回収し、後続のRを登録した。 [準備と境界試験](../internal/hub/phase4_limits_test.go)

| SQLiteまたは管理状態 | DB本体 | WAL | SHM | その他 | 旧状態または準備中 | 観測 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ハブ、最大長パスの一括コミット直後 | 69,632 | 29,602,232 | 65,536 | 32 | 0 | WAL最大。コミット直前のWALは0。 |
| ハブ、固定ページ上限での拒否と再Open | 33,550,336 | 0 | 32,768 | 32 | 0 | DB本体最大。この入力では受理2,563件で507 `storage_full`。既存のGET・再送は成功。 |
| 端末、4,096件の予定を一括コミット直後 | 110,592 | 5,833,952 | 32,768 | 32 | 0 | 各予定のパスは1,024バイト。再Open時のDBは5,898,240バイト。次の予定は保留。 |
| 端末、60 MiBの旧状態と大きなDB・WALの同時観測 | 31,596,544 | 31,678,712 | 65,536 | 32 | 62,914,560 | コミット直後の合計126,255,384。直前はWALが0で、閉じて再Openした後もP/1。 |
| 再登録、準備中DBのコミット直後 | 110,592 | 0 | 0 | 32 | 159,904 | 準備中の状態は16個目のディレクトリにあり、この列はそのDBと補助ファイルの和。 |
| 再登録、切替直後 | 110,592 | 0 | 0 | 32 | 110,624 | 旧DB・識別子を保持。17個目の準備は拒否。 |

ハブのSQLiteの実測では、本体最大、WAL最大、SHM最大の時点が異なる。端末では固定128 MiBの総枠に旧状態と準備中のDBを含め、60 MiBちょうどの旧状態を読み込めること、そこからの準備と60 MiBを1バイト超える状態を拒否することを確認した。旧状態60 MiB、試験用に膨らませたロック68 MiB、現用DBを組み合わせた。Open前の容量検査は128 MiB超過を拒否し、元データを保持した。 [端末の計測](../internal/local/phase4_metadata_test.go)・[ハブの計測](../internal/hub/phase4_sqlite_test.go)

端末の管理コピーでは3つの不足条件を試した。受信枠が0バイト、置換候補には5バイトを確保できるがPの退避には不足、削除退避に1バイト不足のいずれも保留となった。試験用の疎ファイルの論理長はそれぞれ2,013,265,920、2,013,265,915、2,013,265,918バイト。いずれも元パスPと基準1を維持した。同時変更の試行は4,095個の空ファイルから開始し、最後の枠にRの5バイトを保存して4,096個になった。Q側の保存は保留され、ハブ側Q/2とローカルRを保持した。

## 障害注入と保証範囲

P4-07では書込みと同期の境界から意図的に `ENOSPC` と `EIO` を返した。実際のディスク枯渇やOSのデバイス障害は起こしていない。HTTPの中断は実際の接続を閉じ、停止試験は実際のプロセスをSIGKILLした。DBが旧版・旧履歴または新版・新履歴の組になることを照合し、再Openが失敗する状態で成功扱いしないことを確認した。

固定したSQLite、単一接続、WAL/FULL、チェックポイント、cache spill無効と、協調的に外部編集を停止したLinuxファイルシステムが対象。実機電源断、管理領域の非協調編集による無損失、正式手順外のDB巻き戻しは保証範囲外。デバイス試料は隔離環境で作成が `EPERM` だったため未実施を引き継いだ。 [適用条件と受入条件](acceptance.md#phase-4の実測結果)
