# 時計資格の全観測期間をofflineで照合する

基点`fb33af2`、2026-11-05。[事前の受入設計v1](clock-qualification-plan.md)の
通常60分と休止再開後60分を、保存済みJSONからまとめて照合する。
[既存の30分進行率部品](clock-rate-review.md)を4区間分再計算し、提出された「合格report」は入力にしない。
入口は[review_clock_campaign.py](../scripts/review_clock_campaign.py)、
計算は[clock_campaign_review.py](../src/bike_mlops/clock_campaign_review.py)、
合成例と負例は[test_clock_campaign_review.py](../tests/test_clock_campaign_review.py)。

**数値・構造の`offline_checks_status=passed`でも、`clock_qualification_status=incomplete`のまま。**
ハッシュは内容の結合を検証するが、採取の真正性や独立した時刻源の品質を認証しない。
人のreview、修復・保守許可、sourceの独立性/校正と採取protocolの検証は、この入力に自己申告flagを足しても満たせない。
明確な違反は`failed`を優先し、不足もissuesに残す。実Capital採否、実運用性能、過去原因の解決には流用しない。

## 固定policyと入力hash

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

## phase dataと全sample

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

## 外部UTCとQPC校正

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

## future probeと休止境界

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

## 実行・結果・残る条件

採取済みJSONと**事前に別保存した**plan SHAが必要。以下は将来の使用例で実採取の実行記録ではない。

```bash
.venv/bin/python scripts/review_clock_campaign.py \
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
