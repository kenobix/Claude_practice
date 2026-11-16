# P0隔離仕様・実測証拠

検証日: 2026-11-17 JST（JSONの時刻はUTC）。判定: **P0完了（管理下の固定fixtureによる開発検証範囲）**。
このP0実測時点ではP1の受付API・Workerは未実装だった。現在は[受付API](intake-api.md)を実装済み。Workerとジョブ実行経路は引き続き未実装。
P0用のfixtureと監視処理はtests/・scripts/に置き、アプリ本体から参照しない。

## 環境と再現手順

- Docker Engine 29.9.2、Docker Desktop、x86_64、kernel `6.6.87.2-microsoft-standard-WSL2`。
- cgroup v2、cgroupfs、標準seccomp、cgroup namespace。rootlessは未採用。
- ホスト側Python 3.12.3、fixture側Python 3.12.16。
- ベースイメージ: `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`。
- fixtureイメージID: `sha256:037c44a08b4ee0167d3d37fd65e426585cd162f9adf5757349a9d55276e9d5b1`。実測後の一時タグ・イメージは削除した。
- [生の実測JSON](isolation-evidence.json)に各コンテナの設定・終了状態・削除確認・出力バイト数・ソースSHA-256を保存した。
- JSONのgit_headは実測時点の親commit。当時のソースはsource_sha256とcommit `31d3467` のファイルで照合できる。
  P1開発時の回帰で監視ハーネスの同期kill待ちを修正したため、現在の回帰証拠は別の[p1-p0-regression.json](p1-p0-regression.json)に記録する。

```bash
cd ~/work/Claude_practice-261117/261117/ci_runner
.venv/bin/python scripts/verify_isolation.py
# 通常はignored artifacts/isolation-latest.jsonに最新結果を生成
# docsの証拠を意図的に更新するときのみ:
.venv/bin/python scripts/verify_isolation.py --evidence "$PWD/docs/isolation-evidence.json"
.venv/bin/python scripts/verify_db.py
```

Docker接続が拒否された場合は失敗として終了し、mockやホスト実行へ代替しない。
通常pytestではDockerテストを明示的にskipする。verify_isolation.pyのみがopt-inを設定する。
実測実行は10 passed（36.23秒）。DB回帰は4 passed、10 skipped（Dockerを二重実行しない）、既知のTestClient警告1件。

## 設定と強制動作の対応

| 制限 | 設定・実行方法 | 実測と期待結果 | 判定 |
|---|---|---|---|
| CPU | --cpus 0.5、cpu.max=50000 100000 | 3秒busy loopでCPU 1.491秒 / wall 3.000秒、比率0.497。throttled 30回、1236026µs | 抑制を実証 |
| メモリ | --memory 128m --memory-swap 128m、memory.max=134217728、swap.max=0 | 最大192MiBの有限割当を128MiB枠内で実行。OOMKilled=true、exit 137 | OOM停止を実証 |
| PID | --pids-limit 64、pids.max=64 | 親1+子63でpids.current=64、次のforkがEAGAIN(11)、pids.events max増加1 | 追加生成拒否を実証 |
| 時間 | attach開始からmonotonic時計10秒、期限時docker kill | SIGTERMを無視して60秒待つfixtureを10.380秒で終了、exit 137、OOMなし | 監視処理による強制終了を実証 |
| stdout/stderr | selectorsで同時読取、合計保持1MiBで打切り・kill | stdout 507904 + stderr 540672 = 1048576 bytes、exit 137 | 保持上限と停止を実証 |
| daemonログ | --log-driver none | HostConfig.LogConfig.Type=none。保存ファイルを生成するログdriverを利用しない | 実効設定確認 |
| 非root | --user 65534:65534 | 実プロセスuid/gid=65534、setuid(0)がEPERM(1) | 昇格操作拒否を実証 |
| capability/特権 | --cap-drop ALL、privileged=false、no-new-privileges=true | CapEff/CapBnd=0、NoNewPrivs=1、raw socket生成がEPERM | 権限拒否とkernel状態を実証 |
| seccomp | Docker標準profile | /proc/self/statusのSeccomp=2 | filter有効を観測。全syscall網羅試験ではない |
| ネットワーク | --network none、公開portなし | interfaceはloのみ、TEST-NET 192.0.2.1:443接続がENETUNREACH(101) | 外向き接続拒否を実証 |
| root FS | --read-only | イメージ内0666のcanaryへの書込がEROFS(30) | UID権限の影響と区別して拒否を実証 |
| 書込領域 | /tmpだけtmpfs 16MiB | 通常書込成功、16MiB後の追加書込がENOSPC(28)、削除成功 | 書込先と容量制限を実証 |
| tmpfs実行 | noexec,nosuid,nodev | 実行ビット付きスクリプトのexecがEACCES(13) | noexecを実証。nosuid/nodevは設定確認のみ |
| Docker socket/host資源 | bind・device・host PID/IPC・host network・host環境変数の継承なし | socket接続ENOENT(2)、/hostなし、ホストenv canary非継承。Mountsにhost/volumeなし | 当該経路の非公開を実証 |
| 回収 | 各ケースfinallyで作成したIDだけrm -f -v | 全10コンテナで終了後Pid=0・removed=true。sessionラベルの残存コンテナ0 | 停止・削除と無残存を実証 |
| 起動拒否 | stress前にuid/cgroupの実値を照合 | メモリを意図的に64MiBへ厳しく変更したケースはmemory.max不一致でexit 1、stdoutなし | 設定不一致で負荷を開始しないことを実証 |

## ログ上限の正確な意味

上限は**監視処理が保持するstdout+stderrの合計バイト数**。超過検出時にコンテナをkillする。
既にコンテナ・daemon・Docker CLI・pipeへ送られたデータを遡って消せないため、出力された総量を厳密に1MiBへ制限する保証ではない。
今回の有限fixtureはstdout/stderr各2MiB、合計4MiBを生成し、観測値も各2097152 / 2097152 bytesだった。
監視処理は超過分を保持せず破棄し、daemonログはnone。保存するログは1MiB、kill後のexitは137。
証拠JSONにも大量ログ本体を保存せず、保持量・観測量・SHA-256だけを残した。

## 安全性と後片付け

各probeの実行前にcpu.max、memory.max、memory.swap.max、pids.max、uidをコンテナ自身で確認する。
違えば負荷の前に停止する。stressは並列実行せず、CPU 3秒、割当192MiB、fork試行80回までの有限処理。
CPU/RAM/PIDとも上限なしの対照実験は行わない。既存workloadの構成を変えず、制限内の強制動作を確認する。既存workloadへの性能影響がゼロであることは測定していない。

各ケースで作成したcontainer IDを保持し、終了・例外時ともそのIDだけ削除する。
イメージはsessionラベルとランダムタグを付け、終了時に自分のタグだけ削除する。
ジョブ用の永続volume/networkは作成しない。tmpfsはコンテナ削除で解放される。
DB回帰用のvolume/networkは専用Compose projectで生成・削除した。
既存コンテナ、daemon設定、OS設定、グループ、contextを変更せず、global pruneは行っていない。
ベースイメージcacheとDockerのbuild cacheは再実行用に残る。ホスト全体の空きRAM/ディスクが完全に元へ戻るという測定ではない。

## 完了判定と適用範囲

P0で要求された設計文書、fixture仕様、環境・依存固定、migration、正常/失敗系、制限の実測証拠が揃ったため、現在環境の管理下fixtureについてP0完了とする。
rootlessへ切り替えずDocker Desktopの現構成を使用した。専用の使い捨てVMに任意コードを受け入れる構成ではない。
Rootlessはcgroup v2/systemdなどの前提を別途確認する必要があるため、OS無変更という依頼範囲で導入していない。
環境やDocker設定を変えた場合は今回の結果を流用せず再実測する。

以下は完了判定に含まない。

- コンテナ脱出・カーネル脆弱性に対する完全な隔離、任意リポジトリ/外部PRの受付。
- no-new-privilegesに対する全setuidバイナリの攻撃試験、nosuid/nodevの個別強制試験、全seccomp syscallの検証。
- ホスト監視プロセス自体のSIGKILL/電源断時の自動回収。現在はfinallyでの回収であり、強制中断は残存確認が必要。
- Worker障害、DB断、lease失効、古いtoken、取消競合、成果物昇格の実装・障害試験（P1/P2以降）。

P1では本番の実行処理を別途実装し、今回の制限・失敗時拒否・ログ/時間監視を同じ実測試験で検証する。
テスト用ハーネスがあるだけでWorker側も保護済みとは扱わない。[次のP1実装計画](p1-plan.md)。

参照: [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)、
[Running containers](https://docs.docker.com/engine/containers/run/)、
[Docker security](https://docs.docker.com/engine/security/)、
[Rootless resource prerequisites](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources)。
