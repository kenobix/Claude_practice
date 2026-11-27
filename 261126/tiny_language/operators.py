import operator

from .diagnostics import DiagnosticError, Position
from .integers import MAX_INTEGER_BITS
from .scanner import Token
from .values import BoolValue, IntValue, Value, ValueType

_ARITHMETIC = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
    "/": operator.floordiv, "%": operator.mod,
}
_ORDERING = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_EQUALITY = {"==": operator.eq, "!=": operator.ne}
_UNARY_TYPES = {"-": ValueType.INT, "!": ValueType.BOOL}
UNARY_OPERATORS = frozenset(_UNARY_TYPES)
BINARY_OPERATORS = frozenset(_ARITHMETIC.keys() | _ORDERING.keys() | _EQUALITY.keys())


def unary_type(token: Token, operand: ValueType) -> ValueType:
    if operand is _UNARY_TYPES[token.kind]:
        return operand
    raise DiagnosticError(
        "E_TYPE", "type", f"Operator {token.kind!r} does not accept {operand}.", token.position
    )


def binary_type(token: Token, left: ValueType, right: ValueType) -> ValueType:
    if token.kind in _EQUALITY:
        if left is right:
            return ValueType.BOOL
    elif left is ValueType.INT and right is ValueType.INT:
        if token.kind in _ARITHMETIC:
            return ValueType.INT
        if token.kind in _ORDERING:
            return ValueType.BOOL
    raise DiagnosticError(
        "E_TYPE", "type", f"Operator {token.kind!r} does not accept {left} and {right}.",
        token.position,
    )


def _integer_result(value: int, position: Position) -> IntValue:
    if value.bit_length() > MAX_INTEGER_BITS:
        raise DiagnosticError(
            "E_LIMIT", "runtime", f"Integer result exceeds {MAX_INTEGER_BITS:,} bits.",
            position,
        )
    return IntValue(value)


def _invalid_types(kind: str, position: Position) -> DiagnosticError:
    return DiagnosticError("E_INTERNAL", "internal", f"Invalid values for {kind!r}.", position)


def apply_unary(kind: str, operand: Value, position: Position) -> Value:
    if kind == "!" and isinstance(operand, BoolValue):
        return BoolValue(not operand.value)
    if kind == "-" and isinstance(operand, IntValue):
        return _integer_result(-operand.value, position)
    raise _invalid_types(kind, position)


def apply_binary(kind: str, left: Value, right: Value, position: Position) -> Value:
    if kind in _EQUALITY:
        if left.tag is not right.tag:
            raise _invalid_types(kind, position)
        return BoolValue(_EQUALITY[kind](left, right))

    if not isinstance(left, IntValue) or not isinstance(right, IntValue):
        raise _invalid_types(kind, position)
    if kind in _ORDERING:
        return BoolValue(_ORDERING[kind](left.value, right.value))
    if kind in ("/", "%") and right.value == 0:
        raise DiagnosticError(
            "E_ZERO_DIVISION", "runtime", f"Zero divisor for {kind!r}.", position
        )
    return _integer_result(_ARITHMETIC[kind](left.value, right.value), position)
