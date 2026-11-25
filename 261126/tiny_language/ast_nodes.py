from __future__ import annotations

from dataclasses import dataclass, field

from .scanner import Token


@dataclass(frozen=True, slots=True)
class Program:
    statements: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class Node:
    anchor: Token
    depth: int = field(default=1, kw_only=True, repr=False)


@dataclass(frozen=True, slots=True)
class Let(Node):
    name: Token
    annotation: Token
    initializer: Expression


@dataclass(frozen=True, slots=True)
class Assignment(Node):
    value: Expression


@dataclass(frozen=True, slots=True)
class Print(Node):
    value: Expression


@dataclass(frozen=True, slots=True)
class Block(Node):
    statements: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class If(Node):
    condition: Expression
    then_block: Block
    else_block: Block | None


@dataclass(frozen=True, slots=True)
class While(Node):
    condition: Expression
    body: Block


@dataclass(frozen=True, slots=True)
class IntegerLiteral(Node):
    value: int


@dataclass(frozen=True, slots=True)
class BooleanLiteral(Node):
    value: bool


@dataclass(frozen=True, slots=True)
class Variable(Node):
    pass


@dataclass(frozen=True, slots=True)
class Unary(Node):
    operand: Expression


@dataclass(frozen=True, slots=True)
class Binary(Node):
    left: Expression
    right: Expression


type Expression = IntegerLiteral | BooleanLiteral | Variable | Unary | Binary
type Statement = Let | Assignment | Print | Block | If | While
