import re
from dataclasses import dataclass

from .diagnostics import DiagnosticError, Position
from .integers import MAX_INTEGER_BITS, parse_decimal

MAX_TOKENS = 8_192

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
            integer = parse_decimal(lexeme)
            if integer is None:
                raise DiagnosticError(
                    "E_LIMIT", "lex", f"Integer literal exceeds {MAX_INTEGER_BITS:,} bits.", position
                )
            tokens.append(Token("INTEGER", lexeme, position, integer))
        elif category == "word" and lexeme not in _KEYWORDS:
            tokens.append(Token("IDENT", lexeme, position))
        else:
            tokens.append(Token(lexeme, lexeme, position))

    tokens.append(Token("EOF", "", Position(line, column, byte_offset, byte_offset)))
    return tokens
