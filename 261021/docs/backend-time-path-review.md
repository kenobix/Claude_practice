# backend時刻取得経路の静的照合と保守判断

2026-11-04、基点`891a420`。[前回のinitd管理層照合](initd-owner-correlation.md)の続き。
原本・失敗・hashは[今回の検証索引](reviews/20261104-backend-boundary-checks.json)を参照する。

**保存済みの同じinitd binaryに、backendの時刻を取得し、Settimeofdayを呼ぶ経路が存在した。**
今回は実行fileの静的解析であり、新しい時計書込・backend応答・goroutine stackの観測ではない。
現在のPHC/UDP所有、tick11000の内部理由、hostの外部UTC精度、時計資格は未解決。

## 方法と不採用の試行

前回保存した公開ELFのSHA
`37ddac517530bcdf87c40c2b1df37188d093a6ee6204df43e64edbfba3c27b43`を先に照合した。
稼働processへのattach・memory読取・uprobe・追加traceやbackend HTTP要求は行っていない。
今回の結果はこの保存済みbinaryだけに結び付き、現在も同じPID/開始時刻/inodeとは主張しない。

[Go公式pclntab reader](https://go.dev/src/debug/gosym/pclntab.go)と
[runtime moduledata](https://go.dev/src/runtime/symtab.go)を参照した。
ELFはPIE形式で、disk上のpcHeader.textStartは0だった。
最初の解析はこれを基準にしたため、CALLの多くが未解決で、関数途中を逆アセンブルする結果となった。
scriptのexit0でも正しい解析とは扱わず、log・不正なassembly/summaryを`static-v1-invalid/`へ保存して不採用にした。

次の試行は`.go.module`のR_X86_64_RELATIVEを使ったが、最終functab境界をetextと同一視してassertで失敗した。
maxpcとetextには末尾paddingがある。失敗を残し、minpc/text・maxpc/functab末尾・etextとの範囲関係を別々に検証した。
最終試行はtext=`0x832640`、maxpc=`0x2176ec1`、etext=`0x2176ed0`、75,982関数の単調な境界を照合した。
section先頭へ推測で一律補正したものではない。

## コードとして対応した範囲

アドレスはこのELF内のvirtual address。ASLR後のruntime addressやfile offsetと混ぜない。

| 対象 | 静的に照合した内容 |
|---|---|
| `desktop.runBackendTimeSync` | `0x2095280`〜`0x2095880`。`backendclient.New`、`time.Now`、`time.Since`、`syscall.Settimeofday`等への直接CALL |
| backend呼出し | `backendclient.New`が返すinterface table=`0x40a8458`。呼出し側のoffset=`0x140`とrelocationを結び、`(*backendClient).GetCurrentTime`=`0x109a6c0`へ対応 |
| 時計書込 | CALL site=`0x20955d8`の相対命令bytesからtarget=`0x8e4f20`を再計算し、`syscall.Settimeofday`の関数境界へ照合 |
| 時刻要求の識別 | 同経路のliteralに`backend /time request timed out: %v`、`backend time: %w`、`settimeofday: %w`。単に全binaryを文字列検索した結果ではなく、対象関数から参照するbytesを確認 |

これにより、backend時刻同期という説明は「symbol名が存在する」段階から、同じコード内の取得interfaceと時計書込CALLの対応まで進んだ。
ただしbackendの応答値、接続先process、backend内のWindows時計取得関数は未観測。
前回の負方向setterがこの関数を実行したとする動的stackも今回取得していない。
過去の全T03/T13失敗や、chronyd飽和の原因をこの経路へ断定しない。

検算では同じELFのCALL opcode/相対target、interface/relocationとsummaryを比較した。
合成コピー5件（基準アドレス0、Settimeofday欠落、CALL target改変、別ELF SHA、別interface method）を全拒否した。
診断summaryの検証であり、製品に新しく5 unit testを追加したという意味ではない。

## 公開仕様と修復案への影響

[CanonicalのWSL時計説明](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)は、Hyper-V側とguest NTP側が異なる時刻源を使う場合の競合を説明している。
今回の複数setter観測と整合する一般的な説明だが、本環境の飽和原因を確定するものではない。
同資料にはUbuntu releaseごとの既定動作の違いがある。Ubuntu用の説明をMariner側chronydの生成設定と取り違えない。

Dockerの[利用者設定](https://docs.docker.com/desktop/settings-and-maintenance/settings/)と
[管理者設定の公開一覧](https://docs.docker.com/desktop/enterprise/hardened-desktop/settings-management/settings-reference/)も保存・確認した。
確認した2資料からは、今回のbackend時刻同期だけを停止する設定を特定できなかった。
全実装・全releaseに設定が存在しないとする証明ではない。推測したJSON keyや非公開endpointで切り替えない。

| 段階 | 次の条件・影響・切戻し |
|---|---|
| 読取を継続 | 新しい[管理者PHC/socket採取](clock-owner-admin-read.md#chrony-resources-admin)で現在identity/所有/設定を先に確認。今回は旧log1件だけで、新しい採取はなかった |
| 同期責任を設計 | host/backendの時刻源と外部UTC差、Hyper-V・Mariner chronyd・Ubuntu timesyncd・Docker initdの役割を分ける。Ubuntuの調整者1つを止めても、残りが消えたとは扱わない |
| 保守比較案 | 別途OS変更許可と保守時間を得てから、管理元で1条件ずつ比較する案をreview。現配信はDockerに依存するため、Docker停止を無影響の診断として実施しない。initd全体のkillも行わない |
| 影響と切戻し | 元のservice active/enabled、生成設定・exe/hash、Docker管理状態を保存し、同じ管理元で元へ戻す手順を先に固定。共有kernel全体への影響と、時計履歴は消せない点を残す |
| 資格確認 | 修復許可後に事前固定した長時間/休止再開、QPC/RAW/MONOTONIC、UTC順序、厳密future拒否を新規採取。短い未再発を合格にしない。過去p95を後から補正しない |

上記は保守案の前提整理で、実施許可や修復済みを意味しない。
OS/時計設定、実DB/registry/現配信は不変。future拒否の緩和・clamp・自動retryは行っていない。
T03の自然停止は今回観測しておらず、新しいphase/stackはない。
