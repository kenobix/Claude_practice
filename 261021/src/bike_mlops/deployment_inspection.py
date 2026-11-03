"""Read-only state/ledger diagnosis. It cannot approve, migrate or repair state."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat

from bike_mlops.capital_deployment_evidence import strict_json


def compare_state(state, row, pending, profile, target):
    out = dict(status="unconfirmed", exit_code=2, automatic_repair=False,
               deployment_id=state.get("last_deployment_id"), pending_count=len(pending),
               differences=[], approval_evidence=False)
    if (state.get("data_profile_id", "uci-bike-sharing") != profile
            or state.get("target_contract_id", "uci-bike-sharing-cnt-dataset-local-hour") != target):
        return dict(out, status="state_profile_mismatch", exit_code=1)
    if pending:
        return dict(out, status="incomplete_deployment_requires_reconciliation")
    if row is None:
        return dict(out, status="legacy_unverifiable")
    expected = dict(active_version=str(row["to_version"]), active_slot=row["to_slot"],
                    approver=row["approver"], reason=row["reason"], last_deployment_id=row["deployment_id"])
    differences = [key for key, value in expected.items() if state.get(key) != value]
    if state.get("deployment_operation") is not None:
        try:
            import json
            prepared = json.loads(row["probe_json"] or "{}")["recovery_context"]["candidate_identity"]["deployment_operation"]
            from bike_mlops.deployment_metadata import operation_identity
            if state["deployment_operation"] != prepared or prepared != operation_identity(row, prepared["artifact_sha256"]):
                differences.append("deployment_operation")
        except (KeyError, TypeError, ValueError):
            differences.append("deployment_operation")
    if row["data_profile_id"] != profile or row["target_contract_id"] != target:
        differences.append("ledger_profile")
    if row["status"] != "completed":
        differences.append("ledger_status")
    return dict(out, status="metadata_mismatch" if differences else "matched",
                exit_code=1 if differences else 0, differences=differences,
                approval_evidence=False, limitation="Metadata consistency alone is not a deployment approval.")


def _read_state_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
            raise ValueError("Unsupported state file")
        body = stream.read(65537)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(path.lstat()) != identity(before) or len(body) != before.st_size:
        raise ValueError("State changed during read")
    return body, identity(before)


def inspect_state(path: Path, url: str, profile: str, target: str):
    connection = None
    base = dict(status="unavailable", exit_code=2, automatic_repair=False, approval_evidence=False)
    try:
        body, identity = _read_state_file(path)
        state = strict_json(body.decode())
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            if not raw or raw == ":memory:" or "?" in raw or "#" in raw:
                raise ValueError("Unsupported database URL")
            connection = sqlite3.connect(Path(raw).resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            placeholder = "?"
        elif url.startswith(("postgresql://", "postgres://")):
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000")
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            placeholder = "%s"
        else:
            raise ValueError("Unsupported database URL")
        # Profile is checked before any SELECT of deployment rows.
        metadata = connection.execute("SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1").fetchone()
        if metadata is None or metadata["data_profile_id"] != profile or metadata["target_contract_id"] != target:
            return dict(base, status="database_profile_mismatch", exit_code=1)
        pending = connection.execute("SELECT deployment_id FROM deployments WHERE status='in_progress' LIMIT 2").fetchall()
        row = connection.execute("SELECT * FROM deployments WHERE deployment_id=" + placeholder,
                                 (state.get("last_deployment_id"),)).fetchone()
        result = compare_state(state, dict(row) if row else None, pending, profile, target)
        # Detect atomic replacement, same-file rewrite, and same-size rewrite.
        if _read_state_file(path) != (body, identity):
            return dict(base, status="state_changed_during_inspection")
        return result
    except Exception as error:
        # Never include URL, SQL, state fields or native error strings.
        return dict(base, error_type=type(error).__name__)
    finally:
        if connection is not None:
            try:
                connection.rollback()
                connection.close()
            except Exception as error:
                try:
                    connection.close()
                except Exception:
                    pass
                return dict(base, error_type=type(error).__name__)
