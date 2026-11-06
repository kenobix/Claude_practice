# fake QPCの固定周期・phase deadline

2026-11-06、基点`89c5443`。[合成採取](clock-fake-acquisition.md)の追加モード。
[実装](../src/bike_mlops/clock_campaign_schedule.py)と[専用試験](../tests/test_clock_campaign_schedule.py)は
仮想counterを進めるだけで、実時間の待機、Windows QPC、外部UTCや実負荷を採取しない。
fake hostとfake外部参照の共通生成元も変わらず、独立性や実時計資格を証明しない。

## 実行方法

`261021`で、未使用の原本directoryと原本外の出力pathを選ぶ。

```bash
mkdir -p data/reports
.venv/bin/python scripts/capture_fake_clock_campaign.py \
  --output-dir data/reports/fake-scheduled-example-001 --schedule punctual
# stdoutのsynthetic_plan_sha256を別に保持する。
PLAN_SHA='<表示された64桁のSHA256>'
.venv/bin/python scripts/derive_clock_campaign.py \
  --input-dir data/reports/fake-scheduled-example-001 \
  --output data/reports/fake-scheduled-campaign-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
.venv/bin/python scripts/review_clock_campaign.py \
  --input data/reports/fake-scheduled-campaign-001.json \
  --output data/reports/fake-scheduled-report-001.json \
  --expected-plan-sha256 "$PLAN_SHA"
```

captureのexit 0は原本保存/子process回収の完了だけ。derive/reviewは、数値成功でも全資格incompleteでexit 2。
数値違反はexit 1、不正/未完了の原本もexit 2なので、出力の有無・statusを併せて確認する。
`--schedule`を省略した既存CLI/APIと旧raw形式の読取は維持する。
新形式は`synthetic-clock-scheduled-raw-v1`。source原本はschedulerを加えた7ファイル。
planのworkload SHAに周期/終端/固定scenarioを含め、既存offline policy/hashを変更しない。

## 固定した予定表と採取の判定

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

## 合成scenarioと負例

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

## raw replayと不足の保持

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
[8用語の説明](glossary.md#合成時計採取の固定周期で使う用語)も参照できる。
実source、真正性/不確かさdossier、
事前reviewの真正な保存、実host deadline/固定負荷、管理元/修復/保守と人の全受入は引き続き未完了。
