#!/usr/bin/env python3
"""Run three checked demos using only private temporary databases and stdlib."""

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile

MODULE = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)


def invoke(args, expected=0, data=None, timeout=15, cwd=None):
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout, cwd=cwd)
    require(result.returncode == expected,
            f"unexpected exit {result.returncode}, expected {expected}: "
            f"{args!r}; stderr={result.stderr.decode(errors='replace')}")
    return result


def cli(kv, db, op, expected=0, key=None, data=None):
    args = [str(kv), op, "--db", str(db)]
    if key is not None:
        args += ["--key", key]
    if data is not None:
        args += ["--value-file", "-"]
    result = invoke(args, expected, data)
    emit("cli", scenario=db.name, operation=op, key=key, exit=result.returncode,
         stdout_base64=base64.b64encode(result.stdout).decode(),
         stderr=result.stderr.decode(errors="replace"))
    return result


def fixtures():
    parts = (MODULE / "tests/fixtures.md").read_text(encoding="utf-8").split("```text\n")[1:]
    require(len(parts) == 3, "expected three public fixtures")
    result = [bytes.fromhex(part.split("```", 1)[0]) for part in parts]
    require([len(b) for b in result] == [91, 64, 91], "fixture lengths changed")
    return result


def stopped_writer(point):
    # Run the existing real-file rendezvous/SIGKILL harness, not a shipped fault flag.
    name = "TestCrashRecoveryCheckpoints/put/" + point
    result = invoke([str(MODULE / "scripts/go-env.sh"), "go", "test", "-json",
                     "-count=1", "-timeout=45s", "-run",
                     "^TestCrashRecoveryCheckpoints$/^put$/^" + point + "$",
                     "./internal/engine"], timeout=90, cwd=MODULE)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    require(any(e.get("Action") == "pass" and e.get("Test") == name for e in events),
            "expected SIGKILL case did not run/pass")
    evidence = [e["Output"].strip() for e in events
                if e.get("Test") == name and "acknowledged=" in e.get("Output", "")]
    require(len(evidence) == 1, "missing recovery evidence")
    emit("sigkill", test=name, evidence=evidence[0],
         model="parent holds ack history; real FD; OS page cache survives")


def normal(kv, root):
    db = root / "normal"
    db.mkdir()
    for seq, key, value in [(1, "k", b"old"), (2, "empty", b""),
                            (3, "k", b"\x00\xff\n")]:
        result = cli(kv, db, "put", key=key, data=value)
        require(json.loads(result.stdout) == {"sequence": seq}, "bad success sequence")
    require(cli(kv, db, "get", key="k").stdout == b"\x00\xff\n", "binary value lost")
    require(cli(kv, db, "get", key="empty").stdout == b"", "empty value lost")
    require(json.loads(cli(kv, db, "delete", key="k").stdout) == {"sequence": 4},
            "bad delete sequence")
    require(cli(kv, db, "get", expected=3, key="k").stdout == b"", "missing key returned data")
    report = json.loads(cli(kv, db, "inspect").stdout)
    require(report["status"] == "ok" and report["latest_sequence"] == 4
            and report["record_count"] == 4 and report["live_keys"] == 1, "bad normal report")
    stopped_writer("after_ack")
    emit("scenario_pass", scenario="normal", proof="separate CLI processes plus SIGKILL after ack")


def tail(kv, root, data):
    stopped_writer("partial_body")
    db = root / "tail"
    db.mkdir()
    log = db / "data.log"
    log.write_bytes(data)
    emit("fixture", scenario="tail", origin="public incomplete fixture copy", bytes=len(data))
    report = json.loads(cli(kv, db, "inspect", expected=5).stdout)
    require(report["status"] == "incomplete_tail" and report["valid_bytes"] == 54
            and report["invalid_offset"] == 54, "bad tail report")
    require(log.read_bytes() == data, "inspect changed incomplete log")
    result = cli(kv, db, "get", key="k")
    require(result.stdout == b"v", "lost complete PUT")
    diagnostic = json.loads(result.stderr)
    require(diagnostic["event"] == "tail_truncate_planned"
            and diagnostic["old_size"] == 64 and diagnostic["last_valid_offset"] == 54,
            "bad recovery diagnostic")
    require(log.read_bytes() == data[:54], "wrong repair boundary")
    again = cli(kv, db, "get", key="k")
    require(again.stdout == b"v" and again.stderr == b"", "recovery was not idempotent")
    report = json.loads(cli(kv, db, "inspect").stdout)
    require(report["status"] == "ok" and report["latest_sequence"] == 1
            and report["log_bytes"] == 54, "bad repaired report")
    emit("scenario_pass", scenario="tail", old_bytes=64, valid_bytes=54,
         proof="actual partial-write SIGKILL and separate public-fixture CLI demonstration")


def corrupt(kv, root, data):
    db = root / "corrupt"
    db.mkdir()
    log = db / "data.log"
    log.write_bytes(data)
    emit("fixture", scenario="corrupt", origin="public CRC corruption fixture copy", bytes=len(data))
    report = json.loads(cli(kv, db, "inspect", expected=5).stdout)
    require(report["status"] == "corrupt" and report["invalid_offset"] == 16
            and report["valid_bytes"] == 16 and report["record_count"] == 0,
            "bad corruption report")
    for _ in range(2):
        require(cli(kv, db, "get", expected=5, key="k").stdout == b"", "corrupt get succeeded")
        require(log.read_bytes() == data, "Open changed corruption or adopted following DELETE")
    emit("scenario_pass", scenario="corrupt", unchanged_bytes=91, invalid_offset=16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv", type=Path, default=MODULE / "bin/kv", help="built product CLI")
    args = parser.parse_args()
    kv = args.kv.resolve()
    require(kv.is_file(), "build the product CLI first: make build")
    data = fixtures()
    with tempfile.TemporaryDirectory(prefix="kv-demo-") as path:
        root = Path(path)
        normal(kv, root)
        tail(kv, root, data[1])
        corrupt(kv, root, data[2])
    emit("demo_pass", scenarios=3, cleanup=True, power_loss_verified=False)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps(dict(event="demo_failed", reason=str(exc)), ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
