# 段階1・2の実テストと段階3の検証計画

段階0のgo testはテスト関数なしのコンパイル確認だった。段階1では以下の実テストを追加した。
段階2では実ファイルの再起動、OSロック、同期呼び出しと限定的末尾復旧も検証した。電源断耐性は未検証。実行結果は[作業ログ](../../work_log.md)に記録。

担当プロジェクトの[専用CI](ci.md)で、整形・vet・全テスト・CLI build・race detectorをLinux上で自動実行する。
リモートの成功は該当commitのChecksで確認し、ローカル検証と区別する。

## 実装済み: 入力・レコード・CLI（段階1）

- UTF-8の1/256バイト、0/257バイト、無効UTF-8、NUL、正規化違い、バイナリ値、空値、1 MiBと+1。
- PUT上書き、DELETE不在、再PUTと参照モデルの辞書を照合。不在と空値を区別。
- fixtures.mdの期待バイト列をencode/decode両方向で照合。CRC計算の自己一致だけを正解にしない。
- 全切断位置、header CRC、record CRC、op、reserved、sequence、長さoverflow、未知versionを検査。
- 不正な巨大長さを本文read前に拒否し、fuzzでpanic/範囲外アクセスを調べる。確保上限はコードと境界テストで確認し、メモリ使用量の実測は未実施。

- engine: 単純な辞書を正解モデルに、3つの固定seed×1000操作で全16キーを操作ごとに照合。
- engine: Put/Getのコピー、Close、sequence枯渇、並列呼び出しを検証。
- record: 実fixtureの読取・生成が固定hexと一致。完全なレコードの各バイト1bit改変を検出。
- record: 正しいCRCを付け直した巨大長さ・未知op等も本文read前に拒否。
- record: ファイルヘッダーの全切断位置、未知version、I/Oエラーを区別。
- CLI: 同一sessionの操作列、プロセス間で状態が残らないこと、2 MiB行上限、値上限、JSON・Base64不正、I/Oエラー。
- CLI: t.TempDirに実行ファイルをbuildし、別プロセスのexit 0/2/3を検証。
- race detectorとFuzzDecode/FuzzFileHeaderの短時間探索も実行する。fuzzの完了は全入力安全性の証明ではない。

## 実装済み: 永続化・ロック・限定復旧（段階2）

- `t.TempDir()` にDBを作り、混在列→Close→Openで辞書と照合。
- 初回作成のfile/dir/parent Sync順序をI/O注入層で記録。
- 2つの独立プロセスでロック競合を確認。通常終了・kill後の再取得、LOCKファイル残存も確認。
- 同一DBの並列Put/Getを `go test -race ./...` で確認。Closeとの同時競合の専用試験は段階3で追加済み（末尾参照）。
- 256 MiBの直前/一致/超過を容量検査し、拒否時にログ・sequence・索引が変化しないことを確認。

## 一部実施: 障害注入（段階3）

| 注入点・障害 | 期待する判断 |
|---|---|
| write前/部分write/全write後、Sync前 | 応答なし。検証済みprefixまたは完全な未応答1操作を許容 |
| Sync後、索引更新前/応答直前 | 未応答操作が全体として残り得る |
| 成功応答後にプロセス停止 | 全応答済み更新が復旧結果に反映される |
| 初回header write/File.Sync/dir.Sync/parent.Sync前後 | 成功応答なし。短い既存headerは停止、完全headerは再検証 |
| 部分write+error/0バイトwrite/ENOSPC/EIO/Sync失敗 | 成功を返さず、後続操作を拒否。再Openで検証 |
| 完全headerのCRC/長さ破損、途中値のbit改変 | 位置付き停止。勝手なtruncateや後続レコードの採用なし |
| 不完全な末尾とTruncate/Sync失敗 | 正常境界のみ復旧対象。修復I/O失敗ならOpen失敗 |
| inspectで破損/不完全ログを走査 | 診断後も全バイト不変 |

ハーネスは子プロセスを動かし、親が成功応答sequence・操作・値を保持する。
常に未応答操作は最大1件。応答を受け取る前に次の操作を送らない。
障害注入は環境変数任せのsleepではなく、明示的な注入点/親子ハンドシェイクで止める。
復旧後は「全成功応答列」または「全成功応答列＋最後の未応答1操作」を辞書へ再生した結果と比較する。
最後の操作はPUT/DELETEとも全体だけを許容し、部分値は許容しない。
seed、注入点、応答列、ログ長、復旧境界、OS/FS/Go版を人工データで記録する。
応答ログまで同じ障害対象のプロセス内だけに置かない。

全テスト用DBは `t.TempDir()`。CLI手動試験は `mktemp -d` の専用DBのみを使う。
異常系はfixtureのコピーを壊し、原本や利用者DBを変更しない。
kill -9ではOSページキャッシュは残る。電源断、ホストクラッシュ、デバイスflushの真偽は別の検証課題。

## 段階2で追加した実テストの範囲

- 実DBの上書き・空値・不在DELETE・再PUT、別Open後の値とsequence、128操作の参照モデルと8回の再Open。
- 別プロセスが保持するflockとの競合、解放後再取得、同じLOCK inodeの維持。
- 新規header write→File.Sync→DBディレクトリSync→親Sync、および更新のSync完了前に索引を公開しないこと。
- 3バイトずつの短いwrite、0バイトwrite、部分write+ENOSPC、Sync EIO、Get EIO、初期化・Truncate・診断失敗。
- 容量境界は内部状態を境界へ置いた疎ファイルで検査。実データ256 MiBを蓄積する負荷試験ではない。
  上限超過ログも疎ファイルで作り、Open拒否とサイズ不変を確認。
- DELETEレコードの全不完全位置を実ファイルへ書き、inspect不変・Open切り戻し・次sequence再使用を検証。
- 短いfile header、未知version、CRC破損ではOpenが変更せず停止することを確認。
- 段階0の3種類のfixture原本を変えず、一時コピーをOpenして正常/末尾復旧/破損拒否を確認。
- 実CLIの複数プロセスでバイナリ/空値/ファイル入力/削除/inspect/ロック競合/破損exitを検証。

ENOSPCやEIOは注入であり、実ディスクの空き容量を枯渇させていない。
段階2の別プロセス実験は通常終了・再起動。段階3初回でSIGKILLを追加した。ホスト停止・電源断は未検証。

## 段階3の初回単位: 更新停止点とSIGKILL（実施済み）

`internal/engine/crash_linux_test.go`に、既存のdiskOpsを使うテスト専用ラッパーと親子プロトコルを追加。
製品CLIとログ形式は変更していない。PUT/DELETE×8停止点=16ケース、正常終了、未知停止点の拒否を検証。
親が成功応答列を保持し、最大1件の未応答操作の有無を参照モデルで照合する。
時間待ちで停止位置を推測せず、子の停止点通知を受けてからSIGKILLを送り、実際の終了シグナルを確認。
15秒の子プロセス期限と終了処理を設け、異常時も子とLOCKを残さない。
停止後の独立inspect、限定末尾復旧、空値/不在/バイナリ値、次の更新とsequenceも確認。

再現コマンドと実測表は[クラッシュ実験](crash-experiments.md)に記載。
新規作成/復旧中の停止は次の単位で実施済み（下記）。複数seedは後続単位で実施済み（下記）。複合I/O障害は後続単位で限定実施済み（下記）。Close競合は後続単位で実施済み（下記）。
このSIGKILL試験中もOSのページキャッシュは生存する。電源断耐性の証明にはしない。

## 段階3のOpen単位: 初回作成・末尾復旧中のSIGKILL（実施済み）

`internal/engine/open_crash_linux_test.go`にOpen前から有効なテスト専用I/Oラッパーを追加。
既存の親子プロトコル・SIGKILL確認・子の期限/cleanupを再利用し、製品コードは変更しない。

- 初回作成: header write前/途中/直後、File.Sync前後、DB dir Sync前後、parent Sync前後の9点。
  未完成headerは2回Openしても拒否・不変、完全headerは空DBからsequence=1で更新・再Open可能。
- 末尾復旧: Truncate前後と復旧File.Sync前後の4点。
  まず3件をack済みにした別writerを部分DELETE中にSIGKILLし、そのログを開く復旧プロセスもSIGKILLする。
  元の成功応答列、切り戻し境界、診断、2回目のOpenの冪等性、次sequence=4を確認。
- 正常対照: 同じFDラッパーと同期経路で停止だけを無効化し、初回作成・復旧が完了することを確認。
  未知のOpen停止点はファイルを作る前に拒否する。
- 13停止ケース。復旧4ケースは各2回killするため、この単位のSIGKILLは合計17回。

停止点別の観測・再現手順は[Open実験記録](open-crash-experiments.md)を参照。
複数seedの後続単位は下記。ホスト停止・電源断は引き続き未検証。

## 段階3の複数seed単位（実施済み）

`TestSeededCrashRecovery`で3固定seed×PUT/DELETE×8停止点の48 SIGKILLケースと正常終了6ケースを追加。
親の55件のack履歴と最後の1操作を辞書・sequenceで照合し、件数・ログ長・切り戻し診断も確認。
全キー/不在キー、2度目のOpenの冪等性、次の更新と再Openを検証する。
[実験条件と実測表](seeded-crash-experiments.md)に再現手順と限界を記録。
複合I/O障害は下記で実施。Close競合は下記で実施済み。製品CLI応答周辺は残課題。

## 段階3の複合I/O単位（実施済み）

`TestCompoundIORecovery`で更新の部分write+ENOSPC/Sync EIOと再Openの失敗を組み合わせる。
複合障害30ケース、再Open追加障害なし6ケース、障害なし正常対照1ケースが成功。
失敗後の全操作拒否・I/O停止、メモリ索引非公開、prefix全バイト保持、診断・修復境界、FD/LOCK解放を確認。
注入を除いて同じログを再試行し、参照辞書・sequence・次の更新と再Openを照合する。
[故障モデルと実測結果](compound-io-experiments.md)に限定した検証で、実デバイス故障や電源断は未検証。
Closeと各APIの同時競合は下記で検証済み。製品CLI応答周辺、性能計測、デモは残る。

## 段階3のClose競合単位（実施済み）

close_linux_test.goでI/O先行6ケース、Close先行8ケース、barrier同時開始4ケースを検証。
実FDの同期・読取り・閉鎖をchannelで待機させ、成功/EIOと競合させる。
Closeまでの排他保持、FD閉鎖1回、同時Closeの冪等性、ErrClosed後のI/O非実行を確認する。
同時開始は各ラウンド64 API+8 Close。成功更新sequence順の参照辞書と再Open結果を照合し、Inspectの一貫性も検査する。
[契約・観測・未検証条件](close-concurrency.md)を参照。次は製品CLIのClose/stdout応答周辺。
