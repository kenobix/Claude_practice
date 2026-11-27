from typing import assert_never

from . import ast_nodes as ast
from .diagnostics import DiagnosticError
from .operators import binary_type, unary_type
from .resolver import Resolution
from .scanner import Token
from .values import ValueType


def check_types(program: ast.Program, resolution: Resolution) -> None:
    checker = _TypeChecker(resolution)
    for statement in program.statements:
        checker.statement(statement)


def _require_type(actual: ValueType, expected: ValueType, token: Token) -> None:
    if actual is not expected:
        raise DiagnosticError(
            "E_TYPE", "type", f"Expected {expected}, found {actual}.", token.position
        )


class _TypeChecker:
    def __init__(self, resolution: Resolution) -> None:
        self.resolution = resolution
        self.types = tuple(
            ValueType(declaration.annotation.kind) for declaration in resolution.declarations
        )

    def _name_type(self, name: Token) -> ValueType:
        return self.types[self.resolution.declaration_id(name)]

    def statement(self, statement: ast.Statement) -> None:
        match statement:
            case ast.Let(name=name, initializer=initializer):
                _require_type(self.expression(initializer), self._name_type(name), name)
            case ast.Assignment(anchor=name, value=value):
                _require_type(self.expression(value), self._name_type(name), name)
            case ast.Print(value=value):
                self.expression(value)
            case ast.Block(statements=statements):
                for child in statements:
                    self.statement(child)
            case ast.If(condition=condition, then_block=then_block, else_block=else_block):
                _require_type(self.expression(condition), ValueType.BOOL, statement.anchor)
                self.statement(then_block)
                if else_block is not None:
                    self.statement(else_block)
            case ast.While(condition=condition, body=body):
                _require_type(self.expression(condition), ValueType.BOOL, statement.anchor)
                self.statement(body)
            case _:
                assert_never(statement)

    def expression(self, expression: ast.Expression) -> ValueType:
        match expression:
            case ast.IntegerLiteral():
                return ValueType.INT
            case ast.BooleanLiteral():
                return ValueType.BOOL
            case ast.Variable():
                return self._name_type(expression.anchor)
            case ast.Unary(operand=operand):
                return unary_type(expression.anchor, self.expression(operand))
            case ast.Binary(left=left, right=right):
                left_type = self.expression(left)
                right_type = self.expression(right)
                return binary_type(expression.anchor, left_type, right_type)
            case _:
                assert_never(expression)
