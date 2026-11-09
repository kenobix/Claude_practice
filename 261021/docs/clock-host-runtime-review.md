# Windows管理者ログのruntime設定・同期責任review

2026-11-09、基点`b0589ee`。新しい`clock-host-admin-00f021d1-2b24-4c53-aaf9-325fbe81cdba`をreviewした。
[今回の証拠索引](reviews/20261109-host-admin-checks.json)、[前回のPHC/UDP照合](clock-resources-admin-review.md)、
[最新計画](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**前回拒否されたsource/configurationの管理者読取は完了。WindowsはNTPを設定しているが、採取時は同期未実行と報告した。**
これは実際の提出ログのreviewである。時計のUTC誤差の実測、未同期原因の確定、時計資格の合格とは区別する。

基点`9c32298`後の[peer/status/identity追補](clock-host-cause-review.md)で、下記のpeer/last-error読取不足も解消した。
全体のno time dataとpeerのpending/現在有効sample0を照合済み。以下の「次の読取」は当時の履歴であり、同じ採取を直後に反復しない。
次は新しい状態変化/独立源資料、または[別途許可された保守比較](clock-host-maintenance-plan.md)を条件とする。

## 原本・真正性・identityの範囲

添付consoleの保存先と実directoryが一致し、`status/source/configuration`はすべてnative exit0。
`collection.json`の管理者/読取mode/query順と実出力を照合した。UTF-8 BOM/CRLFの元bytesを保持し、decodeした解析文字列だけを正規化した。
原本6fileのうち、`hashes.json`が列挙する5fileのSHAが全一致。manifest自身と添付原本のSHAも別に保存した。
UNC pathは期待するdirectoryとbasenameだけへ照合し、manifestに書かれた任意pathを開いていない。
別ignored review directoryのsnapshotと原本も一致する。

SHAは受領後の内容結合であり、署名・採取前の改変防止・独立源の真正性を保証しない。
`admin=true`だけで認定せず、提示された実行手順/console/query出力との整合を確認した範囲の受入である。
今回手順はhost boot ID・OS版・service開始時刻/exe SHA・UTC/QPC取得bracketを採っていない。
sequentialなqueryを同時snapshotや連続監視と呼ばず、file mtimeを正確な採取UTCへ代用しない。
前回のWSL boot/PID/dev/inodeや過去のsetterを、今回のhost captureと動的に結合したとは主張しない。

## 確認した値と判断

| 対象 | 提出された値 | 判断・限界 |
|---|---|---|
| service | W32Time Running/Manual、vmictimesync Stopped/Manual | 前回の読取と同じ値。serviceが動いていることだけで同期成功にならない |
| status | LI3（同期未実行）、階層0、参照ID0、最終正常同期未指定 | このqueryが未同期と報告。実UTC誤差が何秒かや、過去全期間の未同期は不明 |
| source | `Local CMOS Clock`、statusのsourceとも一致 | 今回使用中と報告するsource。設定peerの名前とは違う |
| NtpClient | Enabled1、InputProvider1、Type=NTP、peer=`time.windows.com,0x9` | 前回の保存registry値に加え、今回はruntime設定として確認 |
| NtpServer | Enabled0、InputProvider0 | hostから他機器へ時刻を配る機能の設定。NtpClientの無効と読み違えない |
| 設定の出所 | 解析した34設定が`ローカル` | この出力の設定起源。全policy不存在の証明ではない |
| 間隔 | status poll10（1024秒）、config MinPoll10/MaxPoll15、SpecialPollInterval16384 | 瞬時status値と設定値をそのまま記録。次の送信時刻や「待てば修復する」を推測しない |
| 補正関連 | MaxAllowedPhaseOffset1、MaxNeg/PosPhaseCorrection各54000、UpdateInterval360000、SpikeWatchPeriod900 | 設定値であり、今回step/進行率変化が起きた観測ではない。実offsetを設定閾値から逆算しない |
| VMICTimeProvider | 通常configuration出力にsectionなし | 前回の保存registry Enabled1とも区別。section不在をprovider完全不存在/稼働の証拠にしない |

[Microsoftのquery/設定説明](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)で、
configurationはruntime設定、0x9はClient0x8とSpecialInterval0x1の組合せと確認した。
このflagは源の暗号認証や精度証明ではない。`119.209ns`のprecision表示もUTC精度を保証せず、
root delay/dispersion0やLocalClockDispersion10を測定不確かさとして採用しない。
UpdateIntervalをネットワークpoll秒数と読まず、設定値を短くする対処も実施していない。

前回既存イベントにはDNS失敗134だけでなく、後の受信37/同期開始35もあった。
今回はpeer状態・last-error・当該query付近の新イベントが無いため、現在原因をDNS/UDP/大offset/起動直後のいずれかへ断定しない。
「設定済みだが同期未実行」は確認済みで、「設定が壊れている」「time.windows.comが停止中」「恒久的な通信遮断」は未確認である。
未同期は源の準備不足として記録するが、外部UTCなしに固定policyの±1秒違反を実測したとは扱わない。
全資格は必須資料未達の`incomplete`を維持する。

## 同期責任の対応表

| 管理層 | 確認済みの役割 | 残る動的情報・責任の境界 |
|---|---|---|
| Windows W32Time | hostのNTP入力を設定。今回は実使用sourceがLocal CMOSで未同期 | peer/last-error等で未同期の条件を絞る。host修復をguest serviceの停止で代用しない |
| Hyper-V/WSLのPHC | 前回のchronyd fd3がhyperv PHCを開く | host依存経路なのでhostと相互比較して独立校正としない。今回host queryとPHC sampleの同時値はない |
| Mariner chronyd | 前回PID213のPHC設定/FD/UDP323・生成設定/mini_init親を照合済み | 新しいtick11000内部値/kernel書込なし。今回host未同期を過去の飽和原因へ遡及適用しない |
| Ubuntu timesyncd | 前回PID973/systemd配下、旧PID496とは開始tick/本人と親SHAが変化 | 現実体の同期先/稼働経緯は未照合。旧setter496を新実体へ帰属しない |
| Docker backend/initd | 保存ELFにbackend時刻取得→Settimeofdayの経路を静的照合済み | backendの実応答・host時計API・該当setter動的stackは未採取。initd全体を停止しない |
| 独立UTC/elapsed参照 | 未選定/資料未提出 | host/PHC/QPCと共有する故障源、真正性、輸送/読取の不確かさを別途review |

[CanonicalのWSL説明](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)はhost側同期とguest NTPの競合可能性を説明している。
本環境で競合が実際にどのイベントを起こしたかの動的証明ではなく、Ubuntu用の既定値をMarinerへ適用しない。
文書にservice停止例があっても、今回の実行許可や修復結果にはしない。

## 次の読取と、修復案・影響・切戻し

同じbasic queryやPHC/UDP採取は再依頼しない。
未同期の原因切り分けに新しく必要なのは、[限定したpeer/verbose状態・既存の新イベント](clock-owner-admin-read.md#host-w32time-cause-read)。
この追加読取は独立参照の採用や実資格collectorではなく、source/last-errorの現状診断である。
新情報を受け取るまではtrace釣りや受入済み試験/原本取得を反復しない。

| 段階/案 | 開始条件と具体的な進め方 | 影響と切戻し |
|---|---|---|
| 原因の限定読取 | 新しいpeer状態/last-error/既存イベントとhost boot/service identityを保存する。失敗や欠落もそのままreview | query/log読取のみ。OS設定の切戻しは不要。同じ保存先を上書きせず原本を残す |
| hostのNTP回復案 | 到達性/受信拒否/offset等の実根拠を得て、修復対象と変更前値を確定する。許可前にresync/設定変更/防火壁変更は実行しない | resyncも時計をstepさせ得る変更。承認時刻やguestへ影響。設定の復元は管理元の元値へ戻すが、経過した時計履歴は戻せない |
| guest補正層の整理案 | host/backend/PHC責任が分かってから、明示許可と保守内でUbuntu管理service等を1条件ずつ比較する案をreview | shared kernel/Dockerへ影響し得る。元のactive/enabled/config hash・復元コマンドを先に固定。自動生成Mariner設定の直接編集やinitd killはしない |
| 全資格 | 独立源の採用、修復判断、実collector/固定負荷の受入、保守許可後に固定policyの全期間で新規採取 | 短期回復やsource名変更だけでpassedにしない。boot/設定/identity変更後は資格を持ち越さず、過去p95は補正しない |

今回、別途許可を要する修復操作は提案の前提整理までに留めた。
OS/時計・実DB/registry/現配信・future拒否は不変。実fit/採点、T02実producer/T11/T12/T13bは前提待ち。
T09/T10静的受入は完了のまま維持し、独立源のない実採取器やT10再集計を新規進捗として追加しない。

## 今回の検証範囲

ignoredな`data/reports/20261109-host-admin-review/review.py`で、6原本・manifest5件・UTF8 decode・query順/exit・provider別34設定を照合した。
12負例（うち7件はmember SHAも再計算）で、改変/欠落/余剰/不正UNC/重複entry/query失敗/admin false/
source不一致/client無効/provider差替え/status重複/JSON重複keyを拒否した。
これは今回原本専用のoffline診断で、製品unit12件追加や一般的な入力validatorの受入ではない。
製品source/schema/writer/lock/workflowは変更せず、製品suite/受入済みCIを反復しない。
追加案内のPowerShellは構文だけ確認し、管理者blockの実採取を今回実行したとは主張しない。
