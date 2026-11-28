#!/usr/bin/env python3
"""Run P5-01 or P5-02 with built CLI processes and the fixed fixture."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import platform
import re
import secrets
import selectors
import shlex
import socket
import sqlite3
import statistics
import subprocess
import tempfile
from threading import Thread
from time import perf_counter_ns

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((PROJECT / "fixtures/expected.json").read_text())
TOOLCHAIN = {"GOTOOLCHAIN": "local", "GOWORK": "off", "CGO_ENABLED": "1"}
STAGES = Path(".file-sync/stage")
SQLITE = Path(".file-sync/state/state.sqlite")
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length"}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def run(args, *, env=None, expected=0):
    result = subprocess.run(args, text=True, capture_output=True, timeout=240,
                            env=env)
    # Commands have credentials only in the child environment.
    check(result.returncode == expected,
          f"{args[0]} {args[1]}: exit {result.returncode}, wanted {expected}: {result.stderr.strip()}")
    return result


def build(go, directory, product_sha):
    def git(*args):
        return run(["git", "-C", str(PROJECT), *args]).stdout.strip()

    sha = git("rev-parse", "--verify", (product_sha or "HEAD") + "^{commit}")
    product = ["cmd", "internal", "go.mod", "go.sum", "fixtures"]
    check(not git("diff", "--name-only", sha, "--", *product)
          and not git("ls-files", "--others", "--exclude-standard", "--", *product),
          "product files or fixed fixture differ from the recorded SHA")
    env = dict(os.environ, **TOOLCHAIN)
    go_version = run([go, "env", "GOVERSION"], env=env).stdout.strip()
    check(go_version == "go1.27.4", "Go 1.27.4 is required")
    bin_path = directory / "file-sync"
    subprocess.run([go, "mod", "verify"], cwd=PROJECT, env=env, check=True, timeout=240)
    subprocess.run([go, "build", "-mod=readonly", "-buildvcs=false", "-o", bin_path,
                    "./cmd/file-sync"], cwd=PROJECT, env=env, check=True, timeout=240)
    # Inspect the C library linked from the same module with identical build flags.
    probe = directory / "sqlite-version.go"
    probe.write_text('package main\nimport ("fmt"; "github.com/mattn/go-sqlite3")\n'
                     'func main() { v, _, _ := sqlite3.Version(); fmt.Println(v) }\n')
    sqlite_version = subprocess.check_output(
        [go, "run", "-mod=readonly", "-buildvcs=false", probe],
        cwd=PROJECT, env=env, text=True, timeout=240).strip()
    check(sqlite_version == "3.53.4", "bundled SQLite 3.53.4 is required")
    commit = {"product_sha": sha, "go": go_version, "sqlite": sqlite_version}
    return bin_path, commit


def fixture(content):
    item = FIXTURE["contents"][content]
    data = bytes.fromhex(item["unit_hex"]) * item["repeat"]
    check(len(data) == item["size"] and hashlib.sha256(data).hexdigest() == item["sha256"],
          f"bad fixture {content}")
    return data


def verify_file(path, content, *, record_body=True):
    if content is None:
        check(not os.path.lexists(path), f"{path.name}: must be absent")
        return {"exists": False}
    data = path.read_bytes()
    item = FIXTURE["contents"][content]
    actual = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    check(data == fixture(content) and actual == {k: item[k] for k in actual},
          f"{path.name}: content {content} differs")
    if record_body:
        actual["body"] = data.decode("ascii")
    return {"exists": True, **actual}


def blob_path(root, content):
    return root / "hub/blobs" / ("demo." + FIXTURE["contents"][content]["sha256"])


def verify_version(entry, rev, content, hub=False):
    expected = {"revision": rev, "content_hash": None if hub else "", "size": 0}
    if content:
        item = FIXTURE["contents"][content]
        expected.update(content_hash=item["sha256"], size=item["size"])
    if hub:
        expected["deleted"] = content is None
    check({k: entry[k] for k in expected} == expected, f"wrong version: {entry}")


class CLI:
    def __init__(self, binary, root):
        self.binary, self.root = str(binary), root
        self.tokens = {client: secrets.token_urlsafe(32) for client in ("A", "B")}

    def init(self):
        for name in ("hub", "A", "B"):
            (self.root / name).mkdir()
        self.call("hub-init", "hub", "-space", "demo", "-client-a", "client-a",
                  "-client-b", "client-b", provision=True)
        for name in ("A", "B"):
            self.call("init", name)

    def args(self, command, root, *options):
        return [self.binary, command, "-volume", str(self.root), "-root", root, *options]

    def call(self, command, root, *options, expected=0, provision=False):
        env = {k: v for k, v in os.environ.items() if not k.startswith("FILE_SYNC_TOKEN")}
        if root in self.tokens:
            env["FILE_SYNC_TOKEN"] = self.tokens[root]
        if provision:
            for client, token in self.tokens.items():
                env[f"FILE_SYNC_TOKEN_{client}"] = token
        return run(self.args(command, root, *options), env=env, expected=expected)

    @contextmanager
    def hub(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            address = f"127.0.0.1:{probe.getsockname()[1]}"
        args = self.args("hub", "hub", "-listen", address)
        with subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as hub:
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(hub.stdout, selectors.EVENT_READ)
                    check(ready.select(10), "hub did not report readiness")
                    line = hub.stdout.readline().strip()
                    check(line == f"listening {address}", "hub failed to start; choose an unused port")
                yield address, line
            finally:
                if hub.poll() is None:
                    hub.terminate()
                try:
                    _, error = hub.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    hub.kill()
                    hub.communicate()
                    raise
                check(hub.returncode == 0, f"hub exit {hub.returncode}: {error.strip()}")

    def network(self, command, client, address, *options, expected=0):
        return self.call(command, client, "-hub", address, *options, expected=expected)

    def status(self, client):
        return json.loads(self.call("status", client).stdout)

    def snapshot(self):
        db = self.root / "hub" / SQLITE
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            entries = {}
            for path, rev, hash_, size, deleted, client, op in conn.execute(
                    "SELECT path, revision, hash, size, deleted, client, op FROM entries ORDER BY path"):
                entries[path] = dict(path=path, revision=rev, content_hash=hash_, size=size,
                                     deleted=bool(deleted), client_id=client, op_id=op)
            history = []
            for client, op, generation, request, response in conn.execute(
                    "SELECT client, op, generation, request, response FROM receipts ORDER BY rowid"):
                change, receipt = json.loads(request), json.loads(response)
                check(change["op_id"] == op == receipt["op_id"]
                      and change["client_id"] == client == receipt["entry"]["client_id"]
                      and change["hub_generation"] == generation == receipt["hub_generation"]
                      and change["path"] == receipt["entry"]["path"], "receipt identity mismatch")
                history.append(dict(client_id=client, op_id=op, generation=generation,
                                    base_revision=change["base_revision"], result=receipt["result"],
                                    entry=receipt["entry"]))
            blobs = dict(conn.execute("SELECT hash, size FROM blobs"))
        return entries, history, blobs


def verify_hub(snapshot, versions, expected_history):
    entries, history, _ = snapshot
    check(set(entries) == set(versions), "unexpected hub paths")
    check(len(history) == len(expected_history), "unexpected receipt count")
    check(len({row["generation"] for row in history}) == 1, "unexpected generation change")
    check(len({(row["client_id"], row["op_id"]) for row in history}) == len(history),
          "unexpected operation ID reuse")
    for path, (rev, content) in versions.items():
        verify_version(entries[path], rev, content, hub=True)
    received, wanted = defaultdict(list), defaultdict(list)
    for row in history:
        received[row["entry"]["path"]].append(row)
    for path, *entry in expected_history:
        wanted[path].append(entry)
    check(received.keys() == wanted.keys(), "unexpected receipt paths")
    latest = {}
    for path, operations in wanted.items():
        check(len(received[path]) == len(operations), f"{path}: unexpected receipt count")
        for row, (client, base, rev, content, result) in zip(received[path], operations, strict=True):
            check(row["client_id"] == client and row["base_revision"] == base and row["result"] == result,
                  f"{path}: unexpected receipt")
            verify_version(row["entry"], rev, content, hub=True)
            if result == "applied":
                latest[path] = row["entry"]
    check(entries == latest, "hub entries and latest applied receipts differ")


def baseline(status, path, rev, content, *, conflict_pending=False):
    verify_version(status["baseline"][path], rev, content)
    allowed = {path} if conflict_pending else set()
    check(not status.get("outbox") and set(status.get("holds", {})) <= allowed,
          f"unexpected outbox or hold: {status}")


def stored_copy(root, client, candidate, content):
    check(candidate["saved"] and candidate["stage_file"], "conflict copy is unsaved")
    return verify_file(root / client / STAGES / candidate["stage_file"], content)


def demo(binary, directory):
    frames = {}

    def display(cli, key, command, client, *options, expected=0):
        result = cli.network(command, client, address, *options, expected=expected)
        safe = re.sub(r"127\.0\.0\.1:[0-9]+", '$address', shlex.join(options))
        safe = re.sub(r"(?<=--generation )[0-9a-f]{32}", '$generation', safe)
        line = f"file-sync {command} -volume \"$demo\" -root {client} -hub \"$address\""
        if safe:
            line += " " + safe
        frames[key] = {"command": line, "exit": result.returncode, "stderr": result.stderr.strip()}
        return result

    path, empty = "notes.txt", "empty.txt"
    fixture_cases = FIXTURE["cases"]
    initial = fixture_cases["P2-01"]
    conflict = fixture_cases["P3-02"]
    deletion = fixture_cases["P3-06"]
    first = (path, "client-a", 0, 1, "P", "applied")
    with tempfile.TemporaryDirectory(prefix="demo-", dir=directory) as volume:
        cli = CLI(binary, Path(volume))
        cli.init()
        for name, content in FIXTURE["demo"]["client-a"].items():
            (cli.root / "A" / name).write_bytes(fixture(content))

        with cli.hub() as (address, listening):
            frames["startup"] = {"stdout": listening}
            verify_file(cli.root / "B" / empty, None)
            check(not cli.snapshot()[0], "fresh hub must have no registered paths")
            display(cli, "seed", "register", "A", "--seed")
            display(cli, "pull", "register", "B", "--pull")
            snapshot = cli.snapshot()
            es = (empty, "client-a", 0, 1, "E", "applied")
            verify_hub(snapshot, {path: (initial["hub_revision"], initial["hub_content"]), empty: (1, "E")},
                       [es, first])
            frames["initial"] = {"history": snapshot[1], "inspection": {}}
            for name, content in FIXTURE["demo"]["client-a"].items():
                for client, who in (("A", "a"), ("B", "b")):
                    status = cli.status(client)
                    rev = initial[f"{who}_base_revision"] if name == path else 1
                    baseline(status, name, rev, content)
                    frames["initial"]["inspection"][f"{client}/{name}"] = dict(
                        baseline=status["baseline"][name], **verify_file(cli.root / client / name, content))
                verify_file(blob_path(cli.root, content), content)
            verify_file(cli.root / "B/never-created.txt", None)
            check("never-created.txt" not in snapshot[0], "unexpected absent path row")

            (cli.root / "A" / path).write_bytes(fixture(conflict["a_content"]))
            (cli.root / "B" / path).write_bytes(fixture(conflict["b_content"]))
            display(cli, "sync_A", "sync-once", "A")
            display(cli, "hold_B", "sync-once", "B", expected=3)
            snapshot = cli.snapshot()
            second = (path, "client-a", 1, conflict["hub_revision"], conflict["hub_content"], "applied")
            verify_hub(snapshot, {path: (2, "Q"), empty: (1, "E")}, [es, first, second])
            state = cli.status("B")
            baseline(cli.status("A"), path, conflict["a_base_revision"], "Q")
            baseline(state, path, conflict["b_base_revision"], "P", conflict_pending=True)
            listing = json.loads(display(cli, "list_B", "conflicts", "B").stdout)
            saved = listing
            check(len(saved) == 1 and state["conflicts"] == saved, "expected the same single conflict")
            item = saved[0]
            check(item["state"] == "pending" and item["saved"], "expected a pending saved conflict")
            verify_version(item["baseline"], 1, "P")
            hub_candidate = item["hub_candidates"][-1]
            verify_version(hub_candidate, 2, "Q")
            local_candidate = item["detected_local"]
            conflict_data = {"status": item, "history": snapshot[1], "hub": snapshot[0][path],
                             "a_file": verify_file(cli.root / "A" / path, "Q"),
                             "b_file": verify_file(cli.root / "B" / path, "R"),
                             "q_copy": stored_copy(cli.root, "B", hub_candidate, "Q"),
                             "r_copy": stored_copy(cli.root, "B", local_candidate, "R")}
            frames["conflict"] = conflict_data
            for content in ("P", "Q"):
                verify_file(blob_path(cli.root, content), content)
            display(cli, "resolve_B", "resolve", "B", "-path", path, "--generation",
                    item["hub_generation"], "--revision", str(hub_candidate["revision"]), "--choose", "local")
            display(cli, "receive_A", "sync-once", "A")
            snapshot = cli.snapshot()
            verify_hub(snapshot, {path: (3, "R"), empty: (1, "E")},
                       [es, first, second, (path, "client-b", 2, 3, "R", "applied")])
            resolution = cli.status("B")["conflicts"][0]
            check(resolution["state"] == "resolved" and resolution["resolution"]["state"] == "complete",
                  "manual resolution did not complete")
            frames["resolution"] = {"state": resolution["state"], "receipt": snapshot[1][-1],
                                    "a_file": verify_file(cli.root / "A" / path, "R"),
                                    "b_file": verify_file(cli.root / "B" / path, "R"),
                                    "q_copy": stored_copy(cli.root, "B", hub_candidate, "Q"),
                                    "r_copy": stored_copy(cli.root, "B", local_candidate, "R")}
            for client in ("A", "B"):
                baseline(cli.status(client), path, 3, "R")
                frames["resolution"][f"{client}_base"] = cli.status(client)["baseline"][path]
            for content in ("P", "Q", "R", "E"):
                verify_file(blob_path(cli.root, content), content)

    with tempfile.TemporaryDirectory(prefix="delete-", dir=directory) as volume:
        cli = CLI(binary, Path(volume))
        cli.init()
        (cli.root / "A" / path).write_bytes(fixture("P"))
        with cli.hub() as (address, _):
            display(cli, "delete_seed", "register", "A", "--seed")
            display(cli, "delete_pull", "register", "B", "--pull")
            verify_hub(cli.snapshot(), {path: (1, "P")}, [first])
            for client in ("A", "B"):
                baseline(cli.status(client), path, 1, "P")
                verify_file(cli.root / client / path, "P")
            (cli.root / "A" / path).unlink()
            display(cli, "send_delete", "sync-once", "A")
            second = (path, "client-a", 1, deletion["hub_revision"], deletion["hub_content"], "applied")
            versions = {path: (deletion["hub_revision"], deletion["hub_content"])}
            before = cli.snapshot()
            verify_hub(before, versions, [first, second])
            offline = cli.status("B")
            baseline(offline, path, 1, "P")
            frames["offline"] = {"hub": before[0][path], "b_baseline": offline["baseline"][path],
                                 "b_file": verify_file(cli.root / "B" / path, "P")}
            display(cli, "receive_delete", "sync-once", "B")
            receiver = cli.status("B")
            plans = [p for p in receiver["plans"] if p["path"] == path and p["target"]["revision"] == 2]
            check(len(plans) == 1 and plans[0]["status"] == "complete" and plans[0]["backed_up"],
                  "missing completed deletion plan")
            trash = STAGES / (plans[0]["id"] + ".trash")
            frames["deleted"] = {"plan": plans[0], "b_trash": verify_file(cli.root / "B" / trash, "P"),
                                 "hub_blob": verify_file(blob_path(cli.root, "P"), "P"),
                                 "history": before[1]}
            for client, who in (("A", "a"), ("B", "b")):
                status = cli.status(client)
                baseline(status, path, deletion[f"{who}_base_revision"], None)
                check(not status.get("conflicts"), "unexpected conflict during delete")
                frames["deleted"][f"{client}_base"] = status["baseline"][path]
                frames["deleted"][f"{client}_file"] = verify_file(cli.root / client / path, None)
                display(cli, f"recheck_{client}", "sync-once", client)
                verify_file(cli.root / client / path, None)
            check(cli.snapshot() == before, "no-op rerun changed tombstone, receipts or blobs")
            verify_file(cli.root / "B" / trash, "P")
    return {"result": "pass", "frames": frames}


def http_group(method, path):
    if path.startswith("/v1/blobs/") and method in ("PUT", "GET"):
        return "blob_" + method.lower()
    if (method, path) == ("GET", "/v1/manifest"):
        return "manifest"
    if (method, path) == ("POST", "/v1/changes"):
        return "changes"
    raise ValueError(f"unexpected measurement request: {method} {path}")


@contextmanager
def http_meter(target):
    counts = {name: Counter(requests=0, request_body_bytes=0, response_body_bytes=0)
              for name in ("blob_put", "blob_get", "manifest", "changes")}
    errors = []

    class Meter(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def forward(self):
            try:
                self.close_connection = True
                group = http_group(self.command, self.path)
                check(not self.headers.get("Transfer-Encoding"), "unexpected chunked request")
                n = int(self.headers.get("Content-Length", 0))
                check(0 <= n <= 10 * 1024 * 1024, "request body too large")
                payload = self.rfile.read(n)
                check(len(payload) == n, "short request")
                useful = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                if "Content-Length" in self.headers:
                    useful["Content-Length"] = str(n)
                with closing(HTTPConnection(target, timeout=60)) as hub:
                    hub.request(self.command, self.path, payload if "Content-Length" in useful else None,
                                useful)
                    resp = hub.getresponse()
                    data = resp.read()
                    check(resp.status in (200, 201), f"unexpected hub status {resp.status}")
                    self.send_response_only(resp.status)
                    for key, value in resp.getheaders():
                        if key.lower() not in HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    counts[group].update(requests=1, request_body_bytes=len(payload),
                                         response_body_bytes=len(data))
                    self.wfile.write(data)
            except (OSError, ValueError) as err:
                errors.append(str(err))
                raise

        do_GET = do_POST = do_PUT = forward

    with HTTPServer(("127.0.0.1", 0), Meter) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"127.0.0.1:{server.server_port}", counts, errors
        finally:
            server.shutdown()
            thread.join()


def file_size(path):
    return path.stat().st_size if path.exists() else 0


def timed_ns(start):
    return perf_counter_ns() - start


def sample(binary, directory, count, content):
    # A brand-new hub and two unregistered clients for every sample.
    with tempfile.TemporaryDirectory(prefix="measure-", dir=directory) as volume:
        root = Path(volume)
        cli = CLI(binary, root)
        cli.init()
        data = fixture(content)
        expected = {f"sample-{i:04}.bin": (1, content) for i in range(count)}
        for path in expected:
            (root / "A" / path).write_bytes(data)
        with cli.hub() as (direct, _), http_meter(direct) as (meter, http, errors):
            start = perf_counter_ns()
            scanned = cli.call("scan", "A").stdout
            scan_a = timed_ns(start)
            start = perf_counter_ns()
            cli.network("register", "A", meter, "--seed")
            seed_end = perf_counter_ns()
            cli.network("register", "B", meter, "--pull")
            end = perf_counter_ns()
            db = root / "hub" / SQLITE
            blobs = list((root / "hub/blobs").iterdir())
            storage = dict(db_bytes=file_size(db), wal_bytes=file_size(db.with_name(db.name + "-wal")),
                           shm_bytes=file_size(db.with_name(db.name + "-shm")),
                           blob_files=len(blobs), blob_bytes=sum(p.stat().st_size for p in blobs))
            check(not errors, "HTTP meter failed")
            check(http["blob_put"]["requests"] > 0 and http["blob_get"]["requests"] > 0,
                  "expected actual HTTP blob upload and download")
        # No request, disk or verification work below is included in the intervals.
        hash_ = FIXTURE["contents"][content]["sha256"]
        check(json.loads(scanned) == [
            {"path": path, "size": len(data), "content_hash": hash_} for path in expected],
            "scan output differs from fixed input")
        snapshot = cli.snapshot()
        verify_hub(snapshot, expected, [
            (path, "client-a", 0, 1, content, "applied") for path in expected])
        check(snapshot[2] == {hash_: len(data)} and storage["blob_files"] == 1
              and storage["blob_bytes"] == len(data), "unexpected shared hub blob")
        verify_file(blob_path(root, content), content, record_body=False)
        for client in ("A", "B"):
            status = cli.status(client)
            check(set(status["baseline"]) == set(expected) and not status.get("conflicts"),
                  "unexpected baseline or conflict")
            check({p.name for p in (root / client).iterdir() if p.name != ".file-sync"} == set(expected),
                  "unexpected user files")
            for path in expected:
                baseline(status, path, 1, content)
                verify_file(root / client / path, content, record_body=False)
        return {"scan_a_ns": scan_a, "seed_a_ns": seed_end - start, "pull_b_ns": end - seed_end,
                "convergence_ns": end - start, "http": http, "storage": storage}


def measure(binary, directory, trials):
    host = dict(tuple(field.strip() for field in line.split(":", 1))
                for line in Path("/proc/cpuinfo").read_text().splitlines() if ":" in line)
    memory = next(line for line in Path("/proc/meminfo").read_text().splitlines()
                  if line.startswith("MemTotal:"))
    settings = {
        "os": platform.freedesktop_os_release()["PRETTY_NAME"],
        "kernel": platform.release(), "arch": platform.machine(),
        "cpu": host.get("model name") or host.get("Hardware") or "unknown", "logical_cpus": os.cpu_count(),
        "memory_bytes": int(memory.split()[1]) * 1024,
        "filesystem": run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(directory)]).stdout.strip(),
        "python": platform.python_version(), "transport": "IPv4 localhost HTTP via local Python meter",
        "clock": "time.perf_counter_ns", "warmup_trials": 0, "trials_per_case": trials,
        "scan_scope": "A standalone scan process including JSON stdout",
        "storage_scope": "logical lengths after B exits; hub still running; before SQLite observer",
    }
    groups = []
    for count, content in ((100, "E"), (1000, "E"), (1, "M")):
        samples = []
        for i in range(trials):
            samples.append(sample(binary, directory, count, content))
            print(f"{count} × {content}: {i + 1}/{trials} verified", flush=True)
        metrics = {}
        for key in ("scan_a_ns", "seed_a_ns", "pull_b_ns", "convergence_ns"):
            values = [trial[key] for trial in samples]
            metrics[key] = dict(min=min(values), median=statistics.median(values), max=max(values))
        groups.append(dict(files=count, content=content, file_bytes=FIXTURE["contents"][content]["size"],
                           statistics=metrics, samples=samples))
    return {"result": "pass", "environment": settings, "cases": groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("demo", "measure"))
    parser.add_argument("--go", default="go")
    parser.add_argument("--product-sha", help="check cmd, internal, modules and fixture against this commit")
    parser.add_argument("--trials", type=int, default=5, help="independent trials per measurement case")
    parser.add_argument("--output", type=Path, required=True, help="new JSON file for verified results")
    args = parser.parse_args()
    if args.trials < 1 or args.output.exists():
        parser.error("positive trials and a new output file are required")
    with tempfile.TemporaryDirectory(prefix="file-sync-p5-") as temp:
        directory = Path(temp).resolve()
        binary, version = build(args.go, directory, args.product_sha)
        result = demo(binary, directory) if args.mode == "demo" else measure(binary, directory, args.trials)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump({**version, **result}, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(f"{args.mode}: PASS ({args.output})")


if __name__ == "__main__":
    main()
