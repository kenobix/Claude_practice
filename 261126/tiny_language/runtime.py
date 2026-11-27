from collections.abc import Callable

from .diagnostics import DiagnosticError, Position
from .values import Value, format_value

MAX_OUTPUT_LINES = 1_024
MAX_STEPS = 100_000


class ProgramOutput:
    def __init__(self, write: Callable[[str], None]) -> None:
        self.write = write
        self.lines = 0

    def print_value(self, value: Value, position: Position) -> None:
        if self.lines == MAX_OUTPUT_LINES:
            raise DiagnosticError(
                "E_LIMIT", "runtime", f"Output exceeds {MAX_OUTPUT_LINES:,} lines.", position
            )
        self.write(format_value(value) + "\n")
        self.lines += 1
