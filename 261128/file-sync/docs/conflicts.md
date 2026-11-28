# 競合、削除、端末の再登録

ハブと端末は [初回登録の手順](hub-sync.md#人工データでの起動と同期) で起動する。対象はLinuxまたはWSL2のLinuxファイルシステムとlocalhost。登録、同期、解決、復旧の間は対象フォルダの外部編集を止める。終了コードは0が成功、3が保留、1がその他のエラー。

## 競合を確認する

`sync-once` が保留を返したら、`status` と `conflicts` を確認する。前者はローカルの基準版、送信待ち、反映予定、競合を表示する。後者はmanifestを取得し、未解決の競合に新しいハブ版があれば候補を加える。

```bash
"$bin" status -volume "$demo" -root B
FILE_SYNC_TOKEN="$token_b" "$bin" conflicts -volume "$demo" -root B -hub "$address"
```

- `baseline` は比較に使った基準版。 `detected_local` は検出時のローカル候補。 `hub_candidates` は最初の検出時から確認した順に並び、未解決の競合では末尾が確認済みの最新ハブ版。
- 各候補の `kind` は `file`、`deleted`、`unregistered`、または構造上の `directory`。生存候補の `stage_file` は `.file-sync/stage/` 内の名前。 `saved: true` は必要な本文を照合して同期できたことを示す。 `saved: false` と `reason` が出たときは候補の取得や容量を確認する。
- `structural_hold` が出たら、関係するファイルやディレクトリを確認して別の場所へ退避し、同期をやり直す。端末が作成を記録した空ディレクトリだけが自動削除の対象になる。再登録の照合時は構造を整理してから新しい計画を表示する。
- 再編集した元パスはそのまま残り、検出時のコピーは固定される。関連しないパスは同じ回で同期できるため、終了コード3でも別パスの受理履歴や基準版は進み得る。

## 手動で解決する

`conflicts` で確認した `hub_generation` と最新ハブ候補の `revision` を指定する。次の例はハブ側の候補を選ぶ。 `generation` と `revision` には表示された値を入れる。

```bash
FILE_SYNC_TOKEN="$token_b" "$bin" resolve -volume "$demo" -root B -hub "$address" \
  -path notes.txt --generation "$generation" --revision "$revision" --choose hub
```

| 選択 | 確定する内容 |
| --- | --- |
| `--choose local` | 検出時に保存したローカル候補。以降の編集は別のコピーに保存する。 |
| `--choose hub` | 表示した最新ハブ候補。現在のハブ行と同じ内容を選んだ場合は同じ版で受理記録が増える。 |
| `--choose delete` | 明示削除。ハブに登録済みのパスを対象にする。既にtombstoneなら同じ版で受理記録が増える。 |
| `--choose file --candidate PATH` | 同じルート内の指定した通常ファイルを照合し、コピーを選ぶ。指定ファイル自体はその場所に残る。 |

まだハブに登録されていないパスを不存在のままにする場合は、利用者がローカルの作業を別に退避してから再確認する。ハブに版0の削除を送ると `unknown_delete` になる。

版が進んだ場合は終了コード3になり、`conflicts` で最新候補を確認して選び直す。CASで拒否されたIDには受理記録がなく、新しい選択には新しいIDを使う。途中で応答が失われた場合や受理後に停止した場合は、`sync-once` で元の解決を再開する。 `resolution.state` は、送信・受理照会中の `ready`、受理確認済みで端末反映待ちの `accepted`、再選択待ちの `stale`、端末反映まで済んだ `complete` を示す。解決後も候補コピーは残る。

ハブから明示的な容量拒否を受けた解決は `ready` のまま保留される。 `status` の `holds` に `resolution_capacity` が表示され、`resolve` と、保留が続く間の `sync-once` は終了コード3を返す。 `sync-once` は同じIDと候補で解決を再試行し、無関係なパスを同期する。容量待ちの間に対象の版が進みCASが拒否されたときは、表示された最新の版で選び直す。

## 削除を伝える

次の例は、同じ基準版で本文が一致した状態から始める。Aの削除を送信し、Bで受信する。

```bash
rm -- "$demo/A/notes.txt"
FILE_SYNC_TOKEN="$token_a" "$bin" sync-once -volume "$demo" -root A -hub "$address"
FILE_SYNC_TOKEN="$token_b" "$bin" sync-once -volume "$demo" -root B -hub "$address"
"$bin" status -volume "$demo" -root B
```

Bは本文を `.file-sync/stage/<反映予定ID>.trash` に退避する。退避の照合・同期とDBへの記録を済ませてから元パスを削除する。削除の反映が完了すると、Bの基準版はtombstoneの版になり、反映予定は `complete` になる。Bが先に編集していた場合は、本文と削除候補が競合になる。過去のハブblob、受理記録、削除退避は残る。

## 状態消失や世代変更の後に再登録する

状態DBの消失、初期化IDの不一致、世代の不一致、既知のrevisionの巻き戻しで通常同期が止まった場合は、旧管理領域や別の場所に保存したDBを保持する。 `register --reconcile` の最初の実行では、manifest、ローカルの一覧、パスごとの照合、確認できた旧作業データをJSONで表示する。

```bash
FILE_SYNC_TOKEN="$token_b" "$bin" register -volume "$demo" -root B -hub "$address" --reconcile
```

内容を確認し、構造衝突がなければ、表示された `approval` のSHA-256を指定する。端末は承認時に世代とmanifest、ローカル状態を再取得し、一覧と照合する。いずれかが変化したら新しい計画を表示する。

```bash
FILE_SYNC_TOKEN="$token_b" "$bin" register -volume "$demo" -root B -hub "$address" \
  --reconcile --approve "$approval"
```

一致したパスは基準版を設定する。差分や片側だけにあるファイルは競合として保留する。再登録からハブへ更新は送らない。旧DBと初期化IDは管理領域内の `reconcile-<ID>/`、以前の作業ファイルは `stage/` に隔離して保持する。旧ステージの一覧は診断用の情報で、自動同期や解決の候補へ取り込まない。管理領域が安全に開けない場合は、旧領域をそのまま別の保存先に保全し、再照合する。

停止後は同じ承認値で再実行できる。承認済みの状態へ切り替わっていて、世代・manifest・ローカルが同じなら残りを再開する。違いがある場合や準備段階の停止では、改めて計画を表示して内容を確認する。通常のハブ再起動は世代を維持する。古いバックアップを使う場合は、新世代を発行してから公開することが前提になる。

コピーや退避を整理するときは、停止中に必要なデータを別の保存先へ書き出し、サイズとhashを照合する。活動中の反映予定、送信待ち、競合から参照されるファイルは保持する。CLIによる自動整理は提供していない。 [復旧の設計](design.md#永続化と障害復旧)
