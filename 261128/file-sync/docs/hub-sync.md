# ハブ経由の同期

固定のGo 1.27.4とCgoを使う。ハブと2つのクライアントを同じLinuxまたはWSL2のLinuxファイルシステムで動かし、登録、同期、復旧が終わるまで利用者による編集を止める。ハブは `127.0.0.1` のみにバインドし、クライアントも同じアドレスへ直接接続する。 [技術選定と依存の再現](toolchain.md)

## 人工データでの起動と同期

`261128/file-sync/` から実行する。サンプルのポートは8080。使用中なら、すべてのコマンドで同じ空きポートに変更する。以下の環境変数はデモごとに生成し、ハブはDBにトークンのSHA-256だけを保存する。

```bash
set -e
set +x
export CGO_ENABLED=1 GOTOOLCHAIN=local GOWORK=off
go mod download all
go mod verify
demo=$(mktemp -d)
mkdir "$demo/A" "$demo/B" "$demo/hub"
go build -mod=readonly -o "$demo/file-sync" ./cmd/file-sync
bin="$demo/file-sync"
address=127.0.0.1:8080
token_a=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
token_b=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
FILE_SYNC_TOKEN_A="$token_a" FILE_SYNC_TOKEN_B="$token_b" \
  "$bin" hub-init -volume "$demo" -root hub -space demo
"$bin" init -volume "$demo" -root A
"$bin" init -volume "$demo" -root B
"$bin" hub -volume "$demo" -root hub -listen "$address" &
hub_pid=$!
trap 'kill "$hub_pid" 2>/dev/null || true; wait "$hub_pid" 2>/dev/null || true' EXIT
```

ハブが `listening 127.0.0.1:8080` と表示したら、同じシェルで実行する。Aの `abc` がBへ届いた後、Bを `hello` に編集してAへ戻す。編集はそれぞれのコマンドが終わってから行う。

```bash
printf abc > "$demo/A/notes.txt"
FILE_SYNC_TOKEN="$token_a" "$bin" register -volume "$demo" -root A -hub "$address" --seed
FILE_SYNC_TOKEN="$token_b" "$bin" register -volume "$demo" -root B -hub "$address" --pull
cmp "$demo/A/notes.txt" "$demo/B/notes.txt"
"$bin" status -volume "$demo" -root B

printf hello > "$demo/B/notes.txt"
FILE_SYNC_TOKEN="$token_b" "$bin" sync-once -volume "$demo" -root B -hub "$address"
FILE_SYNC_TOKEN="$token_a" "$bin" sync-once -volume "$demo" -root A -hub "$address"
cmp "$demo/A/notes.txt" "$demo/B/notes.txt"
"$bin" status -volume "$demo" -root A
"$bin" status -volume "$demo" -root B
```

初回は両方の基準版が1、更新後は2。 `status` はローカルの基準版、初回登録の状態、未完了時の登録計画、反映予定、送信待ち、保留理由をJSONで表示する。正常終了は0、保留は3、初回登録の前提違反やI/O障害などのエラーは1。ハブの通常再起動は同じルートを指定して `hub` を実行する。 `hub-init` は空の専用ルートへ初めて作るときに使う。

## APIの確認

`GET /v1/manifest` で現在版と世代を読む。blobの取得と登録には、その世代を `X-Hub-Generation` で送る。同期のPOSTは本文に世代と期待版を含める。実行例ではトークンをファイル記述子からcurlへ渡す。シェルのトレースを無効にしてから実行する。 [要求と応答の仕様](protocol.md)

```bash
set +x
api() {
  curl --noproxy '*' --silent --show-error --config /dev/fd/3 "$@" \
    3< <(printf 'header = "Authorization: Bearer %s"\n' "$token_a")
}
manifest=$(api "http://$address/v1/manifest")
printf '%s\n' "$manifest"
generation=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_generation"])' <<<"$manifest")
hash=2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
api -H "X-Hub-Generation: $generation" "http://$address/v1/blobs/$hash" | sha256sum
api -X PUT -H "X-Hub-Generation: $generation" -H 'Content-Type: application/octet-stream' \
  --data-binary @"$demo/A/notes.txt" "http://$address/v1/blobs/$hash"
operation=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
request=$(printf '{"hub_generation":"%s","client_id":"client-a","op_id":"%s","path":"notes.txt","base_revision":2,"content_hash":"%s","size":5,"deleted":false}' \
  "$generation" "$operation" "$hash")
api -H 'Content-Type: application/json' --data-binary "$request" "http://$address/v1/changes"
api -H 'Content-Type: application/json' --data-binary "$request" "http://$address/v1/changes"
```

同期例の直後に実行すると、PUTは200、POSTは2回とも同じ200 `unchanged` を返す。POSTの受理記録は1件増え、版2の発行元はBのままになる。同期例と追加のAPI確認で、受理記録の期待件数が異なる。

## 中断後の再開と保留

- 初回のseedはmanifestに行がないとき、pullはローカルにユーザーファイルがないときに開始できる。seedでは登録計画にある全本文を確保してから送信する。中断後は同じモードの `register` を実行する。計画の本文と操作IDを使い、対象の全基準版を確認してから登録を完了する。
- 登録の開始後に加わった編集は保留する。登録予定の本文自体をまだ確保できておらず、ローカル本文が変わった場合は登録も保留する。全対象を登録できた後に別の編集が残った場合は、登録を完了し、そのパスを `registration_local_edit` として残す。ローカルが登録済みの基準の状態に戻るまで、そのパスを自動送信しない。
- 通常同期の送信待ちは、元の要求と本文を保存する。再開時は世代を照合し、元のPOSTを先に送る。 `blob_missing` なら保存済み本文をPUTし、試行枠があれば同じPOSTを再送する。受理応答のrevisionを端末の基準版に記録し、現在のローカル本文を新しいmanifestと照合する。受理前後の利用者の編集は元パスに残す。
- 受信はサイズとSHA-256を照合してから、Phase 1と同じFD基準の反映・退避・復旧へ進む。破損や不完全な応答は元パスへ設置しない。ハブの通常再起動ではDBと実体を照合し、世代は維持する。DB消失・破損、登録済みblobの破損、結果不明のI/O障害では、データを保持して停止する。
- 両方の内容が変わって異なる場合、名前空間の衝突、削除、世代不一致を検出した場合は自動処理を保留する。通常の比較で検出した競合は元パスとハブの本文を保持する。CAS後に競合となった場合は送信待ちの本文も保持する。競合候補の管理コピーと手動解決、削除伝播、世代変更後の再登録はPhase 3で扱う。

## 状態DBの移行

ハブと端末はともに単一物理接続のSQLiteを使用する。WAL、FULL、外部キー、ページ長4,096、8,192ページの本体上限を起動時に照合する。管理枠は128 MiB、ハブのblob枠は1,920 MiB・4,096件。ハブでは生存1,000件、パス表4,096件、受理履歴8,192件まで。更新前にWALをチェックポイントし、既存のblobや履歴は自動削除しない。 [資源上限](design.md#容量と満杯時の扱い)

端末DBの `user_version=1` では、元の基準版と反映予定を保持したまま登録・登録計画・送信待ち・保留の表を加え、`user_version=2` にする。単一トランザクションで移行し、初期化IDの不一致、不明な版、破損は拒否する。Phase 1の手動コピーで基準版や反映予定があるルートは、ハブの初回登録に使えない。ハブ登録済みまたは登録中のルートを手動の `copy` や `recover` の反映先に指定した場合も拒否する。

## 試験の再現

```bash
test -z "$(gofmt -l cmd internal)"
go vet -mod=readonly ./...
go test -mod=readonly -v -count=1 -timeout=4m ./...
go test -mod=readonly -race -count=1 -timeout=4m ./...
```

通常試験はLinuxの一時領域に人工データを作り、localhostのTCPとUnixソケットを使う。seedの停止試験は最初の受理コミットを観測してから子プロセスへSIGKILLを送る。3プロセスの試験はビルドしたCLIを使い、ハブとA・Bのコマンドを別プロセスで実行する。デバイスとbind mountは隔離環境を条件とするPhase 1の試験で、通常のCIではスキップする。 [隔離試験の手順](local-copy.md#os試料の再現条件)・[受入結果](acceptance.md#phase-2の実測結果)
