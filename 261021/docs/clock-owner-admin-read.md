# Windows管理者での限定した時計process読取

**2026-11-09更新:** 指定されたowner/resourcesの2原本を[照合済み](clock-resources-admin-review.md)。
直接保存方式の終了値/実完了行/hashと、採取区間のchronyd・PHC fd・UDP所有を確認した。
基本inventory/PHC/socketの同じ採取は不要。hostの[basic管理者queryも受入済み](clock-host-runtime-review.md)。
以下の未実施・未採取は各旧時点の履歴であり、現在は[peer/last-error等の原因限定読取](#host-w32time-cause-read)と独立源資料が次の入力になる。
dev差・timesyncd identity変更・tick11000内部原因・同期責任・全時計資格は未解決である。

[現在の調査結果](clock-process-ownership.md)の続き。**時計設定を変更する手順ではない。**
現チャットのWindows tokenは非管理者で、`wsl --debug-shell`の起動を拒否された。
Ubuntu rootやCodexのsandbox許可はWindows管理者tokenとは異なる。
基点2181bfeからの[追加確認](chrony-control-analysis.md)でも保存先に管理者ログはなく、ユーザーも未採取と回答した。以下の採取が次の依存作業である。チャット側で読取可能な範囲の調査は実施済みで、この管理者tokenをチャットから代行取得できていない。

## 採取する情報と実施条件

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

## 今回の結果と入力待ち

[管理者ログの照合結果](clock-admin-log-review.md)で、readerの両process採取完了を確認した。今回の基本inventoryの再実行は不要。
旧`.ps1`と対話pipelineはdebug shellの終了/後続metadata保存まで未検証だった。今回collection.jsonがないため採取全体の正常終了とは記録しない。
`>>`だけで未実行とは判断しない。実行中の旧採取pipelineが残っている場合、readerの実完了行を確認してからCtrl+Cでその採取クライアントを中断できる。WSL shutdownやDocker停止は不要。

## 今後追加採取が必要な場合

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

## 出力の読み方と範囲

保存先はWSLから`261021/data/reports/clock-owner-admin-<GUID>/`として読める。チャットへdirectory名を伝えれば、fileから照合を続けられる。旧PowerShell出力のUTF-8 BOMは`utf-8-sig`で読む。新方式はinventory.log/reader-exit.txt/hashes.sha256を使い、Windows側のcollection.jsonは作らない。

readerはchronyd/systemd-timesynだけを候補として列挙し、exe/hash/argv/親/namespace/cgroup/開始tickと、process root内のchrony設定の限定directiveを読む。
設定のinclude/confdirやargvの`-fFILE`形式など、未追跡の指定があれば手動照合を追加する。認証key・環境変数・全processのargvを一括出力しない。

- 終了77: root不足。69: 必要な読取tool不足。42: 一方のprocessが見えない、identity変化、読取失敗などの不完全採取。
- 終了0/`INVENTORY_COMPLETE_NEEDS_TRACE_CORRELATION`: 候補の一覧採取ができたという意味。**犯人特定・時計修復・資格確認の合格ではない。**
- current boot、PID namespace、開始tick、exe hashを今回のsetter traceと照合する。名前が一致するだけで管理元を確定しない。別bootなら旧PID対応は使わない。
- `CLOCK_OWNERSHIP_RESULT`行とnative終了値の両方を確認する。debug shellのechoや外側exit 0を内側の成功に読み替えない。

rawは端末path・UID・argv等を含むためignored reportsへ保持する。Gitへ原本を追加せず、公開用summaryとSHAだけをreview後に残す。
実DB・registry・配信は採取対象に含めない。実行file/起動者/生成設定の照合後に、Solが修復案・影響・切戻しと、必要な別のOS変更許可を整理する。

## 採取後に照合する項目

表示された`clock-owner-admin-<GUID>`のWSLパスをチャットへ伝える。raw全文の貼付けは不要。
このreaderはPHCの装置一覧を読むが、daemonの開いたPHC fdやUDP socketの所有まで検証するものではない。
Solは開始/終了のidentity、exe/hash、親/namespace、選択configを先に確認し、fd/socket・config include・管理unit等の不足があれば、そのprocessだけの追加読取を設計する。
別bootやPID再利用、identity変化があれば旧setter traceへ結び付けない。
`INVENTORY_COMPLETE`でもtickの内部変数や修復完了を証明しないため、[追加調査の未確定事項](chrony-control-analysis.md)を残す。

<a id="chrony-resources-admin"></a>

## 追加のPHC/socket所有採取（2026-11-03）

2026-11-04時点でも追加directoryは未採取。[別scopeでのinitd管理層照合](initd-owner-correlation.md)は進んだが、
chronydのPHC fd/UDP所有や現在の初期namespace inventoryを採取した代わりにはならない。
次にユーザー側で必要な限定読取は、引き続きこの節の手順。既に保存済みであればdirectoryだけを伝えればよい。

修復後の観測期間・判定式・欠測/失効の扱いは[時計資格の計測設計v1](clock-qualification-plan.md)に整理した。
設計は管理者採取・修復・休止再開を実施する許可や、資格の合格を意味しない。

[追加調査](clock-followup.md)でWindows非管理者tokenと、Ubuntuからchronydが見えない境界を再確認した。
以下は未採取のPHC fd/UDP inodeだけを補う手順。基本inventoryも同じ新規directoryへ採り、identityを対応付ける。
旧PIDを固定しない。OS・時計・service・実行policyは変更しない。

現在のreaderはexeの`dev:inode:size`も前後比較する。
[内部数値採取](chrony-runtime-control.md)の同じboot/dev42/inode4227/SHAと照合するが、別bootならその番号を流用しない。
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

<a id="host-w32time-admin"></a>

## W32Timeの管理者限定読取（取得済み・手順の履歴）

**追補:** `clock-host-admin-00f021d1-…`でこのblockの実結果を[review済み](clock-host-runtime-review.md)。
3queryのexit0・原本hashを確認したため、同じbasic queryを再実行する必要はない。
以下の「未実施」は案内作成時点の履歴。原因調査の新しい不足分は[次の節](#host-w32time-cause-read)を参照する。

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

<a id="host-w32time-cause-read"></a>

## 未同期原因の限定読取（peer・last-error・新イベント）

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
