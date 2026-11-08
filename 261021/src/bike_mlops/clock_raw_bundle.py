"""Portable synthetic raw evidence only; never execute archived code or qualify clocks."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile
import zipfile

from bike_mlops import clock_campaign_capture as raw

KIND = "synthetic-clock-raw-bundle-v1"
LIMIT = raw.MAX_BYTES
ZIP_LIMIT = LIMIT + 65536
MANIFEST_LIMIT = 16384
NAMES = {"manifest.json", *("raw/" + name for name in raw.MEMBERS)}


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _identity(path):
    s = path.stat(follow_symlinks=False)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _replay(payloads, plan_sha256):
    # Write only fixed names to a private directory; never ZipFile.extract*().
    with tempfile.TemporaryDirectory(prefix="synthetic-clock-bundle-") as directory:
        root = Path(directory)
        for name in raw.MEMBERS:
            (root / name).write_bytes(payloads["raw/" + name])
        return raw.derive_raw(root, expected_plan_sha256=plan_sha256)


def _validate(payloads, plan_sha256):
    raw.digest(plan_sha256)
    manifest = raw.decode(payloads["manifest.json"])
    raw.object_exact(manifest, {"kind", "plan_sha256", "members"})
    if manifest["kind"] != KIND or manifest["plan_sha256"] != plan_sha256:
        raise ValueError("Synthetic bundle plan mismatch")
    raw.object_exact(manifest["members"], NAMES - {"manifest.json"})
    for name, entry in manifest["members"].items():
        raw.object_exact(entry, {"bytes", "sha256"})
        raw.integer(entry["bytes"])
        raw.digest(entry["sha256"])
        if entry != {"bytes": len(payloads[name]), "sha256": _sha(payloads[name])}:
            raise ValueError("Synthetic bundle member mismatch")
    # Replay verifies synthetic schemas, nonce/sequence, sources, chain and marker,
    # as well as retaining known violations and missingness in the numerical review.
    campaign, report = _replay(payloads, plan_sha256)
    return campaign, report


def _zip_bytes(payloads):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payloads[name])
    return output.getvalue()


def _publish(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # No overwrite, including an existing broken symlink.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_bundle(root, output, *, expected_plan_sha256):
    """Export complete fixture raw only; refuse arbitrary saved source bytes."""
    raw.digest(expected_plan_sha256)
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != raw.MEMBERS:
        raise ValueError("Incomplete synthetic raw")
    if root.resolve() in output.resolve().parents:
        raise ValueError("Bundle must be outside raw evidence")
    before = {name: _identity(root / name) for name in raw.MEMBERS}
    payloads = {"raw/" + name: raw.read_stable(root / name) for name in raw.MEMBERS}
    if sum(map(len, payloads.values())) > LIMIT:
        raise ValueError("Synthetic bundle too large")
    # Narrow export allowlist: do not publish substituted code, credentials or
    # arbitrary private source text merely because an attacker rehashed it.
    saved = raw.decode(payloads["raw/sources.json"])
    scheduled = "clock_campaign_schedule.py" in saved
    _, _, allowed = raw.make_plan(scheduled_recipe={} if scheduled else None)
    if saved != allowed:
        raise ValueError("Export supports installed synthetic source bytes only")
    manifest = {"kind": KIND, "plan_sha256": expected_plan_sha256,
                "members": {name: {"bytes": len(data), "sha256": _sha(data)}
                            for name, data in payloads.items()}}
    payloads["manifest.json"] = raw.encode(manifest)
    _validate(payloads, expected_plan_sha256)
    if ({p.name for p in root.iterdir()} != raw.MEMBERS
            or any(_identity(root / name) != before[name]
                   or raw.read_stable(root / name) != payloads["raw/" + name]
                   for name in raw.MEMBERS)):
        raise ValueError("Synthetic originals changed during export")
    data = _zip_bytes(payloads)
    _publish(output, data)
    return {"kind": KIND, "archive_sha256": _sha(data), "archive_bytes": len(data),
            "plan_sha256": expected_plan_sha256, "synthetic_only": True,
            "clock_qualification_status": "incomplete"}


def review_bundle(path, *, expected_plan_sha256):
    """Recompute only from a stable ZIP snapshot. No saved code, path or URL executes."""
    raw.digest(expected_plan_sha256)
    data = raw.read_stable(path, ZIP_LIMIT)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) != len(NAMES) or {item.filename for item in infos} != NAMES:
            raise ValueError("Unexpected synthetic bundle members")
        if sum(item.file_size for item in infos if item.filename != "manifest.json") > LIMIT:
            raise ValueError("Synthetic bundle too large")
        payloads = {}
        for item in infos:
            maximum = MANIFEST_LIMIT if item.filename == "manifest.json" else LIMIT
            if (item.is_dir() or item.create_system != 3
                    or not stat.S_ISREG(item.external_attr >> 16)
                    or item.flag_bits & 1 or item.compress_type != zipfile.ZIP_STORED
                    or item.file_size > maximum or item.compress_size != item.file_size):
                raise ValueError("Invalid synthetic ZIP member")
            payloads[item.filename] = archive.read(item)
    # Only our deterministic stored representation is supported. Reject ZIP
    # comments, hidden prefix/trailing bytes, extra fields and metadata channels.
    if _zip_bytes(payloads) != data:
        raise ValueError("Noncanonical synthetic ZIP")
    campaign, report = _validate(payloads, expected_plan_sha256)
    return {"kind": KIND, "archive_sha256": _sha(data), "archive_bytes": len(data),
            "plan_sha256": expected_plan_sha256,
            "campaign_sha256": raw.canonical_sha256(campaign),
            "synthetic_only": True, "members": {
                name: {"bytes": len(value), "sha256": _sha(value)}
                for name, value in payloads.items()}, "review": report}
