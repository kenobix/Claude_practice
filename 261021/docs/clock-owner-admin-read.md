# Windows管理者での限定した時計process読取

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

scriptfile実行が端末policyで許可される場合のコマンド:

```powershell
& '\\wsl.localhost\Ubuntu\home\kenshin\work\claude_practice\261021\scripts\collect_clock_owner_admin.ps1'
```

今回の環境ではこの`.ps1`直接実行が**policyで拒否された**。その結果を保存しており、成功と記録していない。
policyを変えず、端末の運用規則上対話コマンドの実施が許される場合は、次の限定した採取コマンドを管理者PowerShellへ貼り付ける。
端末policyの判断が必要なら端末管理者に渡す。`Set-ExecutionPolicy`、`-ExecutionPolicy Bypass`、UAC自動昇格は手順へ追加していない。
[Microsoftの説明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-5.1)にあるとおり、policyのscopeや対話入力とscriptfile実行は区別する。

```powershell
if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator token required' }
$taskRunning = (& wsl.exe --list --running --quiet | Out-String).Replace([string][char]0, '')
if ($LASTEXITCODE -ne 0 -or -not ($taskRunning -split '\s+' -contains 'Ubuntu')) { throw 'Ubuntu must already be running' }
$taskRepo = '\\wsl.localhost\Ubuntu\home\kenshin\work\claude_practice'
$taskReader = Join-Path $taskRepo '261021\scripts\read_clock_process_ownership.sh'
$taskText = [IO.File]::ReadAllText($taskReader)
$taskDir = Join-Path $taskRepo ('261021\data\reports\clock-owner-admin-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskDir | Out-Null
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$taskStart = [DateTime]::UtcNow.ToString('o')
$taskQpc = [Diagnostics.Stopwatch]::GetTimestamp()
$taskText | & wsl.exe --debug-shell 2>&1 | Out-File -FilePath (Join-Path $taskDir 'debug-shell.log') -Encoding utf8
$taskExit = $LASTEXITCODE
[ordered]@{start_utc=$taskStart;end_utc=[DateTime]::UtcNow.ToString('o');qpc_start=$taskQpc;qpc_end=[Diagnostics.Stopwatch]::GetTimestamp();qpc_frequency=[Diagnostics.Stopwatch]::Frequency;native_exit=$taskExit;command='read_clock_process_ownership.sh -> wsl.exe --debug-shell';status='unreviewed'} | ConvertTo-Json | Out-File (Join-Path $taskDir 'collection.json') -Encoding utf8
Get-FileHash -Algorithm SHA256 -LiteralPath $taskReader,(Join-Path $taskDir 'debug-shell.log'),(Join-Path $taskDir 'collection.json') | Select-Object Path,Hash | ConvertTo-Json | Out-File (Join-Path $taskDir 'hashes.json') -Encoding utf8
$taskDir
```

この管理者/対話経路は現在の権限でend-to-end実行できていない。native stderrがPowerShell errorになる場合や中断時は、ログと例外を保持し、正常採取と見なさない。
90秒程度たっても結果が返らない場合はCtrl+Cで採取クライアントを中断し、その事実と残ったlogを保存する。再試行は別directoryで行い、速い/成功した結果だけを残さない。自動timeoutを検証済みのcollectorではない。

## 出力の読み方と範囲

保存先はWSLから`261021/data/reports/clock-owner-admin-<GUID>/`として読める。チャットへdirectory名を伝えれば、fileから照合を続けられる。UTF-8 BOMを含む出力は`utf-8-sig`で読む。

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
