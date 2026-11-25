import operator

from .diagnostics import DiagnosticError
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


def _integer_result(value: int, token: Token) -> IntValue:
    if value.bit_length() > MAX_INTEGER_BITS:
        raise DiagnosticError(
            "E_LIMIT", "runtime", f"Integer result exceeds {MAX_INTEGER_BITS:,} bits.",
            token.position,
        )
    return IntValue(value)


def apply_unary(token: Token, operand: Value) -> Value:
    if token.kind == "!":
        assert isinstance(operand, BoolValue)
        return BoolValue(not operand.value)
    assert token.kind == "-" and isinstance(operand, IntValue)
    return _integer_result(-operand.value, token)


def apply_binary(token: Token, left: Value, right: Value) -> Value:
    kind = token.kind
    if kind in _EQUALITY:
        assert left.tag is right.tag
        return BoolValue(_EQUALITY[kind](left, right))

    assert isinstance(left, IntValue) and isinstance(right, IntValue)
    if kind in _ORDERING:
        return BoolValue(_ORDERING[kind](left.value, right.value))
    if kind in ("/", "%") and right.value == 0:
        raise DiagnosticError(
            "E_ZERO_DIVISION", "runtime", f"Zero divisor for {kind!r}.", token.position
        )
    return _integer_result(_ARITHMETIC[kind](left.value, right.value), token)
