# 時計資料：手順と選定・保留事項

[文書一覧・統合対応表](README.md)。2026-11-15に用途別へ統合。過去の結論・未解決事項・原本hashは保持。

**時計研究・追加採取・OS操作は引き続き保留。ここにある過去手順は実行許可ではない。**

各節の日付・「未完了」は執筆時点の記録を含みます。現在の状態は[プロジェクトREADME](../README.md)と[計画書の最新残件](../../261014/plan_mlops.md#remaining-work-latest)を優先してください。再実行用の入口は新配置へ更新し、当時の原文・コマンド・hashはGit `9479201`、work_log、review JSONに保持しています。

## 目次

- [Windows管理者での限定した時計process読取](#clock-owner-admin-read)
- [時計調査を再開するために必要な入力](#clock-input-request)
- [修復後の時計資格：事前に固定する受入手順 v1](#clock-qualification-plan)
- [独立UTC源・QPC校正の選定前review](#clock-source-readiness)
- [Windows同期の保守比較案（未許可・未実施）](#clock-host-maintenance-plan)
- [1回のresync要求後の時刻設定・停止記録](#clock-resync-result)
- [Windows管理者ログのruntime設定・同期責任review](#clock-host-runtime-review)
- [時刻同期タスクの定義と停止原因の境界](#clock-task-definition-review)
- [time.is・NICTを時計の参照元に使う場合](#clock-web-source-review)
- [NICT NTPの取得方法・不確かさのreview](#clock-nict-method-review)
- [指定された時刻APIの確認（2026-11-14）](#time-api-options)

<a id="clock-owner-admin-read"></a>

## clock-owner-admin-read

<a id="clock-owner-admin-read--windows管理者での限定した時計process読取"></a>

### Windows管理者での限定した時計process読取

**2026-11-09更新:** 指定されたowner/resourcesの2原本を[照合済み](clock-history.md#clock-resources-admin-review)。
直接保存方式の終了値/実完了行/hashと、採取区間のchronyd・PHC fd・UDP所有を確認した。
基本inventory/PHC/socketの同じ採取は不要。hostの[basic管理者queryも受入済み](clock-operations.md#clock-host-runtime-review)。
peer/status/identityの限定読取も`clock-host-cause-a52e422e-…`で[受入済み](clock-history.md#clock-host-cause-review)。同じqueryを直後に反復しない。
以下の未実施・未採取は各旧時点の履歴。次は新しい状態変化/自然event・独立源資料、または[明示許可後の保守比較](clock-operations.md#clock-host-maintenance-plan)を条件とする。
dev差・timesyncd identity変更・tick11000内部原因・同期責任・全時計資格は未解決である。

[現在の調査結果](clock-history.md#clock-process-ownership)の続き。**時計設定を変更する手順ではない。**
現チャットのWindows tokenは非管理者で、`wsl --debug-shell`の起動を拒否された。
Ubuntu rootやCodexのsandbox許可はWindows管理者tokenとは異なる。
基点2181bfeからの[追加確認](clock-history.md#chrony-control-analysis)でも保存先に管理者ログはなく、ユーザーも未採取と回答した。以下の採取が次の依存作業である。チャット側で読取可能な範囲の調査は実施済みで、この管理者tokenをチャットから代行取得できていない。

<a id="clock-owner-admin-read--採取する情報と実施条件"></a>

#### 採取する情報と実施条件

すでにUbuntu/WSLがRunningであることを確認し、Windows TerminalのPowerShellを「管理者として実行」で開く。
新しいWSL/distro起動、再起動、shutdown、service停止、w32tm resync、時計/実行policy変更は行わない。
管理者起動できない端末ではここで止め、端末管理者へこの読取範囲を渡す。

```powershell
$taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
wsl.exe --list --running --quiet
Get-ExecutionPolicy -List
```

最初がTrueで、UbuntuがRunning一覧にあることを確認する。
readerは[read_clock_process_ownership.sh](../scripts/read_clock_process_ownership.sh)、ログ採取用は[collect_clock_owner_admin.ps1](../scripts/collect_clock_owner_admin.ps1)。内容を確認してから使う。

<a id="clock-owner-admin-read--今回の結果と入力待ち"></a>

#### 今回の結果と入力待ち

[管理者ログの照合結果](clock-history.md#clock-admin-log-review)で、readerの両process採取完了を確認した。今回の基本inventoryの再実行は不要。
旧`.ps1`と対話pipelineはdebug shellの終了/後続metadata保存まで未検証だった。今回collection.jsonがないため採取全体の正常終了とは記録しない。
`>>`だけで未実行とは判断しない。実行中の旧採取pipelineが残っている場合、readerの実完了行を確認してからCtrl+Cでその採取クライアントを中断できる。WSL shutdownやDocker停止は不要。

<a id="clock-owner-admin-read--今後追加採取が必要な場合"></a>

#### 今後追加採取が必要な場合

`.ps1`のpolicyを変更せず、管理者PowerShellで次の**1行だけ**実行する。

```powershell
wsl.exe --debug-shell
```

`root@... #`が出たら、以下は**PowerShellではなく、そのLinux debug shell**に貼り付ける。
最後にEnterを押す。初期namespaceから、既に稼働しているUbuntuのrootを通じてreaderを読む。新しいdistroは起動しない。
PID固定ではなく、reader fileが存在するrootを探索する。見つからなければ停止し、勝手に別pathへ保存しない。

```sh
(
    task_repo=
    for task_root in /proc/[0-9]*/root; do
        task_candidate="$task_root/home/kenshin/work/claude_practice/261021"
        if [ -r "$task_candidate/scripts/read_clock_process_ownership.sh" ]; then
            task_repo=$task_candidate
            break
        fi
    done
    if [ -z "$task_repo" ]; then printf 'Ubuntu reader path not found\n'; exit 42; fi
    task_run="clock-owner-admin-$(cat /proc/sys/kernel/random/uuid)"
    task_dir="$task_repo/data/reports/$task_run"
    umask 022
    mkdir -m 755 "$task_dir" || exit 1
    cat /proc/uptime > "$task_dir/uptime-before.txt"
    sh "$task_repo/scripts/read_clock_process_ownership.sh" > "$task_dir/inventory.log" 2>&1
    task_code=$?
    printf 'reader_exit_code=%s\n' "$task_code" > "$task_dir/reader-exit.txt"
    cat /proc/uptime > "$task_dir/uptime-after.txt"
    sha256sum "$task_repo/scripts/read_clock_process_ownership.sh" "$task_dir"/*.txt "$task_dir/inventory.log" > "$task_dir/hashes.sha256"
    printf 'WSL保存先: /home/kenshin/work/claude_practice/261021/data/reports/%s\nreader_exit_code=%s\n' "$task_run" "$task_code"
)
```

この新しい管理者経路はshell構文を確認した案内で、初期namespaceでのend-to-end再採取は未実施。
fileを直接保存するため、外側native clientの終了を採取完了の代わりにしない。
出力後はLinux promptへ戻る。`exit`でdebug shellを抜ける。consoleが戻らなければ採取fileを保持してCtrl+Cでクライアントを中断し、その事実を残す。
reader_exitと実完了行・identityをSolが確認するまでは未review扱い。
OS/実行policy変更、UAC自動昇格、service操作は含めない。

<a id="clock-owner-admin-read--出力の読み方と範囲"></a>

#### 出力の読み方と範囲

保存先はWSLから`261021/data/reports/clock-owner-admin-<GUID>/`として読める。チャットへdirectory名を伝えれば、fileから照合を続けられる。旧PowerShell出力のUTF-8 BOMは`utf-8-sig`で読む。新方式はinventory.log/reader-exit.txt/hashes.sha256を使い、Windows側のcollection.jsonは作らない。

readerはchronyd/systemd-timesynだけを候補として列挙し、exe/hash/argv/親/namespace/cgroup/開始tickと、process root内のchrony設定の限定directiveを読む。
設定のinclude/confdirやargvの`-fFILE`形式など、未追跡の指定があれば手動照合を追加する。認証key・環境変数・全processのargvを一括出力しない。

- 終了77: root不足。69: 必要な読取tool不足。42: 一方のprocessが見えない、identity変化、読取失敗などの不完全採取。
- 終了0/`INVENTORY_COMPLETE_NEEDS_TRACE_CORRELATION`: 候補の一覧採取ができたという意味。**犯人特定・時計修復・資格確認の合格ではない。**
- current boot、PID namespace、開始tick、exe hashを今回のsetter traceと照合する。名前が一致するだけで管理元を確定しない。別bootなら旧PID対応は使わない。
- `CLOCK_OWNERSHIP_RESULT`行とnative終了値の両方を確認する。debug shellのechoや外側exit 0を内側の成功に読み替えない。

rawは端末path・UID・argv等を含むためignored reportsへ保持する。Gitへ原本を追加せず、公開用summaryとSHAだけをreview後に残す。
実DB・registry・配信は採取対象に含めない。実行file/起動者/生成設定の照合後に、Solが修復案・影響・切戻しと、必要な別のOS変更許可を整理する。

<a id="clock-owner-admin-read--採取後に照合する項目"></a>

#### 採取後に照合する項目

表示された`clock-owner-admin-<GUID>`のWSLパスをチャットへ伝える。raw全文の貼付けは不要。
このreaderはPHCの装置一覧を読むが、daemonの開いたPHC fdやUDP socketの所有まで検証するものではない。
Solは開始/終了のidentity、exe/hash、親/namespace、選択configを先に確認し、fd/socket・config include・管理unit等の不足があれば、そのprocessだけの追加読取を設計する。
別bootやPID再利用、identity変化があれば旧setter traceへ結び付けない。
`INVENTORY_COMPLETE`でもtickの内部変数や修復完了を証明しないため、[追加調査の未確定事項](clock-history.md#chrony-control-analysis)を残す。

<a id="clock-owner-admin-read--chrony-resources-admin"></a>

<a id="clock-owner-admin-read--追加のphcsocket所有採取（2026-11-03）"></a>

#### 追加のPHC/socket所有採取（2026-11-03）

2026-11-04時点でも追加directoryは未採取。[別scopeでのinitd管理層照合](clock-history.md#initd-owner-correlation)は進んだが、
chronydのPHC fd/UDP所有や現在の初期namespace inventoryを採取した代わりにはならない。
次にユーザー側で必要な限定読取は、引き続きこの節の手順。既に保存済みであればdirectoryだけを伝えればよい。

修復後の観測期間・判定式・欠測/失効の扱いは[時計資格の計測設計v1](clock-operations.md#clock-qualification-plan)に整理した。
設計は管理者採取・修復・休止再開を実施する許可や、資格の合格を意味しない。

[追加調査](clock-history.md#clock-followup)でWindows非管理者tokenと、Ubuntuからchronydが見えない境界を再確認した。
以下は未採取のPHC fd/UDP inodeだけを補う手順。基本inventoryも同じ新規directoryへ採り、identityを対応付ける。
旧PIDを固定しない。OS・時計・service・実行policyは変更しない。

現在のreaderはexeの`dev:inode:size`も前後比較する。
[内部数値採取](clock-implementation.md#chrony-runtime-control)の同じboot/dev42/inode4227/SHAと照合するが、別bootならその番号を流用しない。
当該採取は公開実行fileの読取FDを使ったもので、daemonのPHC/socket fdや開始時刻/親を採取したものではない。

既にUbuntuがRunningの状態で、管理者PowerShellから`wsl.exe --debug-shell`を起動する。
Linuxのroot promptが出てから、以下を貼り付けてEnterを押す。

```sh
(
    task_repo=
    for task_root in /proc/[0-9]*/root; do
        task_candidate="$task_root/home/kenshin/work/claude_practice/261021"
        if [ -r "$task_candidate/scripts/read_chrony_resources.sh" ]; then
            task_repo=$task_candidate
            break
        fi
    done
    if [ -z "$task_repo" ]; then printf 'Ubuntu reader path not found\n'; exit 42; fi
    task_run="clock-resources-admin-$(cat /proc/sys/kernel/random/uuid)"
    task_dir="$task_repo/data/reports/$task_run"
    umask 022
    mkdir -m 755 "$task_dir" || exit 1
    cat /proc/uptime > "$task_dir/uptime-before.txt"
    sh "$task_repo/scripts/read_clock_process_ownership.sh" > "$task_dir/inventory.log" 2>&1
    task_inventory_code=$?
    sh "$task_repo/scripts/read_chrony_resources.sh" > "$task_dir/resources.log" 2>&1
    task_resources_code=$?
    printf 'inventory_exit=%s\nresources_exit=%s\n' "$task_inventory_code" "$task_resources_code" > "$task_dir/reader-exit.txt"
    cat /proc/uptime > "$task_dir/uptime-after.txt"
    sha256sum "$task_repo/scripts/read_clock_process_ownership.sh" "$task_repo/scripts/read_chrony_resources.sh" "$task_dir"/*.txt "$task_dir"/*.log > "$task_dir/hashes.sha256"
    printf 'WSL保存先: /home/kenshin/work/claude_practice/261021/data/reports/%s\ninventory_exit=%s resources_exit=%s\n' "$task_run" "$task_inventory_code" "$task_resources_code"
    if [ "$task_inventory_code" -ne 0 ] || [ "$task_resources_code" -ne 0 ]; then exit 42; fi
)
```

表示された`clock-resources-admin-…`のdirectory名をチャットへ伝える。raw貼付けは不要。
初期namespaceでの新手順のend-to-endはまだ未実施で、構文/fixture成功を実採取と呼ばない。
0以外でもfileを削除せず保存する。終了行とexit/hashをreviewするまでは不完全扱い。
出力後はLinux promptへ戻り、`exit`でdebug shellを抜けられる。WSL shutdownやDocker再起動は不要。

<a id="clock-owner-admin-read--host-w32time-admin"></a>

<a id="clock-owner-admin-read--w32timeの管理者限定読取（取得済み・手順の履歴）"></a>

#### W32Timeの管理者限定読取（取得済み・手順の履歴）

**追補:** `clock-host-admin-00f021d1-…`でこのblockの実結果を[review済み](clock-operations.md#clock-host-runtime-review)。
3queryのexit0・原本hashを確認したため、同じbasic queryを再実行する必要はない。
以下の「未実施」は案内作成時点の履歴。原因調査の新しい不足分は[次の節](clock-operations.md#clock-owner-admin-read--host-w32time-cause-read)を参照する。

2026-11-09のチャット側照会はW32Time Running、statusは同期未実行/Local CMOS Clockだったが、
source/configurationはWindows非管理者tokenで0x80070005となった。
次のblockはその不足分を採る案内。**Linux debug shellを`exit`で抜けた後、Windowsの管理者PowerShellで実行する。**
既にUbuntuがRunningであることをblock内でも確認し、停止していたら新しく起動せず終了する。
時計変更・resync・service起動/停止・実行policy変更は含まない。[w32tm公式説明](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings#command-line-parameters-for-w32time)のqueryだけを使う。

```powershell
& {
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '管理者PowerShellで実行してください。自動昇格は行いません。'
    }
    $taskRunning = (& wsl.exe --list --running --quiet) -replace "`0", ''
    if ($LASTEXITCODE -ne 0 -or -not ($taskRunning | Where-Object { $_.Trim() -eq 'Ubuntu' })) {
        throw 'UbuntuがRunningではありません。起動せず停止します。'
    }
    $taskRun = 'clock-host-admin-' + [Guid]::NewGuid().ToString()
    $taskDir = '\\wsl.localhost\Ubuntu\home\kenshin\work\claude_practice\261021\data\reports\' + $taskRun
    New-Item -ItemType Directory -Path $taskDir -ErrorAction Stop | Out-Null
    Get-CimInstance Win32_Service -Filter "Name='W32Time' OR Name='vmictimesync'" -ErrorAction Stop |
        Select-Object Name, State, StartMode | ConvertTo-Json |
        Out-File -LiteralPath ($taskDir + '\services.json') -Encoding utf8 -ErrorAction Stop
    $taskRecords = @()
    foreach ($taskQuery in @('status', 'source', 'configuration')) {
        $taskNative = & "$env:SystemRoot\System32\w32tm.exe" /query "/$taskQuery" 2>&1
        $taskExit = $LASTEXITCODE
        $taskNative | Out-File -LiteralPath ($taskDir + '\' + $taskQuery + '.log') -Encoding utf8 -ErrorAction Stop
        $taskRecords += [PSCustomObject]@{ query = $taskQuery; native_exit = $taskExit }
    }
    [PSCustomObject]@{ admin = $true; mode = 'read-only-local-query'; queries = $taskRecords } |
        ConvertTo-Json -Depth 4 | Out-File -LiteralPath ($taskDir + '\collection.json') -Encoding utf8 -ErrorAction Stop
    @('services.json', 'status.log', 'source.log', 'configuration.log', 'collection.json') |
        ForEach-Object { Get-FileHash -Algorithm SHA256 -LiteralPath ($taskDir + '\' + $_) -ErrorAction Stop } |
        Select-Object Path, Hash | ConvertTo-Json |
        Out-File -LiteralPath ($taskDir + '\hashes.json') -Encoding utf8 -ErrorAction Stop
    Write-Output ('WSL保存先: /home/kenshin/work/claude_practice/261021/data/reports/' + $taskRun)
    $taskRecords | Format-Table
    if ($taskRecords | Where-Object { $_.native_exit -ne 0 }) {
        Write-Warning '一部queryが失敗しました。失敗logも保持して保存先を伝えてください。'
    }
}
```

この追加blockはPowerShell parserで構文を確認した案内で、管理者での実採取は未実施。
元のログを削除せず、表示されたWSL directoryだけをチャットへ伝える。rawにpeer/domain等が含まれる可能性があるためGitへ追加しない。
権限拒否や一部失敗でも保存結果を保持する。文字化けがあれば元fileを保持して伝え、再試行で成功扱いに書き換えない。
query結果やservice Runningだけで外部UTC精度・QPC校正・同期責任の全体を認定しない。

<a id="clock-owner-admin-read--host-w32time-cause-read"></a>

<a id="clock-owner-admin-read--未同期原因の限定読取（取得済み・peer・last-error・新イベント）"></a>

#### 未同期原因の限定読取（取得済み・peer・last-error・新イベント）

**取得後追補:** `clock-host-cause-a52e422e-…`の[実結果をreview済み](clock-history.md#clock-host-cause-review)。
9file/8member SHA、query exit0、host/service identity前後一致を確認し、event該当なしの診断も保持した。
以下は採取手順の履歴。構文確認だけでなく実原本も受入済みなので、同じblockを直後に再実行する必要はない。

基点`b0589ee`のhost管理者原本は取得済み。以下は**追加項目だけ**を採る任意の次手順であり、同じsource/configuration/PHC採取のやり直しではない。
今回不足したpeer状態とverbose last-error、前回既存logの最大RecordId26321より後のTime-Serviceイベント最大32件を読む。
RecordIdの巻戻り/保存範囲/最大32件の打切りはあり得るため、結果0件でも失敗や原因の不存在を証明しない。
queryの前後にhostのUTC/Stopwatch tickを残すが、いずれも未校正のローカル値で、実時計資格や外部源認定には使わない。
host boot/OS版とW32Timeのprocess ID/開始時刻/実行file SHAも同時期の診断資料として保存する。
svchost実行fileのhashだけでは中で使うDLL全体や同期元の真正性を認証しない。

Windows管理者PowerShellでblock全体を実行する。resync、DNS問い合わせ、stripchart、設定変更、service操作、event log有効化は含まない。
Windows shellなのでLinux debug-shellのpromptへ貼らない。
Ubuntuが既にRunningの場合だけ新しいignored directoryへ保存し、停止中なら起動せず終了する。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '管理者PowerShellで実行してください。自動昇格は行いません。'
    }
    $taskRunning = (& wsl.exe --list --running --quiet) -replace "`0", ''
    if ($LASTEXITCODE -ne 0 -or -not ($taskRunning | Where-Object { $_.Trim() -eq 'Ubuntu' })) {
        throw 'UbuntuがRunningではありません。起動せず停止します。'
    }
    $taskRun = 'clock-host-cause-' + [Guid]::NewGuid().ToString()
    $taskDir = '\\wsl.localhost\Ubuntu\home\kenshin\work\claude_practice\261021\data\reports\' + $taskRun
    New-Item -ItemType Directory -Path $taskDir -ErrorAction Stop | Out-Null
    $taskRecords = [Collections.Generic.List[object]]::new()
    try {
        $taskOS = Get-CimInstance Win32_OperatingSystem
        [PSCustomObject]@{
            Version = $taskOS.Version; BuildNumber = $taskOS.BuildNumber
            LastBootUpTimeUTC = $taskOS.LastBootUpTime.ToUniversalTime().ToString('o')
            StopwatchFrequency = [Diagnostics.Stopwatch]::Frequency
        } | ConvertTo-Json | Out-File ($taskDir + '\host.json') -Encoding utf8
        foreach ($taskEdge in @('before', 'after')) {
            $taskService = Get-CimInstance Win32_Service -Filter "Name='W32Time'"
            $taskIdentity = [ordered]@{ Name = $taskService.Name; State = $taskService.State; StartMode = $taskService.StartMode; ProcessId = $taskService.ProcessId }
            if ($taskService.ProcessId -gt 0) {
                $taskProcess = Get-Process -Id $taskService.ProcessId
                $taskIdentity.StartUTC = $taskProcess.StartTime.ToUniversalTime().ToString('o')
                $taskIdentity.Exe = $taskProcess.MainModule.FileName
                $taskIdentity.ExeSHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $taskProcess.MainModule.FileName).Hash
            }
            $taskIdentity | ConvertTo-Json | Out-File ($taskDir + '\service-' + $taskEdge + '.json') -Encoding utf8
            if ($taskEdge -eq 'after') { break }
            foreach ($taskQuery in @('status', 'peers')) {
                $taskBeforeUTC = [DateTime]::UtcNow.ToString('o')
                $taskBeforeQPC = [Diagnostics.Stopwatch]::GetTimestamp()
                $taskNative = & "$env:SystemRoot\System32\w32tm.exe" /query "/$taskQuery" /verbose 2>&1
                $taskExit = $LASTEXITCODE
                $taskAfterQPC = [Diagnostics.Stopwatch]::GetTimestamp()
                $taskAfterUTC = [DateTime]::UtcNow.ToString('o')
                $taskNative | Out-File ($taskDir + '\' + $taskQuery + '-verbose.log') -Encoding utf8
                $taskRecords.Add([PSCustomObject]@{ query = $taskQuery; native_exit = $taskExit; before_utc_local = $taskBeforeUTC; after_utc_local = $taskAfterUTC; before_qpc_local = $taskBeforeQPC; after_qpc_local = $taskAfterQPC })
            }
        }
        $taskEventErrors = @()
        $taskEvents = @(Get-WinEvent -LogName System -FilterXPath "*[System[Provider[@Name='Microsoft-Windows-Time-Service'] and (EventRecordID > 26321)]]" -MaxEvents 32 -ErrorAction SilentlyContinue -ErrorVariable taskEventErrors |
            Select-Object RecordId, Id, @{Name='TimeCreatedUTC'; Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}}, Message)
        ConvertTo-Json -InputObject $taskEvents -Depth 4 | Out-File ($taskDir + '\events.json') -Encoding utf8
        [PSCustomObject]@{ record_id_lower_exclusive = 26321; maximum = 32; returned = $taskEvents.Count; error_records = @($taskEventErrors | ForEach-Object { $_.ToString() }) } |
            ConvertTo-Json -Depth 4 | Out-File ($taskDir + '\event-result.json') -Encoding utf8
    } finally {
        [PSCustomObject]@{ admin = $true; mode = 'read-only-local-cause-diagnostics'; queries = @($taskRecords.ToArray()); clock_values = 'local-unqualified' } |
            ConvertTo-Json -Depth 5 | Out-File ($taskDir + '\collection.json') -Encoding utf8
        $taskMembers = @('host.json', 'service-before.json', 'service-after.json', 'status-verbose.log', 'peers-verbose.log', 'events.json', 'event-result.json', 'collection.json')
        @($taskMembers | ForEach-Object { if (Test-Path -LiteralPath ($taskDir + '\' + $_) -PathType Leaf) { Get-FileHash -Algorithm SHA256 -LiteralPath ($taskDir + '\' + $_) } }) |
            Select-Object Path, Hash | ConvertTo-Json -Depth 4 | Out-File ($taskDir + '\hashes.json') -Encoding utf8
        Write-Output ('WSL保存先: /home/kenshin/work/claude_practice/261021/data/reports/' + $taskRun)
    }
}
```

このblockはPowerShellの構文確認だけで、追加の管理者実採取は未実施。
イベント読取の失敗はerror_recordsへ残す。空配列や`admin=true`を成功の代わりにせず、
query終了値・取得fileの欠落・service前後identity・収集範囲を受領後にreviewする。
途中停止/エラーでも新directoryを上書きせず保存先を伝える。native queryが戻らない場合は中断し、既存fileを残す。
rawのpeer/IP/端末pathはprivate保存で、チャットへ全文貼付けやGit追加をしない。
既存手順同様、管理者block自体を署名/独立参照の証拠とは扱わない。

<a id="clock-input-request"></a>

## clock-input-request

<a id="clock-input-request--時計調査を再開するために必要な入力"></a>

### 時計調査を再開するために必要な入力

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

**2026-11-09 NICT方法追補:** [公式FAQの頻度条件と方法review](clock-operations.md#clock-nict-method-review)を追加。
必要入力は頻度/用途についての提供元の回答、認証/来歴・source誤差、固定境界の独立elapsedへの対応根拠。
問い合わせ文案は同文書に用意したが送信していない。今すぐservice起動や追加採取を実行する必要はない。
16秒下限だけで毎分取得可とは扱わず、受入済みPHC/task/peer原本は再依頼しない。

**2026-11-09 task/Web追補:** 新しい[タスク定義XMLのreview](clock-operations.md#clock-task-definition-review)で、
task_startedとForceSynchronizeTimeのCOM Action、現在の限定HKLM登録/DLL hashを確認した。
同じXMLの再採取は不要。停止caller/引数の内部意味、実源の根拠は未確定。
提示された[time.is/NICTの用途と制限](clock-operations.md#clock-web-source-review)を記録し、外部源候補が未提示という扱いは更新した。
次の資料は新しい停止/起動責任の根拠、またはNICT等のsource/輸送/真正性/elapsedの受入根拠。
基本queryやPHC/UDPを繰り返さず、service起動・設定変更は別の具体案/許可を必要とする。

**2026-11-09 resync後追補:** ユーザーの1回要求について[原本・Operational eventをreview済み](clock-operations.md#clock-resync-result)。
受信/同期開始/時刻設定（報告+2ms）/code0停止が同じ要求区間にあるが、事後queryは失敗し、現状態はStopped。
同じhelperを再実行しない。次はtask起動形態/停止責任の根拠、新しい自然event、独立source資料、または別途許可された保守比較。
service起動/追加同期/poll・task・log設定変更は今回の1回許可へ含めない。全資格と実fit/採点は保留。

**2026-11-09 peer追補:** `clock-host-cause-a52e422e-…`の[peer/status/identity review](clock-history.md#clock-host-cause-review)も完了。
全体はno time data/未同期、peerはpending/現在のvalid sample0。host/serviceの前後identityとevent該当なしの診断も保存した。
同じbasic/peer/PHC採取を直後に再依頼しない。次の条件は新しい状態変化・自然event、独立UTC/elapsed資料、または明示許可後の[保守比較](clock-operations.md#clock-host-maintenance-plan)。
根本原因/同期責任の全体・独立源の真正性/不確かさ・全資格は未確定で、実fit/採点の保留は維持する。

**2026-11-09 host追補（peer追補前の履歴）:** `clock-host-admin-00f021d1-…`の[管理者runtime review](clock-operations.md#clock-host-runtime-review)でsource/configuration取得も完了。
設定peerはtime.windows.com、採取時はLI3/Local CMOS Clock。basic queryや同じPHC/UDPの再採取は不要。
原因を絞る新しい不足は[peer/verbose last-error・新イベントの限定読取](clock-operations.md#clock-owner-admin-read--host-w32time-cause-read)と、独立UTC/elapsedの根拠である。
hostの誤差量/現在の未同期原因/全同期責任は未確定。実資格・fit/採点の保留は維持する。

**2026-11-09更新（host追補前の履歴）:** owner `b9641680-…` とresources `ed1b37fc-…` の追加管理者原本は[照合済み](clock-history.md#clock-resources-admin-review)。
採取区間のchronyd実体・PHC fd・UDP所有を確認できたため、同じinventory/PHC採取を再依頼しない。
次の不足は[hostの管理者限定query](clock-operations.md#clock-owner-admin-read--host-w32time-admin)、独立UTC/elapsedの根拠、同期責任等。
今回のhost statusは同期未実行/Local CMOS Clock、source/configurationは権限拒否で部分取得だった。
PHCやhostの自己申告を独立参照として採用しておらず、実時計資格・fit/採点は保留を維持する。

2026-11-08、基点`99f14cd`。追加管理者ログ・独立時計源の資料は、今回もユーザーから「まだ取得していない」と回答された。
同じtrace、非飽和値、原本公開状況の確認を繰り返さない。T09/T10の静的受入は完了済みで、再取得は不要。
この一覧は必要資料の具体化であり、新たな時計源の採用、OS変更、実fitの許可ではない。

<a id="clock-input-request--まず返してほしいもの"></a>

#### まず返してほしいもの

| 入力 | ユーザーから伝える最小限の内容 | Solが次に行うこと |
|---|---|---|
| 追加管理者ログ（指定2件は照合済み） | 同じPHC/socket採取は不要。新しい変更・自然再発等で追加資料ができた場合のみ保存先を伝える | 採取区間のidentity/所有の受入を保持し、新情報がある範囲だけ対応付ける。将来の再起動やidentity変更へ今回値を持ち越さない |
| 独立参照の候補 | 実際に利用可能な機器・サービス名、管理元、説明書/校正資料/原本の保存先。まだ無い項目は「未取得」 | 下記の依存関係・真正性・不確かさをreview。候補名だけでは採用しない |
| host/backendの同期資料 | basic/peer/status/identity queryは取得済み。新しい状態変化・自然event・管理責任を示す既存資料がある場合のみ保存先を伝える | 未同期/保留/現在有効sample0の受入を維持し、追加情報のある範囲で原因と各管理層の責任をreview。独立源や動的stackの代用にはしない |

管理者権限が使えない場合、その旨だけを伝えればよい。Ubuntu root、Codex許可、Windows管理者tokenは別である。
PHC/socketの旧PIDを手入力で固定せず、既存readerが現在の実体を探索する方式を使う。
古い`clock-owner-admin-053cb32639e54e04aea39f299e82711d/debug-shell.log`は照合済みの歴史的原本であり、今回のidentityを代用しない。
この一覧のために再起動・同期・service停止・時計変更・負荷試験を実行する必要はない。

<a id="clock-input-request--独立源の資料に必要な項目"></a>

#### 独立源の資料に必要な項目

以下をprivateな新しい提出directoryへまとめる。文書形式は任意でよい。
空欄は未取得として残し、`independent=true`や`approved=true`などのflagで埋めない。
1つの物理参照がUTCとelapsedの両役を担うことは可能だが、それぞれの根拠を分けてreviewする。

| 必要資料 | 最低限含める内容 | 不足なら何が判断できないか |
|---|---|---|
| 源のidentity/管理元 | 機器/サービスの識別、管理者、UTC基準と経過秒の定義、読取方式、版/校正の有効範囲 | 別名/別IP/別processだけでは独立性を認定できない |
| 依存関係図 | host/backend/guest/QPC、PHC、同期・仮想化・電源・network経路との共有部分、共通障害の可能性 | QPCから作った秒数や同host UTCを独立elapsedとして採用できない |
| 真正性 | 署名/認証の検証原本、または監督下の取得手順とその記録、誰がいつどの方法で確認したか | SHAは受領後の内容結合のみ。源の本物性や採取前改変を証明しない |
| 源の精度根拠 | 校正/仕様資料の原本、誤差範囲、丸め/分解能、安定期間/有効期限、holdover等の条件 | 根拠のないゼロ誤差・宣伝値のみでは境界を計算できない |
| 生応答と要求対応 | nonce/sequence/読取順、source counter/UTC、source status、送受信bracketの整数値・単位/周波数、欠測/エラー | 応答がどの読取時点を覆うか、別要求への差替えがないか判断できない |
| 遅延/不確かさの式 | 輸送・読取遅延の上下限、source誤差/丸め/量子化を合成してUTC/elapsed intervalを作る式と根拠 | 対称networkや端点誤差相殺を仮定して、必要な精度へ切り詰めない |
| readerの再現資料 | entryだけでなく依存code/config/lockのbytesとSHA、実際の起動方法、raw→intervalの独立再計算方法 | 実装・設定・原本・計算結果の結合を確認できない |
| 固定policyへの事前適合性 | UTC±1秒、QPC比±100ppmを区間全体で満たす見込み。60秒×30窓と1800秒全体を区別 | 長区間だけの良好値や絶対UTC一致だけでは全校正にならない |

例示の提出構成（自動読取schemaではなく、必要資料の置き場所の案）:

```text
data/reports/clock-source-input-<new-id>/
  dossier.md              # 上表の根拠・未取得・依存関係
  originals/              # 提供元資料・生応答・限定設定原本
  reader/                 # 読取実装・依存manifest・負例記録
  uncertainty.md          # 単位/式/誤差項と各根拠の対応
  hashes.sha256           # 対象を明示した原本bytesのhash
```

上記directoryを作っただけでは採用にならない。秘密/認証key/全環境変数や無関係なprocess情報は採取しない。
原本はGit対象外へ保持し、公開要約・公開可能範囲・hashはSolがreviewしてから決める。
rawに機器識別や端末情報が含まれる場合、チャットへ全文を貼らず保存先だけを伝える。

<a id="clock-input-request--必要精度を満たせない場合"></a>

#### 必要精度を満たせない場合

[源選定reviewの理想予算](clock-operations.md#clock-source-readiness--固定policyから先に分かる不確かさの予算)では、
中心経過時間が完全一致し他の誤差が無い場合でも、60秒の各端点半径は約2.999700ms以下である。
これは候補源の実測/認定ではなく必要精度の計算で、実際にはsource/transport/readoutの誤差も必要になる。
独立参照を用意できない、またはその不確かさがこの条件へ入らない場合は、
**実時計資格と、それを前提とする実fit/採点・実運用測定/採否を保留する。**
新しい実行環境/別計測環境での評価を提案する場合も、別途identity・同期責任・真正性・全policy受入をreviewする。
別PCという名前だけで合格扱いにせず、現環境の過去性能を新環境の時計で遡及承認しない。

tick11000の内部値とkernel書込、T03のphase/stackは自然再発時に対応付ける。
未再発や静的解析だけでは過去の動的原因を解決しない。OS修復・保守/休止再開は具体案・影響・切戻しと別の許可が必要である。
当面の独立作業は[合成原本bundle](clock-implementation.md#clock-raw-bundle)などのfixture検証に限定し、源の選定を先回りしない。

<a id="clock-qualification-plan"></a>

## clock-qualification-plan

<a id="clock-qualification-plan--修復後の時計資格：事前に固定する受入手順-v1"></a>

### 修復後の時計資格：事前に固定する受入手順 v1

基点`24c30c0`、2026-11-04。これは残課題「環境修復の具体化と時計資格」の**計測設計**である。
修復、時計資格の合格、実候補の運用測定を実施した記録ではない。
[調査結果](clock-history.md#backend-time-path-review)、[管理者採取](clock-operations.md#clock-owner-admin-read--chrony-resources-admin)、
[計画の残件](../../261014/plan_mlops.md#remaining-work-latest)を前提とする。

以前の手順は「長時間・休止再開後に確認する」までで、期間・判定式・欠測時の扱いが未固定だった。
その不足をここで補う。数値はこの学習環境の工学上の受入方針で、OSの保証値、時刻精度の標準、本番SLOではない。
採取器・自動判定器の実装とその負例検証は後続作業。既存の短い診断をこの方針で遡及採点しない。

<a id="clock-qualification-plan--実行開始前の条件"></a>

#### 実行開始前の条件

1. 新しいPHC/UDP所有・process identityを照合し、host/backend/guestの同期責任をreviewする。
   未確定のtick11000の過去原因を、この設計の存在だけで解決扱いにしない。
2. 修復案ごとに対象管理元・変更前設定・影響・切戻しを固定し、OS変更と保守時間の許可を得る。
   休止再開も現Docker配信へ影響し得る操作なので、この文書で実行許可を得たとは扱わない。
3. 採取policy本文/SHA、採取器のcommit/hash、OS/WSL/Docker版、boot ID、選択clock、
   processの開始時刻・exe/hash/dev/inode・namespace、同期設定hash、試行UUIDを保存する。
   credential、全環境変数、個人argvを公開証拠へ含めない。
4. Windows QPC周波数を読み、hostを基準にした経過時間比較と、独立した外部UTCとの比較を両方計画する。
   hostのUTC表示、HTTP Date、同期flagだけでは外部UTCとの比較は成立しない。
   外部時刻源と読取方法・応答遅延の上限・時刻源自身の不確かさを先に固定する。
   読取方法が未実装、必要な接続が使えない、または不確かさを評価できない場合は開始条件未充足とする。

<a id="clock-qualification-plan--固定する観測区間"></a>

#### 固定する観測区間

試行はWindows QPCによる連続60分。前半30分は通常の待機、後半30分は使い捨てfixtureの
事前固定した負荷とする。負荷生成器の仕様・hash・資源上限を採取前に固定し、実DBや実registryへ要求を送らない。
QPC/UTCとWSL REALTIME/MONOTONIC/RAW/BOOTTIMEを、要求前後のbracket付きで1秒ごとに採る。
各phaseの最初と最後を必須sampleとし、単なるsample数やWSL sleep回数で60分経過と判定しない。

別途許可された保守内でhost休止再開を1回行い、再開後に同じ60分を新試行として採取する。
休止をまたぐQPC/RAW/BOOTTIMEの差を通常稼働の進行率へ混ぜない。再開前後のUTC順序とboot/identityも保存する。
再開時のUTCを読めなかった場合は、その範囲の逆行なしを主張しない。
休止再開の未実施・採取不能を、通常稼働の成功で代用しない。

sample欠落、QPC上の隣接間隔が2秒を超える箇所、通信失敗、順序逆転、identity/設定変更、
instrumentationのlost/overrun、採取process中断は原本に残す。必要な区間が欠けた試行は`incomplete`とする。
原因調査用のprobeを付けた区間を、probeのない実運用性能測定へ流用しない。
再試行が必要なら理由をreviewし、新UUIDへ全試行を保存する。合格するまでの自動retryや窓の延長は行わない。

<a id="clock-qualification-plan--経過時間の比較式と受入基準"></a>

#### 経過時間の比較式と受入基準

同じWindows QPC sampleの読取時点を、WSL clockで要求前`b`と応答後`a`に挟む。
各clockのraw値とQPC tick・frequencyを保存し、丸め前の有理数で計算する。
sample0/1について`q=(QPC1-QPC0)/frequency > 0`なら、そのclockの進行率を次の区間で評価する。

```text
lower = (b1 - a0) / q
upper = (a1 - b0) / q
```

WSL値は秒単位へ換算する。bracketの非重複・各`b <= a`・QPC順序を先に検証する。
Windows readがそのbracket内で行われたことを保証する取得protocolと、QPC自体の安定性確認が前提である。
外部UTCの絶対offsetが範囲内という結果だけでQPCの秒数校正を済ませない。
QPC参照側の進行率/不確かさを確認できなければ、WSL/QPC比が範囲内でも資格全体は`incomplete`とする。
UTC逆行のあるREALTIMEへこの式を適用して経過時間を合格させない。

MONOTONICとRAWについて、最初のsampleを起点とする60秒ごとの非重複区間と全phase区間をすべて判定する。
終点はQPCで境界に最初に到達したsampleを使い、都合のよいsampleへ差し替えない。
各比の**区間全体が`[0.999, 1.001]`に含まれる**ことを要求する。点推定や単なる区間の重なりで合格させない。
区間が広すぎる場合は`incomplete`、許容範囲と完全に離れる場合は`failed`とする。
BOOTTIMEは順序/休止境界の診断にも残すが、未観測の休止区間の正しさをRAWとの一致で代用しない。

許容比±0.1%は、250msの経過時間に対し尺度誤差0.25ms相当を上限の目安にした設計値である。
これはQPCを基準にした寄与であり、QPC参照側の誤差と要求別の計測誤差は別に評価する。
60秒平均の比が合っても個々のHTTP要求の誤差を保証しない。
後続のT12測定にも前後のbracket・同boot/設定確認を結び、境界付近の結果は計測不確かさを含めreviewする。
この比で過去のp95やtimeout秒数を割り戻さない。

<a id="clock-qualification-plan--utcと承認時刻の受入基準"></a>

#### UTCと承認時刻の受入基準

- 採取されたWindows UTC/WSL REALTIMEの逆行は0件。採取間の未観測変化まで不存在と宣言しない。
- 固定した外部時刻源とのoffsetを、輸送遅延・外部源精度を含む区間として各phase開始/終了と60秒ごとに記録する。
  Windows、WSLの両方について、区間全体が±1秒に入ることを要求する。
  同じhostに依存するPHC・backend・guestを、互いに独立した外部源として数えない。
  区間が境界をまたぐ場合や外部源が未確認なら`incomplete`。明確な逸脱は`failed`。
  ±1秒はこの実験のUTCずれ予算であり、future拒否の許容値ではない。
- 同期元・設定と観測したsetterを照合し、未説明の負方向stepや急な進行率変更があればreviewへ戻す。
  setter名の存在だけ、またはNTP同期flagだけで合否を決めない。
- 厳密future境界の既存fixtureを維持し、同値許可・1µs future拒否・拒否時のblocked/tag不変を検証する。
  実時計を使う合成承認probeでも生成/検証UTCとMONOTONICを保存する。自然future拒否は不合格として記録する。
  clamp、待機、自動retry、future許容幅の追加は行わない。
- T03が自然再発した場合だけphase/stackを照合する。未再発は過去原因の解決ではなく、
  人工停止の成功も過去の停止原因の証明にはならない。

<a id="clock-qualification-plan--判定・保存・失効"></a>

#### 判定・保存・失効

明確な違反を1件でも観測したら`failed`、違反未観測でも必須情報/区間が不足なら`incomplete`とする。
両方ある場合は`failed`を主状態とし、不足も併記する。全受入条件を満たしreviewした場合だけ`passed`とする。
有限期間・特定環境の資格であり、原因の全解明や永続安定性の保証ではない。

policy、全sample/journal、command/cwd/終了値、生成/終了UTC・QPC/monotonic、各原本bytes/SHA、
区間別計算と除外理由、setter採取の範囲/負荷/cleanup、review理由を保存する。
切戻し・失敗試行も残す。公開するのは秘密情報を除いた要約とhash。試行後のpolicy変更は別versionで再reviewする。

再起動、休止再開、同期設定/process実体、OS/WSL/Docker版、clocksourceの変更後は以前の資格を再利用しない。
今回予定した休止再開後試行も、再開前資格を持ち越す方式ではない。
実運用測定の前後に同boot/identity/設定と時計を照合し、違いまたは途中の逆行を見つけた測定は未採用とする。
過去の採否や性能を新しい資格で遡及承認しない。

次の実装は、保存済みbracket/外部UTC区間を読むoffline判定器と合成負例から始められる。
採取器・負荷生成器・外部源readerの実装/検証、管理者PHC/UDP採取、同期責任review、修復許可、
修復そのものとこの全受入は残件。T09/T10の入力待ちや実producerの前提を省略しない。

<a id="clock-source-readiness"></a>

## clock-source-readiness

<a id="clock-source-readiness--独立utc源・qpc校正の選定前review"></a>

### 独立UTC源・QPC校正の選定前review

**2026-11-14方針改訂:** [製品作業の開始・停止条件](capital.md#product-completion-policy)を優先する。時計研究は再開指示まで保留、資格未合格と過去の失敗は保持する。以下の以前の日付の一律fit禁止/時計資料の追加依頼は当時の記録であり、現在の開始条件ではない。

**2026-11-09 NICT方法追補:** [頻度・認証・非循環の計算候補](clock-operations.md#clock-nict-method-review)をreview。
FAQの平均20回/時・480回/日は前回の16秒下限とは別条件。直接毎分取得は未許可。
因果bracketの数値包含を合成検算したが、源採用/固定境界への対応/全資格は未達。QPC由来RTTの自己校正や境界補間は採用しない。

**2026-11-09 Web源追補:** ユーザー提示のtime.isとNICTを[用途・規約・不確かさでreview](clock-operations.md#clock-web-source-review)した。
time.isは人の目視用と自動APIを区別し、NICTはJST5表示・終了済みHTTP配信・継続するNTPを区別する。
NTPは調査候補だが、実sample/真正性/輸送誤差/独立elapsedの根拠は未取得。源採用・全資格・実fit保留は維持する。

2026-11-05、基点`002718e`。今回の[合成採取](clock-implementation.md#clock-fake-acquisition)を実採取へ置き換える前のD3/Sol判断。
**採用できる源と校正readerは、まだ選定されていない。**
利用可能な独立機器/記録、源の認証・精度根拠、Windows/WSLへ輸送する際の遅延境界が未提出である。
未確認provider名やaddress、`independent=true`の自己申告で不足を埋めない。
現時点の入力から特定製品/サービスの独立性や精度を認定することもできない。

2026-11-09 peer追補：[実peer/status/identity review](clock-history.md#clock-host-cause-review)で、peerはNoAuth、pending、現在有効sample0と確認した。
個別peerのerror0を全体同期成功へ昇格させない。今回metadataは認証・精度・輸送区間・独立elapsed校正の根拠を欠き、採用できない。
このreaderで資料が不足するという判断で、同名サービスの別の認証方式まで永久に不採用とする判断ではない。
新dossierは標準保存先に無く、源選定保留を維持する。host修復と独立源採用は別の条件である。

2026-11-09 host追補（peer追補前の履歴）：[管理者runtime review](clock-operations.md#clock-host-runtime-review)でhostのsource/configuration取得も完了。
runtimeはNtpClient有効/Type=NTP/time.windows.com,0x9だが、状態はLI3/Local CMOS Clockと報告された。
config flag/precision/root dispersionやquery exit0は真正性・外部UTC誤差・QPC校正の根拠ではない。
peer/last-error等の原因診断と独立源の採用を分け、採用保留を維持する。

2026-11-09（host追補前の履歴）：[管理者PHC/UDPの照合](clock-history.md#clock-resources-admin-review)で、指定採取区間のchronydがHyper-V PHCとlocal UDP323を所有すると確認した。
これは同期経路の実体照合で、独立源の真正性・UTC精度・elapsed不確かさを満たす資料ではない。
hostのW32TimeはRunningでもstatusは同期未実行/Local CMOS Clockで、source/configurationは非管理者tokenに拒否された。
独立源の採用は引き続き保留。追加資料の入口は[更新した入力一覧](clock-operations.md#clock-input-request)を参照し、同じPHC採取を反復しない。

2026-11-08追補：追加資料は未取得とユーザーが回答した。
[必要入力と保存先](clock-operations.md#clock-input-request)へ、管理者ログ・源のidentity/真正性・依存関係・不確かさの根拠を具体化した。
資料待ちで独立して進められる[合成raw bundle](clock-implementation.md#clock-raw-bundle)を追加したが、実源の選定/資格は保留を維持する。

| 案/入力 | 今回の判断 | 採用前に必要な根拠 |
|---|---|---|
| fake readerのcounter/UTC | 合成受入だけに使用 | 実源ではないので実資格へ採用しない |
| 同一host/guestのUTC差、QPCから生成した秒数 | 独立校正とみなさない | 共通の故障源を除いた参照と、その同一性/原本 |
| 現在のPHCや同期flag | 源/同期責任の追加読取資料 | 稼働設定・fd/UDP・process identity・管理層と参照元の対応、独立性のreview |
| HTTP Dateの単発応答 | 原本公開状態の日時補助だけ | 精度/丸め/取得時点/輸送区間/認証を証明しない限り校正に使わない |
| 未選定の外部UTC/elapsed参照 | 採用保留 | 下記の完全なsource dossier、readerと負例、事前承認 |

<a id="clock-source-readiness--source-dossierで判断する内容"></a>

#### source dossierで判断する内容

1. 源のUTC基準/経過秒の定義、識別、管理元、認証/署名または監督下の原本取得方法を明記する。
   hashは受領後の内容結合だけで、源が本物であることや採取前の改変を認証しない。
2. host/backend/guest/QPCと共有する時計・同期・電源/仮想化経路を列挙し、同じ障害が双方へ反映する可能性を評価する。
   「別process」「別IP」だけで独立と判定しない。
3. 源自身の誤差、丸め、counter分解能/周波数の校正、安定期間/有効期限を示す。
   根拠のないゼロ誤差を認めない。
4. 生応答、要求ID/sequence、送受信時点/順序、輸送/読取遅延の上下限からsample時点を覆うUTC intervalを作る。
   対応しない時点のUTC値をそのままsampleへ貼らない。reader停止/遅延/再送/並行/応答差替えの負例を持つ。
5. 校正は固定60秒窓30個とphase全体について独立elapsed区間を出す。
   QPC elapsedから同じreferenceを逆算しない。UTC offsetが小さいだけで経過時間の校正を成立させない。
6. [固定policy](clock-implementation.md#clock-campaign-review)のUTC ±1秒、QPC比±100ppmを、**不確かさ区間全体**で満たせるか事前計算する。
   設計の許容値へ入るよう不確かさを切り詰めない。難しい場合は本採取を始めず不足を記録する。
7. 取得実装/設定/依存bytes、source原本、derived interval、事前plan/hash、review者/日時を別原本で結ぶ。
   欠測を後の成功応答で埋めず、再開後は別trialで全観測を実施する。

<a id="clock-source-readiness--固定policyから先に分かる不確かさの予算"></a>

#### 固定policyから先に分かる不確かさの予算

源未選定でも必要な精度の条件は計算できる。以下は**QPCと参照の中心経過時間Cが完全に一致**し、
参照の両端の誤差半径が同じu、他の誤差なしという理想条件だけの必要予算である。
校正比の区間は `[C/(C+2u), C/(C-2u)]`。
全区間を固定の`[9999/10000,10001/10000]`へ入れるには、`2u <= C/10001`が必要になる。

| 参照区間 | 各端点の最大u（理想条件） | 含意 |
|---|---|---|
| 固定60秒窓 | 約2.999700 ms | ±3 msでも上側比がわずかに許容を越え、区間全体の成功にはできない |
| phase全体1800秒 | 約89.991001 ms | 長区間だけに入っても、必須の60秒窓30個の不足を補えない |

実際には中心の進行率差、源/輸送/読取/丸め等も予算を使うため、上表だけで使用できる源と認定しない。
UTC offset ±1秒が良好でも、60秒の校正に必要な不確かさへ入るとは限らない。
両端誤差が相殺するとの根拠が無ければ、保守的に半径を合算する。
14条件をFractionで検算し、60秒ではu=2,999,700nsは区間内、次の1nsは越えることも保存した。
これは源の実測や真正性の証拠ではない。原本とhashは[追加review索引](reviews/20261105-clock-acquisition-startup-checks.json)に記録する。

<a id="clock-source-readiness--次に進められる範囲"></a>

#### 次に進められる範囲

fake transport/原本照合の追加負例と可搬な原本bundleの改善は、実源未選定でも進められる。
実source adapterと実clock collectorを開始するには、上記dossierと採用判断を先に固定する。
OS修復・休止再開・実負荷/配信に関する許可は別であり、この文書は設定変更の許可を与えない。
future拒否、失敗優先、時計資格incompleteは維持する。

<a id="clock-host-maintenance-plan"></a>

## clock-host-maintenance-plan

<a id="clock-host-maintenance-plan--windows同期の保守比較案（未許可・未実施）"></a>

### Windows同期の保守比較案（未許可・未実施）

**2026-11-09実施後追補:** 案Aのresync1回はユーザーが実行済み。[実結果](clock-operations.md#clock-resync-result)で、
時刻設定eventの直後に停止/事後query失敗を確認した。以下の未許可・未実施は案作成時点の履歴。
案B/C・service起動・追加resyncは未許可/未実施のまま。案Aをもう一度実行する手順ではない。

2026-11-09、基点`9c32298`。[peer/statusの実review](clock-history.md#clock-host-cause-review)を受けた次段階のレビュー資料。
ユーザーの「OS/時計設定…を変更せず」に従い、**ここに記した変更コマンドは実行していない。**
現時点で根本原因を確定しておらず、長いpollを短くすれば過去障害が解決すると主張しない。

<a id="clock-host-maintenance-plan--新しく具体化できた条件"></a>

#### 新しく具体化できた条件

前回のruntime原本ではpeer `time.windows.com,0x9`、Type=NTP、SpecialPollInterval16384、MinPoll10、MaxPoll15、HoldPeriod5、LargePhaseOffset50000000。
今回のpeerは保留で残り10301.3131813秒、全体はUnset/no time data。これらは取得時点が違う原本であり、全設定が連続不変と仮定しない。

[Microsoft KB2638243](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/specialpollinterval-polling-interval-time-service-not-correct)は、
special pollと大offsetによりSPIKEへ入る条件で同期が進まない事象を説明する。
資料にある回避条件は`SpecialPollInterval < 2^MinPollInterval * (HoldPeriod+1)`。
保存設定を代入すると右辺は`1024*6=6144`で、16384は満たさない。
ただし今回の実状態はUnsetで、実offset/当該SPIKE遷移を観測していない。**式の不一致だけでKBが今回の原因とは判定しない。**

[Microsoft KB3205089](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/w32time-sync-specialpollInterval-ignored)には、
special pollに関する別の待ち時間問題と、0x9から0x8へ切り替える案がある。
単に設定を既定へ戻す、長い残り時間だけ待つ、無関係なserviceを止める、のいずれも自動的な修復とは扱わない。

<a id="clock-host-maintenance-plan--保守を別途許可された場合の実施順候補"></a>

#### 保守を別途許可された場合の実施順候補

下記は選択肢で、A/Bを一度に実行する手順ではない。どの操作を許可するか、配信への影響と観測期間をreviewしてから1条件ずつ固定する。
通常queryの繰返しで進捗を作らず、既存原本は保持し、新しい操作ごとに別UUIDと結果を残す。

1. 開始前にhost boot/版、W32Timeのprocess/開始時刻/exe hash、runtime設定とpolicy起源、既存peer/status、
   Mariner/Ubuntu/Dockerの管理状態とidentityを限定保存する。今回と違うidentity/設定なら停止して再reviewする。
   OS状態を変えず、実DBやregistryをsnapshot名目で書き換えない。
2. 許可された1条件だけを変更する。失敗しても自動retryや別案への自動移行を行わない。
3. native終了値・操作時点のローカルUTC/QPC・元bytes/SHAと、その後のquery/eventを保存する。
   query exit0、resync exit0、source名の変更、短時間の未再発だけでは全資格passedにしない。
4. 想定外の時計step/identity変更/配信影響が出た場合は次の操作へ進まず、失敗/未確定としてreviewする。
   設定の切戻しも同期を引き起こし得るため、事前許可された範囲と保守内で行う。

| 案 | 変更する1条件 | 開始条件・得られる切り分け | 影響・切戻し |
|---|---|---|---|
| A：現在設定のまま1回のresync | Windows管理者で`w32tm /resync`を1回。rediscoverやconfig変更は混ぜない | 明示許可後のみ。現在peerで時刻を利用できるか、即時の結果を新規証拠として得る。原因の完全確定ではない | 時計step/統計破棄の可能性。設定は変えないが操作自体は取消せない。手動で時刻を逆に戻すことは切戻しとしない |
| B：poll方式の比較 | peerを同じtime.windows.comの0x9から0x8へ変更しupdate。直後resync/service再起動/閾値変更は混ぜない | 新しいruntimeが旧原本と一致し、policy管理の有無・変更責任を確定してから。0x1特殊間隔の寄与を比較する候補。KB原因を確定した対処ではない | 自動同期/時計stepが起き得る。元Type/peer/runtime起源と設定hashを保存し、元0x9/管理元へ復元。履歴・sample統計は戻らない |
| C：guest管理層の比較 | hostの責任を整理後、Ubuntu等の管理対象1条件をreview | hostの同期回復/独立源・管理範囲が明確になってから別案を固定。現段階では開始不可 | shared kernelやDockerへ影響。active/enabled/configの元値と復元操作が未確定なので停止。initd kill/Mariner生成設定直接編集は対象外 |

案Bのレビュー用コマンド候補は次のとおり。**未許可のため実行しない。**

```text
変更候補: w32tm /config /manualpeerlist:"time.windows.com,0x8" /syncfromflags:manual /update
復元候補: w32tm /config /manualpeerlist:"time.windows.com,0x9" /syncfromflags:manual /update
```

これは保存値Type=NTP/peer0x9を前提とする候補で、全policy/全設定の復元コマンドではない。
開始時に起源/値が異なる、企業policyで管理される、操作後のruntimeが期待と違う場合は使用せず管理元のreviewへ戻す。
新peerの選定・防火壁変更・UDP問い合わせ/packet採取・private debug log有効化は、この案に含めない。
接続調査が必要になった場合は、対象/採取範囲/秘密除外/負荷/cleanupを別に具体化する。

<a id="clock-host-maintenance-plan--修復と資格を分ける"></a>

#### 修復と資格を分ける

この案が成功しても、host/PHC/guest/QPCは共通故障源を持ち得る。
peerのNoAuth表示は独立UTC/elapsedの真正性や不確かさを満たす資料ではない。
[source dossier](clock-operations.md#clock-source-readiness)を先にreviewし、必要な実reader/host deadline/固定負荷を検証してから、
[固定policyの通常60分・許可後の休止再開60分](clock-operations.md#clock-qualification-plan)の全観測で別途資格を判定する。
保守後の良好値で過去のUTC逆行、tick11000/T03、p95、採否を遡及承認しない。
実fit/採点/窓消費は、それぞれの前提を満たすまで保留のままにする。

<a id="clock-host-maintenance-plan--service-start-comparison"></a>

<a id="clock-host-maintenance-plan--案d：引数なしのservice起動1回を比較する場合（未許可・未実施）"></a>

#### 案D：引数なしのservice起動1回を比較する場合（未許可・未実施）

2026-11-09の[XML/COM review](clock-operations.md#clock-task-definition-review)を受けた追加の比較案。
目的はtask_startedを渡さない起動後の寿命/同期eventを調べることで、過去の停止原因の再現・確定とは分ける。
実行helperは未作成。案Dを次に選ぶ場合は、まず記録/停止条件付きのhelperを作成・構文検証し、
具体的なscriptをreviewできる状態にしてから操作許可を求める。裸のコマンドを今実行しない。

| 項目 | 事前に固定する条件 |
|---|---|
| 対象/操作 | WindowsのW32Timeへ、引数なしの`sc.exe start W32Time`を1回だけ。task実行/再resync/設定updateを混ぜない |
| 開始条件 | ユーザーの別許可と作業時間帯を確認。host/build、service Stopped/PID0、Manual、設定/policy/trigger/task hashを新しい操作の直前に保存し、今回の基準から差があれば実行せずreviewへ戻る。既にRunningなら停止して試験を作らない |
| 影響 | 自動同期によりWindows UTCがstep/slewする可能性。WSL/PHC/他アプリにも影響し得るため、重要な処理と同時実施しない。実DB/registry/配信へ操作命令は送らないが、OS時計経由の間接影響は否定しない |
| 記録 | 新UUIDへintentを先に保存。native exit/stdout/stderr、local UTC/QPC、開始後PID/start/exe/hash、既存Time-Service eventの新RecordId、status/peers/configを保存。失敗/空file/欠測も保持する |
| 観察/終了 | 最大20回・30秒間隔を上限とする予備観察案。各queryに個別timeout、観察失敗/停止/identity変化で次の操作を打たず終了。QPC等が未資格なので名目約10分を実時間保証にしない。正常値だけを拾う自動retryなし |
| 操作後の状態 | 自動停止や元PIDへのkillは行わない。Runningが残る可能性を含めた起動許可が必要。終了時に状態を保存し、停止を求める場合は影響とidentity guardを固定して別許可を得る。task/log/起動種別は変更しない |
| 切戻しの限界 | 永続設定を変えないためconfig復元操作はない。一方、起動/同期の履歴やUTC変更は取り消せず、手動で時計を逆に戻さない。元のStoppedへ戻す操作も今回案の自動処理に含めない |
| 判定 | 継続Running/良好statusだけでは修復/全資格passedにしない。task_startedとの差を論じるには実際の起動引数/identityに結び付く両条件の資料が必要。過去taskのlaunch証拠が不足する限り、一条件観察に留める |

この案を承認しない場合も、[外部参照候補の資料review](clock-operations.md#clock-web-source-review)はOS変更なしで進められる。

<a id="clock-resync-result"></a>

## clock-resync-result

<a id="clock-resync-result--1回のresync要求後の時刻設定・停止記録"></a>

### 1回のresync要求後の時刻設定・停止記録

**後続追補:** [XML2件とCOM登録先のreview](clock-operations.md#clock-task-definition-review)でForceSynchronizeTimeの処理を具体化した。
これは静的な現在設定の追加資料であり、以下の過去停止callerが確定したという更新ではない。

2026-11-09、基点`d285aa9`。ユーザーが案Aを実行した実結果をreviewした。
[証拠索引](reviews/20261109-resync-result-checks.json)、[保守案](clock-operations.md#clock-host-maintenance-plan)、
[計画§11.6](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**要求1回のあと、Windowsのログは受信・同期開始・時刻設定・停止を記録した。事後queryは失敗し、全時計資格は未達。**
コマンド応答は「シャットダウン中のため同期しなかった」だったが、応答だけから「時計は変更されなかった」と判断してはいけない。

<a id="clock-resync-result--原本と実行範囲"></a>

#### 原本と実行範囲

`clock-host-resync-d77eedf4-87ab-4e92-b108-daa760b07f6a`の原本13file/manifest12件とhelper SHAが一致した。
helperはignoredな`data/reports/clock-resync-guide-20261109/Invoke-ClockResyncOnce.ps1`、SHA
`f77404569cc96f957e379e3009c88be2f35aa787d71bc6068f1affe111c5dd3e`。
`/resync`は1回だけで、config/rediscover/service起動・停止の命令は含まない。
host/開始前serviceは前回と同じ報告値。設定原本のSHAも前回と一致した。
ユーザーがWindows時計操作を実施したことと、チャット側の追加操作は読取だけだったことを分ける。

| 記録 | 結果 |
|---|---|
| before configuration/status/peers | すべてnative exit0。既存設定/未同期状態のgateを通過 |
| resync-once | native exit0だが、本文はシャットダウン中のため同期しなかったと報告 |
| after status/peers | 両方`0x80070426`、native exit−2147023834 |
| service-after.json | 0byte。正常なJSONではなく、事後identity取得不能の原本として保持 |
| failure.log | service ProcessId<=0のためhelperが停止。サービスの自動起動/再試行なし |
| 後続の限定CIM/sc読取 | Stopped/Manual/PID0、Win32/service exitとも0 |

actionの`attempted_once/native_exit0`は操作試行の記録であり、成功の判定ではない。
空のJSONを成功のidentityとして補完したり、原本を書き換えたりしない。
13原本を別ignored directoryへsnapshot保存し、実際のquery/失敗/時刻bracket/元bytes/SHAを保持した。

<a id="clock-resync-result--同じ要求区間に存在したoperational記録"></a>

#### 同じ要求区間に存在したOperational記録

既に有効だったTime-Service/Operationalの、host表示UTCで02:55〜02:56の最大64件を読み取った。
該当5件はいずれもXMLのProcessID34104で、開始前serviceのPIDと対応する。
event単体には開始時刻/exe SHAが無いため、それだけで無execの連続性を認証するものではない。

| RecordId / EventId | host表示UTC 2026-11-09 | 記録された内容 |
|---|---|---|
| 2051 / 266 | 02:55:31.8289280 | 管理者の明示的resync通知、ReasonCode0 |
| 2052 / 264 | 02:55:31.9460790 | NTP peerからのデータ受信 |
| 2053 / 265 | 02:55:31.9658846 | time.windows.comを参照した同期開始、local stratum5 |
| 2054 / 261 | 02:55:31.9680008 | OldTime `.965Z`→NewTime `.967Z`への時刻設定を記録 |
| 2055 / 258 | 02:55:31.9714235 | 停止を記録。return code0 |

[Microsoftのevent定義](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-for-traceability)で261はsystem time設定、258はservice停止の記録と確認した。
ログのOld/New差は+2msだが、ミリ秒表示のprovider報告値である。外部UTCとの差の実測や継続精度の保証ではない。
System logのRecord26322/37（受信）、26323/35（同期開始）も同じ区間にあった。
native応答とこれらのeventを両方残し、「同期が一度も行われなかった」「同期できて今も安定」のどちらにも単純化しない。
チャット初期説明の「同期は行われていません」は応答本文だけからの推定で、追加eventにより上記へ修正した。

<a id="clock-resync-result--停止の管理元について分かった範囲"></a>

#### 停止の管理元について分かった範囲

`sc.exe queryex/qtriggerinfo/qc W32Time`はすべてexit0。
workgroup（PartOfDomain=false）、Manual/LocalServiceを確認した。
現triggerはDOMAIN JOINEDとCUSTOM SYSTEM STATE CHANGEのSTART2件で、STOPは表示されていない。
一般的な[workgroupの停止trigger例](https://learn.microsoft.com/en-us/troubleshoot/windows-client/active-directory/w32time-not-start-on-workgroup)を、そのまま今回原因としない。

既存SynchronizeTime taskのactionは`sc.exe start w32time task_started`、最終実行のhost表示UTCは00:25:21、result0。
既存process開始00:25:22.0395997と近接するが、TaskScheduler/Operationalは無効で起動履歴を取れない。
タスクを起動/変更したりlogを有効化したりしていない。
ForceSynchronizeTimeのExecute/Argumentsがnullなのは未展開action情報で、taskの処理が無いと証明しない。
既存SCMの限定範囲には該当なしで、停止callerやidle/task modeの内部理由は未特定。
code0は今回の終了値であり、意図どおり/恒久安定/停止原因解決を意味しない。

<a id="clock-resync-result--次の範囲と停止条件"></a>

#### 次の範囲と停止条件

同じhelperは再実行しない。今回の1回許可を、service起動・追加resync・poll変更・task/log設定変更へ拡張しない。
次はtask起動形態と停止方針の根拠、または新しい自然eventをreviewする。
もしservice起動の1条件比較を提案する場合も、起動で同期/時計stepが生じ得るため、前後証拠・影響・復元方針を固定して別途許可を得る。
「常時稼働でないから故障」とも、単発補正できたから長期資格passedとも扱わない。
独立UTC/elapsed sourceの真正性/不確かさは未達で、実collector/全資格・実fit/採点/T02/T11/T12/T13bは保留。
過去T03/tick11000の動的原因へ今回の停止や+2msを遡及適用しない。
今回の追加作業は原本照合/限定read-onlyのみ。製品source/schema/writer/lock/workflow、実DB/registry/現配信、future拒否は変更していない。

<a id="clock-host-runtime-review"></a>

## clock-host-runtime-review

<a id="clock-host-runtime-review--windows管理者ログのruntime設定・同期責任review"></a>

### Windows管理者ログのruntime設定・同期責任review

2026-11-09、基点`b0589ee`。新しい`clock-host-admin-00f021d1-2b24-4c53-aaf9-325fbe81cdba`をreviewした。
[今回の証拠索引](reviews/20261109-host-admin-checks.json)、[前回のPHC/UDP照合](clock-history.md#clock-resources-admin-review)、
[最新計画](../../261014/plan_mlops.md#remaining-work-latest)を参照する。

**前回拒否されたsource/configurationの管理者読取は完了。WindowsはNTPを設定しているが、採取時は同期未実行と報告した。**
これは実際の提出ログのreviewである。時計のUTC誤差の実測、未同期原因の確定、時計資格の合格とは区別する。

基点`9c32298`後の[peer/status/identity追補](clock-history.md#clock-host-cause-review)で、下記のpeer/last-error読取不足も解消した。
全体のno time dataとpeerのpending/現在有効sample0を照合済み。以下の「次の読取」は当時の履歴であり、同じ採取を直後に反復しない。
次は新しい状態変化/独立源資料、または[別途許可された保守比較](clock-operations.md#clock-host-maintenance-plan)を条件とする。

<a id="clock-host-runtime-review--原本・真正性・identityの範囲"></a>

#### 原本・真正性・identityの範囲

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

<a id="clock-host-runtime-review--確認した値と判断"></a>

#### 確認した値と判断

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

<a id="clock-host-runtime-review--同期責任の対応表"></a>

#### 同期責任の対応表

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

<a id="clock-host-runtime-review--次の読取と、修復案・影響・切戻し"></a>

#### 次の読取と、修復案・影響・切戻し

同じbasic queryやPHC/UDP採取は再依頼しない。
未同期の原因切り分けに新しく必要なのは、[限定したpeer/verbose状態・既存の新イベント](clock-operations.md#clock-owner-admin-read--host-w32time-cause-read)。
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

<a id="clock-host-runtime-review--今回の検証範囲"></a>

#### 今回の検証範囲

ignoredな`data/reports/20261109-host-admin-review/review.py`で、6原本・manifest5件・UTF8 decode・query順/exit・provider別34設定を照合した。
12負例（うち7件はmember SHAも再計算）で、改変/欠落/余剰/不正UNC/重複entry/query失敗/admin false/
source不一致/client無効/provider差替え/status重複/JSON重複keyを拒否した。
これは今回原本専用のoffline診断で、製品unit12件追加や一般的な入力validatorの受入ではない。
製品source/schema/writer/lock/workflowは変更せず、製品suite/受入済みCIを反復しない。
追加案内のPowerShellは構文だけ確認し、管理者blockの実採取を今回実行したとは主張しない。

<a id="clock-task-definition-review"></a>

## clock-task-definition-review

<a id="clock-task-definition-review--時刻同期タスクの定義と停止原因の境界"></a>

### 時刻同期タスクの定義と停止原因の境界

2026-11-09、基点`67f4586`。[証拠索引](reviews/20261109-task-source-checks.json)、
[前回resyncの実結果](clock-operations.md#clock-resync-result)、[残課題](../../261014/plan_mlops.md#remaining-work-latest)。

ユーザー提出の`clock-task-definition-c6cba967-567c-4f62-98f3-60e64ab3f850`は、XML2件とhashes.jsonの3原本。
manifestのSHA-256は両XMLの元bytesと一致した。原本をignoredな別directoryへsnapshotし、変更していない。
採取時UTC/QPC、host/build、service identity、実行履歴はこの提出物には含まれない。後から補わない。

<a id="clock-task-definition-review--xmlから確認できた処理"></a>

#### XMLから確認できた処理

| 項目 | SynchronizeTime | ForceSynchronizeTime |
|---|---|---|
| 実行主体 | LocalService（S-1-5-19）、HighestAvailable | 同左 |
| Action | `%windir%\system32\sc.exe`、引数`start w32time task_started` | COM ClassId `{A31AD6C2-FF4C-43D4-8E90-7101023096F9}`、Data `TimeSyncTask` |
| Trigger | WNF状態変更1件、Delay PT1M | WNF状態変更2件、一方Delay PT1M、他方Data 01 |
| 多重起動/ネットワーク | IgnoreNew / RunOnlyIfNetworkAvailable=true | 同左 |
| 電池への移行 | DisallowStartIfOnBatteries=false / StopIfGoingOnBatteries=false | 同左 |
| IdleSettings | StopOnIdleEnd=true / RestartOnIdle=false | 同左 |
| その他の明記 | StartWhenAvailable=true、Maintenance Period P1D / Deadline P1DT1H | これらの要素は未記載 |

WNFはWindowsの状態変更通知。状態番号の名前や、その通知が過去に発火したかは、このXMLだけでは分からない。
PT1Mはtriggerの遅延、P1D/P1DT1Hはmaintenanceの周期/期限であり、W32Timeの停止timeoutではない。
未記載のExecutionTimeLimitやRunOnlyIfIdle等へ独自の値を補って停止原因にしない。

[sc startの公式仕様](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc742126(v=ws.11))から、
`task_started`はサービスへ渡す起動引数と読める。ただし、この引数でW32Timeがどの内部branchへ進み、いつ終了するかは今回未確認。
旧版のcommunity Q&Aを現在buildの実装根拠にしない。

[StopOnIdleEnd](https://learn.microsoft.com/en-us/windows/win32/taskschd/idlesettings-stoponidleend)はタスクの終了条件。
`sc.exe`という起動用プログラムと、SCMが管理するサービスの寿命は別に確認が必要で、
この設定だけで「ユーザー操作によりW32Timeが停止した」とは結論できない。
前回の起動時刻近接・code0停止・TaskScheduler log無効という限界は変わらない。

<a id="clock-task-definition-review--com登録先の追加限定読取"></a>

#### COM登録先の追加限定読取

[ClassId](https://learn.microsoft.com/en-us/windows/win32/taskschd/comhandleraction-classid)は処理を担うCOMクラスの識別子。
前回Execute/Argumentsがnullだったのは、ExecではないActionを未展開だったためで、処理の欠落ではなかった。
今回、Windowsの当該HKLMクラスのInprocServer32だけを64/32bit viewで読み、固定2 DLLの版/サイズ/SHAを取得した。

| 現在の静的読取 | 値 |
|---|---|
| OS版の報告 | 10.0.26200.0 |
| Registry64 | TimeSyncTask.dll、ThreadingModel=Both |
| Registry32 | 当該InprocServer32 keyなし |
| TimeSyncTask.dll | 45,056 bytes、版10.0.26100.1、SHA `995dc71eb07d6f6aa1e7bfec3abb5f5d0a9c9ec9788dd7019f4f09bc825da171` |
| w32time.dll | 745,472 bytes、版10.0.26100.1、SHA `3b8a03d852a067710eeafef6642466bda86841502d93743656cb2f166179e00c` |

登録はmachine-wideの静的値で、LocalServiceの有効な全override、過去のmodule load/COM activationを証明しない。
DLLをロード/実行せず、署名・発行者真正性の確認も今回行っていない。file hashは同一性確認に限定する。
通常sandboxはinteropのsocket拒否でexit1。許可付きの同じ限定readはexit0で、両試行を保持した。

<a id="clock-task-definition-review--検証と残件"></a>

#### 検証と残件

UTF-8 BOMの実bytesにXML宣言encoding=UTF-16が残っていた。
これは前回提示した`Set-Content -Encoding UTF8`がexport文字列を保存した際の不一致で、ユーザーの操作ミスではない。
厳密UTF-8-sigでUnicode文字列へ復号してXML構造を読んだ。原本の宣言/bytes/hashを変更せず、再importに使用しない。

固定captureの10負例（SHA不一致、欠落/余剰、manifest重複、別UNC parent、traversal、別algorithm、
再hash後の不正XML/Actions欠落/URI差）を拒否した。製品unit追加や実資格の試験ではない。
offline-task-review-v1ではlocal UTCが0.254948秒逆行し、MONOTONIC差は正だった。
これは新しいwrapper観測であり、setter・tick11000・T03のphase/stackを取得したものではない。

停止caller/内部方針は未確定。許可なしのtask起動・service操作・event log有効化・追加resyncは行わない。
新しい動的資料か、[別許可が必要な1条件比較案](clock-operations.md#clock-host-maintenance-plan--service-start-comparison)をreviewする。
[Web時計源のreview](clock-operations.md#clock-web-source-review)も進めたが、源採用/全資格/実fitの保留を解除するものではない。

<a id="clock-web-source-review"></a>

## clock-web-source-review

<a id="clock-web-source-review--timeis・nictを時計の参照元に使う場合"></a>

### time.is・NICTを時計の参照元に使う場合

**2026-11-09追加訂正:** [取得方法review](clock-operations.md#clock-nict-method-review)で、公式FAQの平均20回/時・480回/日と超過時の事前連絡を確認した。
下記の「16秒以上」だけでは利用条件として不足。毎分の直接取得案は、頻度の回答・真正性・不確かさの根拠が揃うまで開始しない。

2026-11-09、基点`67f4586`。ユーザー提示2サイトと提供元の説明をreviewした。
[証拠索引](reviews/20261109-task-source-checks.json)、[既存の源受入条件](clock-operations.md#clock-source-readiness)を参照。

**外部との大きな時刻差を人が確認する用途には使えるが、画面の表示だけで今回の全時計資格は満たせない。**
時計設定をサイトへ合わせる操作、実source reader、継続取得、資格試験は実施していない。

| 候補 | 利用できる範囲 | 今回の採用判断 |
|---|---|---|
| time.is/Japan | 人による時刻/ずれの目安の確認 | 自動収集は採用しない。専用APIは条件未取得 |
| NICT JST5表示ページ | 人による内蔵時計との差の確認 | 表示のみをUTC/elapsedの正式原本にしない |
| NICT公開NTP ntp.nict.jp | 機械向け時刻取得の調査候補 | 真正性・輸送/精度境界・独立elapsedの資料とreaderが不足し、採用保留 |

<a id="clock-web-source-review--timeis"></a>

#### time.is

[提供元の説明](https://time.is/about)は、表示精度が通常0.02〜0.10秒で回線/PC負荷に依存するとしている。
典型値は、個々の取得について保証された誤差上限でも、source/transportの全不確かさでもない。
[利用規約](https://time.is/terms_of_use)はスクリプトやアプリからの利用・自動refreshを禁止し、API利用は問い合わせを求めている。
人が開いて確認する用途と自動計測を区別する。scraper/API実装、問い合わせ送信、有料契約は行っていない。
provider名・TLS接続・画面の「正確」表示だけで、sourceのUTC精度や独立elapsedの真正性を認定しない。

<a id="clock-web-source-review--nictの表示ページと終了済みapiを混同しない"></a>

#### NICTの表示ページと終了済みAPIを混同しない

[JST5ページ](https://www.nict.go.jp/JST/JST5.html)自体が、正確な標準時提供を目的とせず通信状況で誤差が生じると説明している。
[表示の技術説明](https://jjy.nict.go.jp/tsp/link/JST.html)では、取得後はPCの時計を基準に表示が進む。
その画面を継続撮影しただけでは、PCと独立した経過時間の測定にならない。現在のbrowser実装を動かして確認したわけではない。

旧JSON/JSONP/HTTPS時刻APIの説明も残っているが、[公式の終了告知](https://jjy.nict.go.jp/httphttps-index.html)で
HTTP/HTTPS時刻配信は2022-03-31終了と明記されている。
**同じ告知は、JST5の時刻差表示ページとNTPはこの終了の影響を受けないとも明記している。**
JST5まで終了したと説明しない。反対に、JST5のHTMLを取得できたことを、旧APIを自由に利用できる証拠にもしない。
終了済みendpointへ試験queryを送り、動くものを探す作業はしていない。

<a id="clock-web-source-review--nict-ntpを検討する次の段階"></a>

#### NICT NTPを検討する次の段階

[公開NTPの説明](https://jjy.nict.go.jp/tsp/PubNtp/index.html)は、利用者側の精度が回線により数ms〜数百msと変わり、保証しないとしている。
サーバ内の精度を、そのままPCでの取得誤差に流用しない。
[開発者ガイド](https://jjy.nict.go.jp/tsp/PubNtp/clients.html)のpoll間隔は16秒以上。
追加reviewで[FAQ Q1-4](https://jjy.nict.go.jp/tsp/PubNtp/qa.html)の平均20回/時・480回/日と超過時の事前連絡も確認した。
将来の計測設計では両条件を守り、必要頻度と合わなければ提供元への確認または別の源/方法をreviewする。高速queryで不足を埋めない。

次にreviewする不足は以下。これは実queryやreader開始の許可ではない。

1. 通常のNTP応答の送信元・要求対応・再送/差替え・認証をどう確認するか。公式hostnameやnonceだけを認証にしない。
2. source自身のUTC基準と不確かさ、送受信bracket/非対称経路/読取/丸め/欠測から作る保守的な区間。
3. 校正対象QPCの秒数を使って同じQPCを正しいと判断しない計算と、host/PHC/guestとの共有依存。
4. 保存するraw応答・sequence・status・reader/config/hashと、固定policy全期間への対応。成功sampleだけを選別しない。

UTC±1秒と、QPCの進む速さ±100ppmは別の条件である。
既存の[理想的な不確かさ予算](clock-operations.md#clock-source-readiness--固定policyから先に分かる不確かさの予算)でも、
60秒窓の各端点の誤差半径は約3ms未満を要する。ネットワーク参照が永久に不可能という意味ではなく、
今回の回線・読取方法でその境界を証明できるかが未確認という意味である。
機器購入を前提とせず、まず上記の資料/方法が成立するか検討する。値が入るよう閾値や不確かさを緩めない。

<a id="clock-web-source-review--今回の到達点"></a>

#### 今回の到達点

独立源が「何も提示されていない」状態から、提示2サイトの用途/規約/限界とNTP調査候補を具体化した。
Web説明のreviewであって、源の採用・実採取・時計修復・全資格の合格ではない。
Web toolで読んだ公式説明のURL/要約を記録し、生HTTP原本のローカル保存やbrowser動作試験とは呼ばない。
time.is/accuracyの取得はtoolエラーで根拠に使わず、旧NICT資料のAPI説明は終了告知と区別した。
実fit/採点・T02/T11/T12/T13bは既存の前提待ちを維持する。

<a id="clock-nict-method-review"></a>

## clock-nict-method-review

<a id="clock-nict-method-review--nict-ntpの取得方法・不確かさのreview"></a>

### NICT NTPの取得方法・不確かさのreview

2026-11-09、基点`114af9d`。[前回のWeb源review](clock-operations.md#clock-web-source-review)、
[入力条件](clock-operations.md#clock-source-readiness)、[今回の証拠索引](reviews/20261109-nict-method-checks.json)を参照。
**NICTは候補のまま。実query・source adapter・時計資格・実fitは開始していない。**
新しく判明した利用条件と、計算方法の成立条件を整理した資料である。

<a id="clock-nict-method-review--前回のpoll説明の訂正"></a>

#### 前回のpoll説明の訂正

[開発者ガイド](https://jjy.nict.go.jp/tsp/PubNtp/clients.html)は16秒以上という下限を示す。
一方、今回新しく確認した[公式FAQ Q1-4](https://jjy.nict.go.jp/tsp/PubNtp/qa.html)は、
平均20回/時・合計480回/日を超える利用に事前連絡を求め、短いpollの例を配下clientへ供給するstratum2と関連付けている。
FAQには古いOSの例も混在するが、制限撤廃や個人計測の例外を確認できない。
**前回の「16秒以上」だけでは利用条件の説明として不足していた。両方を保守的に適用し、超える計画は提供元の回答待ちにする。**
複数IP/processへの分散、短い再試行、同じPC内の中継を利用回数の回避策にしない。
末端PCを、配下を持つstratum2と自己申告して例外扱いにもしない。

現policyは各30分phaseの0〜1800秒に60秒ごとの外部UTCを求めるため31境界。
通常60分の2phaseで同じ境界原本を共有できる最も有利な場合でも61時点、半開区間[0,3600)で60回になる。
再開後の別trialも同じなら合計122時点（共有しない4phaseでは124）。全日480未満でも時間平均の条件を満たさない。
これは各境界へ直接1queryを対応させる案の計数であり、実施回数ではない。
少ないqueryから未観測境界を補うなら、別の独立elapsed/holdoverの上限が必要で、現在は無い。
16秒間隔を守るだけでこの問題は解消せず、policyを180秒窓へ変更して回避もしない。

<a id="clock-nict-method-review--真正性と要求対応を分ける"></a>

#### 真正性と要求対応を分ける

[NTPの仕様 §8](https://www.rfc-editor.org/rfc/rfc5905.html#section-8)ではorigin照合等で要求対応を調べる。
ただしIP/port一致、DNS、echo値、stratum、root dispersionやSHAは、単独で送信者の認証にはならない。
[NTS §1・§8](https://www.rfc-editor.org/rfc/rfc8915.html#section-8)は認証の仕組みと残る遅延攻撃・証明書検証問題を説明する。
NICTの今回読んだ公式公開NTP資料には、採用できるNTS endpoint/認証方法を確認できなかった。
「未確認」を「NTS非対応確定」へ変えず、別providerの機能をNICTへ転記しない。
外部に問い合わせを送信したり、UDP123/TCP4460や終了済みHTTP APIを試したりしていない。

将来のreaderを実装する前に、以下をsource dossierと取得protocolへ固定する。まだ実装済みの機能ではない。

| 境界 | 原本・拒否条件 |
|---|---|
| 送信前 | trial/reader/設定hash、sequence、要求bytes、期待peerとaddress family、送信intent。未送信・送信失敗も残す |
| 要求対応 | raw originと送信値のbyte一致、未消費の要求、重複/再送/別trial/遅延到着/並行の区別。timestamp欄を独自nonce protocolへ勝手に変更しない |
| packet | 長さ/version/server mode、LI、stratum/KoD、時刻0/順序/era、拡張fieldと認証を採用方式に従い検証。不明な末尾を無視して認証済みにしない |
| 認証 | 証明書/署名等の実検証、または監督下の取得と来歴の根拠。自己申告flagは代用不可。時計不確実を理由にTLS検証を無効化しない |
| 失敗 | raw/error/timeout/欠測と未確定状態を保存。[KoDの指針](https://www.rfc-editor.org/rfc/rfc8633.html#section-5.4)も踏まえ、今回の案では中断してreviewし、自動retry/fallbackで成功だけを選ばない |

独立した複数源は誤動作の検出には役立つ場合があるが、共通の基準源/回線/仮想化経路や誤差が残る。
票数だけで真正性・精度・独立性を認定しない。

<a id="clock-nict-method-review--通信遅延とutc区間"></a>

#### 通信遅延とUTC区間

一般的なNTPのoffset点推定を、そのまま誤差なしのUTC値へ変えない。
非対称経路、server処理時間、OS内の送受信待ち、読取bracket、量子化/丸め、source誤差、leap/eraを評価する。
[NICTの説明](https://jjy.nict.go.jp/tsp/PubNtp/index.html)のserver側精度は、client到着時の誤差保証ではない。
root dispersion等も、認証/仕様/読取条件なしで全誤差の証明にしない。

以下は今回の工学的な**提案式**で、NICTが保証する計算方法ではない。
外部UTCが連続して進む区間で、server受信/送信のUTC表示をT2/T3、誤差半径をe2/e3、
client送信前〜受信後の真の経過時間の独立した上限をDとする。
対応と因果順が確かならclient受信時刻は`[T3-e3, T2+e2+D]`に含まれる。
T2はclient送信以降、T3はclient受信以前に起こるためで、往路/復路の対称性は不要。
上下が逆なら不整合、Dが無いなら計算不能。未校正QPCの秒換算RTTをDの保証として使わない。
受信時点とWindows/WSL sampleの対応にも追加bracketが必要で、packet値を異なるsampleへ貼り付けない。
UTC逆行/非連続区間は別の既知違反として残し、負delayやoffsetをclampしない。

<a id="clock-nict-method-review--qpcを自分自身で校正しない方法の候補と限界"></a>

#### QPCを自分自身で校正しない方法の候補と限界

外部の同じ種類の送信event A/Bが、各clientのQPC bracket
`[a_send,a_recv]`と`[b_send,b_recv]`の中に因果的に入るとする。
`a_send <= a_recv < b_send <= b_recv`を要求する。
外部表示の差をS、source両端誤差の合計をE、QPCの公称周波数をFとすると、
両event間の平均QPC進行率（QPC公称秒/外部秒）は次の区間へ入る。

```text
lower = ((b_send - a_recv) / F) / (S + E)
upper = ((b_recv - a_send) / F) / (S - E)
S > E >= 0, F > 0
```

QPCの読取順序だけを使い、QPCのSI秒精度や通信の対称性を仮定しない。
sourceの真の差は[S-E,S+E]、event間のQPC差は両bracketから包含できるためである。
ただしsource精度/真正性、要求以降に応答が生成されたこと、QPCの単調性、counter読取誤差の包含が別途必要。
分解能より細かいevent時刻を断定せず、実readerは読取誤差込みのbracketを出す必要がある。

合成例では、60秒差、F=1MHz、両bracket幅1ms、source半径各10µsで数値区間だけは±100ppm内。
幅20msにすると中点比1.0でも全区間は入らない。これらは今回回線やNICTの値ではない。
450通りの有限な隠れevent/誤差の組合せで包含を検算したが、実際のnetwork/認証の検証ではない。

**この式も固定60秒境界の校正値へ直接挿入できない。**
remote eventの時点と、policyが指定するQPC first-reach sampleは異なる。
例として外部秒/QPC公称秒が(0,0)→(70,60)→(120,120)なら、全体比1.0でも最初のQPC60秒窓は60/70。
未観測区間の一定速度や滑らかさを仮定した補間は、この違反を隠す。
固定境界への対応とその不確かさを独立に証明する方法が無い間は、campaignへの変換を実装しない。
1秒ごとのhost/guest採取をNTPへ置換したり、長phaseだけで必須30窓を代用したりしない。

<a id="clock-nict-method-review--依存関係・同期責任"></a>

#### 依存関係・同期責任

NICTの基準時計は外部候補でも、packetを読むPC・QPC・WSL/PHC/backendは観測経路を共有する。
WSL MONOTONIC/RAWとhost QPCの一致だけを独立elapsedにせず、同じ源へ同期した中継を別々の独立源と数えない。
host同期用peerの変更と、時計を変更しない計測readerの採用は別の判断である。
今回NICTをW32Time/chronydへ設定しておらず、task_started/停止caller/tick11000/T03の過去原因は解決していない。

<a id="clock-nict-method-review--次の判断と必要資料"></a>

#### 次の判断と必要資料

| 選択肢 | 次に必要なもの | 現在の状態 |
|---|---|---|
| NICTを直接使う | 頻度/用途を明記した提供元の回答、認証/来歴、source誤差と固定境界への対応の根拠 | 未取得。問い合わせ文案は下記。送信していない |
| 利用可能な別の参照 | 利用条件、認証、独立elapsed、校正/誤差・共有依存の資料 | 新資料未提示。機器購入を必須とはしていない |
| 引数なしW32Time起動比較 | [案D](clock-operations.md#clock-host-maintenance-plan--service-start-comparison)のhelper作成/検証と、その後の別許可 | sourceの利用条件/不確かさを解決する比較ではないため今回は着手しない。未許可/未実施 |

提供元への問い合わせ文案（**送信にはユーザーの指示が必要**）:

> 個人の機械学習学習環境で、OS時計を変更せず時計の評価用に参照することを検討しています。
> 通常60分と休止再開後60分で、各分境界に最低1回、理想的な境界共有でも計122時点を想定しています。
> これはFAQの平均20回/時を超えるため、実施可否と必要な申請条件を教えてください。
> 実装を決める際には境界前後の取得や失敗分を含む要求上限を別途確定し、再確認します。
> 利用できる認証方式（NTS等の有無・公式endpoint資料）と、時刻stampの基準・精度・運用条件の資料も教えてください。
> client通信遅延込みの精度保証を、server精度の説明から推定する意図はありません。

頻度の回答だけで源採用とはしない。真正性と不確かさが不足すれば実採取は始めない。
サービス比較は現在の source dossier review の必須前提ではなく、今すぐ実行すべきOSコマンドは無い。

<a id="clock-nict-method-review--今回の検証範囲"></a>

#### 今回の検証範囲

`data/reports/20261109-nict-method-review/verify_math.py`の標準libraryだけの13件で、
450組の有限包含、広い区間/明確な逸脱、無効domain、source誤差、負delay、境界補間の反例、query数を検算した。
製品unit/packet parser試験ではない。raw NTP、campaign/registry/DBへの書込は0件。
原本recipe/hash・基点資料・コマンド/cwd/終了値/未資格UTC/MONOTONICは[証拠索引](reviews/20261109-nict-method-checks.json)へ結合する。
Web説明はtoolで読んだURL/要約で、生HTTP archive・動的サービス認証の証拠ではない。
source/schema/writer/lock/workflowを変更せず、受入済みsuite/CI/T09/T10は繰り返していない。
実reader/collector/host deadline/固定負荷/全資格とT02/T11/T12/T13bは前提待ちを維持する。

<a id="time-api-options"></a>

## time-api-options

<a id="time-api-options--指定された時刻apiの確認（2026-11-14）"></a>

### 指定された時刻APIの確認（2026-11-14）

時計研究は[保留](capital.md#product-completion-policy)。ユーザーが指定した4ページの公開仕様と、記載されたUTC endpointへのWeb tool読取を各1回だけ確認した。
collector、校正、OS同期、APIキー作成、追加NTP queryは行わない。

| サービス | 公開仕様で確認した取得方法 | 今回の判断 |
|---|---|---|
| [timeapi.world](https://timeapi.world/) / [FAQ](https://timeapi.world/faq) | gateway.timeapi.world/timezone/{tz}。FAQは無料20,000回/月、通常の時間サーバーを利用すると説明。pricingにはRapidAPI keyの入口がある | UTC JSONの提供仕様あり。gatewayへの今回のWeb tool読取はInternal Errorで、HTTP status/本文未取得。匿名呼出の実動作・認証要否を確認済みとはしない |
| [Time.Now](https://time.now/developer) | HTTPS /developer/api/time?timezone=UTC。API key不要、README/About等への帰属リンクを要求 | 表示・大まかな時刻照合の候補。今回endpointのWeb tool読取はInternal Error、成功JSONは未取得。利用する場合は要求される帰属表示と利用条件を再確認 |
| [CurrentTime.now](https://currenttime.now/developer/docs) | HTTPS /public/api/timezone/Etc/UTC、標準endpointはkey不要と説明 | 今回endpointのWeb tool読取はInternal Error。既知の時刻源との独立性・提供元間の共有依存・端末側誤差上限は未確認 |
| [紹介記事](https://note.com/maci_26/n/nbb7734eedb58) | API一般の紹介。ページ内のTime検索は該当なし | 時刻APIの公式仕様・精度保証の根拠にはしない。技術判断には各提供元資料を使う |

APIが現在UTCを返す機能と、PCの精密時計を校正する証拠は別である。
HTTPSは接続先証明書の確認に役立つが、レスポンスの生成時点、cacheの鮮度、通信遅延、
上流時計の誤差・共有依存まで保証しない。表示桁数や「atomic」「high precision」という説明だけを精度保証にしない。
今回のツールエラーはサービス停止・利用拒否・endpoint不存在の証明でもない。
公開ページはWeb toolによる本文読取/要約で、生HTTP原本ではない。取得値を時計資格やfit開始の根拠にしない。

将来、画面表示や手動の大幅なずれ確認に使う余地はあるが、現在の製品完成には採用必須ではない。
追加のAPI試験やsource選定は今回ここで止める。保留を解除して調査を繰り返さない。
