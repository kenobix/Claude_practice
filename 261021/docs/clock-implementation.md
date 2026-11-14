# 時計資料：offline部品と合成検証

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

**時計研究・追加採取・OS操作は引き続き保留。ここにある過去手順は実行許可ではない。**

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [保存済みbracketの経過時間componentを照合する](#clock-rate-review)
- [時計資格の全観測期間をofflineで照合する](#clock-campaign-review)
- [全観測期間の採取protocol：次の実装に向けた契約](#clock-campaign-acquisition)
- [fake readerから全campaign判定までの合成採取](#clock-fake-acquisition)
- [fake QPCの固定周期・phase deadline](#clock-fixed-schedule)
- [合成時計raw原本の可搬bundle](#clock-raw-bundle)
- [chronydの配布build照合と内部計算の観測](#chrony-runtime-control)

<a id="clock-rate-review"></a>

## clock-rate-review

<a id="clock-rate-review--保存済みbracketの経過時間componentを照合する"></a>

### 保存済みbracketの経過時間componentを照合する

基点`61643ac`。[資格計測設計v1](clock-operations.md#clock-qualification-plan)のうち、
**30分の1 phaseに記録したQPC対MONOTONIC/RAWの進行率だけ**を実装した。
OS・時計・ネットワーク・DB・registryへ接続せず、指定された入力JSONだけを読む。
`.env`や実行profileを読む処理もない。

入口は[`clock_tools.py rate-review`](../scripts/clock_tools.py)、
純粋な計算部品は[`clock.py`](../src/bike_mlops/clock.py)。
結果が`rate_component_status=passed`でも、`clock_qualification_status`は常に`not_evaluated`。
sampleの真正性/採取protocol、QPCの校正、外部UTC、future拒否、boot/process/設定、
通常/負荷の両phaseと休止再開、保守許可と人のreviewは、この部品では判定しない。
切り出した良好なphaseだけで時計資格を通す用途には使わない。

<a id="clock-rate-review--入力と判定"></a>

#### 入力と判定

JSON objectの項目は次の5つだけ。未知の項目、重複key、NaN/Infinityは拒否する。
timestampsは整数。bool/小数/負数を時刻として受け入れない。

| 項目 | 値 |
|---|---|
| `schema_version` | 整数1 |
| `kind` | `clock-rate-phase-v1` |
| `phase` | `idle`または`fixture-load` |
| `qpc_frequency_hz` | 正の整数。記録元が読んだQPC周波数 |
| `samples` | 2〜10,000件、採取順。QPC tickは同じ参照clockの値 |

sampleは`index`、`qpc_tick`と、`monotonic_before_ns`/`monotonic_after_ns`/
`raw_before_ns`/`raw_after_ns`の6項目だけ。
indexは0開始で連続。`before`はWindows sample要求前、`after`はその応答後のWSL値。
QPC sampleがその間に行われたという保証は、将来の採取protocol側で必要になる。
今回のreaderへ数値を渡すだけでは、その保証やQPC秒の正しさを確立できない。

最初のsampleを起点にQPCで60/120/…/1800秒へ最初に到達したsampleを選ぶ。
30個の非重複区間とphase全体で、MONOTONIC/RAWそれぞれをFractionによる厳密な有理数で検算する。
丸めた平均や、後から選んだ良好なsampleへ差し替えない。
最後のsampleは最初の1800秒到達点であることを要求する。余分なsampleや途中終了は`incomplete`。
隣接QPC gapが2秒を超える場合も`incomplete`とする。
二つの端点だけで1800秒をまたいでも、中間の観測が欠けるため合格しない。

bracket下限/上限の全体が`[0.999, 1.001]`内なら、その区間は`passed`。
完全に外れる場合は`failed`、部分的に重なる場合は`incomplete`。
QPCの非増加、bracket内の逆行、MONOTONIC/RAWの順序違反/観測の重なりは`failed`とする。
明確な違反と不足が共存したら、両方のissuesを保存して`failed`を優先する。

<a id="clock-rate-review--cliとファイル保護"></a>

#### CLIとファイル保護

新しい出力pathを指定して次を実行する。入力はまだ実採取していないので、以下は将来の使用例である。

```bash
.venv/bin/python scripts/clock_tools.py rate-review \
  --input "$TASK_CLOCK_INPUT" --output "$TASK_CLOCK_REPORT"
```

終了値は0=経過時間component合格、1=明確な違反、2=不足または不正入力/IO失敗。
0でも時計資格全体の合格ではない。schema違反時はレポートを作らず、内容や絶対pathをstderrへ出さない。
schemaが有効で不足/違反を判定できる場合は、そのレポートを保存して非0で終了する。

入力は単一の読取descriptorから最大16MiBで読み、前後のdev/inode/size/mtime/ctimeとpathのstatを比較する。
Linux/WSL用のreaderであり、O_NOFOLLOW/O_NONBLOCKで開いて通常fileだけを受け入れる。
symlinkやFIFO/装置を拒否し、FIFOのwriter待ちへ入らない。
原本のbytes/SHAをreportに結合する。これは読取中に見える変化の検知で、悪意ある原本の真正性を保証しない。
出力は同じdirectoryへstagingし、fsync後に排他的linkで確定する。既存reportを上書きせず、
IO失敗時はstagingを削除する。入力や原本の台帳を更新しない。

<a id="clock-rate-review--残る受入"></a>

#### 残る受入

[専用テスト](../tests/test_clock.py)は合成数値と一時fileだけを使う。
別processのCLI/入力hash/終了値、境界、欠測、10%のずれ、wide bracket、失敗優先順、
JSON拒否、読取中変更、link/fsync失敗cleanup、既存report保護を検証する。
これを実Capital運用性能や時計資格の証拠へ流用しない。

実装`f26b334`の[CI41845551048](https://github.com/kenobix/Claude_practice/actions/runs/41845551048)で
最終の専用18件を含む全482件と全隔離systemが成功した。
[最終CI索引](reviews/20261104-clock-rate-ci-checks.json)でartifact/member hash・archive-only再計算・cleanupも確認した。
localの全480件は最終IO guard追加前の記録として保持し、CIの482件と区別する。

次は、全campaignの入力schemaとpolicy binding、外部UTC区間/校正/identity/採取真正性・coverageの判定を設計・実装する。
採取器、固定fixture負荷、外部時刻源readerも未実装。実採取・設定変更や休止再開を自動実行する部品は追加していない。

2026-11-05追記：[4区間を結合する全観測期間のoffline reader](clock-implementation.md#clock-campaign-review)を追加した。
このphase部品の契約は変更せず、各phaseから同じ計算を呼ぶ。外部UTC/校正/identity/欠測の数値照合が揃っても、
全体資格はincompleteのままとし、採取真正性/独立源/許可・修復・人のreviewを残す。

<a id="clock-campaign-review"></a>

## clock-campaign-review

<a id="clock-campaign-review--時計資格の全観測期間をofflineで照合する"></a>

### 時計資格の全観測期間をofflineで照合する

基点`fb33af2`、2026-11-05。[事前の受入設計v1](clock-operations.md#clock-qualification-plan)の
通常60分と休止再開後60分を、保存済みJSONからまとめて照合する。
[既存の30分進行率部品](clock-implementation.md#clock-rate-review)を4区間分再計算し、提出された「合格report」は入力にしない。
入口は[clock_tools.py review](../scripts/clock_tools.py)、
計算は[clock.py](../src/bike_mlops/clock.py)、
合成例と負例は[test_clock.py](../tests/test_clock.py)。

**数値・構造の`offline_checks_status=passed`でも、`clock_qualification_status=incomplete`のまま。**
ハッシュは内容の結合を検証するが、採取の真正性や独立した時刻源の品質を認証しない。
人のreview、修復・保守許可、sourceの独立性/校正と採取protocolの検証は、この入力に自己申告flagを足しても満たせない。
明確な違反は`failed`を優先し、不足もissuesに残す。実Capital採否、実運用性能、過去原因の解決には流用しない。

<a id="clock-campaign-review--固定policyと入力hash"></a>

#### 固定policyと入力hash

既存設計本文は書き換えていない。そのSHAと既存rate policy SHAを、今回の`clock-campaign-offline-v1`へ結合した。
本文に数値未定だった**QPC参照校正の区間を±100ppm（比0.9999〜1.0001）**と事前に固定した。
これは当プロジェクトの設計値でOSの保証ではない。QPCに対するWSLの±1000ppmとは別の予算である。
両者を加えれば最大約1100ppmとなり、250msあたり約0.275ms相当の尺度寄与になる。
個々の要求のbracket誤差や非定常な進行を保証しない。過去p95の補正に使わない。
すべてFractionで比較し、許容区間に一部重なるだけの場合は不足とする。

canonical hashは`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`のUTF-8 SHA-256。
入力fileそのもののbytes/SHAも別にreportへ保存する。canonical hashとbyte hashを混同しない。

最上位は次の8項目だけ。未知の項目、JSON重複key、非有限数、誤った型を拒否する。
全時刻は非負整数ns、QPCは整数tick。bool/floatは整数として受け付けない。
欠測を表す`null`は下記で許した項目だけに使う。schema違反時はreportを作らない。

| 項目 | 契約 |
|---|---|
| `schema_version` / `kind` | 整数1 / `clock-campaign-v1` |
| `policy` / `policy_sha256` | 実装内POLICYの完全な値 / canonical SHA。変更したpolicyの自己rehashも拒否 |
| `plan` / `plan_sha256` | 下記planとそのcanonical SHA。CLIへ別に渡す事前保存SHAにも一致必須 |
| `campaigns` | 最大2件、順序は`normal`, `post-resume`。末尾の欠落は不足、順序変更はschema拒否 |
| `resume` | 下記休止境界object、未取得は`null`（不足） |

planは`policy_sha256`, `collector_sha256`, `workload_sha256`, `utc_reader_sha256`,
`utc_source_sha256`, `calibration_source_sha256`, `qpc_frequency_hz`だけ。
SHAは小文字16進64桁、周波数は正の整数。同じhost/guestに依存する源を独立源と自己申告しても認証されない。
時刻源の不確かさ・transport/bracketの根拠・生の応答・実行fileは、将来のreaderと別reviewで保存する。
事前SHAの保存時期/真正性もこのCLI単独では検証できない。入力に書かれたSHAをそのまま期待値へコピーしても、その保証にはならない。

campaignは`role`, `trial_sha256`, `phases`だけ。trialは採取UUIDのSHA、2試行で同じ値は違反。
phasesは最大2件、順序は`idle`, `fixture-load`。各要素は`sha256`と`data`で、dataのcanonical SHAを照合する。
同じfile名や保存済みreportのstatusは信用せず、全sampleから再計算する。

<a id="clock-campaign-review--phase-dataと全sample"></a>

#### phase dataと全sample

phase dataは以下の9項目だけ。

| 項目 | 契約 |
|---|---|
| `phase` | `idle`または`fixture-load`、配列内の位置と一致 |
| `identity_start`, `identity_end` | 下記identity、未取得は`null`。区間内で異なれば違反 |
| `samples` | 0〜10,000件、全sampleを採取順。短縮/欠測を良好な区間だけで補わない |
| `external_utc` | 最大31件。開始/60秒ごと/1800秒到達の外部UTC区間 |
| `qpc_calibration` | 最大31件。60秒窓30個とphase全体の校正区間 |
| `future_probes` | 最大3件。equal / future-1us / live |
| `lost_samples` | 非負整数。1以上はinstrumentation不足 |
| `interrupted` | bool。trueは中断による不足 |

identityは`host_boot_sha256`, `guest_boot_sha256`, `process_set_sha256`, `sync_configuration_sha256`,
`versions_sha256`, `clocksource_sha256`の6個のSHA。
元のboot ID、process開始時刻・exe/hash/dev/inode/親/namespace、OS/WSL/Docker版、設定等をcanonicalにまとめる
採取protocolは未実装。単なるPID/commや文字列hashだけで所有者を確定しない。公開入力へ個人argvやcredentialは載せない。

sampleは既存rateの6項目（`index`, `qpc_tick`, `monotonic_before_ns`, `monotonic_after_ns`,
`raw_before_ns`, `raw_after_ns`）と、`windows_utc_before_ns`, `windows_utc_after_ns`,
`realtime_before_ns`, `realtime_after_ns`, `boottime_before_ns`, `boottime_after_ns`, `identity_sha256`の合計13項目。
indexは0開始で連続。index以外は`null`で欠測を記録できる。
identity hashはそのphaseの開始identityに一致必須。
各clockのbracket逆転・観測した逆行、QPC非増加は違反。
QPC隣接間隔>2秒、中断/lost、必要な境界やclock値の不足はincomplete。
一項目欠測でも、別項目で観測した逆行/進行率違反を消さない。

最初のsampleから60秒ごとに**最初にQPC境界へ到達したsample**を選ぶ。
MONOTONIC/RAWは既存componentと同じ全30窓+phase全体を判定する。
欠測時は残る既知の端点でも違反を計算するが、全観測の成功へ格上げしない。
idle終端とfixture-load始端は、phase-local index以外が同じ共有sampleであることを要求する。
間の未観測gapやidentity変更を、別々の良好な30分の結合で隠さない。

<a id="clock-campaign-review--外部utcとqpc校正"></a>

#### 外部UTCとQPC校正

`external_utc`の各要素は`boundary_seconds`, `sample_index`, `lower_ns`, `upper_ns`, `source_sha256`だけ。
境界は0,60,…,1800秒。indexは上で選んだsampleと一致。
UTC区間は、そのsampleの読取時点を覆う独立外部UTCの上下限とし、外部源精度・transport遅延・bracket不確かさを含める。
Windows/WSLそれぞれのoffsetを`[local_before - external_upper, local_after - external_lower]`で再計算する。
全区間が±1秒内なら数値上成功、完全な逸脱は違反、境界にかかるなら不足。
±1秒をfuture許容幅として使わない。重複境界、誤ったindex、源の差替え、逆転区間は拒否/違反。
境界が未観測の場合は不一致を断定せず不足を保存する。

`qpc_calibration`の各要素は`first_index`, `last_index`, `reference_lower_ns`, `reference_upper_ns`, `source_sha256`だけ。
referenceは、各固定60秒窓とphase全体の**独立参照による経過時間の区間**で、両端は正。
QPC経過秒をqとすると、校正比は`[q * 1e9 / reference_upper_ns, q * 1e9 / reference_lower_ns]`。
全区間が[0.9999,1.0001]へ入ることを要求する。参照intervalをhostのUTC差やQPC自身から作って独立と呼ばない。
source SHAはplanに一致必須。31区間すべてを要求し、重複/欠落/余剰を検出する。
外部UTCの絶対offset成功だけをQPC秒数の校正に代用しない。

<a id="clock-campaign-review--future-probeと休止境界"></a>

#### future probeと休止境界

future probeは`case`, `sample_index`, `generated_utc_ns`, `validated_utc_ns`, `generated_monotonic_ns`,
`validated_monotonic_ns`, `decision`, `blocked_before_sha256`, `blocked_after_sha256`, `tags_before_sha256`, `tags_after_sha256`だけ。
caseは`equal`（UTC同値/allow）、`future-1us`（生成UTCが1000ns未来/reject）、`live`（生成UTC<=検証UTC/allow）。
MONOTONICの生成<=検証も必要。正のUTC差は許容幅なしでrejectし、blocked/tagのbefore/after SHAは不変。
liveで自然futureとなったら、実際にrejectされても試行はfailed。retry/clamp/待機で隠さない。
fixture2件のsample_indexはnull、liveは対応sampleを指定し、UTC/MONOTONIC両方の生成/検証がそのbracket内であることを照合する。
これは保存した数値の整合で、実際の既存publisherやrejectコードが実行されたことの認証ではない。

resumeは`before_identity_sha256`, `after_identity_sha256`, `windows_before_ns`, `windows_after_ns`,
`guest_before_ns`, `guest_after_ns`だけ。identityは通常試行の最終/再開後試行の最初に結合する。
UTCは通常終端<=休止前<=再開後<=再開後試行始端。UTC欠測は不足、観測した逆行は違反。
休止をまたぐQPC/MONOTONIC差を進行率へ混ぜず、新試行の値を別に判定する。
このrecordだけでは本当に休止/再開したことや保守許可は認証しない。

<a id="clock-campaign-review--実行・結果・残る条件"></a>

#### 実行・結果・残る条件

採取済みJSONと**事前に別保存した**plan SHAが必要。以下は将来の使用例で実採取の実行記録ではない。

```bash
.venv/bin/python scripts/clock_tools.py review \
  --input "$TASK_CLOCK_CAMPAIGN" --output "$TASK_CLOCK_REPORT" \
  --expected-plan-sha256 "$TASK_CLOCK_PLAN_SHA256"
```

終了値は1=明確な違反、2=不足またはschema/IO拒否。0を返す経路はない。
offline_checks_statusはpassed/failed/incomplete、clock_qualification_statusはfailed/incomplete。
不足/違反もreportへ保存し、schema/IO拒否は安全なstderrだけで内容/絶対pathを出さない。
reportだけで後続の実運用測定を解禁するconsumerは作っていない。

最大32MiB、通常file限定・単一FD/nofollow/nonblock・読取前後stat/最終path比較、
原本byte SHA、出力のfsyncと排他的確定、上書き拒否と失敗時staging cleanupを使う。
単一JSONに全原本数値を含め、外部path/URLをdereferenceしない。OS/network/DB/profile/registryには接続しない。
TOCTOUの見える変化を検出するもので、敵対的なfile真正性、parent directory差替え等の完全な防御ではない。

残件は、実採取protocol/readerと独立源不確かさの校正、identity元資料、固定負荷生成器、
host/backend同期責任review・新PHC/UDPログ、別途許可後の修復/休止再開、全原本の人によるreview、
資格失効と実測前後の同環境照合。今回のoffline部品の成功で、これらを完了扱いにしない。

2026-11-05追加review：QPCの一部欠測でfirst-reach coverageを確定できない場合でも、
既知のQPC値が正確な60秒境界に一致する端点同士の進行率は診断として計算する。
ここで確認した逸脱はfailedとするが、良好な端点だけで欠測を補完/合格させない。
提出外部UTC区間そのものの上下逆転は、対応境界が欠けていても違反として残す。

最終37件を含む全519件・全systemを実装f6b3cadの[CI41939041432](https://github.com/kenobix/Claude_practice/actions/runs/41939041432)で確認した。
[最終索引](reviews/20261105-clock-campaign-ci-checks.json)にartifact/member SHA・別process再計算・cleanupを残す。
次の実装の[採取protocol](clock-implementation.md#clock-campaign-acquisition)は設計契約であり、採取真正性や実資格の証明ではない。

<a id="clock-campaign-acquisition"></a>

## clock-campaign-acquisition

<a id="clock-campaign-acquisition--全観測期間の採取protocol：次の実装に向けた契約"></a>

### 全観測期間の採取protocol：次の実装に向けた契約

2026-11-05。追補：[fake採取/原本結合](clock-implementation.md#clock-fake-acquisition)と[源選定review](clock-operations.md#clock-source-readiness)を実装・整理した。
以下は実装前に固定した契約として保持する。実collector/固定負荷/実外部readerは未実装、実資格は未達。

2026-11-05。[全観測期間のoffline入力](clock-implementation.md#clock-campaign-review)を作る採取器の実装前契約。
採取器、外部reader、負荷生成器を実装・実行した証拠ではない。
既存の[受入設計](clock-operations.md#clock-qualification-plan)を維持し、以下の未選定事項がある間は本採取を開始しない。
OS修復/休止再開/実サービスへ負荷をかける許可もこの文書で付与しない。

<a id="clock-campaign-acquisition--先に確定する入力と保存"></a>

#### 先に確定する入力と保存

- 独立外部UTC源、独立した経過時間参照、源自身の精度・輸送/取得遅延の不確かさ、読取実装をreviewする。
  公開NTP名、HTTP Date、host UTCやPHC応答を見つけただけで独立性や精度を認定しない。
  使用可能な源が未定なので、今回provider/addressを仮に埋めて動かすreaderは作らない。
- collector/workload/UTC readerの実行file、config、protocol、公開可能な源識別を採取前に固定し、bytes/SHAを保存する。
  collector/workload/reader SHAはentry fileだけでなく依存コード/設定を含む再構築可能なmanifestへ結合する。
- planのcanonical SHAを、trial開始前に別のreview記録へ固定する。後から同じfileへSHAを書くだけで事前性を証明しない。
  外部源の認証情報、全環境変数、個人argvや無関係なprocess一覧を公開原本へ含めない。
- 全trial UUID、開始/終了、command/終了値、raw journal、manifest、derived JSONを新pathへ排他的に保存する。
  原本には合成/実採取の区分を入れ、生成済みの合成fixtureを実採取へ転記しない。

<a id="clock-campaign-acquisition--windowswsl間の要求とbracket"></a>

#### Windows/WSL間の要求とbracket

1. 一つのtrial/phase/sequenceに対し未使用nonceを生成し、WSL側でREALTIME/MONOTONIC/RAW/BOOTTIMEのbeforeを保存する。
2. host readerへtrial/phase/sequence/nonceを送る。hostは同じ値をechoし、実際の読取順序とQPC tick/frequency、
   QPCの読取を挟むWindows UTC before/after、読取エラーを返す。
3. WSL側でafterを採り、応答のnonce/sequence/trial/phaseを一致確認してraw journalへ書く。
   接続/応答喪失、遅延、余剰応答、重複、別trial応答は保存して不足/違反とする。
   古い成功応答の再利用、同じsample番号の再要求、都合のよい応答への差替えをしない。
4. WSLのbefore/afterの間でhost読取が行われることを、実装と負例で確認する。
   応答だけを後で受け取った場合や並列要求で対応が不明な場合は、数値が良くても真正なbracketと扱わない。
   live future probeは対応するWSL bracket内に生成/検証を含め、UTCとMONOTONICの両方を残す。

要求周期と1800/3600秒の終了はhost QPC上の固定deadlineで制御する。
WSL sleep回数や未資格MONOTONICで観測時間を証明しない。
遅れを取り戻すためにsampleをまとめて生成したり、窓を伸ばしたりしない。
phase-local indexは0開始。idle終端のraw sampleを次の負荷phase始端として共有し、元journal IDも結合する。
休止をまたぐ差分は進行率から除き、保守許可・休止前後の独立raw・新trialを保存する。

<a id="clock-campaign-acquisition--identity-hashの元資料を定義する"></a>

#### identity hashの元資料を定義する

入力の6個のidentity SHAは、次のcanonical objectの元資料と対応付ける。
canonical化はoffline readerと同じJSON方式で、文字列の連結やファイル名だけをhashしない。
源が取得できない場合は代替の架空値を作らず、そのidentityを欠測として扱う。

| 入力SHA | 元資料に含める内容 |
|---|---|
| host_boot | 読取方式/versionと、host起動を識別する値。表示UTCだけで起動同一性を認定しない |
| guest_boot | guest boot IDと対象namespace。別distro/別bootのIDを取り違えない |
| process_set | 対象同期processの役割、開始tick、kernel/local PID対応、親identity、exe SHA/dev/inode、pid/mnt/time namespace。各行の安定キーでsort |
| sync_configuration | 生成設定本体/include展開の各bytes/SHAと有効源の識別。可視package既定fileを稼働設定と呼ばない |
| versions | OS/WSL/Docker/collector dependencyの版と読取方法。ユーザー名など不要な識別を除く |
| clocksource | 選択clocksourceと実際に呼ぶclock API、参照QPC frequency、読取protocol版 |

process_setの対象範囲は管理元reviewで固定し、同名commだけで対象を補充しない。
読取前後のidentity、phase境界、各sampleで可視の変更/読取不能を記録する。
取得していない瞬間のidentityを開始snapshotのコピーで埋めない。
高価なexe hash等をcacheするなら、cacheの同一性/失効条件と可視変更検知を負例で証明し、未観測時間の不変を主張しない。
PID再利用、namespace移動、設定差替えを同一processと扱わない。

<a id="clock-campaign-acquisition--外部源・負荷と失敗時の扱い"></a>

#### 外部源・負荷と失敗時の扱い

外部UTCのraw応答、読取順序、精度根拠、取得遅延の上限から、sample時点を覆うUTC区間を生成する。
QPC校正は固定60秒窓30個とphase全体について、独立参照による経過時間の区間を別に生成する。
不確かさをゼロと仮定したり、QPCから独立参照の秒数を作り戻したりしない。
rawからderived区間への変換は別processでも再計算可能とし、源の真正性reviewは数値一致と別に残す。

負荷は使い捨てfixtureだけへ送り、内容・資源上限・開始終了・cleanupを事前固定する。
現在の実DB/registry/API配信を負荷先にしない。今回具体的な負荷recipeは未実装/未凍結。
SIGINT/TERM、外部readerの停止、disk full、journal書込失敗、子process未終了はrawへ残し、
正常終了markerや資格合格を発行しない。未確定cleanupを成功とせず、今回作った資源だけを照合/回収する。

<a id="clock-campaign-acquisition--実装時の受入負例"></a>

#### 実装時の受入負例

まずfake host/外部readerと使い捨てjournalで、実時計/設定に触れず次を検証する。
nonce/sequence違い・応答喪失/遅延/重複・QPC frequency変化、UTC逆行、1sample欠測と既知違反の併存、
process再利用/namespace/設定変更、raw読取中の変更、終了marker不整合、signal/disk失敗と子process cleanup。
raw→canonical identity→全campaign JSON→offline CLIまで別processで結び、hash/状態/非0伝播を確認する。
fixture成功だけで実採取の独立源、OS修復、保守許可、全時計資格を成立させない。
独立源/校正方法の選定と採取真正性の受入判断はD3/Solが行い、Lunaには判断を固定した後のfixture実装を委ねる。

<a id="clock-fake-acquisition"></a>

## clock-fake-acquisition

<a id="clock-fake-acquisition--fake-readerから全campaign判定までの合成採取"></a>

### fake readerから全campaign判定までの合成採取

2026-11-05、基点`002718e`。[採取protocol契約](clock-implementation.md#clock-campaign-acquisition)のうち、
合成readerと使い捨て原本を使う部分を実装した。
実装は[採取/照合部品](../src/bike_mlops/clock.py)、
[固定reader](../src/bike_mlops/clock.py)、
[受入試験](../tests/test_clock.py)。

**実時計の採取器、実負荷生成器、独立外部時刻源のreaderではない。**
`fixture-load`は合成区間名であり、実サービスへの負荷やCPU負荷の受入を意味しない。
通常/再開後それぞれのidle/fixture-loadを整数で生成する。実際の2時間を観測せず、休止/再開もしない。
fake hostとfake外部参照は同じ合成counterから作るため、源の独立性の証明にはならない。

<a id="clock-fake-acquisition--操作と終了値"></a>

#### 操作と終了値

`261021`で実行する。`ATTEMPT`には**存在しない新しいディレクトリ**を指定し、親だけを先に作る。
この例のplan SHAは採取時に表示される。合成試験であり、表示値をコピーしても実採取の事前reviewを証明しない。

```bash
mkdir -p data/reports
ATTEMPT=data/reports/fake-clock-example-001
.venv/bin/python scripts/clock_tools.py capture-fake --output-dir "$ATTEMPT"
# stdoutの synthetic_plan_sha256= に続く64桁を設定
PLAN_SHA='<表示されたSHA256>'
.venv/bin/python scripts/clock_tools.py derive \
  --input-dir "$ATTEMPT" --output data/reports/fake-campaign-example-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
# exit 2は、数値成功でも時計資格incompleteであることを含む。
.venv/bin/python scripts/clock_tools.py review \
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
| existing review | 1 / 2 | [従来契約](clock-implementation.md#clock-campaign-review)どおり。全時計資格passedやexit 0はない |

`derive`のexit 2だけで数値成功を判断しない。派生JSONの有無とstdout、続く既存reviewのreportを確認する。
依存コマンドは、先行するcaptureのexit 0と新規原本を確認してから進める。
`--fault`は固定された合成負例だけを選び、任意command/URL/実サービスを指定する機能はない。
子processの起動引数も固定で、shellを経由しない。

<a id="clock-fake-acquisition--原本4ファイル"></a>

#### 原本4ファイル

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

<a id="clock-fake-acquisition--要求・応答とrawからの再計算"></a>

#### 要求・応答とrawからの再計算

trialとnonceは32桁のランダムhex。1要求ずつ送信し、role/phase/phase-local sequence/nonce/trialを全てecho照合する。
sampleのguest before→request intent保存→要求送信→応答受領→guest after→sample保存はcontrollerの逐次呼出し順。
応答喪失/中断でも未完了のrequestを原本へ残す。送信前記録だけでは実際に送信完了したと断定せず、sampleのないintentは未確定扱い。
replay時はintentと応答後sampleの要求・before bracketも一致確認する。
readerはUTC-before→QPC→UTC-afterの固定順序を返す。
これはfake protocolの呼出し順の検証で、実Windows APIのbracket真正性を認証するものではない。

通常/再開後の各trialは別token。最初のphaseは0..1800、次のphaseは1..1800を要求する。
次phaseのindex 0には、直前phase終端の**同じraw record**を再利用する。要求し直しや似た値への置換をしない。
全7,202要求、各phase1,801 sample、原本14,415行。real QPCの周期/deadline schedulerは未実装。
2026-11-06追加の[固定周期/終端の合成モード](clock-implementation.md#clock-fixed-schedule)は`--schedule`で選ぶ。
この節の既存形式は省略時の互換モードとして保持し、新形式では欠測/遅延も原本へ結合する。
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

<a id="clock-fake-acquisition--拒否・中断・io境界"></a>

#### 拒否・中断・IO境界

- hashを付け直しても、nonce/sequence/trial/role/phase差、再利用、欠落/重複/並べ替え、余剰応答、周波数/順序違反は拒否する。
- 原本のUTC逆行、identity差、future許可は派生JSONへ残し、既存数値判定でfailedにする。clamp/retryはしない。
- 欠測QPCなどprotocol必須値の欠落は原本照合段階で拒否。併存する既知違反はrawに残るが、数値summaryへ進まない。
  既存offline JSONのnullable sample契約を変更したものではない。
- SIGINT/TERMを中断例外へ変換し、対象readerだけをEOF/TERM/KILLで回収する。
  child生成時だけparent当該threadのsignal maskを保留し、controllerへの所有権移譲後に元のmaskへ戻す。
  fixture childは継承したmaskを自process内だけで解除する。生成後のselector登録失敗もchildを回収する。
  OS全体/他processのsignal設定や時計設定を変える操作ではない。
  エラー種別と回収状態のfailure recordの保存も試す。例外の値/任意argv/認証情報は記録しない。
  SIGKILL/OS停止ではcleanupを保証せず、markerなしの未完了原本を成功扱いにしない。
- reader応答は16KiB、原本fileは各64MiBまで。単一fd/O_NOFOLLOW/非blockingでregular fileだけを読む。
  読取前後と処理全体のstat、最後のbytes再読取で可視変更/同内容replacementも検知する。不可視の一時変更の検知保証はない。
- exact4member以外・symlink・FIFO・重複JSON key・非有限数・manifest/source bytes差・marker/chain差を拒否する。
- 派生outputは原本dir外へ排他的に作り、既存output/原本は上書きしない。stderrに原本値やoperator絶対pathを出さない。

<a id="clock-fake-acquisition--残る実採取条件"></a>

#### 残る実採取条件

独立UTC/経過時間源、認証/不確かさ、review済みの事前plan、実collector/固定負荷/host scheduler、
管理元/修復/保守許可と実休止後の受入、人の全原本reviewは未完了。
[源選定のreview](clock-operations.md#clock-source-readiness)と[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)に従う。
合成成功を実Capital採否、過去の時計原因、実運用p95や時計資格へ流用しない。

<a id="clock-fake-acquisition--最終受入と次の境界"></a>

#### 最終受入と次の境界

実装a5c1605の[CI42037278421](https://github.com/kenobix/Claude_practice/actions/runs/42037278421)で
専用30件を含む全549件/119.271表示秒と両job全stepが成功した。
[最終索引](reviews/20261105-clock-acquisition-ci-checks.json)にunit log・原本/試行hashと既存16 artifactの検算/cleanupを保存する。
CI unitのclock原本は使い捨てで、local代表原本と区別する。16 artifactにclock原本を追加したとは主張しない。

上記はa5c1605時点の受入。続くfake QPCを使った固定周期/phase deadline schedulerの制御は
[追加実装/検証](clock-implementation.md#clock-fixed-schedule)に記録する。
catch-upや観測窓延長で遅延を隠さず、欠測・中断と既知違反を保持する。
これは実host reader/実負荷/独立源未選定のまま進められる制御部の合成受入であり、実採取への許可や資格とは分ける。

<a id="clock-fixed-schedule"></a>

## clock-fixed-schedule

<a id="clock-fixed-schedule--fake-qpcの固定周期・phase-deadline"></a>

### fake QPCの固定周期・phase deadline

2026-11-06、基点`89c5443`。[合成採取](clock-implementation.md#clock-fake-acquisition)の追加モード。
[実装](../src/bike_mlops/clock.py)と[専用試験](../tests/test_clock.py)は
仮想counterを進めるだけで、実時間の待機、Windows QPC、外部UTCや実負荷を採取しない。
fake hostとfake外部参照の共通生成元も変わらず、独立性や実時計資格を証明しない。

<a id="clock-fixed-schedule--実行方法"></a>

#### 実行方法

`261021`で、未使用の原本directoryと原本外の出力pathを選ぶ。

```bash
mkdir -p data/reports
.venv/bin/python scripts/clock_tools.py capture-fake \
  --output-dir data/reports/fake-scheduled-example-001 --schedule punctual
# stdoutのsynthetic_plan_sha256を別に保持する。
PLAN_SHA='<表示された64桁のSHA256>'
.venv/bin/python scripts/clock_tools.py derive \
  --input-dir data/reports/fake-scheduled-example-001 \
  --output data/reports/fake-scheduled-campaign-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
.venv/bin/python scripts/clock_tools.py review \
  --input data/reports/fake-scheduled-campaign-001.json \
  --output data/reports/fake-scheduled-report-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
```

captureのexit 0は原本保存/子process回収の完了だけ。derive/reviewは、数値成功でも全資格incompleteでexit 2。
数値違反はexit 1、不正/未完了の原本もexit 2なので、出力の有無・statusを併せて確認する。
`--schedule`を省略した既存CLI/APIと旧raw形式の読取は維持する。
新形式は`synthetic-clock-scheduled-raw-v1`。source原本はschedulerを加えた7ファイル。
planのworkload SHAに周期/終端/固定scenarioを含め、既存offline policy/hashを変更しない。

<a id="clock-fixed-schedule--固定した予定表と採取の判定"></a>

#### 固定した予定表と採取の判定

- 周波数1,000 tick/秒、周期1,000 tick、phase幅1,800,000 tick。起点と終端を開始時から固定する。
  通常trialはtick 0/1,800,000、再開後の合成trialは4,000,000/5,800,000が各phaseの起点。
- 各slotの予定は`origin + index * 1000`。仮想waitが早く戻る/時計が逆行する場合は未完了で停止する。
- 予定時刻に到達した場合だけdispatchする。1 tickでも過ぎたslotは`missed`として保存し、
  過去の要求を一気に送ったり、その後の予定表を遅らせたりしない。連続した未採取slotも全て記録する。
- 送信前intentに予定/観測/deadlineを保存する。応答後sampleに仮想完了tickと遅延判定を保存する。
  応答の許容完了は`min(予定 + 1000, phase終端)`。それを越えて到着した応答も原本を保持し、欠損計数へ加える。
  遅延をゼロに書き換えず、guest after bracketも仮想完了時点まで広げる。
- phase終端ちょうどの要求は送れるが、それを過ぎてから新しい要求は送らない。
  終端後に受け取った応答は遅延として残り、観測期間を延長した成功endpointへ置き換えない。
  次phaseの起点は固定されたままで、前phaseの同じraw終端をindex 0へ共有する。
  終端が欠測/遅延なら両phaseに不足を伝える。終端を再取得しない。

仮想時計は各scenarioに従って進む。実readerが壁時計のphase終端を過ぎた瞬間に強制停止する実装ではない。
実hostでのdeadline-aware IO/固定負荷/採取は、源と実collectorのreviewが済むまで未実装である。
pipe待ちに使うlocal MONOTONICの3秒timeoutとEOF/TERM/KILL回収は輸送の安全上限であり、QPC校正の代用ではない。

<a id="clock-fixed-schedule--合成scenarioと負例"></a>

#### 合成scenarioと負例

| `--schedule` | 固定された仮想事象 | 記録/判定 |
|---|---|---|
| punctual | 全slotが予定どおり | 各phase1,801 sample、原本14,415行/7,202要求。数値passed、全資格incomplete |
| wake-late | 最初のphaseのslot20で2.5秒遅いwake | 20/21/22を欠測、23から元の予定へ戻る。数値incomplete |
| response-late | 同slot20の応答が2.5秒後 | 20の応答/広いbracketを保持し遅延計数、21/22欠測。数値incompleteまたは併存違反によるfailed |
| deadline | 同slot20でtick1,801,000へ飛ぶ | 20..1800を欠測、終端後の追加要求なし。次phaseの共有起点も欠測 |
| endpoint-late | 最初のphase終端の応答が1 tick遅い | 同じendpointを両phaseへ共有し、両方に不足を残す |
| qpc-backwards | slot20で仮想QPC逆行 | 前回/観測tickとsequenceをscheduler-errorへ保存、partial/回収、markerなし |
| interrupt | 同slotで仮想中断 | 同様にpartial/回収、markerなし |

`--fault`の既存固定reader負例と組み合わせられる。任意command/実endpointを渡す機能は追加しない。
scenarioの選択は合成入力の固定で、実源が独立/真正という自己申告flagではない。

<a id="clock-fixed-schedule--raw-replayと不足の保持"></a>

#### raw replayと不足の保持

4ファイルのexact member、chain/bytes SHA、期待plan、source manifestの照合を経てから、
独立processで各slotの予定/仮想観測/skip/完了/終端/計数を再計算する。
hashを付け直しても、後追いdispatch、欠測行の削除、起点/deadlineの変更、遅延flag/完了tickの隠蔽は拒否する。
source bytesは読取照合だけに使い、保存されたコードを実行しない。hashは第三者認証ではない。

予定どおり要求しなかったslotは既存offline schemaのnullable sampleへ変換する。
無い外部UTCや校正端点を後の応答から埋めず、可能な原本だけで区間を作る。
遅延応答は実際の合成値を保持する。欠測と一緒に存在するUTC逆行/identity差/future許可を、
不足を理由に隠さずfailedとして優先する。数値successを補うためのretry/clampやfuture条件の緩和はない。
captureのphase-end `lost_samples`はそのphaseで要求したslotの損失だけ。
派生phaseはこれに共有endpointの損失を加え、同じ原本が二phaseの受入に必要なことを表す。

SIGINT/TERM、IO timeout、disk失敗や仮想時計逆行でも、対象readerだけを回収しfailure/partialを残す。
生成時のsignal mask保留/復元は旧採取と同じ所有権境界に限定する。
SIGKILL/OS停止、disk障害での全原本保存、不可視の一時差替えの検出を保証しない。
complete公開直後の中断とexit非0の境界も旧契約どおり。実設定/DB/registry/配信は変更しない。

[最終CI索引](reviews/20261106-clock-scheduler-ci-checks.json)で実装c1c5dcb/CI42126656283の
全568件（専用19件）/169.811表示秒、全system・既存16 artifact/hash/archive-only/cleanupを照合した。
clock unitのrawはCIで使い捨て、代表原本はlocal保存であり16 artifactへ加えたとは主張しない。
記録だけの後続commitを先行実装CIで直接試験したとも呼ばない。
[8用語の説明](architecture.md#glossary--合成時計採取の固定周期で使う用語)も参照できる。
実source、真正性/不確かさdossier、
事前reviewの真正な保存、実host deadline/固定負荷、管理元/修復/保守と人の全受入は引き続き未完了。

<a id="clock-raw-bundle"></a>

## clock-raw-bundle

<a id="clock-raw-bundle--合成時計raw原本の可搬bundle"></a>

### 合成時計raw原本の可搬bundle

2026-11-08、基点`99f14cd`。[合成採取](clock-implementation.md#clock-fake-acquisition)の4原本を、1個のZIPだけで別processへ渡す。
[実装](../src/bike_mlops/clock.py)は保存codeを実行せず、現在のoffline verifierで再計算する。
これは実時計のcollector/参照源/認証器ではない。数値が全て良好でも時計資格は`incomplete`を維持する。

2026-11-09追加review：未対応ZIP versionとgeneral-purpose flag（ZIP内の追加機能指定）の入力拒否を補強した。
version 9.9、patched-data（bit 5）、strong-encryption（bit 6）はPythonのZIP readerが
`NotImplementedError`を出すため、従来はCLIがtracebackを表示し終了値1になっていた。
未対応versionを入力エラーへ変換し、固定ZIPが使用しないflagはmember読取前に拒否する。
local headerだけにflagを付けた不一致も含む5変種を、ライブラリと別processのCLIで確認する。
拒否時は終了値2・固定の短い診断だけを返し、原本を書き換えずreport/stagingも作らない。
数値上の既知違反を示す終了値1と、構造が不正な入力の終了値2を区別する。
修正前失敗・修正後試験・CIの範囲は[追加索引](reviews/20261109-clock-bundle-input-checks.json)に記録する。
実装`3d1a5bf`の[CI42539864950](https://github.com/kenobix/Claude_practice/actions/runs/42539864950)は
全590件/専用20件・両job全stepと16 artifact/hash・archive-only再計算・cleanupに成功した。
[最終CI索引](reviews/20261109-clock-bundle-input-ci-checks.json)を参照。後続の説明更新commitを同CIが直接試験したとは呼ばない。

<a id="clock-raw-bundle--使用方法と終了値"></a>

#### 使用方法と終了値

以下は`261021`からの使用例。`--expected-plan-sha256`には、採取時に別に保存したplan SHAを渡す。
archive内の値を期待値として自動コピーする機能はない。合成planの保存は実採取の事前review真正性を保証しない。
既存rawと同じsource revisionを持つverifier環境を用意し、出力には未使用の名前を選ぶ。

```bash
.venv/bin/python scripts/clock_tools.py bundle \
  --export-dir data/reports/fake-scheduled-example-001 \
  --output data/reports/fake-clock-example-001.zip \
  --expected-plan-sha256 "$PLAN_SHA"

# 別process・別作業directoryでも、元raw directoryは不要。
.venv/bin/python scripts/clock_tools.py bundle \
  --review-archive data/reports/fake-clock-example-001.zip \
  --output data/reports/fake-clock-review-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
```

| 操作/状態 | 終了値 | 保存されるもの |
|---|---:|---|
| exportが完成 | 0 | ZIPのみ。資格/数値の合格ではない。既知の数値違反がある完全rawも保管できる |
| reviewで明確な違反 | 1 | member/hash・campaign SHA・全offline reviewを含むreport |
| reviewで数値passedまたは不足 | 2 | 同report。時計資格は`incomplete` |
| 不正schema/ZIP、IO拒否、中断 | 2 | 完成reportは作らない。stderrは入力内容・絶対pathを出さない固定文 |

export stdoutは保存の受領票を返し、時計資格欄は常に`incomplete`を示す。既知違反を含む数値の合否はreview reportで確認する。

CLIではTERM/INTを例外へ変換してprivate stagingを回収し、元のsignal handlerへ戻す。
bundle処理自体は子processを起動せず、archive-onlyの検証試験では別processの終了値も確認する。
atomic publish直後のsignalでは完成fileが存在して非0になり得る。終了値を0へ補正しない。
SIGKILL/電源断時の回収は保証せず、不完全rawにcomplete markerを生成する機能もない。

<a id="clock-raw-bundle--固定memberと結合"></a>

#### 固定memberと結合

ZIPは`manifest.json`と`raw/{plan.json,sources.json,journal.jsonl,complete.json}`の5通常fileのみ。
manifestのkindは`synthetic-clock-raw-bundle-v1`、外から渡すplan SHAと各rawのbytes/SHAを保存する。
ZIP byte SHAもreportへ記録し、raw→chain/nonce/sequence→source manifest/identity→全campaign→offline reviewを再計算する。
失敗/欠測は元の数値を保持し、completeはraw採取/reader回収の完了であって時計資格ではない。

rawは元のbytesを保つ。export前後のfile identity/stat/内容とmember集合を照合し、見える変更を拒否する。
exportは保存source bytesが現在installされた固定6 module/script（scheduled時7件）と完全一致する場合だけ許可する。
再hashした任意codeや`.env`等の余剰fileをZIPへ取り込まない。source bindingは現在のraw契約の範囲であり、
bundle verifier自身/全import/interpreterの実行真正性を認証しない。それらのrevision/hashは別の検証記録で管理する。

ZIP_STORED、固定順序/日時/通常file属性の表現だけを受け付ける。
重複/欠落/余剰、絶対path、traversal、symlink/directory、圧縮・暗号化memberを拒否し、`extract*()`を使わない。
読取は最大64MiB+64KiB、raw総量は64MiB、manifestは16KiBを上限とする。
再構築ZIPとのbyte一致でcomment/extra field・前置/末尾の隠しbyteも拒否する。
単一FD/nofollow/nonblockのstable byte snapshotを検証し、snapshot SHAを保存する。
読取後に外側pathが変化しても、後で置かれたfileを検証したとは主張しない。

<a id="clock-raw-bundle--境界と共有範囲"></a>

#### 境界と共有範囲

別process試験は、元rawを持たないcwdとPATHだけの環境から、ZIP・期待plan・install済みverifierで再計算する。
archiveはcodeの自己完結した実行環境ではなく、対応するverifierが必要である。保存sourceは実行しない。
真正性、源の独立性/不確かさ、実hostのphase deadline、OS修復、保守、実時計資格/実候補採否は未受入のまま。
自己申告やhash一致で実時計の前提を満たすconsumerは追加しない。

代表raw/ZIP/reportはignoredな`data/reports/20261108-clock-bundle/`へ保存し、公開Gitや既存16CI artifactには追加しない。
任意の秘密情報を検出する汎用scannerではない。合成schema/許可sourceでも、公開前には内容と共有範囲を別途reviewする。
既存の実T10 snapshot/protocol/ledgerは変更しない。次の実入力は[必要資料一覧](clock-operations.md#clock-input-request)を参照する。

修正後067d3ceの[CI42449911296/最終索引](reviews/20261108-clock-bundle-ci-checks.json)で全588件（専用18件）と全system、既存16 artifact/hash/archive-only/cleanupを確認した。
初期のZIP末尾byte拒否漏れと、その後のraw/manifest予算混同は、修正前FAILと修正後成功を別索引に保持する。
別processの可搬性は受入済みだが、合成raw ZIPをCI artifactへ公開したものではない。[用語集](architecture.md#glossary--時計源の提出資料と原本bundleで使う用語)も参照できる。

<a id="chrony-runtime-control"></a>

## chrony-runtime-control

<a id="chrony-runtime-control--chronydの配布build照合と内部計算の観測"></a>

### chronydの配布build照合と内部計算の観測

2026-11-04追補：[initdのsetter・Docker Desktop管理層の照合](clock-history.md#initd-owner-correlation)を追加した。
今回のinitdは実exe/親/namespaceまで対応したが、PHC/socket管理者採取・tick11000の内部原因・時計資格は引き続き未了。
以下の11月3日の未確定記述と数値観測は、その時点の記録として保持する。

基点`298c6c82db98c2ae607e69f81e9f99695a4a1f59`、2026-11-03。
[前回の時計調査](clock-history.md#clock-followup)の続き。原本・失敗・hashは
[検証索引](reviews/20261103-chrony-runtime-checks.json)で確認する。

**一致する完全なdebug情報を取得し、非飽和時の内部計算とkernel書込を照合した。
今回tick=11000は再現しておらず、過去の上限到達原因・時計修復・資格確認は未完了。**

<a id="chrony-runtime-control--配布物と稼働実体の対応"></a>

#### 配布物と稼働実体の対応

Microsoftの[公式binary一覧](https://packages.microsoft.com/yumrepos/cbl-mariner-2.0-prod-base-x86_64/Packages/c/)と
[debuginfo一覧](https://packages.microsoft.com/yumrepos/cbl-mariner-2.0-prod-base-debuginfo-x86_64/Packages/c/)から、
Mariner2.0の`chrony-4.1-3.cm2.x86_64.rpm`と対応するdebuginfoを取得した。
OSへinstallせず、libarchiveで必要な通常fileだけを作業directoryへ読み出した。package内のkeyfileは読んでいない。

| 照合 | 結果 |
|---|---|
| RPM内のchronyd | 320,600 bytes、SHA `bf39d010dfc6177cd864d96ddd0cb32eb796704e8c68fe8bd65088d20d2e64b8`。過去の管理者採取と一致 |
| Build ID | binary/debugとも`5bff56b8a81ac12e2fbf0c249759c6e1271cdac6` |
| debuglink | `chronyd-4.1-3.cm2.x86_64.debug`、CRC32 `c87254da`。取得debug fileと一致 |
| 変数の型・位置 | 完全DWARFのcompile unitと型を照合。sys_generic.cのdouble6項目、sys_linux.cのint2項目とdouble1項目を限定 |
| 同名変数の区別 | `max_freq`・`offset_register`・`set_frequency`等は別fileにも存在。名前だけでアドレスを選ばない |
| 稼働中の実行file | 読取FDのdev42/inode4227/SHAと、同inodeの指定命令で発生したchronyd213のeventが対応 |

対応debug RPMに含まれる`conf.c`・`local.c`・`sys_generic.c`は取得済み上流4.1と全bytes一致した。
`sys_linux.c`の差はseccomp許可syscallのclone3/pread64追加で、確認した差分に周波数計算の変更はなかった。
これは再現buildやRPM署名の検証ではない。公開HTTPS・SHA・Build ID・debuglink CRCの照合範囲として記録する。

<a id="chrony-runtime-control--稼働値を読む前の負例と方法の検証"></a>

#### 稼働値を読む前の負例と方法の検証

既に稼働するsystem distroの公開chronyd fileをO_RDONLYで開き、専用Unix socketのSCM_RIGHTSで読取FDだけを渡した。
mountやWindows管理者tokenの取得、daemonへのsignal、ptrace停止、process全メモリのdumpは行っていない。
**このFDはhelperが開いた実行fileであり、daemonのPHC fdを採取したものではない。**

先に、時計syscallを含まない小さなPIE fixtureで、1.5→−2.25という既知のdoubleを検証した。
初回はtracefsのO_CREAT付きopenがEINVALとなり、専用instanceを回収した。
O_CREATを外すとeventは記録できたが、`@+file-offset`方式は既知値を0と読んだ。
data LOADのoffset変換を変更しても0のままで、fixture検証を通らない実行はchronyd採取の前で停止した。
この方式が0となる根本理由は未確定で、offset変更だけで修正済みにしない。

明示pointer・IP相対の対照採取では正しい値が読めた。
そこで入口とret命令直前の2地点を使うIP相対方式に限定し、両地点の既知値が一致してから稼働採取した。
ret命令の位置は一致したbinaryの逆アセンブルで確認し、関数入口と同じ相対値をreturn側へ流用しない。

<a id="chrony-runtime-control--35秒と75秒の固定した観測結果"></a>

#### 35秒と75秒の固定した観測結果

時間はMONOTONIC基準。現環境の秒数は外部秒として資格確認済みではない。
成功やtick11000が出るまで延長・自動retryする採取ではなく、目的の異なる2つの固定attemptを保存した。

| attempt | 確認結果 | 限界 |
|---|---|---|
| 最初の35秒 | 入口/出口8組で内部状態と計算結果が一致 | kernel書込を同時採取しておらず、driver cache/戻り値からの検算 |
| 続く75秒 | 16組で内部計算、同じTIDのtimex入力、成功戻り値、driver cache/戻り値を照合 | 32秒pollを複数回含む有限区間。tick11000/負方向上限制限は0組 |

基準側の`base_freq`は両区間で約−73,579.259270ppm。
`max_freq`は100,000ppm、`max_corr_freq`は1/12だった。
これは内部の推定・制御値であり、物理時計の誤差を校正して得た値ではない。

確認した計算は、残った時刻差と補正の速さから補正比を作り、基準周波数と合わせてdriver範囲へ制限する経路である。
検算では概ね次を用いた。実際には小さい時刻差の境界や整数量子化も扱う。

```text
duration = max(correction_rate / abs(offset_register), 1)
corr_freq = clamp(offset_register / duration, -max_corr_freq, +max_corr_freq)
total = clamp(base_freq + corr_freq × (1000000 - base_freq), -max_freq, +max_freq)
kernel correction ppm = 100 × (10000 - tick) - freq / 65536
```

75秒区間では16組すべて、量子化の範囲内で計算が一致し、対応するclock_adjtimeは0を返した。
同区間にtimesyncd496の負方向要求3件も記録したが、**それだけで過去の上限到達原因や長期feedbackを確定しない。**
今回観測した非飽和状態を、以前のtick11000期間の内部状態へ遡及適用しない。

<a id="chrony-runtime-control--回収と残件"></a>

#### 回収と残件

すべてのfixture/稼働attemptで専用instanceとprobeを回収し、global trace制御・kprobe/uprobe定義の前後hash一致を確認した。
稼働採取のbuffer lost/overrun/droppedは0。実行file bytesの前後SHAも一致し、FD・一時IPCは閉じた。
ただしuprobeは一時的に命令をinstrumentする診断で、観測の負荷がある。今回の時間・挙動を無観測時の性能や時計資格へ流用しない。
OS時計/同期設定、実DB・実registry・現配信は変更していない。

次は[管理者採取](clock-operations.md#clock-owner-admin-read--chrony-resources-admin)でPHC fd/UDP所有、現在の開始時刻・親・namespaceを照合する。
追加readerはexeのdev/inode/sizeを前後比較し、hashが同じ別inodeへの変更も不完全扱いにする。
現在の実行fileの対応が分かっても、元のlauncherやPHC/socket所有が全て確定したわけではない。

tick11000が自然に現れる区間で同じ限定数値とtimexを対応させること、initdの実体、host外部UTC差/同期責任、
許可された保守比較と資格確認は残る。OS修復は別途許可が必要。future拒否の緩和・clamp・自動retryは行わない。
T03は今回9件成功して自然停止未再発、T09は公式202610が404のままで、実fitは行っていない。

<a id="chrony-runtime-control--readerのci受入"></a>

#### readerのCI受入

実装`f45733a467e09c79d9d557682195c85da37b8b4a`の[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704)は全464件・全system成功。
[CI索引](reviews/20261103-chrony-runtime-ci-checks.json)で実artifact16member/hash・別process再計算・正常/TERM/対応復元cleanupまで確認した。
17件のreader fixtureを含むコード回帰の受入であり、この文書の稼働uprobeをGitHub runnerで再現したことや、時計資格の合格ではない。
初期local索引のCI_PENDINGは当時の記録として保持する。後続文書commitはこの実装CIが直接試験したcommitではない。
