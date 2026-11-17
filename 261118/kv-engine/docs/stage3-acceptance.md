# 段階3の受入判定

2026-11-17 JST。[元計画](../../../261014/plan_database_engine.md)の段階3を、実装・テスト・実験記録に照らして判定する。
対象コードの基準は `69578977441067d25fc2d0dddfb12f80f655cea4`。本判定では製品コード、形式v1、fixture、テストを変更しない。

**判定: 下記の故障モデル内で段階3を完了とする。** 成功応答済み操作の保持、未応答操作の許容結果、
限定末尾復旧、破損時停止、I/O失敗後の拒否を既存の自動試験で確認した。
段階4の性能計測と段階5の第三者向けデモは未完了で、MVP全体の完成判定ではない。

## 故障モデルと正解

- Linuxの協調する単一writer。DB内のAPIはmutexで直列化し、別プロセスは同じLOCK inodeのflockを守る。
  外部によるLOCKの削除・置換、非協調のログ編集、ディレクトリ差替えを許すモデルではない。
- 実ファイル上の指定write/Sync/応答境界で子を待機させ、親が通知を受けてSIGKILLし、Waitで終了signalを確認する。
  OS・ページキャッシュ・ファイルシステムは生存する。停止位置の推測にsleepを使わない。
- I/Oラッパーで短いwrite、0バイトwrite、部分write+ENOSPC、Sync/read/復旧等のEIOを注入する。
  実媒体やOS全体を故障させず、任意のI/O失敗・スケジュールの全組合せを網羅しない。
- 親が成功応答を受け取った操作列を `A`、最後の未応答操作（最大1件）を `P` とする。
  単純な辞書へ適用した `A` または `A+P` と復旧値・不存在・sequenceを照合する。
  上書きで過去の値が見えなくなることと、成功済み操作の喪失を混同しない。
- write前・不完全末尾は `A`、同期成功後は `A+P`、最後のack受信後は最後も含む `A` を要求する。
  全長write後/Sync前は一般の許容結果として両方を認め、今回のSIGKILLでは `A+P` を観測した。
  部分値、余分なキー、sequenceの欠番・重複は許容しない。
- ライブラリ試験のackはPut/Delete成功後のテスト応答。製品共通CLI処理の停止試験は別に実施する。
  CLIで成功を確認する呼出し元は完全な応答と通常終了exit 0を確認する。
  Close/出力エラーや異常終了でも同期済み操作は残り得る。再試行の重複排除は提供しない。

## 計画・受入条件との対応

各行は上記モデル内で合格。根拠はテストのassertionとリンク先の観測表であり、件数だけを合格理由にしない。

| ID / 条件 | 実装・自動検証の根拠 | 観測・限界 |
|---|---|---|
| S3-1 write前後・同期前後・応答直前/後の停止 | [crash_linux_test.go](../internal/engine/crash_linux_test.go) `TestCrashRecoveryCheckpoints` | PUT/DELETE×8点。親の5/6件ackと辞書・sequenceを照合。[停止点表](crash-experiments.md)。全バイト位置での実プロセス停止ではない |
| S3-2 成功履歴の保持・未応答操作の全体性 | [seeded_crash_linux_test.go](../internal/engine/seeded_crash_linux_test.go) `TestSeededCrashRecovery` | 3 seed×55件の履歴、PUT/DELETE×8点で48停止＋正常6ケース。全候補キー、件数、ログ長、次sequenceと再Openを照合。[実測表](seeded-crash-experiments.md)。全seed・任意操作番号の停止ではない |
| S3-3 正常EOF / 不完全末尾 / 破損の区別 | [scan.go](../internal/recovery/scan.go)、[scan_test.go](../internal/recovery/scan_test.go) `TestScanBoundaries` / `TestScanReadErrors` | 正常境界16/54/91 Bと全切断位置、snapshot終端より早いEOFを区別。早いEOFはio_errorとして修復しない |
| S3-4 限定末尾復旧・処置前診断 | [disk_linux_test.go](../internal/engine/disk_linux_test.go) `TestTailRecoveryConditions`、[diagnostic_linux_test.go](../internal/engine/diagnostic_linux_test.go) `TestRecoveryDiagnosticCompletion` | DELETEの全不完全位置でinspect不変→診断→正常prefixへの切戻し→次更新。診断0/部分write・EIOはログ変更なし。[診断表](recovery-diagnostics.md)。Writer受理は診断先の永続化ではない |
| S3-5 途中破損で停止し、後続を黙って採用しない | [fixtures_test.go](../tests/fixtures_test.go) `TestCorruptFixture` / `TestDiskFixtureCompatibility`、`TestCorruptionNeverTruncates`（disk_linux_test.go） | 後続レコードのある破損fixtureを拒否し原本不変。header/record CRC、未知形式、短いfile headerも変更せず拒否。CRCは暗号的な改ざん防御ではない |
| S3-6 長さ破損を未完と誤認しない・確保を制限 | [record_test.go](../internal/record/record_test.go) `TestInvalidHeadersBeforeBodyAllocation` / `TestAllTruncations` / `TestCRCFailures` / `TestSequenceValidation` | header検証後に本文を確保。不正長・op・sequence・全1bit改変等を拒否。decoder境界検査と実プロセス停止を区別する |
| S3-7 一部write・容量不足・同期失敗を成功扱いしない | `TestDiskWriteAndSyncFailures` / `TestSyncOrdering`（disk_linux_test.go）、[compound_io_linux_test.go](../internal/engine/compound_io_linux_test.go) `TestCompoundIORecovery` | 短いwrite補完、0バイト拒否、ENOSPC/EIO後の索引非公開・全API拒否・追加I/Oなし。複合障害30＋再Open対照6＋正常1。[故障モデル](compound-io-experiments.md)。実ディスク枯渇ではない |
| S3-8 初回作成・復旧途中で再停止しても安全に再Open | [open_crash_linux_test.go](../internal/engine/open_crash_linux_test.go) `TestCrashInitializationCheckpoints` / `TestCrashRecoveryOpenCheckpoints` | 初回9点＋復旧4点。0/8 B headerは不変のまま拒否、16 Bは再開。復旧再停止でもack済み3件/150 B prefixを保持し、再Open冪等・次sequence=4。[観測表](open-crash-experiments.md) |
| S3-9 復旧I/O失敗・Close競合で資源/更新を誤って公開しない | `TestCompoundIORecovery`、[close_linux_test.go](../internal/engine/close_linux_test.go)のClose競合試験 | Open失敗はDB=nil、FD/LOCK解放。復旧Sync失敗は切戻し済みでも失敗とする。Close18ケースでmutex/LOCK順序・閉鎖1回・再Openモデル一致。[契約](close-concurrency.md)。LOCK閉鎖失敗は未注入 |
| S3-10 CLIの応答境界・出力失敗 | [response_crash_linux_test.go](../cmd/kv/response_crash_linux_test.go) `TestCLIResponseCrashBoundaries` / `TestPersistentCLIOutputDeviceFailure`、[response_linux_test.go](../cmd/kv/response_linux_test.go) | PUT/DELETE×Close/stdout周辺5点で10停止。0/部分/完全応答とexitを区別し、同期済みsequence 4を保持。短い出力はexit 6。[実験表](cli-response-experiments.md)。停止制御は製品共通処理を呼ぶテストバイナリのみ |

排他ロック競合は段階2の `TestLockProcess`、通常の混在操作再起動は `TestDiskReferenceModelAcrossReopens` でも検証する。
これらを含む既存テストを受入ゲートとして実行し、専用[CI](ci.md)も維持する。

## 今回の再現と結果

[環境](../../README.md): Go 1.27.4 linux/amd64、Ubuntu 24.04.4 LTS / WSL2 / ext4。module内で実行する。

```sh
make check
./scripts/go-env.sh go test -json -count=1 -timeout=3m ./...
./scripts/go-env.sh go test -race -count=1 -timeout=3m ./...
```

受入判定のため既存回帰をキャッシュ無効で再実行した。全5パッケージ成功、Test関数72個（子helper含む）、
Fuzz関数2個の既存seed、サブテスト等を含むpassイベント411件、fail 0、skip 0。
fmt/vet/buildとrace detectorも成功。新しいテスト・fuzz探索・故障注入点は追加していない。
すべて専用t.TempDir内の人工DBを使い、通常DB・テストログ・build生成物をGitへ追加しない。
リモートCIの実結果は当該commitのDraft PR #2のChecksで別に確認する。

## 未検証条件と完了判断の境界

| 未検証条件 | 今回の完了判定への扱い |
|---|---|
| 実機電源断、ホスト/カーネル停止、WSL仮想ディスク、装置のflush契約、実媒体故障 | 定義したプロセス障害モデルの外。Sync呼出し成功と電源断後残存を同一視しない。実機での検証は隔離した実験環境と別計画で扱う |
| 診断出力先のfsync/保存保証 | 全長write受理までを検証。診断イベントが電源断後も残る保証は提供しない |
| 全writeバイト位置・全seed・全並行スケジュール・全複合I/O障害 | 有限の停止点と注入に限定。decoder全切断位置の成功を、全位置SIGKILLの成功と記載しない |
| 実pipeのSIGPIPE、LOCK自体のCloseエラー | EPIPEはWriter注入、Close EIOは実Close後の付加エラー。これらの実障害まで検証済みとはしない |
| 非協調な外部変更、CRC衝突/攻撃、修復ツール、冪等再試行 | 対象外の契約を維持。破損を自動で飛ばす処理や複数writerを追加しない |
| 性能・メモリ使用量・第三者のクリーン環境デモ | 段階4〜5の残課題。耐久性/速度の実測されていない断定をしない |

受入条件の未実装や既知のテスト失敗は本照合では確認されなかった。上記の未検証項目を合格に置換せず、
段階3の「定義した故障モデル内」の条件を満たしたと判定する。今後、成功応答の喪失、破損の黙認、
失敗後の追記等が再現した場合は、性能作業より先に不具合修正と当該受入行の再判定を行う。

## 次の1単位

段階4の最優先は、容量内の固定条件で動く計測ハーネスと機械可読な結果の整備。
inspectと固定seed参照モデルは既存実装を使い、追加実装を繰り返さない。
1千・1万・10万キー、固定キー/値長、毎更新Sync、同一CPU/保存先を明記して、起動時間・更新待ち時間・メモリ量を測る。
生成ログが256 MiB内に収まることを事前計算し、専用一時ディレクトリを使用する。
測定区間、サンプル数、Go/OS/FS/CPU、キャッシュ条件とメモリ指標の定義を結果へ添え、
キャッシュを残す再Open測定をcold startと呼ばない。OS全体のキャッシュ設定は変更しない。
本単位では計測ハーネス・性能結果・デモを作成しない。
