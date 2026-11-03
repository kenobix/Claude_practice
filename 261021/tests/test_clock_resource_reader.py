"""Run the actual shell reader against copied /proc fixtures; no OS changes.

Only the two literal filesystem roots are replaced in a temporary script copy.
The distributed reader never accepts caller-supplied /proc roots or root bypasses.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/read_chrony_resources.sh"


class ClockResourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="clock-resource-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / "proc"
        self.sys = self.root / "sys"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.shim("id", "printf '0\\n'\n")
        self.script = self.root / "reader.sh"
        self.script.write_text(SOURCE.read_text().replace(
            "task_proc_root=/proc", f"task_proc_root='{self.proc}'").replace(
            "task_sys_root=/sys", f"task_sys_root='{self.sys}'"))
        self.write(self.proc / "sys/kernel/random/boot_id", "fixture-boot\n")
        self.link(self.proc / "self/ns/pid", "pid:[1]")
        self.process()

    def shim(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o700)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def link(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def process(self, pid=213):
        p = self.proc / str(pid)
        self.write(p / "comm", "chronyd\n")
        # Rest fields 3..21 (19 fields), then starttime, then trailing fields.
        self.write(p / "stat", f"{pid} (chronyd test) " + " ".join(["S"] + ["0"] * 18 + ["319", "0"]) + "\n")
        self.write(p / "status", f"PPid:\t1\nNSpid:\t{pid}\n")
        self.write(p / "binary", "fixture-executable\n")
        self.link(p / "exe", str(p / "binary"))
        self.write(p / "cmdline", "/sbin/chronyd\0")
        self.write(p / "root/etc/chrony.conf", "refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0\n")
        self.link(p / "root/dev/ptp0", "/dev/null")
        self.link(p / "fd/3", "/dev/null")
        self.link(p / "fd/4", "socket:[3582]")
        self.link(p / "fd/5", "/private/unrelated-secret-file")
        for n in ("pid", "mnt", "time", "net"):
            self.link(p / "ns" / n, f"{n}:[1]")
        self.write(p / "net/udp", "  sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
                   " 1: 0100007F:0143 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 3582 2 pointer 0\n"
                   " 2: 0100007F:9999 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 9999 2 pointer 0\n")
        self.write(p / "net/udp6", "header\n")
        return p

    def run_reader(self):
        return subprocess.run(["/bin/sh", str(self.script)], capture_output=True, text=True,
                              env={"PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin"}, timeout=10)

    def test_phc_device_and_exact_process_socket_inode_are_matched(self):
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("START_TICKS=319\n", result.stdout)
        identity = (self.proc / "213/exe").stat()
        self.assertIn(f"EXE_FILE_ID={identity.st_dev}:{identity.st_ino}:{identity.st_size}\n", result.stdout)
        self.assertIn("PHC_FD=3 RDEV=1:3\n", result.stdout)
        self.assertIn("UDP_FD=4 TABLE=udp LOCAL_REMOTE_INODE=0100007F:0143 00000000:0000 3582", result.stdout)
        self.assertNotIn("9999", result.stdout)
        self.assertNotIn("unrelated-secret-file", result.stdout)
        self.assertIn("READ_COMPLETE_NEEDS_TRACE_CORRELATION", result.stdout)

    def test_root_is_required(self):
        self.shim("id", "printf '1000\\n'\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 77)
        self.assertNotIn("PROCESS_BEGIN", result.stdout)

    def test_missing_process_is_incomplete_not_success(self):
        (self.proc / "213/comm").write_text("other\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("candidates=0", result.stdout)

    def test_multiple_chronyds_require_review(self):
        self.process(214)
        self.assertEqual(self.run_reader().returncode, 42)

    def test_missing_phc_descriptor_cannot_succeed(self):
        (self.proc / "213/fd/3").unlink()
        self.assertEqual(self.run_reader().returncode, 42)

    def test_another_inode_in_the_reader_network_does_not_count(self):
        # The reader's table has the inode, the candidate's table does not.
        self.write(self.proc / "net/udp", (self.proc / "213/net/udp").read_text())
        self.write(self.proc / "213/net/udp", "header\n")
        self.assertEqual(self.run_reader().returncode, 42)

    def test_ipv6_process_socket_is_matched(self):
        table = (self.proc / "213/net/udp").read_text().replace("0100007F:0143", "00000000000000000000000001000000:0143")
        self.write(self.proc / "213/net/udp", "header\n")
        self.write(self.proc / "213/net/udp6", table)
        result = self.run_reader()
        self.assertEqual(result.returncode, 0)
        self.assertIn("TABLE=udp6", result.stdout)

    def test_matching_inode_on_an_unrelated_port_does_not_count(self):
        table = (self.proc / "213/net/udp").read_text().replace("0100007F:0143", "0100007F:9999")
        self.write(self.proc / "213/net/udp", table)
        self.assertEqual(self.run_reader().returncode, 42)

    def test_include_graph_is_not_followed_or_marked_complete(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "include /private/secret\nrefclock PHC /dev/ptp0\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("INCLUDE_GRAPH_REQUIRES_REVIEW", result.stdout)
        self.assertNotIn("/private/secret", result.stdout)

    def test_alternate_config_argument_is_not_followed(self):
        self.write(self.proc / "213/cmdline", "/sbin/chronyd\0-f/private/secret\0")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("UNSUPPORTED_ARGV_REQUIRES_REVIEW", result.stdout)
        self.assertNotIn("/private/secret", result.stdout)

    def test_uppercase_include_directive_also_requires_review(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "INCLUDE /private/secret\nrefclock PHC /dev/ptp0\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("INCLUDE_GRAPH_REQUIRES_REVIEW", result.stdout)

    def test_changed_configuration_hash_is_detected(self):
        self.shim("sha256sum", f'''if [ "$1" = '{self.proc}/213/root/etc/chrony.conf' ]; then
 if [ -f '{self.root}/seen-config' ]; then printf 'changed  %s\\n' "$1"; exit 0; fi
 : > '{self.root}/seen-config'
fi
exec /usr/bin/sha256sum "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)

    def test_unknown_phc_configuration_is_incomplete(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "refclock PHC /dev/ptp1\n")
        self.assertEqual(self.run_reader().returncode, 42)

    def test_changed_descriptor_is_detected(self):
        self.shim("readlink", f'''if [ "$1" = '{self.proc}/213/fd/4' ]; then
 if [ -f '{self.root}/seen-fd' ]; then printf 'socket:[9999]\\n'; exit 0; fi
 : > '{self.root}/seen-fd'
fi
exec /usr/bin/readlink "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("FD_CHANGED_DURING_READ", result.stdout)

    def test_changed_executable_identity_is_detected(self):
        self.shim("sha256sum", f'''if [ "$1" = '{self.proc}/213/exe' ]; then
 if [ -f '{self.root}/seen-exe' ]; then printf 'changed  %s\\n' "$1"; exit 0; fi
 : > '{self.root}/seen-exe'
fi
exec /usr/bin/sha256sum "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)

    def test_descriptor_bound_is_enforced(self):
        for n in range(6, 262):
            self.link(self.proc / f"213/fd/{n}", "socket:[123]")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("FD_BOUND_EXCEEDED", result.stdout)

    def test_same_content_executable_replacement_is_incomplete(self):
        self.shim("stat", f'''if [ "$3" = '{self.proc}/213/exe' ]; then
 if [ -f '{self.root}/seen-file-id' ]; then printf '1:999999:19\\n'; exit 0; fi
 : > '{self.root}/seen-file-id'
fi
exec /usr/bin/stat "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)


if __name__ == "__main__":
    unittest.main()
