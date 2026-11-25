from dataclasses import dataclass
from typing import assert_never

from . import ast_nodes as ast
from .diagnostics import DiagnosticError
from .scanner import Token


@dataclass(frozen=True, slots=True)
class Resolution:
    declarations: tuple[ast.Let, ...]
    bindings: dict[int, int]

    def declaration_id(self, name: Token) -> int:
        # A name token's byte offset uniquely identifies its occurrence in this program.
        return self.bindings[name.position.start]


def resolve(program: ast.Program) -> Resolution:
    resolver = _Resolver()
    for statement in program.statements:
        resolver.statement(statement)
    return Resolution(tuple(resolver.declarations), resolver.bindings)


class _Resolver:
    def __init__(self) -> None:
        self.scopes: list[dict[str, int]] = [{}]
        self.declarations: list[ast.Let] = []
        self.bindings: dict[int, int] = {}
        self.initializing: int | None = None

    def _declare(self, statement: ast.Let) -> None:
        name = statement.name
        scope = self.scopes[-1]
        if name.lexeme in scope:
            raise DiagnosticError(
                "E_REDECLARATION", "resolve",
                f"{name.lexeme!r} is already declared in this scope.", name.position,
            )

        declaration_id = len(self.declarations)
        self.declarations.append(statement)
        self.bindings[name.position.start] = declaration_id
        scope[name.lexeme] = declaration_id
        self.initializing = declaration_id
        self.expression(statement.initializer)
        self.initializing = None

    def _bind(self, name: Token) -> None:
        for scope in reversed(self.scopes):
            declaration_id = scope.get(name.lexeme)
            if declaration_id is None:
                continue
            if declaration_id == self.initializing:
                raise DiagnosticError(
                    "E_SELF_REFERENCE", "resolve",
                    f"{name.lexeme!r} is still being initialized.", name.position,
                )
            self.bindings[name.position.start] = declaration_id
            return
        raise DiagnosticError(
            "E_UNDEFINED", "resolve", f"{name.lexeme!r} is not declared.", name.position
        )

    def statement(self, statement: ast.Statement) -> None:
        match statement:
            case ast.Let():
                self._declare(statement)
            case ast.Assignment(value=value):
                self._bind(statement.anchor)
                self.expression(value)
            case ast.Print(value=value):
                self.expression(value)
            case ast.Block(statements=statements):
                self.scopes.append({})
                for child in statements:
                    self.statement(child)
                self.scopes.pop()
            case ast.If(condition=condition, then_block=then_block, else_block=else_block):
                self.expression(condition)
                self.statement(then_block)
                if else_block is not None:
                    self.statement(else_block)
            case ast.While(condition=condition, body=body):
                self.expression(condition)
                self.statement(body)
            case _:
                assert_never(statement)

    def expression(self, expression: ast.Expression) -> None:
        match expression:
            case ast.Variable():
                self._bind(expression.anchor)
            case ast.Unary(operand=operand):
                self.expression(operand)
            case ast.Binary(left=left, right=right):
                self.expression(left)
                self.expression(right)
            case ast.IntegerLiteral() | ast.BooleanLiteral():
                return
            case _:
                assert_never(expression)
