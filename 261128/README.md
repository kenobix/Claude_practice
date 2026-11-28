# ファイル同期ソフト

2つの作業フォルダでファイルの作成・更新・削除を同期する。通信途絶や同時編集が起きたとき、どの版が受理され、元の内容がどこに残るかを確認できる。ハブと2クライアントは同じマシンで動かす。

[静的な紹介ページの公開予定先](https://kenobix.github.io/Claude_practice/261128/)・[日本語字幕付きデモ（4分）](demo/file-sync.mp4)・[台本・再生成](demo/storyboard.md)

## 構成

```text
作業フォルダ A                                      作業フォルダ B
      ↕                                                   ↕
クライアント A  ←── HTTP 127.0.0.1 ──→  ハブ  ←──→  クライアント B
  SQLiteの基準・反映予定                  │             SQLiteの基準・反映予定
                                         ├─ SQLiteの版・受理履歴
                                         └─ SHA-256別の本文（blob）
```

Go 1.27.4でCLIとHTTPサーバーをビルドする。メタデータは `mattn/go-sqlite3 v1.14.52` 同梱のSQLite 3.53.4に保存し、WAL・`synchronous=FULL` を使用する。LinuxのFDを使ったルート内操作には `golang.org/x/sys v0.45.0` を使う。 [固定環境と取得手順](file-sync/docs/toolchain.md)

## 最短の再現手順

LinuxまたはWSL2のLinuxファイルシステム上で、Go 1.27.4、Cコンパイラ、GitとPython 3を使う。手元の環境にGoがなければ、チェックサムを照合して一時領域に展開できる。 [cloneからの再現](file-sync/docs/phase5-reproduction.md#クリーンな環境と回帰試験)

```bash
cd 261128/file-sync
export GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=1
go mod download all
go mod verify
result=$(mktemp -d)
python3 -B tools/verify_mvp.py demo --output "$result/demo.json"
```

スクリプトがCLIをビルドし、一時領域でハブとA・Bを別プロセスとして起動する。Aの初回登録からBの受信、編集競合の保留と手動解決、別の一時領域でオフライン中の削除を確認する。終了時に実行用フォルダを片付け、照合結果のJSONを残す。端末ごとにコマンドを入力する場合は、起動後に `register --seed`、`register --pull`、`sync-once`、`status` の順に試せる。 [CLIでの起動](file-sync/docs/hub-sync.md#人工データでの起動と同期)・[競合と削除の操作](file-sync/docs/conflicts.md)・[照合する内容](file-sync/docs/phase5-reproduction.md)

## 受理と復旧の設計

| 方式 | 採用した理由と運用上の扱い |
| --- | --- |
| 中央ハブ | 1か所でパスの版と更新順を決める。ハブ停止中は端末が手元のファイルを保持し、通信は再開を待つ。P2Pで必要となる端末間の版の比較や伝播は対象外。 |
| ファイル単位の全量転送とSHA-256 | 変更があるファイルは全体を転送し、サイズとSHA-256を送受信側で照合する。同じ本文はハブで共有できる。ファイル内の小さな編集でも転送対象は全体。 |
| CASと受理履歴 | 更新はパスの期待版に一致した場合に受け付ける。更新と受理履歴はSQLiteで一括確定する。応答消失後は保存した操作IDと同じ要求を再送し、既に受理されていれば元の結果を得る。 |
| tombstone | 削除にも版を付け、端末の復帰時に旧本文と基準版を照合する。未編集なら退避して削除を反映し、編集済みなら競合として保留する。 |
| 両候補の保存と手動解決 | 元のパスと比較基準を保ち、利用者が保存された候補を確認して選ぶ。選択を送るときも、確認済みのハブ版に対してCASを行う。 |

受信では、反映予定を記録してから一時ファイルを照合・同期し、renameと親ディレクトリの同期を経て基準版を確定する。削除では、退避した本文と退避済みの記録を確定した後で元パスを削除する。途中停止後は実体のhashと反映予定を照合する。バックアップから正式に復元すると新しいハブ世代で起動し、旧世代の要求を拒否する。 [データ契約](file-sync/docs/protocol.md)・[状態遷移と復旧](file-sync/docs/design.md)・[バックアップと復元](file-sync/docs/backup-restore.md)

Syncthingは、接続先の端末から受け取ったインデックスを使い、必要なブロックを端末から取得するかローカルから再利用する。同時編集時に生成した競合コピーも通常ファイルとして同期する。このMVPの対象は1台の中の2フォルダで、ハブにファイル全体を保存し、競合の候補は端末の管理領域に保管して選択を待つ。 [Syncthing公式の同期方式](https://docs.syncthing.net/users/syncing.html)

## 障害試験と計測

Phase 4では、HTTP応答前後、受信のrename前後、削除の退避・unlink前後を含む境界で実際に子プロセスをSIGKILLした。コミット後に応答を失った場合も同じ操作IDは1件だけ受理された。復元後は正しく認証しても旧世代の更新がHTTP 409となった。詳細の試験コマンドは再現手順に掲載した。 [障害・容量試験](file-sync/docs/phase4-verification.md)・[応答消失と旧世代拒否の再現](file-sync/docs/phase5-reproduction.md#応答消失と旧世代拒否)

各条件を新しいハブと端末で5回計測した。AのseedからBのpullまでの中央値は、空ファイル100件で7.13秒、1,000件で71.17秒、10 MiBファイル1件で0.89秒だった。空ファイルの実体は1件にまとまり、PUT・GETの各要求数は100件の条件で100回、1,000件の条件で1,000回だった。 [計測区間、走査時間と比較表](file-sync/docs/measurements.md)・[試行別JSON](file-sync/docs/measurement-results.json)

[Phase 0〜5の受入記録](file-sync/docs/acceptance.md)・[固定fixture](file-sync/fixtures/expected.json)・[作業記録](work_log.md)・[元計画](../261014/plan_file_sync.md)

## 利用条件と上限

localhostの2クライアントと1ハブで、LinuxまたはWSL2のLinuxファイルシステムを使う。同期、登録、解決、復旧の間は利用者による外部編集を止める。GitHub Pagesは紹介資料と動画を配信する。同期を試すときは手元でCLIを実行する。

| 項目 | 上限 |
| --- | ---: |
| 1ファイル | 10 MiB |
| 生存ファイル | 1,000件 |
| ハブの全パス行 | 4,096件 |
| ハブの受理履歴 | 8,192件 |
| ハブのblob | 4,096件・1,920 MiB |
| ハブ総量 | 2 GiB |
| SQLite管理枠 | 128 MiB |

件数上限より先にSQLiteのページ上限に達する場合がある。Phase 4の最大長パスを使った試験では、2,563件の受理履歴を保持した時点で次の更新が `507 storage_full` となった。到達可能な件数は入力に依存する。DB、WAL、SHMの各最大観測値は計測時点が異なる。ファイルの論理長と物理ディスク使用量は個別に計測した。 [容量の観測値](file-sync/docs/phase4-verification.md#固定容量とsqliteの論理長)

`ENOSPC` と `EIO` は故障の注入で確認した。実際のディスク枯渇とデバイス故障は起こしていない。隔離環境のbind mountは検証済みで、デバイス試料は作成権限不足で未実施。実機電源断は受入範囲外。

正式手順外のDB巻き戻し、非協調の外部編集、履歴の自動回収、常駐監視、GUI、複数OS、複数ハブ、P2P、差分転送、自動マージ、E2EE、インターネット公開、ファイル属性の同期は対象外。同期は削除も伝播するため、長期バックアップは別に保管する。
