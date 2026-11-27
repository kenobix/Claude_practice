from . import ast_nodes as ast
from .bytecode import Bytecode, Instruction, validate
from .scanner import Token
from .values import BoolValue, IntValue, Value, format_value


def token_data(token: Token) -> dict[str, object]:
    return {"kind": token.kind, "lexeme": token.lexeme, **token.position.as_dict()}


def ast_data(program: ast.Program) -> dict[str, object]:
    return {"kind": "Program", "statements": [_node_data(node) for node in program.statements]}


def value_data(value: Value) -> dict[str, object]:
    return {"type": value.tag, "value": format_value(value) if isinstance(value, IntValue) else value.value}


def instruction_data(pc: int, instruction: Instruction) -> dict[str, object]:
    operand = instruction.operand
    if isinstance(operand, (IntValue, BoolValue)):
        operand = value_data(operand)
    return {
        "pc": pc, "op": instruction.op.name, "operand": operand,
        **instruction.position.as_dict(),
    }


def disassembly_data(code: Bytecode) -> list[dict[str, object]]:
    validate(code)
    return [instruction_data(pc, instruction) for pc, instruction in enumerate(code.instructions)]


def _node_data(node: ast.Statement | ast.Expression) -> dict[str, object]:
    data = {"kind": type(node).__name__, "token": token_data(node.anchor)}
    match node:
        case ast.Let(name=name, annotation=annotation, initializer=initializer):
            data.update(
                name=token_data(name),
                annotation=token_data(annotation),
                initializer=_node_data(initializer),
            )
        case ast.Print(value=value) | ast.Assignment(value=value):
            data.update(value=_node_data(value))
        case ast.Block(statements=statements):
            data.update(statements=[_node_data(statement) for statement in statements])
        case ast.If(condition=condition, then_block=then_block, else_block=else_block):
            data.update(
                condition=_node_data(condition),
                then_block=_node_data(then_block),
                else_block=_node_data(else_block) if else_block is not None else None,
            )
        case ast.While(condition=condition, body=body):
            data.update(condition=_node_data(condition), body=_node_data(body))
        case ast.IntegerLiteral():
            # Preserve decimal values in JSON without relying on Python's digit limit.
            data.update(value=node.anchor.lexeme.lstrip("0") or "0")
        case ast.BooleanLiteral(value=value):
            data.update(value=value)
        case ast.Unary(operand=operand):
            data.update(operand=_node_data(operand))
        case ast.Binary(left=left, right=right):
            data.update(left=_node_data(left), right=_node_data(right))
    return data
