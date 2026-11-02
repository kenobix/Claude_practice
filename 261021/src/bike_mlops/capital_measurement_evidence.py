"""Archive and independently recalculate synthetic Capital measurement evidence.

The verifier consumes only the ZIP path. It does not use Docker, PostgreSQL,
MLflow, environment credentials, or the harness workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from bike_mlops.capital_measurement_recheck import recheck_measurement_records
from bike_mlops.capital_operational_ledger import read_operational_ledger
from bike_mlops.capital_request_journal import JOURNAL_POLICY, inspect_request_journal

MEMBERS = {
    "payload/plan.json": "plan",
    "payload/report.json": "report",
    "payload/operational-ledger.json": "operational_ledger",
    "payload/request-journal.jsonl": "request_journal",
}
MANIFEST = "manifest.json"
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
SENSITIVE_KEY = re.compile(r"(?:password|passwd|secret|token|credential|dsn|api[_-]?key|private[_-]?key|authorization|access[_-]?key|raw[_-]?env|command[_-]?line|argv)", re.I)
ABSOLUTE_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains a duplicate JSON key")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _assert_no_sensitive_or_external(value, *, key="") -> None:
    if SENSITIVE_KEY.search(key):
        raise ValueError(f"Evidence contains a sensitive field name: {key}")
    if key.lower() in {"environment", "env"}:
        raise ValueError("Raw environment data is not eligible for the evidence archive")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _assert_no_sensitive_or_external(child, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_or_external(child, key=key)
    elif isinstance(value, str):
        docker_inventory_row = key == "containers" and re.fullmatch(
            r"/bikemlopstest[0-9a-f]{16}-[a-z0-9-]+-\d+\|[a-zA-Z0-9_.:/-]+\|\d+\|\d+", value)
        if (ABSOLUTE_PATH.match(value) and not docker_inventory_row) or "@" in value.split("://", 1)[-1].split("/", 1)[0]:
            raise ValueError("Evidence contains an absolute path or URI credentials")
        if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
            parsed = urlsplit(value)
            if parsed.scheme and (parsed.username or parsed.password):
                raise ValueError("Evidence contains URI credentials")
            if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
                raise ValueError("Evidence contains a non-local URI")


def _safe_relative(value) -> bool:
    return (isinstance(value, str) and bool(value) and not ABSOLUTE_PATH.match(value)
            and not re.match(r"^[A-Za-z]:", value) and "\\" not in value
            and ".." not in Path(value).parts and Path(value).as_posix() == value and value != ".")


def _no_symlinks(path: Path) -> None:
    if ".." in path.parts:
        raise ValueError("Evidence path contains traversal")
    if any(part.is_symlink() for part in (path.absolute(), *path.absolute().parents)):
        raise ValueError("Evidence path contains a symlink")


def _read_regular(path: Path, *, max_bytes: int = MAX_MEMBER_BYTES) -> bytes:
    _no_symlinks(path)
    absolute = path.absolute()
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in absolute.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(absolute.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError("Evidence input must be a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        sig = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > max_bytes or sig(before) != sig(after) or sig(os.stat(path, follow_symlinks=False)) != sig(after):
            raise ValueError("Evidence input changed while being read or exceeded its limit")
        return raw
    finally:
        if fd >= 0:
            os.close(fd)


def _validate_archive(archive: Path) -> dict:
    archive_raw = _read_regular(archive, max_bytes=MAX_ARCHIVE_BYTES)
    with zipfile.ZipFile(io.BytesIO(archive_raw), "r") as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        expected = {MANIFEST, *MEMBERS}
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate members")
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("Archive member allowlist mismatch")
        contents = {}
        for item in infos:
            name = item.filename
            unix_mode = item.external_attr >> 16
            mode_kind = stat.S_IFMT(unix_mode)
            if (name.startswith("/") or "\\" in name or ".." in Path(name).parts
                    or mode_kind not in {0, stat.S_IFREG} or item.file_size > MAX_MEMBER_BYTES
                    or item.compress_type != zipfile.ZIP_STORED):
                raise ValueError("Archive contains an unsafe, compressed or oversized member")
            raw = bundle.read(item)
            if len(raw) != item.file_size:
                raise ValueError("Archive member size changed during read")
            contents[name] = raw
    manifest = _json(contents[MANIFEST], "manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 or manifest.get("synthetic_only") is not True:
        raise ValueError("Unsupported or non-synthetic archive manifest")
    expected_manifest_fields = {"schema_version", "synthetic_only", "candidate_id", "attempt_id",
                                "source_revision", "member_map", "members", "verification_scope", "limitations"}
    if set(manifest) != expected_manifest_fields:
        raise ValueError("Manifest fields differ from the fixed evidence schema")
    revision = manifest.get("source_revision")
    if (not isinstance(revision, dict) or set(revision) != {"commit", "dirty"}
            or not isinstance(revision.get("commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40,64}", revision["commit"])
            or type(revision.get("dirty")) is not bool):
        raise ValueError("Manifest source revision is invalid")
    if not isinstance(manifest.get("limitations"), list) or not all(isinstance(item, str) for item in manifest["limitations"]):
        raise ValueError("Manifest limitations must be a list of text")
    _assert_no_sensitive_or_external(manifest)
    if manifest.get("member_map") != {name: MEMBERS[name] for name in MEMBERS}:
        raise ValueError("Manifest member map differs from the fixed allowlist")
    descriptors = manifest.get("members")
    if not isinstance(descriptors, dict) or set(descriptors) != set(MEMBERS):
        raise ValueError("Manifest payload descriptors are incomplete")
    for name in MEMBERS:
        descriptor = descriptors[name]
        raw = contents[name]
        if (not isinstance(descriptor, dict) or set(descriptor) != {"source_relative_path", "sha256", "bytes"}
                or descriptor.get("sha256") != _sha(raw)
                or type(descriptor.get("bytes")) is not int or descriptor["bytes"] != len(raw)
                or not _safe_relative(descriptor.get("source_relative_path"))):
            raise ValueError(f"Manifest hash, size or source path mismatch for {name}")

    plan_raw, report_raw = contents["payload/plan.json"], contents["payload/report.json"]
    ledger_raw, journal_raw = contents["payload/operational-ledger.json"], contents["payload/request-journal.jsonl"]
    plan, report = _json(plan_raw, "plan"), _json(report_raw, "report")
    _assert_no_sensitive_or_external(_json(ledger_raw, "operational ledger"))
    _assert_no_sensitive_or_external(_journal_json_lines(journal_raw))
    _assert_no_sensitive_or_external(plan)
    _assert_no_sensitive_or_external(report)
    candidate_id, attempt_id = manifest.get("candidate_id"), manifest.get("attempt_id")
    if (not isinstance(candidate_id, str) or plan.get("identity", {}).get("candidate_id") != candidate_id
            or report.get("model_identity", {}).get("candidate_id") != candidate_id
            or report.get("operational_attempt_id") != attempt_id):
        raise ValueError("Manifest, plan and report candidate/attempt identities differ")
    report_relative = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
    journal_relative = str(Path(report_relative).with_suffix(".requests.jsonl"))
    if (descriptors["payload/report.json"]["source_relative_path"] != report_relative
            or descriptors["payload/operational-ledger.json"]["source_relative_path"] != "data/evaluations/capital-bikeshare/operational-attempts.json"
            or descriptors["payload/request-journal.jsonl"]["source_relative_path"] != journal_relative):
        raise ValueError("Archived report/ledger/journal source path differs from the fixed attempt")
    plan_sha = _sha(plan_raw)
    record = None
    with tempfile.TemporaryDirectory(prefix="capital-measurement-archive-verify-") as directory:
        root = Path(directory)
        ledger_path = root / "operational-attempts.json"
        ledger_path.write_bytes(ledger_raw)
        ledger = read_operational_ledger(ledger_path)
        record = next((row for row in ledger["records"] if row.get("candidate_id") == candidate_id), None)
        if record is None:
            raise ValueError("Candidate is absent from the archived ledger")
        attempt = next((row for row in record["attempts"] if row.get("attempt_id") == attempt_id), None)
        if (attempt is None or attempt.get("status") != "completed" or attempt.get("plan_sha256") != plan.get("plan_sha256")
                or attempt.get("plan_file_sha256") != plan_sha or attempt.get("plan_path") != manifest["members"]["payload/plan.json"]["source_relative_path"]
                or not isinstance(attempt.get("report"), dict)
                or attempt["report"].get("sha256") != _sha(report_raw)
                or attempt["report"].get("path") != manifest["members"]["payload/report.json"]["source_relative_path"]
                or attempt["report"].get("gate_status") != "not_evaluated"):
            raise ValueError("Archived ledger does not bind the exact completed attempt, plan and report")
        if (plan.get("deployment_permission") != "blocked"
                or report.get("deployment_permission", {}).get("status") != "blocked"
                or report.get("operational_gate", {}).get("status") != "not_evaluated"):
            raise ValueError("Archive changes the blocked/not-evaluated operational state")
        descriptor = report.get("request_journal")
        if not isinstance(descriptor, dict):
            raise ValueError("Report request journal descriptor is missing")
        journal_name = descriptor.get("file_name")
        if (journal_name != Path(journal_relative).name
                or descriptor.get("sha256") != _sha(journal_raw)
                or descriptor.get("events") != 2101
                or descriptor.get("policy_id") != JOURNAL_POLICY["policy_id"]):
            raise ValueError("Report journal descriptor does not bind the archived journal")
        journal_path = root / journal_name
        journal_path.write_bytes(journal_raw)
        journal = inspect_request_journal(journal_path, attempt_id=attempt_id, candidate_id=candidate_id,
                                          plan=plan, plan_file_sha256=plan_sha)
        if journal.get("status") != "readable" or journal.get("sha256") != _sha(journal_raw):
            raise ValueError("Request journal is incomplete, torn or changed")
        numeric = recheck_measurement_records(plan, report, attempt_id=attempt_id, plan_file_sha256=plan_sha)
        latencies = journal.get("verified_latencies_ms", {})
        if len(latencies) != 1050 or len(report.get("measurements", [])) != 1050:
            raise ValueError("The request journal and report must each contain all 1,050 responses")
        for row in report["measurements"]:
            if latencies.get(row.get("request_id")) != row.get("latency_ms"):
                raise ValueError("Request journal latency differs from report row")
        if journal.get("counts", {}).get("response_verified") != 1050:
            raise ValueError("Request journal does not confirm all 1,050 successful responses")
    return {
        "status": "archive_recalculated", "synthetic_only": True,
        "candidate_id": candidate_id, "attempt_id": attempt_id,
        "archive_sha256": _sha(archive_raw),
        "members": {name: {"sha256": _sha(contents[name]), "bytes": len(contents[name])} for name in MEMBERS},
        "warmup_requests_excluded": 50, "measured_requests_recalculated": numeric["measured_requests"],
        "measured_errors": 0, "p95_ms": numeric["p95_ms"], "numeric_status": numeric["numeric_status"],
        "request_id_sha256": numeric["request_id_sha256"],
        "journal_verified_responses": 1050, "operational_gate": "not_evaluated",
        "deployment_permission": "blocked",
        "limitations": ["Synthetic evidence only; does not prove real-data performance, clock qualification, DB audit outside the saved journal, or deployment approval."],
    }


def verify_archive_process(archive: Path) -> dict:
    return _validate_archive(archive)


def _workspace_marker(root: Path, project: str, token: str) -> Path:
    _no_symlinks(root)
    resolved = root.resolve(strict=True)
    tmp = Path(tempfile.gettempdir()).resolve(strict=True)
    if (resolved.parent != tmp or not resolved.name.startswith("capital-system-harness-")
            or not stat.S_ISDIR(resolved.lstat().st_mode) or resolved.is_symlink()
            or resolved.lstat().st_uid != os.geteuid() or resolved.lstat().st_mode & 0o077):
        raise ValueError("Export accepts only the harness-owned temporary workspace")
    marker = resolved / ".t16-workspace-identity.json"
    payload = json.dumps({"project": project, "token": token}, sort_keys=True).encode()
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    return marker


def export_synthetic_evidence(*, root: Path, project: str, token: str, candidate_id: str,
                              attempt_id: str, plan_relative: str, report_path: Path,
                              evidence_dir: Path, source_commit: str, source_dirty: bool) -> dict:
    """Copy four exact originals, verify them in another process, then return descriptor."""
    _no_symlinks(root)
    marker = root / ".t16-workspace-identity.json"
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit) or type(source_dirty) is not bool:
        raise ValueError("Source revision metadata is invalid")
    marker_stat = marker.lstat()
    if (not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_mode & 0o077
            or marker_stat.st_uid != os.geteuid()):
        raise ValueError("Harness workspace marker has unsafe type or permissions")
    marker_value = _json(_read_regular(marker, max_bytes=4096), "workspace marker")
    if marker_value != {"project": project, "token": token}:
        raise ValueError("Harness workspace marker identity mismatch")
    workspace = root.resolve(strict=True)
    workspace_stat = workspace.lstat()
    if (workspace.parent != Path(tempfile.gettempdir()).resolve(strict=True)
            or not workspace.name.startswith("capital-system-harness-")
            or workspace_stat.st_uid != os.geteuid() or workspace_stat.st_mode & 0o077):
        raise ValueError("Export source is not a private harness temporary workspace")
    output_base = (Path(__file__).resolve().parents[2] / "data" / "reports").resolve()
    if evidence_dir.is_symlink() or evidence_dir.exists():
        raise FileExistsError("Evidence output directory already exists or is a symlink")
    _no_symlinks(evidence_dir)
    output_dir = evidence_dir.resolve()
    if not output_dir.is_relative_to(output_base) or output_dir == output_base:
        raise ValueError("Evidence output directory must be new and under data/reports")
    output_dir.mkdir(parents=True, mode=0o700)
    try:
        relative_plan = Path(plan_relative)
        if not _safe_relative(plan_relative):
            raise ValueError("Plan source path must be a safe workspace-relative path")
        source_paths = {
            "payload/plan.json": workspace / relative_plan,
            "payload/report.json": report_path,
            "payload/operational-ledger.json": workspace / "data/evaluations/capital-bikeshare/operational-attempts.json",
            "payload/request-journal.jsonl": report_path.with_suffix(".requests.jsonl"),
        }
        originals = {}
        descriptors = {}
        for name, source in source_paths.items():
            _no_symlinks(source)
            if source.is_absolute() and not source.is_relative_to(workspace):
                raise ValueError("Evidence input path must be within the harness workspace")
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(workspace) or source.is_symlink():
                raise ValueError("Evidence source is outside the harness workspace or is a symlink")
            raw = _read_regular(source)
            _assert_no_sensitive_or_external(_json(raw, name) if name.endswith(".json") else _journal_json_lines(raw))
            originals[name] = raw
            descriptors[name] = {"source_relative_path": resolved.relative_to(workspace).as_posix(),
                                 "sha256": _sha(raw), "bytes": len(raw)}
        plan = _json(originals["payload/plan.json"], "plan")
        report = _json(originals["payload/report.json"], "report")
        if plan["identity"]["candidate_id"] != candidate_id or report.get("operational_attempt_id") != attempt_id:
            raise ValueError("Harness return identity differs from selected plan/report")
        if report.get("operational_gate", {}).get("status") != "not_evaluated" or plan.get("deployment_permission") != "blocked":
            raise ValueError("Synthetic evidence must retain blocked/not-evaluated status")
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": candidate_id,
                    "attempt_id": attempt_id, "source_revision": {"commit": source_commit, "dirty": source_dirty},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS}, "members": descriptors,
                    "verification_scope": "byte-preserving originals; archive-only process recomputes request counts, journal/report latency joins and nearest-rank p95",
                    "limitations": ["No real-data, database, clock-qualification or deployment approval claim."]}
        archive = output_dir / "synthetic-measurement-evidence.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr(MANIFEST, json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n")
            for name in MEMBERS:
                bundle.writestr(name, originals[name])
        for name, source in source_paths.items():
            if _sha(_read_regular(source)) != descriptors[name]["sha256"]:
                raise ValueError("An original evidence file changed after its initial read")
        archive_hash = _sha(_read_regular(archive, max_bytes=MAX_ARCHIVE_BYTES))
        env = {key: os.environ[key] for key in ("PATH", "VIRTUAL_ENV") if key in os.environ}
        env["MLFLOW_DISABLE_AGENT_HINT"] = "1"
        start_utc = time.time_ns()
        start_mono = time.monotonic_ns()
        checked = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--verify-archive", str(archive)],
                                 cwd=Path(__file__).resolve().parents[2], env=env,
                                 capture_output=True, text=True, timeout=60, check=False)
        if checked.returncode != 0:
            raise RuntimeError(f"Archive-only verifier failed with exit {checked.returncode}")
        result = _json(checked.stdout.encode(), "archive-only verifier result")
        if result.get("archive_sha256") != archive_hash:
            raise ValueError("Separate verifier archive hash differs")
        (output_dir / "archive-sha256.json").write_text(json.dumps({"archive": archive.name,
            "sha256": archive_hash, "bytes": archive.stat().st_size}, indent=2) + "\n", encoding="utf-8")
        (output_dir / "recalculation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"archive": str(archive.relative_to(output_base.parent)), "archive_sha256": archive_hash,
                "recalculation": result, "verifier_utc_start_ns": start_utc,
                "verifier_monotonic_start_ns": start_mono, "verifier_exit_code": checked.returncode}
    except BaseException as failure:
        try:
            shutil.rmtree(output_dir)
        except BaseException as cleanup_failure:
            raise BaseExceptionGroup("Evidence export and partial-output cleanup both failed", [failure, cleanup_failure])
        raise


def _journal_json_lines(raw: bytes):
    rows = []
    for line in raw.splitlines():
        if line:
            rows.append(_json(line, "journal event"))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-archive", type=Path, required=True,
                        help="Verify and recalculate from this archive only")
    args = parser.parse_args()
    print(json.dumps(verify_archive_process(args.verify_archive), sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
