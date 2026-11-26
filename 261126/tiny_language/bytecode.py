from dataclasses import dataclass
from enum import Enum, auto

from .diagnostics import DiagnosticError, Position
from .integers import MAX_INTEGER_BITS
from .operators import BINARY_OPERATORS, UNARY_OPERATORS
from .values import BoolValue, IntValue, Value, ValueType


class Op(Enum):
    PUSH = auto()
    LOAD = auto()
    STORE = auto()
    UNARY = auto()
    BINARY = auto()
    JUMP_IF_FALSE = auto()
    JUMP = auto()
    PRINT = auto()
    HALT = auto()


type Operand = Value | int | str | None


@dataclass(frozen=True, slots=True)
class Instruction:
    op: Op
    operand: Operand
    position: Position


@dataclass(frozen=True, slots=True)
class Bytecode:
    instructions: tuple[Instruction, ...]
    slot_types: tuple[ValueType, ...]


_STACK_EFFECT = {
    Op.PUSH: (0, 1), Op.LOAD: (0, 1), Op.STORE: (1, 0), Op.UNARY: (1, 1),
    Op.BINARY: (2, 1), Op.JUMP_IF_FALSE: (1, 0), Op.JUMP: (0, 0),
    Op.PRINT: (1, 0), Op.HALT: (0, 0),
}


def _invalid(message: str, position: Position | None = None) -> DiagnosticError:
    return DiagnosticError("E_INTERNAL", "internal", message, position)


def _valid_position(position: object) -> bool:
    if not isinstance(position, Position):
        return False
    fields = position.line, position.column, position.start, position.end
    return (
        all(type(field) is int for field in fields)
        and position.line >= 1 and position.column >= 1 and 0 <= position.start <= position.end
    )


def _valid_constant(operand: Operand) -> bool:
    if isinstance(operand, IntValue):
        return type(operand.value) is int and operand.value.bit_length() <= MAX_INTEGER_BITS
    return isinstance(operand, BoolValue) and type(operand.value) is bool


def _validate_instruction(instruction: Instruction, code: Bytecode) -> None:
    if not isinstance(instruction, Instruction) or not _valid_position(instruction.position):
        raise _invalid("Instruction has no valid source position.")
    op, operand, position = instruction.op, instruction.operand, instruction.position
    match op:
        case Op.PUSH:
            valid = _valid_constant(operand)
        case Op.LOAD | Op.STORE:
            valid = type(operand) is int and 0 <= operand < len(code.slot_types)
        case Op.UNARY:
            valid = type(operand) is str and operand in UNARY_OPERATORS
        case Op.BINARY:
            valid = type(operand) is str and operand in BINARY_OPERATORS
        case Op.JUMP_IF_FALSE | Op.JUMP:
            valid = type(operand) is int and 0 <= operand < len(code.instructions)
        case Op.PRINT | Op.HALT:
            valid = operand is None
        case _:
            raise _invalid("Unknown bytecode instruction.", position)
    if not valid:
        raise _invalid(f"Invalid operand for {op.name}.", position)
    if op is Op.HALT and position.start != position.end:
        raise _invalid("HALT requires an empty source span.", position)


def validate(code: Bytecode) -> None:
    """Check all instructions, then propagate stack depths through the control flow."""
    if type(code.instructions) is not tuple or type(code.slot_types) is not tuple:
        raise _invalid("Bytecode instructions and slot types must be tuples.")
    if not code.instructions:
        raise _invalid("Empty instruction sequence.")
    if any(not isinstance(tag, ValueType) for tag in code.slot_types):
        raise _invalid("Invalid variable slot type.")

    last = len(code.instructions) - 1
    for pc, instruction in enumerate(code.instructions):
        _validate_instruction(instruction, code)
        if (instruction.op is Op.HALT) != (pc == last):
            raise _invalid("HALT must appear once, at the end of bytecode.", instruction.position)

    depths = {0: 0}
    pending = [0]
    while pending:
        pc = pending.pop()
        instruction = code.instructions[pc]
        depth = depths[pc]
        pops, pushes = _STACK_EFFECT[instruction.op]
        if depth < pops:
            raise _invalid("Bytecode stack underflow.", instruction.position)
        if instruction.op is Op.HALT:
            if depth != 0:
                raise _invalid("HALT requires an empty stack.", instruction.position)
            continue

        next_depth = depth - pops + pushes
        successors: tuple[int, ...] = (pc + 1,)
        if instruction.op in (Op.JUMP, Op.JUMP_IF_FALSE):
            target = instruction.operand
            assert type(target) is int  # Checked before traversing the control flow.
            successors = (target,) if instruction.op is Op.JUMP else (target, pc + 1)
        for successor in successors:
            if successor not in depths:
                depths[successor] = next_depth
                pending.append(successor)
            elif depths[successor] != next_depth:
                raise _invalid(
                    "Inconsistent stack depth at control-flow merge.",
                    code.instructions[successor].position,
                )

    for pc, instruction in enumerate(code.instructions):
        if pc not in depths:
            raise _invalid("Unreachable bytecode instruction.", instruction.position)
