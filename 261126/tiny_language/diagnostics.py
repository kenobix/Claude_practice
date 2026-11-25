from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    line: int
    column: int
    start: int
    end: int

    def as_dict(self) -> dict[str, int | list[int]]:
        return {"line": self.line, "column": self.column, "span": [self.start, self.end]}


class DiagnosticError(Exception):
    def __init__(
        self, code: str, phase: str, message: str, position: Position | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.position = position

    @property
    def exit_code(self) -> int:
        if self.phase == "runtime":
            return 3
        if self.phase == "internal":
            return 4
        return 2

    def as_dict(self) -> dict[str, object]:
        position = (
            self.position.as_dict()
            if self.position is not None
            else {"line": None, "column": None, "span": None}
        )
        return {"code": self.code, "phase": self.phase, "message": str(self), **position}
