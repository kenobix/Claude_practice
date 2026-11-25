import re
from dataclasses import dataclass

from .diagnostics import DiagnosticError, Position

MAX_TOKENS = 8_192
MAX_INTEGER_BITS = 4_096
# The largest 4,096-bit value has 1,234 decimal digits.
MAX_INTEGER_DECIMAL_DIGITS = 1_234

_KEYWORDS = frozenset({"let", "int", "bool", "print", "if", "else", "while", "true", "false"})
_LEXEME = re.compile(
    r"(?P<newline>\r?\n)"
    r"|(?P<trivia>[ \t]+|//(?:[^\r\n]|\r(?!\n))*)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<integer>[0-9]+)"
    r"|(?P<symbol>==|!=|<=|>=|[:=;{}()<>+*/%!\-])"
)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    lexeme: str
    position: Position
    integer: int | None = None


def _integer_value(lexeme: str, position: Position) -> int:
    digits = lexeme.lstrip("0")
    if len(digits) <= MAX_INTEGER_DECIMAL_DIGITS:
        # Short conversions work even with Python's decimal digit limit set to its minimum.
        value = 0
        for start in range(0, len(digits), 9):
            chunk = digits[start : start + 9]
            value = value * 10 ** len(chunk) + int(chunk)
        if value.bit_length() <= MAX_INTEGER_BITS:
            return value
    raise DiagnosticError(
        "E_LIMIT", "lex", f"Integer literal exceeds {MAX_INTEGER_BITS:,} bits.", position
    )


def scan(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = byte_offset = 0
    line = column = 1
    while index < len(source):
        match = _LEXEME.match(source, index)
        if match is None:
            char = source[index]
            position = Position(line, column, byte_offset, byte_offset + len(char.encode("utf-8")))
            raise DiagnosticError("E_LEX", "lex", f"Unexpected character {char!r}.", position)

        lexeme = match.group()
        category = match.lastgroup
        end = byte_offset + len(lexeme.encode("utf-8"))
        position = Position(line, column, byte_offset, end)
        index = match.end()
        byte_offset = end
        if category == "newline":
            line += 1
            column = 1
            continue

        column += len(lexeme)
        if category == "trivia":
            continue
        if len(tokens) == MAX_TOKENS:
            raise DiagnosticError("E_LIMIT", "lex", f"Source exceeds {MAX_TOKENS:,} tokens.", position)

        if category == "integer":
            tokens.append(Token("INTEGER", lexeme, position, _integer_value(lexeme, position)))
        elif category == "word" and lexeme not in _KEYWORDS:
            tokens.append(Token("IDENT", lexeme, position))
        else:
            tokens.append(Token(lexeme, lexeme, position))

    tokens.append(Token("EOF", "", Position(line, column, byte_offset, byte_offset)))
    return tokens
