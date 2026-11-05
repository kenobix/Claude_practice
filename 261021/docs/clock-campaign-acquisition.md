# 全観測期間の採取protocol：次の実装に向けた契約

2026-11-05。追補：[fake採取/原本結合](clock-fake-acquisition.md)と[源選定review](clock-source-readiness.md)を実装・整理した。
以下は実装前に固定した契約として保持する。実collector/固定負荷/実外部readerは未実装、実資格は未達。

2026-11-05。[全観測期間のoffline入力](clock-campaign-review.md)を作る採取器の実装前契約。
採取器、外部reader、負荷生成器を実装・実行した証拠ではない。
既存の[受入設計](clock-qualification-plan.md)を維持し、以下の未選定事項がある間は本採取を開始しない。
OS修復/休止再開/実サービスへ負荷をかける許可もこの文書で付与しない。

## 先に確定する入力と保存

- 独立外部UTC源、独立した経過時間参照、源自身の精度・輸送/取得遅延の不確かさ、読取実装をreviewする。
  公開NTP名、HTTP Date、host UTCやPHC応答を見つけただけで独立性や精度を認定しない。
  使用可能な源が未定なので、今回provider/addressを仮に埋めて動かすreaderは作らない。
- collector/workload/UTC readerの実行file、config、protocol、公開可能な源識別を採取前に固定し、bytes/SHAを保存する。
  collector/workload/reader SHAはentry fileだけでなく依存コード/設定を含む再構築可能なmanifestへ結合する。
- planのcanonical SHAを、trial開始前に別のreview記録へ固定する。後から同じfileへSHAを書くだけで事前性を証明しない。
  外部源の認証情報、全環境変数、個人argvや無関係なprocess一覧を公開原本へ含めない。
- 全trial UUID、開始/終了、command/終了値、raw journal、manifest、derived JSONを新pathへ排他的に保存する。
  原本には合成/実採取の区分を入れ、生成済みの合成fixtureを実採取へ転記しない。

## Windows/WSL間の要求とbracket

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

## identity hashの元資料を定義する

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

## 外部源・負荷と失敗時の扱い

外部UTCのraw応答、読取順序、精度根拠、取得遅延の上限から、sample時点を覆うUTC区間を生成する。
QPC校正は固定60秒窓30個とphase全体について、独立参照による経過時間の区間を別に生成する。
不確かさをゼロと仮定したり、QPCから独立参照の秒数を作り戻したりしない。
rawからderived区間への変換は別processでも再計算可能とし、源の真正性reviewは数値一致と別に残す。

負荷は使い捨てfixtureだけへ送り、内容・資源上限・開始終了・cleanupを事前固定する。
現在の実DB/registry/API配信を負荷先にしない。今回具体的な負荷recipeは未実装/未凍結。
SIGINT/TERM、外部readerの停止、disk full、journal書込失敗、子process未終了はrawへ残し、
正常終了markerや資格合格を発行しない。未確定cleanupを成功とせず、今回作った資源だけを照合/回収する。

## 実装時の受入負例

まずfake host/外部readerと使い捨てjournalで、実時計/設定に触れず次を検証する。
nonce/sequence違い・応答喪失/遅延/重複・QPC frequency変化、UTC逆行、1sample欠測と既知違反の併存、
process再利用/namespace/設定変更、raw読取中の変更、終了marker不整合、signal/disk失敗と子process cleanup。
raw→canonical identity→全campaign JSON→offline CLIまで別processで結び、hash/状態/非0伝播を確認する。
fixture成功だけで実採取の独立源、OS修復、保守許可、全時計資格を成立させない。
独立源/校正方法の選定と採取真正性の受入判断はD3/Solが行い、Lunaには判断を固定した後のfixture実装を委ねる。
