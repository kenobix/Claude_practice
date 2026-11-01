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
