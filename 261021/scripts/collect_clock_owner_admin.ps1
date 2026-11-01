param(
    [string]$RepositoryWindowsPath = '\\wsl.localhost\Ubuntu\home\kenshin\work\claude_practice'
)
# Run manually in an already elevated Windows PowerShell. Never self-elevates.
# Only creates evidence files; does not start/stop distros or alter time settings.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Output 'ADMINISTRATOR_TOKEN_REQUIRED; no UAC or configuration change attempted.'
    exit 77
}
$taskRunning = (& wsl.exe --list --running --quiet 2>&1 | Out-String).Replace([string][char]0, '')
$taskListExit = $LASTEXITCODE
if ($taskListExit -ne 0 -or -not ($taskRunning -split '\s+' -contains 'Ubuntu')) {
    throw 'Ubuntu must already be running. Collection will not start it.'
}
$taskReader = Join-Path $RepositoryWindowsPath '261021\scripts\read_clock_process_ownership.sh'
$taskScript = [System.IO.File]::ReadAllText($taskReader)
$taskDirectory = Join-Path $RepositoryWindowsPath ('261021\data\reports\clock-owner-admin-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskDirectory | Out-Null
$taskLog = Join-Path $taskDirectory 'debug-shell.log'
$taskStarted = [DateTime]::UtcNow.ToString('o')
$taskStartQpc = [Diagnostics.Stopwatch]::GetTimestamp()
$taskCode = $null
$taskFailure = $null
try {
    # Pass fixed shell text through stdin; avoid shell quoting through Windows argv.
    # PS 5.1 can turn native stderr into a terminating error. Continue only for this
    # pipeline, then retain native exit and output; no failing read is called success.
    $ErrorActionPreference = 'Continue'
    $taskScript | & wsl.exe --debug-shell 2>&1 | Out-File -FilePath $taskLog -Encoding utf8
    $taskCode = $LASTEXITCODE
} catch {
    $taskFailure = $_.Exception.Message
} finally {
    $ErrorActionPreference = 'Stop'
    $taskLogHash = $null
    if (Test-Path -LiteralPath $taskLog) { $taskLogHash = (Get-FileHash -LiteralPath $taskLog -Algorithm SHA256).Hash.ToLowerInvariant() }
    $taskMetadata = [ordered]@{
        started_at_utc = $taskStarted
        ended_at_utc = [DateTime]::UtcNow.ToString('o')
        qpc_start = $taskStartQpc
        qpc_end = [Diagnostics.Stopwatch]::GetTimestamp()
        qpc_frequency = [Diagnostics.Stopwatch]::Frequency
        command = 'read_clock_process_ownership.sh via stdin -> wsl.exe --debug-shell'
        reader_sha256 = (Get-FileHash -LiteralPath $taskReader -Algorithm SHA256).Hash.ToLowerInvariant()
        native_exit_code = $taskCode
        exception = $taskFailure
        log_sha256 = $taskLogHash
        log_encoding = 'UTF-8 with BOM (Windows PowerShell 5.1 Out-File)'
        status = 'collected_unreviewed_not_clock_qualification'
    }
    $taskMetadata | ConvertTo-Json -Depth 4 | Out-File -FilePath (Join-Path $taskDirectory 'collection.json') -Encoding utf8
    Write-Output ('Evidence directory: ' + $taskDirectory)
}
if ($null -ne $taskFailure -or $null -eq $taskCode) { exit 1 }
exit $taskCode
