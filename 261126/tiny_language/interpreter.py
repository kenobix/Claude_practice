from collections.abc import Callable
from typing import assert_never

from . import ast_nodes as ast
from .diagnostics import DiagnosticError
from .operators import apply_binary, apply_unary
from .resolver import Resolution
from .scanner import Token
from .values import BoolValue, IntValue, Value, format_value

MAX_OUTPUT_LINES = 1_024
MAX_STEPS = 100_000


def interpret(
    program: ast.Program, resolution: Resolution, write: Callable[[str], None]
) -> None:
    """Run a resolved and type-checked program, writing each successful line immediately."""
    interpreter = _Interpreter(resolution, write)
    for statement in program.statements:
        interpreter.statement(statement)


class _Interpreter:
    def __init__(self, resolution: Resolution, write: Callable[[str], None]) -> None:
        self.resolution = resolution
        self.write = write
        # Each let writes its slot whenever it runs, including on block reentry.
        self.slots: list[Value | None] = [None] * len(resolution.declarations)
        self.steps = 0
        self.output_lines = 0

    def _step(self, anchor: Token) -> None:
        if self.steps == MAX_STEPS:
            raise DiagnosticError(
                "E_LIMIT", "runtime", f"Execution exceeds {MAX_STEPS:,} AST steps.", anchor.position
            )
        self.steps += 1

    def _print(self, anchor: Token, value: Value) -> None:
        if self.output_lines == MAX_OUTPUT_LINES:
            raise DiagnosticError(
                "E_LIMIT", "runtime", f"Output exceeds {MAX_OUTPUT_LINES:,} lines.", anchor.position
            )
        self.write(format_value(value) + "\n")
        self.output_lines += 1

    def _condition(self, expression: ast.Expression) -> bool:
        value = self.expression(expression)
        assert isinstance(value, BoolValue)
        return value.value

    def statement(self, statement: ast.Statement) -> None:
        self._step(statement.anchor)
        match statement:
            case ast.Let(name=name, initializer=initializer):
                value = self.expression(initializer)
                self.slots[self.resolution.declaration_id(name)] = value
            case ast.Assignment(anchor=name, value=expression):
                value = self.expression(expression)
                self.slots[self.resolution.declaration_id(name)] = value
            case ast.Print(value=expression):
                self._print(statement.anchor, self.expression(expression))
            case ast.Block(statements=statements):
                for child in statements:
                    self.statement(child)
            case ast.If(condition=condition, then_block=then_block, else_block=else_block):
                branch = then_block if self._condition(condition) else else_block
                if branch is not None:
                    self.statement(branch)
            case ast.While(condition=condition, body=body):
                while self._condition(condition):
                    self.statement(body)
            case _:
                assert_never(statement)

    def expression(self, expression: ast.Expression) -> Value:
        self._step(expression.anchor)
        match expression:
            case ast.IntegerLiteral(value=value):
                return IntValue(value)
            case ast.BooleanLiteral(value=value):
                return BoolValue(value)
            case ast.Variable():
                value = self.slots[self.resolution.declaration_id(expression.anchor)]
                assert value is not None
                return value
            case ast.Unary(operand=operand):
                return apply_unary(expression.anchor, self.expression(operand))
            case ast.Binary(left=left, right=right):
                left_value = self.expression(left)
                right_value = self.expression(right)
                return apply_binary(expression.anchor, left_value, right_value)
            case _:
                assert_never(expression)
