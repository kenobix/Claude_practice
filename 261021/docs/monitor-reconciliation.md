# 監視保存の読取照合契約（T06）

policy IDは `monitor-run-reconciliation-v1`。2026-10-30にSolで状態・比較・読取境界を固定した。**照合コマンドはまだ未実装**であり、今回完了したのは設計と使い捨てSQLite/PostgreSQLでの保存挙動の確認である。[設計証拠](reviews/20261030-t06-design.json)を参照。

## 目的と担当範囲

既存monitorはCSV、JSON、DB transaction、complete markerの順に保存する。ファイルとDBを一度にcommitできないため、marker不在だけではDBの成否を決められない。T06は観測したファイルとDB行を照合し、操作メモをstdoutのJSONへ出す。採点・再送・修復・marker再作成・DB更新・registry操作はしない。

- **T06a / P1 / D2 / Luna実装→Sol review**: ファイル検査、状態判定、SQLite読取、CLI、合成受入。PostgreSQL URLは接続せず `unsupported_input` とする。
- **T06b / P1 / D3 / Sol**: PostgreSQLの読取専用snapshot、型別比較、使い捨てPostgreSQLでの中断・遅延commit受入と全体review。T06aの後に進める。
- T06全体の完了には両方が必要。保存の一致は監視品質の合格や実候補の配信許可を意味しない。T02の実証producerと、実候補/HTTP MLflow監視は別課題である。

## 入力と出力

実装予定のCLIは既存 `monitor` と並ぶ新しいコマンドとする。既存 `monitor` の引数構造は変えない。

```bash
# 未実装。T06aの実装後に、一時SQLite等の検証済み対象へ使う形式。
DATABASE_URL='sqlite:////absolute/path/ops.db' \
  mlops-bikectl monitor-inspect --csv /absolute/path/monitor.csv \
  --run-id '3ae0909d-7ae1-4a09-a756-96a7ed582edf'
```

`--csv`（小文字 `.csv`）と `--run-id`（正規小文字UUID）は必須。CSV自体がなくても指定できる。JSONとmarkerは必ず同じstemの `.json` / `.complete.json` とし、JSON内のpathを開くために使わない。profile/targetはactive config、DBは既存の `DATABASE_URL` / `DEFAULT_DATABASE_URL` を使う。DB URLを受け取る新しいCLI引数は追加せず、出力・例外へURLや認証情報を含めない。

部品の公開入口を次へ固定する。validな呼出しでの期待される欠落・不一致・DB接続失敗はJSONにし、成功に見せない。

```python
inspect_monitor_run(csv_path: Path, run_id: str, *, database_url: str,
                    data_profile_id: str, target_contract_id: str) -> dict
```

出力は `schema_version=1`、`kind=monitor-run-inspection`、上記policy ID、`observed_at_utc`、`monitor_run_id`、`status`、`reason`、`exit_code` を持つ。さらに次を記録する。

- `files`: csv/summary/markerそれぞれのpresent/missing、読み取ったbytes数とSHA-256、整合性。ユーザー指定pathは記録してよいがDB URLは出さない。
- `database`: backend、metadata/profile照合、観測件数、snapshot方式、列ごとの数値表現。照会できない件数を0へ置き換えずnullにする。
- `checks`: `file_snapshot, input_format, run_identity, file_hashes, database_profile, database_rows, latest_window` の各keyにpassed/failed/not_evaluated/unsupported。
- `differences`: window/field/expected/observedの先頭50件まで。総差分件数と打切りの有無を別記する。
- `decision=inspection_only_no_retry_or_approval`、`automatic_repair=false`、`automatic_promotion=false`、`later_commit_possible`、`limitations`。

JSONは有限値またはnullだけを使う。SQL NaNを表示する場合は数値でなく `"SQL_NaN"` の記述へ変換する。CLIはJSONを1個printして `exit_code` で終了する。既存mainの戻り値処理は変えず、新handler内の `SystemExit` で既存コマンドへの影響を限定できる。argparseの引数エラーは従来どおりstderr/exit 2でよい。

各file descriptorは `state=present/missing/invalid/changed`、`path`、`bytes`、`sha256` を持ち、不明な数値/hashはnull。databaseには `backend=sqlite/postgresql/unsupported`、`profile_status=matched/mismatch/missing_metadata/not_evaluated`、`row_count`、`snapshot=sqlite_read_transaction/postgresql_repeatable_read_only/not_queried`、`numeric_storage`（nullable 3列→binary64/binary32）、`missing_representations`（sql_null/sql_nan_missingの総件数）を持たせる。未照会なら件数はnull、mapは空とする。差分は `difference_count` と `differences_truncated` で打切りを説明し、expected/observedの巨大な表示は各512文字で切る。profile/targetの期待値はrootの `data_profile_id` / `target_contract_id` へ記録する。

## 読取手順と安全境界

1. UUID/拡張子を検査し、3つの固定pathの有無を捕捉する。末尾がsymlink、通常ファイル以外、読めないファイルは採用しない。親directoryの明示pathはresolveしてよい。最終ファイルは `O_NOFOLLOW | O_NONBLOCK` で開き、fstatでも通常ファイルか確認する。FIFOへの読取待ちを起こさない。
2. CSV 128 MiB、JSON 16 MiB、marker 64 KiB、CSV 100,000行、1 field 1 MiBを上限とする。超過は `unsupported_input`。上限内でも読込前後でsizeを再検査する。
3. JSONは既存 `strict_json` で重複key/NaN/Infinityを拒否する。CSVは `csv.DictReader` で同名header、壊れた行、重複時刻、不正数値を検査する。浮動値は元の文字列から `float()` へ変換し、pandasの既定CSV丸めを介さない。
4. CSVのdict列は現producerのPython dict表現なので `ast.literal_eval` と型検査を使う。`eval` は禁止。psi/欠測率は文字列keyと有限数値/nullだけ、psi_detailsはJSON互換のlist/dict/有限数値/null/bool/stringだけを許す。呼出式などを拒否する。
5. DBのmetadataを先に照合し、同じsnapshotから対象runの行を読む。必ずparameter付き `WHERE window_id LIKE 'monitor:<UUID>:%'` 相当で限定する。正規UUIDにはLIKE wildcardが入らない。100,001行目があれば上限超過とする。他runや旧形式行を採用しない。
6. DB観測後に3ファイルの有無・device/inode・size・mtime_ns・SHAを再取得する。何か変わった場合は `changed_during_inspection` とし、最初の不一致から破損を断定しない。producerと共有する新しいlock fileは作らない。

SQLiteは既存の通常ファイルを `Path.as_uri()` と `mode=ro` で開く。入力の文字列をURI queryとして流さない。`PRAGMA query_only=ON` と `BEGIN` を使い、同一read transactionでmetadata/行を取得する。存在しないDB・directoryを作らず、`:memory:`も対象外。`Database.connect()`、`Database.initialize()`、CLIの `_database()` は呼ばない（既存実装はmkdir/CREATE/ALTERを行い得る）。`immutable=1`はlockと変更検出を省き、他processからもDBが変わらないと仮定するため、稼働中DBには使わない。SQLite自身のWAL/SHM・read lock動作を、logical rowの変更とは混同しない。[URI公式仕様](https://www.sqlite.org/uri.html#uriimmutable)と[WAL読取条件](https://www.sqlite.org/wal.html#read_only_databases)を根拠にする。受入はSQL行とreport bytesの非変更を検査する。

T06bのPostgreSQLはlocal/設定済み接続先のDBを `BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` で読み、接続timeout 5秒、statement timeout 5秒、lock timeout 1秒を適用する。SELECTとtransaction/session制御だけを使い、schema/table/列の確認もSELECTにする。型の確認とmetadata/行を同じsnapshotで読む。T06aではこの接続をまだ実装しない。

DBの `data_profile_metadata(singleton=1)` がactive profile/targetと違う場合は `database_profile_mismatch`。metadataのない旧DBは `legacy_unverifiable`、monitor table/必須列のない形式は `unsupported_input`。いずれもinitializeで補わない。接続/SELECT権限/timeoutの失敗は `database_unavailable` として秘匿した診断codeを返す。

## 現CLI reportの検査

CSVは `monitor_run_id, window_end, window_start, prediction_count, scored_count, label_coverage, mae, rmse, quality_status, psi, feature_missing_rates, distribution_status` が基本列。Capital v2だけ `psi_algorithm, psi_details, training_reference_sha256` が加わる。UUID列のない旧CSVは `legacy_unverifiable`、それ以外の未知の列構成は `unsupported_input`。

存在するJSONには `monitor_run_id, replay_id, model_name, model_version, data_profile_id, target_contract_id, data_sha256, window_count, monitor_period, latest_window, csv_report, csv_sha256, completion_marker, database_window_id_prefix` を要求する。現在のCLI形式だと分からない項目不足は `unsupported_input`、存在する項目の不正型/不正数値は `invalid_evidence`。JSONのUUID自体がない旧形式は `legacy_unverifiable`。ファイル自体の欠落は項目不足と区別し、後の状態表で扱う。低水準persist関数に任意のminimal reportを渡したもの全てを、現CLI形式と見なす必要はない。

- ユーザー指定UUID、CSV全行、JSON、存在するmarkerのUUIDを一致させる。JSONのprofile/targetもactive configと照合する。名前は指定CSV/markerのbasename、DB prefixは `monitor:<UUID>:` と完全一致。
- `model_version` は正の10進文字列、data SHAは小文字64桁。モデル名/版はreportの記載として出し、`model_binding=report_only` とする。DBのmonitor_windowsにはモデル版/候補/artifact列がないため、ここでregistryの版やモデルの真正性を証明したとはしない。
- `window_end` はnaiveの正時、開始を含み終了を含まないmonitor_periodの全時計と一致し、時刻順・一意である。window_startは各CSV窓のendからreportの `thresholds.window_hours` を引いた時刻。thresholds/window_hoursがない形式はunsupported。監視の閾値式やPSIを再計算する作業ではない。
- 予測/採点件数はboolでない非負整数、採点数は予測数以下。coverageは0〜1、MAE/RMSE/PSIは非負有限値または欠落、欠測率は0〜1またはnull。時刻のUTC変換・ラベルの再計算はしない。
- `window_count` はboolでない正整数で、CSV行数、存在するmarkerのwindow_countと照合する。空のcurrent CSVをmatchedにしない。JSONのcsv_sha、markerのcsv_sha/summary_shaは読んだ元bytesのSHAと照合する。markerはschema 1・status `database_commit_succeeded` のみを現形式として扱う。他schemaはunsupported、schema 1で型やstatusが壊れているものはinvalid_evidence。
- DBのwindow_idはprefix+CSV window_end、replay_idはJSONと一致。全件数・coverage・MAE/RMSE・psi_json・missing_jsonを比較し、余分/不足/値違いを区別する。created_atはCSV側に期待値がないため比較しない。
- latest_windowのkey集合はCSV列からmonitor_run_idを除いた集合とし、値をCSV最終行と比較する。警告状態やPSI詳細はCSV/JSONにあり、DBに存在しない列を要求しない。
- SHAの一致は内容の整合性を示す。署名・権限・候補採用の証明にはならない。quality_statusが警告でも、保存自体が一致すればこの部品のstatusはmatchedになり得る。

## 数値と欠落値の固定ルール

保存挙動probeで、SQLiteはbinary64、PostgreSQLの3列 `label_coverage/mae/rmse` はREAL（binary32）と確認した。例えば1/3はSQLiteで `0.3333333333333333`、PostgreSQLで `0.33333334` になる。JSON text内のpsi/欠測率はbinary64の数値を保つ。T06でschemaを移行しない。

- SQLiteおよびPostgreSQL double precision列は、CSV元文字列をfloatへ戻したbinary64で比較する。0と-0は同じ値と扱う。広い `isclose` の許容差で違いを隠さない。
- DB数値欄に文字列がある場合は暗黙変換せずmismatchにする。fileの数値やDB JSONのpsi/欠測率にboolがある場合も数値1/0として比較しない。
- PostgreSQL REAL列はexpectedとobservedの両方を `struct.pack('!f', value)` で量子化した結果が一致するか比較する。出力へ `numeric_storage=binary32` と丸め後の期待値を記載し、元のbinary64値と完全一致したとは説明しない。未知の数値列型はunsupported。JSON textのpsi/欠測率にはこの丸めを適用しない。
- 現producerがDataFrameの未評価Noneを混在列のNaNへ変え、SQLiteはSQL NULL、PostgreSQLはSQL NaNを保存することもprobeで確認した。**CSVが空欄で、その行がlatestなら対応JSONもnullの場合に限り**、nullableな3列のSQL NULLとSQL NaNを「数値欠落」として比較する。過去窓の誤差はJSONにないためCSVを期待値とする。SQL NaNを正規化した件数は `database.missing_representations.sql_nan_missing` に残す。
- CSVが有限値なのにDBがNaN/NULL、CSVが空なのにDBが有限値の場合はmismatch。Infinityは欠落ではない。JSONやCSVに直接書いたNaN/Infinityはinvalid_evidence。0を欠落へ変換しない。

## 状態と終了値

validなCLI呼出しでは、次のstatus/exitを固定する。missing markerをmatchedにせず、存在しない行を未送信と断定しない。

| status | exit | 意味 |
|---|---:|---|
| matched | 0 | marker・CSV・JSON・DB全行の整合を読取時点で確認。 |
| unconfirmed | 2 | markerがなく保存の完了を確定できない。reasonで下表の状態を分ける。 |
| mismatch | 1 | UUID/identity/hash/件数/値、markerと実体などの確定した不一致。 |
| invalid_evidence | 1 | JSON/CSVの破損、不正値、symlink/FIFO等の不適切な入力。 |
| database_profile_mismatch | 1 | DB metadataがactive profile/targetと違う。対象runの行は読まない。 |
| database_unavailable | 2 | DB未取得/不在/接続・権限・timeoutのため照会不能。 |
| changed_during_inspection | 2 | ファイルが読取中に変わり、安定した照合にならない。 |
| legacy_unverifiable | 2 | UUID/DB metadataを持たない旧記録。保全して自動結合しない。 |
| unsupported_input | 2 | 対応外形式・サイズ・列型。T06aのPostgreSQLもこれ。 |
| inspection_error | 2 | 想定外の内部失敗。例外classのみを出し、message/DSNを漏らさない。 |

ファイル読取前の危険なpathは即invalid、size上限等は即unsupportedでよい。その他の安定性再確認後の優先順は、changed→ファイルの破損/不正値→ファイルidentity/hash不一致/markerありでの必要ファイル欠落→DB profile違い→DB照会不能/未対応backend→legacy/形式非対応→DB行の不一致→下表の一致/未確定。複数の失敗はchecksに残す。ファイルだけで不一致を確定できればDBを読まず返してよいが、読取済みファイルの変化は再確認する。markerがあって必要ファイル、または照会できたDBの必要行がない場合はmismatchである。

| CSV | JSON | marker | DB（対象UUID） | status / reason |
|---|---|---|---|---|
| 一致 | 一致 | 一致 | 全件一致 | matched / files_and_database_match |
| 一致 | 一致 | なし | 0件 | unconfirmed / files_only |
| 一致 | 一致 | なし | 全件一致 | unconfirmed / database_without_marker |
| 有無いずれか | 不足 | なし | 0件または不確定 | unconfirmed / incomplete_files |
| なし | あり | なし | 有無いずれか | unconfirmed / incomplete_files |
| なし | なし | なし | 1件以上 | unconfirmed / database_only |
| なし | なし | なし | 0件 | unconfirmed / not_observed |
| 一致 | 一致 | なし | 一部だけ/余分/値違い | mismatch / database_rows_differ |
| 不足 | 有無いずれか | あり | 有無いずれか | mismatch / marker_without_required_files |
| 一致 | 一致 | 一致 | 0件/一部/余分/値違い | mismatch / database_rows_differ |

上表は先に形式/identity/hash/profileの検査を通る場合の表。CSVだけがありJSONがない場合はCSVとDBが似ていても全体identityを確認できずincomplete_files。DB行の有無はsnapshot時点の観測に限る。marker不在のunconfirmedでは `later_commit_possible=true` とし、全件一致でもmarker生成や自動再試行を勧めない。操作メモは「producerが動いていないか確認し、原本を保全して再度読取照合する」とする。

## 受入ケース（実装後に確認するもの）

| ID | fixture/操作 | 期待結果 |
|---|---|---|
| S01 | current CLI形式、CSV/JSON/marker/SQLite全窓が一致 | matched/0。警告statusがあっても保存一致は一致。 |
| S02 | S01からmarkerを消し、DB対象行も消す | unconfirmed/files_only/2。再送不可。 |
| S03 | S01のmarkerだけ消す | unconfirmed/database_without_marker/2。全DB行を保全。 |
| S04 | CSVだけ/JSONだけ残しmarkerなし | unconfirmed/incomplete_files/2。DBの有無の双方で確認。 |
| S05 | 3ファイルなし、DB対象行だけあり | unconfirmed/database_only/2。 |
| S06 | 3ファイルなし、metadataのある既存DBに対象行なし | unconfirmed/not_observed/2。 |
| S07 | markerありでCSVまたはJSONだけ欠落 | mismatch/1。 |
| S08 | marker/JSONのSHAまたはUUIDを変える | mismatch/1。 |
| S09 | DB対象行の1件欠落、余分行、値/時刻/replay_idの変更 | 各々mismatch/1。markerなしでも部分一致は0にしない。 |
| S10 | 別profile/targetのDB metadata | database_profile_mismatch/1。対象run SELECT前に拒否。 |
| S11 | CSVに複数UUID、重複窓、不正数値、壊れたJSON/重複JSON key | mismatchまたはinvalid_evidence/1。UUID違いはmismatch、破損・重複窓・不正値はinvalid。 |
| S12 | CSV/JSONにrun UUIDがない旧形式、またはDB metadata tableがない | legacy_unverifiable/2。旧行・bytesは変えない。 |
| S13 | DB不存在、読取権限なし、table権限拒否、接続timeout | database_unavailable/2。新規DB/directoryを作らずDSNを出さない。 |
| S14 | 1回目と2回目の読取間にファイル交換/変更/marker新規出現 | changed_during_inspection/2。 |
| S15 | symlink、FIFO、上限超過、未知の列構成 | symlink/FIFOはinvalid/1、上限/未知形式はunsupported/2。待たない。 |
| S16 | 既存の旧形式行と別UUID行を同じDBへ置きS01/S03を照合 | 他run/旧行を選択せず全内容を保持。 |
| S17 | CSV空欄/JSON nullとSQL NULL、有限値0とSQL NULL | 前者は欠落一致、後者はmismatch。CSV NaN/Infinityはinvalid。 |
| S18 | PG REALに1/3と非整数MAE、JSON text内のpsi小数を保存 | T06b。binary32比較とJSON binary64比較を分けて一致。1 ULP違いはmismatch。 |
| S19 | PGに未評価NaNを保存、さらにCSV有限値に対するDB NaNも作る | T06b。欠落だけ正規化、有限値対NaNはmismatch。 |
| S20 | SQLite/PG read-only接続からUPDATEを試す | 使い捨てfixtureで必ず拒否。実照合は更新SQLを一切送らない。 |
| S21 | PGで照会snapshotの後に別接続のcommitが到着 | T06b。同snapshotでは未観測、次回に現れる。未送信/採用へ変えない。 |
| S22 | CSV dict列に呼出式を書き、エラーに認証情報入りDSNを含めるfake | 実行せずinvalid/1。接続失敗等は秘匿codeだけ、秘密文字列はstdout/stderrに出さない。 |
| S23 | T06aにPostgreSQL URLを渡す | 接続せずunsupported/2。接続/initializeへの呼出しをmockで拒否。 |

全caseで入力CSV/JSON/marker bytes、既存DB logical rows、旧runを前後比較する。read-only setupを検証するためのUPDATEはテストfixtureの別経路に限定する。T06aはS18/S19/S21の実PostgreSQL受入を未実施として記録し、T06完了にしない。合成受入やstorage probeを実Capitalの監視証拠へ転用しない。
