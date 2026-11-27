from dataclasses import replace
from typing import assert_never

from . import ast_nodes as ast
from .bytecode import Bytecode, Instruction, Op, Operand
from .diagnostics import Position
from .resolver import Resolution
from .values import BoolValue, IntValue, ValueType


def compile_program(program: ast.Program, resolution: Resolution, eof: Position) -> Bytecode:
    compiler = _Compiler(resolution)
    for statement in program.statements:
        compiler.statement(statement)
    compiler.emit(Op.HALT, eof)
    slot_types = tuple(ValueType(node.annotation.kind) for node in resolution.declarations)
    return Bytecode(tuple(compiler.instructions), slot_types)


class _Compiler:
    def __init__(self, resolution: Resolution) -> None:
        self.resolution = resolution
        self.instructions: list[Instruction] = []

    def emit(self, op: Op, position: Position, operand: Operand = None) -> int:
        pc = len(self.instructions)
        self.instructions.append(Instruction(op, operand, position))
        return pc

    def patch_jump(self, pc: int) -> None:
        self.instructions[pc] = replace(self.instructions[pc], operand=len(self.instructions))

    def statement(self, statement: ast.Statement) -> None:
        position = statement.anchor.position
        match statement:
            case ast.Let(name=name, initializer=expression) | ast.Assignment(anchor=name, value=expression):
                self.expression(expression)
                self.emit(Op.STORE, name.position, self.resolution.declaration_id(name))
            case ast.Print(value=expression):
                self.expression(expression)
                self.emit(Op.PRINT, position)
            case ast.Block(statements=statements):
                for child in statements:
                    self.statement(child)
            case ast.If(condition=condition, then_block=then_block, else_block=else_block):
                self.expression(condition)
                branch = self.emit(Op.JUMP_IF_FALSE, position)
                self.statement(then_block)
                if else_block is None:
                    self.patch_jump(branch)
                else:
                    skip_else = self.emit(Op.JUMP, position)
                    self.patch_jump(branch)
                    self.statement(else_block)
                    self.patch_jump(skip_else)
            case ast.While(condition=condition, body=body):
                start = len(self.instructions)
                self.expression(condition)
                branch = self.emit(Op.JUMP_IF_FALSE, position)
                self.statement(body)
                self.emit(Op.JUMP, position, start)
                self.patch_jump(branch)
            case _:
                assert_never(statement)

    def expression(self, expression: ast.Expression) -> None:
        anchor = expression.anchor
        match expression:
            case ast.IntegerLiteral(value=value):
                self.emit(Op.PUSH, anchor.position, IntValue(value))
            case ast.BooleanLiteral(value=value):
                self.emit(Op.PUSH, anchor.position, BoolValue(value))
            case ast.Variable():
                self.emit(Op.LOAD, anchor.position, self.resolution.declaration_id(anchor))
            case ast.Unary(operand=operand):
                self.expression(operand)
                self.emit(Op.UNARY, anchor.position, anchor.kind)
            case ast.Binary(left=left, right=right):
                self.expression(left)
                self.expression(right)
                self.emit(Op.BINARY, anchor.position, anchor.kind)
            case _:
                assert_never(expression)
