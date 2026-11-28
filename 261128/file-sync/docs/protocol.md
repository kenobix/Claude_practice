# 同期プロトコル v1

同期の単位は、1領域内の1パスにある通常ファイル。本文全体を転送し、SHA-256で識別する。ハブのrevisionをCAS（現在版と期待版の比較）に使う。ローカルのmtimeや端末の時計は新旧の判定に使わない。

以下はMVPの契約。Phase 2は生存ファイルの作成・更新と初回登録までを扱う。既存パスへの削除要求は世代・重複・CASを検査した後に `422 delete_unsupported` を返す。未登録パスの削除は `422 unknown_delete`。どちらも受理記録を作らない。

## データ契約

| 保持先 | キーと記録する値 |
| --- | --- |
| ハブの領域 | `space_id` と `hub_generation`。世代IDは暗号学的乱数128 bitの小文字16進32桁。初期化と明示的な復元で新規発行する。 |
| ハブのパス表 | `(space_id, path)` ごとに `revision, content_hash, size, deleted, client_id, op_id`。末尾2項目は最後に版を進めた操作の発行元。 |
| ハブのblob表と実体 | `(space_id, content_hash)` ごとに `size` と、内容を照合して永続化した通常ファイル。パス表の生存行だけがblobを参照する。登録済みblobはパス表の参照がなくても保持する。 |
| ハブの受理記録 | `(space_id, hub_generation, client_id, op_id)` ごとに正規化した要求の型付き項目と、受理時の応答（`applied` または `unchanged`、その時点のパス行）。パス表の変更があれば同じトランザクションでコミットする。通常の起動や再登録で履歴を消さない。 |
| ハブの登録設定 | `client_id`、トークンのSHA-256、許可された `space_id`。MVPの実行構成は1領域に登録済み2クライアント。 |
| クライアントの登録 | 端末ID、領域ID、同期ルートの識別、`hub_generation`、初回・再登録の進捗、登録完了の有無。同期状態DBと別に初期化時の乱数IDを記録し、DB内の値と照合する。 |
| クライアントの基準版 | パスごとに最後に反映または照合した `revision, content_hash, size, deleted`。望むローカル本文と、候補として読んだmanifestを混同しない。 |
| クライアントの送信待ち | パス・世代・発行元・操作ID・期待版・候補内容。送信に使う本文を管理領域に確保し、要求とともに永続化する。1パスにつき同時に1件。 |
| クライアントの反映予定 | パス、対象世代・版、変更前の観測（本文のhashとサイズ、または不存在）と対象内容、作業段階、一時ファイル、削除退避先、完了時の基準版。 |
| クライアントの競合 | パス、基準版、検出時の世代とハブ版、両候補（本文とhash、または明示的な不存在）、コピーの保存場所、解決時に提示したハブ版、解決状態。パス構造の衝突では関係パスも記録する。 |

同期パスをUTF-8のバイト列として保存・比較し、SQLiteでは `BINARY` の照合を使う。生存行の `content_hash` は小文字16進64桁、`size` は0以上10,485,760以下の整数。削除行（tombstone）は `deleted:true, content_hash:null, size:0`。行自体がない場合を未登録のrevision 0とし、APIで行を求められた場合は `null` を返す。空ファイルは `deleted:false` で空バイト列のhashを持つ。登録済みの版は1から開始し、内容または削除状態が変わるたびに1進む。上限はSQLiteの符号付き64 bit整数の最大値で、加算前に検査する。

`client_id` と `space_id` は `[a-z0-9][a-z0-9-]{0,31}`。`op_id` はクライアントが暗号学的乱数で作る小文字16進32桁。再送では既存IDを使い、新しく判断した操作には新しいIDを割り当てる。

## 認証と共通形式

- ハブは `127.0.0.1` で待ち受ける。クライアントもそのアドレスへ直接接続し、プロキシとリダイレクトを無効にする。すべてのAPIに `Authorization: Bearer <token>` を付ける。トークンは乱数32バイトのbase64url（パディングなし）。ハブは照合を定時間比較で行い、登録済みの端末と同期領域を決定する。設定はGit管理外か環境変数に置き、要求本文・診断・ログにトークンを残さない。
- APIは同期領域を入力で受け付けない。サーバーが認証から決定した領域でパス・blob・受理記録を検索する。別領域に同じhashがあっても認可されず、応答から別領域の存在は分からない。
- JSONはUTF-8、`application/json`。フィールド名の大小文字を区別し、未知フィールド、同名キーの重複、複数のJSON値、不正UTF-8、対応する組のないサロゲートエスケープを拒否する。整数は指数部・小数部のない10進整数。 `base_revision` は0以上、符号付き64 bit整数の最大値以下。成功・エラーとも `Cache-Control: no-store`。4つのルートにクエリを受け付けない。認証・世代ヘッダーは各1つを要求する。HTTPのフレーミングエラーはハンドラに入る前に拒否され得る。フレームの外のバイトはPUT本文として扱わない。
- JSONエラーは `{"error":"<code>"}` を基本形とする。 `revision_mismatch` は同じ読み取り時点の `hub_generation` と `current`（パス行または `null`）も返す。 `generation_mismatch` は現在の `hub_generation` を返す。 `namespace_conflict` は衝突する生存パスをバイト順で返す。
- manifestを取得後、blobのGET・PUTには `X-Hub-Generation` を必須とする。changesでは本文の `hub_generation` を使う。manifestは世代IDが未知でも取得できる。世代が変わった端末はクライアント側で自動処理を止める。

## API

| API | 要求と検査 | 成功応答と再送 |
| --- | --- | --- |
| `GET /v1/manifest` | 認証のみ。本文なし。 | `200` JSON `{space_id,hub_generation,entries}`。同じ読み取りトランザクションで取った全行（tombstoneを含む）をパスのバイト順で返す。各行は `path,revision,content_hash,size,deleted,client_id,op_id`。 `X-Client-ID` ヘッダーには認証済みの端末IDを返す。再取得は新しいスナップショットになり得る。 |
| `PUT /v1/blobs/{hash}` | 認証、世代ヘッダー。URLのhashは未エスケープの小文字16進64桁。 `application/octet-stream` と `Content-Length` が必須。Transfer-Encoding・圧縮なし。長さ上限は10 MiB。宣言長と実本文およびSHA-256を照合する。新規hashでは本文を読む前に容量を予約する。 | 初回は `201`、既存は `200`。JSON `{content_hash,size}`。ファイルとDB登録が永続化済みであることを示す。既存hashへの再送は同じ本文を照合して返す。既存ファイルが壊れていれば拒否し、上書きしない。パスの版と操作履歴は変わらない。 |
| `GET /v1/blobs/{hash}` | 認証、世代ヘッダー。URLのhashはPUTと同じ。本文、Range、圧縮なし。対象領域で登録済みのblobだけを開く。 | `200`、`application/octet-stream`、`Content-Length`、本文全量。応答前に保存実体のサイズとhashを照合し、受信側でも検査する。途中で切断した場合、Phase 2のクライアントは受信バッファを捨てて全量を再取得する。完成した応答だけをローカル反映に渡す。 |
| `POST /v1/changes` | 認証。JSONは8 KiB以下。必須項目は `hub_generation,client_id,op_id,path,base_revision,content_hash,size,deleted`。生存候補は領域内の照合済みblobと同じhash・サイズを指定する。削除候補は `content_hash:null, size:0`。 | `200` JSON `{hub_generation,op_id,result,entry}`。新規操作はCASを行い、内容または削除状態を変えたときは `applied`、現在の登録済み行と候補が同じなら `unchanged`。後者は受理記録だけを追加し、版とその発行元を維持する。再送は受理時の応答を返す。コミットはハブの保存を保証し、別端末の反映は各端末が確認する。 |

`POST` 例（`<generation>`、`<operation>` は各32桁に置換する）。

```json
{
  "hub_generation": "<generation>",
  "client_id": "client-a",
  "op_id": "<operation>",
  "path": "notes.txt",
  "base_revision": 0,
  "content_hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  "size": 3,
  "deleted": false
}
```

## 判定順序

1. 既知のルート・メソッドを判定し、認証・領域割当を確認する。トークンがなければ `401 unauthorized`（`WWW-Authenticate: Bearer`）。認証済みでも本文の `client_id` が違えば `403 client_mismatch`。パスやblobの状態を先に開示しない。
2. メディア型・フレーミング・サイズ上限、JSONの構造・型、必須項目、IDの形式を検査する。POSTの本文サイズとPUTの1ファイル上限は入力制限なので、この位置で検査する。クエリ、圧縮、許可外の転送方式も入力違反とする。必要な世代が欠けるか形式が不正なら `400 invalid_request`。有効な形式で世代が一致しなければ `409 generation_mismatch`。PUT/GETのURLのhashと、POSTのパス・候補値は世代照合の直後に検査する。
3. POSTは書き込みを直列化し、同じ世代・領域・端末・操作IDの受理記録を先に調べる。記録の型付き項目がすべて一致すれば、現在版が進んだ場合や容量到達時も受理時の `200` を返す。JSONの空白やキー順、同じ文字へのエスケープの差は無視する。項目が異なれば `409 op_id_reused`。拒否された操作には受理記録を作らない。元の要求の結果確認やblob不足の補完には同じIDを使う。確定拒否を受けて判断をやり直す場合は新しいIDを使う。
4. POSTの新規操作は現在行の版と `base_revision` を比較する。一致しなければ `409 revision_mismatch`。同一内容でも先にCASを行う。合致したら、未登録パスの削除を `422 unknown_delete`、生存候補のパス構造衝突を `409 namespace_conflict` とする。次にblobの登録とサイズ、版の上限、領域の保存予算を確認する。
5. POSTは変更行と受理記録を同一SQLiteトランザクションで保存する。応答を返せるのはコミット確定後。PUTは登録の有無を直列に確認し、新しいhashの件数とサイズを予約してから受信する。GETは領域内の登録を検索し、読み取った実体を照合する。途中停止は[復旧仕様](design.md#永続化と障害復旧)に従う。

| HTTP | エラーコードと扱い |
| --- | --- |
| `400` / `411` / `413` / `415` | `invalid_request`（構文、パスやIDなどの形式違反）、`length_required`、`body_too_large`、`unsupported_media_type`。要求を修正するまで保留。切断で完全な応答を返せない場合は結果不明として扱う。 |
| `401` / `403` | `unauthorized`、`client_mismatch`。認証設定を確認するまで保留。 |
| `404` / `405` | `blob_not_found`（GET）、`not_found`（未知のルート）、`method_not_allowed`（既知ルート、`Allow` を返す）。 |
| `409` | `generation_mismatch`、`op_id_reused`、`revision_mismatch`、`namespace_conflict`。再登録、保留、再照合、構造衝突の記録の順に、各コードで処理を分ける。 |
| `422` | `hash_mismatch`、`length_mismatch`、`blob_missing`（POST）、`blob_size_mismatch`、`unknown_delete`、`delete_unsupported`。検証済みの元データを保持。 |
| `507` | `storage_limit`（バイト、件数または版の上限）、`storage_full`（DBまたはOSのENOSPC等）。完成済みデータと受理済み履歴を維持し、新規操作は保留する。 |
| `503` | `storage_unavailable`（I/O、破損、DBの結果不明、保守中）。自動処理を止め、原因と未確定操作を残す。再開条件を満たせば同一IDで確認する。 |

クライアントは接続5秒、要求全体60秒で打ち切る。1回のCLI実行で同じ要求を初回を含め最大2回試す。期限、切断、本文を完全に検証できない応答、`503` が残れば、次の実行まで送信待ちを保持する。初回は管理領域に本文を同期してからPUTし、その後でPOSTを送る。送信可能と記録した待機操作を再開するときは、manifestで世代を確認し、先に元のPOSTを送って受理の有無を確認する。本文を確保する前に停止した操作は、確保からやり直す。未受理の `blob_missing` の場合は保存済みの元本文をPUTし、試行枠が残っていれば同じPOSTを送り、残っていなければ次の実行で照会する。確定した `409 revision_mismatch` では新しいmanifestとローカル状態から判定し直す。連続した新規CASの再判断は1回の実行で1パスにつき1回までとし、さらに版が進めば保留する。

受理後の版がすでに進んでいても、再送の応答は現在版を示さない。クライアントは送信待ちの候補と受理時の行を突き合わせて基準版を進め、現在のローカル本文が送信時から変わっていればその内容を次の照合に回す。本文を持たない候補や解決操作も、受理結果とローカルへの反映完了を別々に記録する。
