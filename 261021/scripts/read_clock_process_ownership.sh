#!/bin/sh
# Read-only process/configuration inventory. No signals, clock writes or service operations.
# Intended for an already-running WSL debug shell (initial PID namespace).
set -u
export LC_ALL=C
printf 'CLOCK_OWNERSHIP_BEGIN\n'
if [ "$(id -u)" != 0 ]; then
    printf 'ERROR=root_required\n'
    exit 77
fi
for tool in readlink sed tr sha256sum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'ERROR=missing_tool:%s\n' "$tool"
        exit 69
    fi
done
id
uname -a
cat /proc/sys/kernel/random/boot_id /proc/uptime
for item in pid mnt time user; do
    printf 'READER_NS_%s=' "$item"
    readlink "/proc/self/ns/$item"
done
printf 'READER_STATUS\n'
sed -n '/^NSpid:/p;/^Uid:/p;/^CapEff:/p' /proc/self/status

start_ticks() {
    # /proc/PID/stat field 22; remove comm using the final closing parenthesis.
    rest=${1##*) }
    index=1
    for field in $rest; do
        if [ "$index" -eq 20 ]; then printf '%s\n' "$field"; return; fi
        index=$((index + 1))
    done
    return 1
}

config_read() {
    config_file=$1
    if [ ! -f "$config_file" ]; then
        printf 'CONFIG_ABSENT=%s\n' "$config_file"
        return
    fi
    printf 'CONFIG_SELECTED_DIRECTIVES=%s\n' "$config_file"
    sha256sum "$config_file" || return 1
    # Never read key contents or dump environment variables.
    sed -n -E '/^[[:space:]]*(server|pool|peer|refclock|driftfile|makestep|rtcsync|leapsectz|logdir|cmdport|bindcmdaddress|maxdistance|maxslewrate|maxdrift|corrtimeratio|include|confdir|sourcedir)([[:space:]]|$)/p' "$config_file"
}

chrony_count=0
timesync_count=0
failed=0
for proc_path in /proc/[0-9]*; do
    [ -r "$proc_path/comm" ] || continue
    read -r proc_name < "$proc_path/comm" || continue
    case "$proc_name" in
        chronyd) chrony_count=$((chrony_count + 1));;
        systemd-timesyn) timesync_count=$((timesync_count + 1));;
        *) continue;;
    esac
    printf '\nPROCESS_BEGIN=%s\n' "$proc_path"
    stat_before=$(cat "$proc_path/stat") || { failed=1; continue; }
    exe_before=$(readlink "$proc_path/exe") || { failed=1; continue; }
    hash_before=$(sha256sum "$proc_path/exe") || { failed=1; continue; }
    printf 'EXE=%s\nEXE_HASH=%s\nSTAT_BEFORE=%s\n' "$exe_before" "$hash_before" "$stat_before"
    sed -n '/^Name:/p;/^Pid:/p;/^Tgid:/p;/^PPid:/p;/^NSpid:/p;/^Uid:/p;/^Gid:/p;/^CapEff:/p' "$proc_path/status" || failed=1
    printf 'CLOCK_DAEMON_ARGV_ONE_PER_LINE\n'
    tr '\000' '\n' < "$proc_path/cmdline" || failed=1
    printf 'CGROUP\n'
    cat "$proc_path/cgroup" || failed=1
    for item in pid pid_for_children mnt time user; do
        printf 'NS_%s=' "$item"
        readlink "$proc_path/ns/$item" || failed=1
    done
    parent_id=$(sed -n 's/^PPid:[[:space:]]*//p' "$proc_path/status")
    if [ -r "/proc/$parent_id/status" ]; then
        printf 'PARENT_STATUS\n'
        sed -n '/^Name:/p;/^Pid:/p;/^PPid:/p;/^NSpid:/p' "/proc/$parent_id/status"
        printf 'PARENT_EXE='
        readlink "/proc/$parent_id/exe" || failed=1
        sha256sum "/proc/$parent_id/exe" || failed=1
    else
        printf 'PARENT_UNAVAILABLE=%s\n' "$parent_id"
        failed=1
    fi
    if [ "$proc_name" = chronyd ]; then
        config_read "$proc_path/root/etc/chrony.conf" || failed=1
        config_read "$proc_path/root/etc/chrony/chrony.conf" || failed=1
        alternate=$(tr '\000' '\n' < "$proc_path/cmdline" | sed -n '/^-f$/{n;p;}')
        case "$alternate" in
            /*) config_read "$proc_path/root$alternate" || failed=1;;
            '') :;;
            *) printf 'ALTERNATE_CONFIG_REQUIRES_REVIEW=%s\n' "$alternate"; failed=1;;
        esac
        printf 'DRIFT_FILE_PRESENT='
        if [ -f "$proc_path/root/var/lib/chrony/drift" ]; then
            cat "$proc_path/root/var/lib/chrony/drift"
        else
            printf 'no\n'
        fi
    fi
    stat_after=$(cat "$proc_path/stat") || { failed=1; continue; }
    exe_after=$(readlink "$proc_path/exe") || { failed=1; continue; }
    hash_after=$(sha256sum "$proc_path/exe") || { failed=1; continue; }
    printf 'STAT_AFTER=%s\n' "$stat_after"
    if [ "$(start_ticks "$stat_before")" != "$(start_ticks "$stat_after")" ] ||
       [ "$exe_before" != "$exe_after" ] || [ "$hash_before" != "$hash_after" ]; then
        printf 'IDENTITY_CHANGED_DURING_READ\n'
        failed=1
    fi
    printf 'PROCESS_END=%s\n' "$proc_path"
done
printf '\nPTP_SOURCE\n'
for item in /sys/class/ptp/ptp*/clock_name /sys/class/ptp/ptp*/dev; do
    if [ -r "$item" ]; then printf '%s=' "$item"; cat "$item"; fi
done
printf 'CLOCK_OWNERSHIP_COUNTS chronyd=%s timesyncd=%s errors=%s\n' "$chrony_count" "$timesync_count" "$failed"
if [ "$failed" != 0 ] || [ "$chrony_count" -eq 0 ] || [ "$timesync_count" -eq 0 ]; then
    printf 'CLOCK_OWNERSHIP_RESULT=INCOMPLETE\n'
    exit 42
fi
# This reports inventory completeness, not proof of which process made a clock syscall.
printf 'CLOCK_OWNERSHIP_RESULT=INVENTORY_COMPLETE_NEEDS_TRACE_CORRELATION\n'
exit 0
