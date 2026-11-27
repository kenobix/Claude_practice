from collections.abc import Callable
from typing import cast

from .bytecode import Bytecode, Op, validate
from .diagnostics import DiagnosticError
from .display import instruction_data, value_data
from .operators import apply_binary, apply_unary
from .runtime import MAX_STEPS, ProgramOutput
from .values import BoolValue, Value


def execute(
    code: Bytecode, write: Callable[[str], None],
    trace: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Validate the bytecode and execute it with a fresh stack and variable slots."""
    validate(code)
    stack: list[Value] = []
    slots: list[Value | None] = [None] * len(code.slot_types)
    output = ProgramOutput(write)
    pc = 0
    for step in range(1, MAX_STEPS + 1):
        instruction = code.instructions[pc]
        position = instruction.position
        operand = instruction.operand
        next_pc = pc + 1
        stored = None
        match instruction.op:
            case Op.PUSH:
                stack.append(cast(Value, operand))
            case Op.LOAD:
                value = slots[cast(int, operand)]
                if value is None:
                    raise DiagnosticError("E_INTERNAL", "internal", "Uninitialized slot.", position)
                stack.append(value)
            case Op.STORE:
                slot = cast(int, operand)
                value = stack.pop()
                if value.tag is not code.slot_types[slot]:
                    raise DiagnosticError("E_INTERNAL", "internal", "Incorrect slot type.", position)
                slots[slot] = value
                if trace is not None:
                    stored = {"slot": slot, **value_data(value)}
            case Op.UNARY:
                stack.append(apply_unary(cast(str, operand), stack.pop(), position))
            case Op.BINARY:
                right = stack.pop()
                left = stack.pop()
                stack.append(apply_binary(cast(str, operand), left, right, position))
            case Op.JUMP_IF_FALSE:
                condition = stack.pop()
                if not isinstance(condition, BoolValue):
                    raise DiagnosticError("E_INTERNAL", "internal", "Invalid condition type.", position)
                if not condition.value:
                    next_pc = cast(int, operand)
            case Op.JUMP:
                next_pc = cast(int, operand)
            case Op.PRINT:
                output.print_value(stack.pop(), position)
            case Op.HALT:
                next_pc = None

        if trace is not None:
            trace({
                **instruction_data(pc, instruction),
                "step": step,
                "next_pc": next_pc,
                "stack": [value_data(value) for value in stack],
                "store": stored,
            })
        if next_pc is None:
            return
        pc = next_pc
    raise DiagnosticError(
        "E_LIMIT", "runtime", f"Execution exceeds {MAX_STEPS:,} VM instructions.",
        code.instructions[pc].position,
    )
