# fake readerから全campaign判定までの合成採取

2026-11-05、基点`002718e`。[採取protocol契約](clock-campaign-acquisition.md)のうち、
合成readerと使い捨て原本を使う部分を実装した。
実装は[採取/照合部品](../src/bike_mlops/clock_campaign_capture.py)、
[固定reader](../src/bike_mlops/clock_campaign_fake_reader.py)、
[受入試験](../tests/test_clock_campaign_capture.py)。

**実時計の採取器、実負荷生成器、独立外部時刻源のreaderではない。**
`fixture-load`は合成区間名であり、実サービスへの負荷やCPU負荷の受入を意味しない。
通常/再開後それぞれのidle/fixture-loadを整数で生成する。実際の2時間を観測せず、休止/再開もしない。
fake hostとfake外部参照は同じ合成counterから作るため、源の独立性の証明にはならない。

## 操作と終了値

`261021`で実行する。`ATTEMPT`には**存在しない新しいディレクトリ**を指定し、親だけを先に作る。
この例のplan SHAは採取時に表示される。合成試験であり、表示値をコピーしても実採取の事前reviewを証明しない。

```bash
mkdir -p data/reports
ATTEMPT=data/reports/fake-clock-example-001
.venv/bin/python scripts/capture_fake_clock_campaign.py --output-dir "$ATTEMPT"
# stdoutの synthetic_plan_sha256= に続く64桁を設定
PLAN_SHA='<表示されたSHA256>'
.venv/bin/python scripts/derive_clock_campaign.py \
  --input-dir "$ATTEMPT" --output data/reports/fake-campaign-example-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
# exit 2は、数値成功でも時計資格incompleteであることを含む。
.venv/bin/python scripts/review_clock_campaign.py \
  --input data/reports/fake-campaign-example-001.json \
  --output data/reports/fake-report-example-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
```

| 操作 | 終了値 | 意味 |
|---|---|---|
| capture | 0 | 合成原本の書込とreader回収が完了。数値判定や時計資格の合格ではない |
| capture | 2 | 入出力/応答/中断等により未完了。partial journalを残しcomplete markerを作らない |
| derive | 1 | 原本の構造は照合できたが、再計算した数値に確定違反がある |
| derive | 2 | 数値成功でも資格はincomplete。または原本が未完了/不正で派生JSONを作れない |
| existing review | 1 / 2 | [従来契約](clock-campaign-review.md)どおり。全時計資格passedやexit 0はない |

`derive`のexit 2だけで数値成功を判断しない。派生JSONの有無とstdout、続く既存reviewのreportを確認する。
依存コマンドは、先行するcaptureのexit 0と新規原本を確認してから進める。
`--fault`は固定された合成負例だけを選び、任意command/URL/実サービスを指定する機能はない。
子processの起動引数も固定で、shellを経由しない。

## 原本4ファイル

| ファイル | 内容 |
|---|---|
| plan.json | 既存offline policy、source/recipe/module manifestのcanonical hash、QPC周波数 |
| sources.json | 実装4moduleとCLI2本のsource bytesのhex。読取専用照合に使い、保存コードを実行しない |
| journal.jsonl | header、phase-start、送信前request、応答後sample、phase-end、合成resume、end。各行にsequence/前hash/自身のcanonical hash |
| complete.json | journalの実bytes/SHA・行数・最終chain hash・plan SHA、reader回収確認 |

成功時だけ4ファイルが揃う。開始時に既存ディレクトリの削除/再利用はしない。
plan/sources/markerと派生出力は同じ親に一時fileを作り、write/flush/fsync後にexclusive linkで公開する。
journalは行ごとにflushし、正常endと中断記録ではfsyncも試す。disk障害時に原本保存そのものを保証はできない。
completeはjournalのfsyncとreaderの正常終了/回収（余剰出力なし）後にだけ公開する。
公開完了の直後にprocessが中断されれば、markerが残って終了値が非0になる境界はある。
markerは原本内容/回収の記録であり、そのprocessがexit 0まで進んだ保証ではない。
markerのhash/`reader_reaped`は内容の整合情報で、第三者による実行の認証ではない。

source bytesは採取開始時の可視ファイルを保存する。Python/stdlib/OS全体の実行環境を内包せず、
実行中にsourceを差し替えられない保証もない。人のreview/実行環境の固定は依然必要。
SHAを事前保存する実collectorと、fixture実行開始時に作る今回のplanを混同しない。
source/CLIに秘密を埋め込めば原本に入るため、原本はgitignore下へ保存し、公開前には独立した公開reviewを行う。

## 要求・応答とrawからの再計算

trialとnonceは32桁のランダムhex。1要求ずつ送信し、role/phase/phase-local sequence/nonce/trialを全てecho照合する。
sampleのguest before→request intent保存→要求送信→応答受領→guest after→sample保存はcontrollerの逐次呼出し順。
応答喪失/中断でも未完了のrequestを原本へ残す。送信前記録だけでは実際に送信完了したと断定せず、sampleのないintentは未確定扱い。
replay時はintentと応答後sampleの要求・before bracketも一致確認する。
readerはUTC-before→QPC→UTC-afterの固定順序を返す。
これはfake protocolの呼出し順の検証で、実Windows APIのbracket真正性を認証するものではない。

通常/再開後の各trialは別token。最初のphaseは0..1800、次のphaseは1..1800を要求する。
次phaseのindex 0には、直前phase終端の**同じraw record**を再利用する。要求し直しや似た値への置換をしない。
全7,202要求、各phase1,801 sample、原本14,415行。real QPCの周期/deadline schedulerは未実装。
pipeのIO timeoutに使うlocal MONOTONICはprocess回収の安全上限だけで、観測期間や資格の証拠に使わない。

identityは毎sampleの原本とphase開始/終了を別々に保存する。
fixture限定allowlistでboot、process開始tick/PID/exe SHA/dev/inode/親/namespace、設定bytes、版、clock APIを照合し、
6部品のcanonical SHAとそのobject全体のSHAを既存JSONへ渡す。稼働processを探索する機能ではない。
start tick、namespace、設定変更を異なるidentityとして既存readerがfailedにする。

外部UTCは保存したcenter±uncertaintyから上下限を再計算する。
校正elapsed区間は、固定境界の独立参照**を模した**counterの差に、両端の不確かさを加減する。
QPCの差から逆算してreferenceを作らないが、fixture両者の生成元が同じことは明示する。
30窓と全体の31校正区間、31外部UTC境界を毎phase作る。
future equal/1µs/live probeとresume境界もrawへ保存して照合する。実publisherや休止機構の実行証拠ではない。

## 拒否・中断・IO境界

- hashを付け直しても、nonce/sequence/trial/role/phase差、再利用、欠落/重複/並べ替え、余剰応答、周波数/順序違反は拒否する。
- 原本のUTC逆行、identity差、future許可は派生JSONへ残し、既存数値判定でfailedにする。clamp/retryはしない。
- 欠測QPCなどprotocol必須値の欠落は原本照合段階で拒否。併存する既知違反はrawに残るが、数値summaryへ進まない。
  既存offline JSONのnullable sample契約を変更したものではない。
- SIGINT/TERMを中断例外へ変換し、対象readerだけをEOF/TERM/KILLで回収する。
  エラー種別と回収状態のfailure recordの保存も試す。例外の値/任意argv/認証情報は記録しない。
  SIGKILL/OS停止ではcleanupを保証せず、markerなしの未完了原本を成功扱いにしない。
- reader応答は16KiB、原本fileは各64MiBまで。単一fd/O_NOFOLLOW/非blockingでregular fileだけを読む。
  読取前後と処理全体のstat、最後のbytes再読取で可視変更/同内容replacementも検知する。不可視の一時変更の検知保証はない。
- exact4member以外・symlink・FIFO・重複JSON key・非有限数・manifest/source bytes差・marker/chain差を拒否する。
- 派生outputは原本dir外へ排他的に作り、既存output/原本は上書きしない。stderrに原本値やoperator絶対pathを出さない。

## 残る実採取条件

独立UTC/経過時間源、認証/不確かさ、review済みの事前plan、実collector/固定負荷/host scheduler、
管理元/修復/保守許可と実休止後の受入、人の全原本reviewは未完了。
[源選定のreview](clock-source-readiness.md)と[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)に従う。
合成成功を実Capital採否、過去の時計原因、実運用p95や時計資格へ流用しない。
