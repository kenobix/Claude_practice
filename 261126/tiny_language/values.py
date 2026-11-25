from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .integers import format_decimal


class ValueType(StrEnum):
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True, slots=True)
class IntValue:
    value: int
    tag: ClassVar[ValueType] = ValueType.INT


@dataclass(frozen=True, slots=True)
class BoolValue:
    value: bool
    tag: ClassVar[ValueType] = ValueType.BOOL


type Value = IntValue | BoolValue


def format_value(value: Value) -> str:
    if isinstance(value, IntValue):
        return format_decimal(value.value)
    return "true" if value.value else "false"
