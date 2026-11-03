#!/bin/sh
# Limited, read-only supplement for an already-running initial WSL PID namespace.
# Never open device contents, socket contents, process memory or environments.
set -u
export LC_ALL=C
task_proc_root=/proc
task_sys_root=/sys
printf 'CHRONY_RESOURCES_BEGIN\n'
if [ "$(id -u)" != 0 ]; then printf 'ERROR=root_required\n'; exit 77; fi
for task_tool in cat readlink sed tr sha256sum stat; do
    if ! command -v "$task_tool" >/dev/null 2>&1; then
        printf 'ERROR=missing_tool:%s\n' "$task_tool"; exit 69
    fi
done
cat "$task_proc_root/sys/kernel/random/boot_id" || exit 42
printf 'READER_PID_NS='; readlink "$task_proc_root/self/ns/pid" || exit 42

start_ticks() {
    task_rest=${1##*) }
    task_index=1
    for task_field in $task_rest; do
        if [ "$task_index" -eq 20 ]; then printf '%s\n' "$task_field"; return; fi
        task_index=$((task_index + 1))
    done
    return 1
}

read_namespaces() {
    for task_ns in pid mnt time net; do
        readlink "$task_proc/ns/$task_ns" || return 1
    done
}

task_count=0
task_failed=0
for task_proc in "$task_proc_root"/[0-9]*; do
    [ -r "$task_proc/comm" ] || continue
    read -r task_comm < "$task_proc/comm" || continue
    [ "$task_comm" = chronyd ] || continue
    task_count=$((task_count + 1))
    printf 'PROCESS_BEGIN=%s\n' "${task_proc##*/}"
    task_stat_before=$(cat "$task_proc/stat") || exit 42
    task_start_before=$(start_ticks "$task_stat_before") || exit 42
    task_exe_before=$(readlink "$task_proc/exe") || exit 42
    task_hash_before=$(sha256sum "$task_proc/exe") || exit 42
    task_ns_before=$(read_namespaces) || exit 42
    printf 'START_TICKS=%s\nEXE=%s\nEXE_HASH=%s\nNAMESPACES\n%s\n' "$task_start_before" "$task_exe_before" "$task_hash_before" "$task_ns_before"
    sed -n '/^PPid:/p;/^NSpid:/p' "$task_proc/status" || exit 42

    # This supplement supports the observed WSL-generated default only. Other
    # arguments or include graphs need explicit review, never automatic traversal.
    task_argv=$(tr '\000' '\n' < "$task_proc/cmdline") || exit 42
    case "$task_argv" in /sbin/chronyd|/usr/sbin/chronyd) :;;
        *) printf 'UNSUPPORTED_ARGV_REQUIRES_REVIEW\n'; task_failed=1;;
    esac
    task_config="$task_proc/root/etc/chrony.conf"
    if [ ! -f "$task_config" ]; then printf 'DEFAULT_CONFIG_UNAVAILABLE\n'; task_failed=1; continue; fi
    task_config_before=$(sha256sum "$task_config") || exit 42
    printf 'CONFIG_HASH=%s\n' "$task_config_before"
    if tr '[:upper:]' '[:lower:]' < "$task_config" | sed -n '/^[[:space:]]*\(include\|confdir\|sourcedir\)[[:space:]]/p' | sed -n '1p' | read -r task_include; then
        printf 'INCLUDE_GRAPH_REQUIRES_REVIEW\n'; task_failed=1
    fi
    task_phc_config=$(sed -n 's/^[[:space:]]*refclock[[:space:]][[:space:]]*PHC[[:space:]][[:space:]]*\([^[:space:]]*\).*/\1/p' "$task_config")
    if [ "$task_phc_config" != /dev/ptp0 ]; then
        printf 'PHC_CONFIGURATION_REQUIRES_REVIEW\n'; task_failed=1
    fi
    task_phc="$task_proc/root/dev/ptp0"
    task_phc_dev=
    if [ -c "$task_phc" ]; then
        task_phc_dev=$(stat -Lc '%t:%T' "$task_phc") || exit 42
        printf 'PHC_DEVICE_RDEV=%s\n' "$task_phc_dev"
    else printf 'PHC_DEVICE_UNAVAILABLE\n'; task_failed=1; fi

    task_fds=0
    task_phc_fds=0
    task_udp_fds=0
    for task_fd in "$task_proc"/fd/[0-9]*; do
        [ -L "$task_fd" ] || continue
        task_fds=$((task_fds + 1))
        if [ "$task_fds" -gt 256 ]; then printf 'FD_BOUND_EXCEEDED\n'; task_failed=1; break; fi
        task_link_before=$(readlink "$task_fd") || { task_failed=1; continue; }
        if [ -n "$task_phc_dev" ] && [ -c "$task_fd" ]; then
            task_dev=$(stat -Lc '%t:%T' "$task_fd") || { task_failed=1; continue; }
            if [ "$task_dev" = "$task_phc_dev" ]; then
                printf 'PHC_FD=%s RDEV=%s\n' "${task_fd##*/}" "$task_dev"
                task_phc_fds=$((task_phc_fds + 1))
            fi
        fi
        case "$task_link_before" in 'socket:['*']')
            task_inode=${task_link_before#socket:[}; task_inode=${task_inode%]}
            case "$task_inode" in ''|*[!0-9]*) task_failed=1; continue;; esac
            # Read the process's own network namespace tables, not reader /net.
            for task_table in udp udp6; do
                if [ ! -r "$task_proc/net/$task_table" ]; then task_failed=1; continue; fi
                task_rows=$(while read -r task_sl task_local task_remote task_st task_tx task_tr_when task_ret task_uid task_to task_ino task_tail; do
                    # tx:rx and tr:tm->when are each one whitespace token.
                    if [ "$task_ino" = "$task_inode" ] &&
                       { [ "$task_local" = 0100007F:0143 ] || [ "$task_local" = 00000000000000000000000001000000:0143 ]; }; then
                        printf '%s %s %s\n' "$task_local" "$task_remote" "$task_ino"
                    fi
                done < "$task_proc/net/$task_table") || { printf 'UDP_TABLE_READ_FAILED\n'; task_failed=1; continue; }
                if [ -n "$task_rows" ]; then
                    printf 'UDP_FD=%s TABLE=%s LOCAL_REMOTE_INODE=%s\n' "${task_fd##*/}" "$task_table" "$task_rows"
                    task_udp_fds=$((task_udp_fds + 1))
                fi
            done
        ;; esac
        task_link_after=$(readlink "$task_fd") || { task_failed=1; continue; }
        if [ "$task_link_before" != "$task_link_after" ]; then printf 'FD_CHANGED_DURING_READ\n'; task_failed=1; fi
        # Never print unrelated fd targets or their contents.
    done
    printf 'FD_COUNTS total=%s phc=%s udp=%s\n' "$task_fds" "$task_phc_fds" "$task_udp_fds"
    if [ "$task_phc_fds" -eq 0 ] || [ "$task_udp_fds" -eq 0 ]; then task_failed=1; fi
    task_stat_after=$(cat "$task_proc/stat") || exit 42
    task_start_after=$(start_ticks "$task_stat_after") || exit 42
    task_exe_after=$(readlink "$task_proc/exe") || exit 42
    task_hash_after=$(sha256sum "$task_proc/exe") || exit 42
    task_ns_after=$(read_namespaces) || exit 42
    task_config_after=$(sha256sum "$task_config") || exit 42
    if [ "$task_start_before" != "$task_start_after" ] || [ "$task_exe_before" != "$task_exe_after" ] ||
       [ "$task_hash_before" != "$task_hash_after" ] || [ "$task_ns_before" != "$task_ns_after" ] ||
       [ "$task_config_before" != "$task_config_after" ]; then
        printf 'IDENTITY_OR_CONFIG_CHANGED_DURING_READ\n'; task_failed=1
    fi
    printf 'PROCESS_END=%s\n' "${task_proc##*/}"
done
for task_item in "$task_sys_root"/class/ptp/ptp0/clock_name "$task_sys_root"/class/ptp/ptp0/dev; do
    if [ -r "$task_item" ]; then printf 'PHC_SYSFS=%s\n' "$(cat "$task_item")"; fi
done
printf 'CHRONY_RESOURCES_COUNTS candidates=%s errors=%s\n' "$task_count" "$task_failed"
if [ "$task_count" != 1 ] || [ "$task_failed" != 0 ]; then
    printf 'CHRONY_RESOURCES_RESULT=INCOMPLETE\n'; exit 42
fi
printf 'CHRONY_RESOURCES_RESULT=READ_COMPLETE_NEEDS_TRACE_CORRELATION\n'
