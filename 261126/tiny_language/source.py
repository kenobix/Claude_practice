from pathlib import Path

from .diagnostics import DiagnosticError, Position

MAX_SOURCE_BYTES = 65_536


def read_source(path: Path) -> str:
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        raise DiagnosticError("E_INPUT", "input", f"Cannot read source file {str(path)!r}.") from None

    if len(data) > MAX_SOURCE_BYTES:
        raise DiagnosticError(
            "E_LIMIT", "input", f"Source exceeds {MAX_SOURCE_BYTES:,} bytes."
        )

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        start = error.start
        prefix = data[:start].decode("utf-8")
        position = Position(
            prefix.count("\n") + 1, len(prefix.rpartition("\n")[2]) + 1, start, start + 1
        )
        raise DiagnosticError("E_INPUT", "input", "Invalid UTF-8 sequence.", position) from None
