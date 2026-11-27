import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import test_phase2 as phase2
from tiny_language.__main__ import main
from tiny_language.bytecode import Bytecode, Instruction, Op, validate
from tiny_language.diagnostics import DiagnosticError, Position
from tiny_language.values import BoolValue, IntValue, ValueType
from tiny_language.vm import execute


class VMExecutionTests(phase2.ExecutionTests):
    """Run the same execution cases against VM, with separate instruction-count oracles."""

    def run_file(self, path, *python_args):
        return self.cli(path, "run", "--engine", "vm", python_args=python_args)

    def cli(self, path, *args, python_args=()):
        return subprocess.run(
            [sys.executable, "-B", *python_args, "-m", "tiny_language", *args, str(path)],
            cwd=phase2.PROJECT, capture_output=True, timeout=10,
        )

    def write_source(self, source):
        path = self.workspace / "vm.tiny"
        path.write_bytes(source)
        return path

    def test_all_phase0_programs_match_fixed_expectations(self):
        expected = json.loads((phase2.EXAMPLES / "expected.json").read_bytes())
        self.assertEqual(set(expected), {path.name for path in phase2.EXAMPLES.glob("*.tiny")})
        for filename, case in expected.items():
            with self.subTest(file=filename):
                error = case["diagnostic"]
                if filename == "step_limit.tiny":
                    # PUSH, JUMP_IF_FALSE, JUMP; after 33,333 cycles only PUSH fits.
                    error = {**error, "line": 1, "column": 1, "span": [0, 5]}
                self.assert_result(
                    self.run_file(phase2.EXAMPLES / filename), case["stdout"].encode("utf-8"),
                    status=case["exit_code"], error=error,
                )

    def test_finite_step_boundaries(self):
        # 2 (initialization) + 9*11110 (iterations) + 4 (false condition) = 99996.
        loop = b"let i: int = 0; while (i < 11110) { i = i + 1; }"
        for suffix, stdout, needed in [
            (b" print 7;", b"7\n", 99_999),
            (b" print -7;", b"-7\n", 100_000),
            (b" print 7 + 0;", b"7\n", 100_001),
        ]:
            with self.subTest(instructions=needed):
                source = loop + suffix
                path = self.write_source(source)
                self.assert_result(self.cli(path, "run", "--engine", "ast"), stdout)
                if needed <= 100_000:
                    self.assert_result(self.run_file(path), stdout)
                else:
                    self.assert_error_at(source, "E_LIMIT", "runtime", len(source), 0, stdout=stdout)

    def test_step_limit_stops_before_the_next_node_and_preserves_output(self):
        loop = b"let i: int = 0; while (i < 11110) { i = i + 1; }"
        for suffix, anchor, width in [
            (b" print 7; print -8;", b"print", 5),
            (b" print 7; print 9 / 0;", b"/", 1),
        ]:
            with self.subTest(suffix=suffix):
                source = loop + suffix
                self.assert_error_at(
                    source, "E_LIMIT", "runtime", source.rindex(anchor), width, stdout=b"7\n"
                )

    def test_phase2_ast_step_inputs_with_vm_instruction_counts(self):
        loop = b"let i: int = 0; while (i < 12499) { i = i + 1; }"
        # 2 + 9*11110 = 99992. The next 8 instructions finish at STORE;
        # instruction 100001 is the back edge, before any trailing statement.
        for suffix in [b"{}", b"{}{}", b"{}{}{}", b" print 7; print 8;",
                       b"{}print (7);", b"print 1 / 0;"]:
            with self.subTest(suffix=suffix):
                self.assert_error_at(loop + suffix, "E_LIMIT", "runtime", loop.index(b"while"), 5)

    def test_additional_phase1_boundary_sources_through_vm(self):
        for size in [65_535, 65_536]:
            pairs, remainder = divmod(size - 2, 2)
            self.assert_result(self.run_source(b"//" + b"\xc3\xa9" * pairs + b" " * remainder))
        self.assert_result(
            self.run_source(b" " * 65_537), status=2, error=phase2.diagnostic("E_LIMIT", "input"),
        )
        source = b"@" + b" " * 65_535 + b"\xff"
        self.assert_result(self.run_source(source), status=2, error=phase2.diagnostic("E_LIMIT", "input"))
        source = b"{}" * 4095 + b"{"
        self.assert_error_at(source, "E_PARSE", "parse", 8191, 0)

        for depth in [63, 64]:
            self.assert_result(self.run_source(b"{" * depth + b"}" * depth))
        self.assert_error_at(b"{" * 65 + b"}" * 65, "E_LIMIT", "parse", 64, 1)
        for count, start, width in [(63, 0, 5), (64, 6, 1), (65, 70, 1)]:
            self.assert_error_at(b"print " + b"-" * count + b"1;", "E_LIMIT", "parse", start, width)
        for keyword in [b"if", b"while"]:
            source = b"{" * 62 + keyword + b"(((!true))){}" + b"}" * 62
            self.assert_error_at(source, "E_LIMIT", "parse", 65 + len(keyword), 1)
            # The original successful while variant has a constant true condition.
            without_negation = source.replace(b"!", b"")
            if keyword == b"if":
                self.assert_result(self.run_source(without_negation))
            else:
                self.assert_error_at(without_negation, "E_LIMIT", "runtime", 62, 5)
        source = b"if(true){" * 32 + b"print 1;" + b"}" * 32
        self.assert_error_at(source, "E_LIMIT", "parse", 8, 1)

        largest = phase2.decimal_bytes(phase2.MAX_INTEGER)
        for ending, expected in [(b"", b"0"), (b"1", b"1"), (largest, largest)]:
            source = b"print " + b"0" * 5000 + ending + b";"
            self.assert_result(self.run_source(source, "-X", "int_max_str_digits=640"), expected + b"\n")
        overflow = phase2.decimal_bytes(phase2.MAX_INTEGER + 1)
        source = b"if(false){print " + b"0" * 5000 + overflow + b";}"
        self.assert_error_at(source, "E_LIMIT", "lex", 16, 5000 + len(overflow))
        self.assert_error_at(b"print " + b"9" * 5000 + b";", "E_LIMIT", "lex", 6, 5000)
        result = self.run_source(b"{}" * 4096 + overflow)
        self.assertIn("tokens", json.loads(result.stderr)["message"])
        self.assert_result(
            result, status=2, error=dict(
                code="E_LIMIT", phase="lex", line=1, column=8193, span=[8192, 8192 + len(overflow)]
            ),
        )

    def test_phase1_utf8_and_syntax_diagnostic_sources_through_vm(self):
        for character, length in [("\r", 1), ("\ufeff", 3), ("\v", 1), ("é", 2), ("😀", 4), ("٢", 2)]:
            with self.subTest(character=character):
                self.assert_error_at(character.encode("utf-8"), "E_LEX", "lex", 0, length)
        for source, line, column, span in [
            (b"//\xc3\xa9\r\n\tA\xe2\x82", 2, 3, [8, 9]),
            (b"//\xc3x\xff", 1, 3, [2, 3]),
        ]:
            with self.subTest(source=source):
                self.assert_result(
                    self.run_source(source), status=2, error=dict(
                        code="E_INPUT", phase="input", line=line, column=column, span=span
                    ),
                )
        for source, start, width in [
            (b"print 1 < 2 >= 3;", 12, 2), (b"print 1 == 2 != false;", 13, 2),
            (b"if (true != false == false) {}", 18, 2),
            (b"let true: int = 1;", 4, 4), (b"let x: int = 1", 14, 0), (b"}", 0, 1),
            (b"{\r\n", 3, 0), (b"if (true) print 1;", 10, 5),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, "E_PARSE", "parse", start, width)

    def test_disassembly_and_trace_match_hand_written_instruction_sequence(self):
        source = (
            "// 日本語\r\nlet x: int = 0;\r\nwhile (x < 1) {\r\n"
            "  if (x == 0) { x = -1 + 2; } else { x = 9; }\r\n"
            "}\r\nprint x;\r\n"
        ).encode("utf-8")
        path = self.write_source(source)

        def integer(number):
            return {"type": "int", "value": str(number)}

        # Column and width refer to original tokens, with byte offsets including CRLF and UTF-8.
        rows = [
            ("PUSH", integer(0), 2, 14, 1), ("STORE", 0, 2, 5, 1),
            ("LOAD", 0, 3, 8, 1), ("PUSH", integer(1), 3, 12, 1),
            ("BINARY", "<", 3, 10, 1), ("JUMP_IF_FALSE", 19, 3, 1, 5),
            ("LOAD", 0, 4, 7, 1), ("PUSH", integer(0), 4, 12, 1),
            ("BINARY", "==", 4, 9, 2), ("JUMP_IF_FALSE", 16, 4, 3, 2),
            ("PUSH", integer(1), 4, 22, 1), ("UNARY", "-", 4, 21, 1),
            ("PUSH", integer(2), 4, 26, 1), ("BINARY", "+", 4, 24, 1),
            ("STORE", 0, 4, 17, 1), ("JUMP", 18, 4, 3, 2),
            ("PUSH", integer(9), 4, 42, 1), ("STORE", 0, 4, 38, 1),
            ("JUMP", 2, 3, 1, 5), ("LOAD", 0, 6, 7, 1), ("PRINT", None, 6, 1, 5),
            ("HALT", None, 7, 1, 0),
        ]
        lines = source.splitlines(keepends=True)
        expected = []
        for pc, (op, operand, line, column, width) in enumerate(rows):
            start = sum(map(len, lines[:line - 1])) + column - 1
            expected.append(dict(pc=pc, op=op, operand=operand, line=line, column=column,
                                 span=[start, start + width]))
        result = self.cli(path, "--disassemble")
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout), expected)

        result = self.cli(path, "run", "--engine", "vm", "--trace")
        self.assertEqual((result.returncode, result.stdout), (0, b"1\n"))
        trace = [json.loads(line) for line in result.stderr.splitlines()]
        pcs = [*range(16), 18, 2, 3, 4, 5, 19, 20, 21]
        self.assertEqual([row["pc"] for row in trace], pcs)
        self.assertEqual([row["step"] for row in trace], list(range(1, len(pcs) + 1)))
        self.assertEqual([row["next_pc"] for row in trace], pcs[1:] + [None])
        self.assertEqual(
            [{key: row[key] for key in expected[0]} for row in trace], [expected[pc] for pc in pcs]
        )
        self.assertEqual([row["store"] for row in trace if row["store"] is not None],
                         [{"slot": 0, **integer(0)}, {"slot": 0, **integer(1)}])
        self.assertEqual([trace[pc]["stack"] for pc in [0, 1, 4, 5, 12, 13, 14]],
                         [[integer(0)], [], [{"type": "bool", "value": True}], [],
                          [integer(-1), integer(2)], [integer(1)], []])
        self.assertEqual(trace[-1]["stack"], [])

    def test_disassembly_and_trace_before_executing_a_failing_program(self):
        source = b"print 7; print 1 / 0;"
        path = self.write_source(source)
        result = self.cli(path, "--disassemble")
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual([row["op"] for row in json.loads(result.stdout)],
                         ["PUSH", "PRINT", "PUSH", "PUSH", "BINARY", "PRINT", "HALT"])
        result = self.cli(path, "run", "--engine", "vm", "--trace")
        self.assertEqual((result.returncode, result.stdout), (3, b"7\n"))
        *trace, error = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual([row["pc"] for row in trace], [0, 1, 2, 3])
        self.assertTrue(error.pop("message"))
        self.assertEqual(error, phase2.diagnostic("E_ZERO_DIVISION", "runtime", source, 17, 1))

    def test_disassembly_and_trace_share_pre_execution_diagnostics(self):
        source = b"print 7; let x: int = true; print y;"
        path = self.write_source(source)
        error = phase2.diagnostic("E_UNDEFINED", "resolve", source, source.index(b"y"), 1)
        for flags in [("--disassemble",), ("run", "--engine", "vm", "--trace")]:
            with self.subTest(flags=flags):
                self.assert_result(self.cli(path, *flags), status=2, error=error)

    def test_large_integer_in_disassembly_and_trace_with_host_digit_limit(self):
        digits = phase2.decimal_bytes(phase2.MAX_INTEGER)
        path = self.write_source(b"print " + digits + b";")
        host_limit = ("-X", "int_max_str_digits=640")
        disassembly = self.cli(path, "--disassemble", python_args=host_limit)
        self.assertEqual((disassembly.returncode, disassembly.stderr), (0, b""))
        expected = {"type": "int", "value": digits.decode("ascii")}
        self.assertEqual(json.loads(disassembly.stdout)[0]["operand"], expected)
        trace = self.cli(path, "run", "--engine", "vm", "--trace", python_args=host_limit)
        self.assertEqual((trace.returncode, trace.stdout), (0, digits + b"\n"))
        self.assertEqual(json.loads(trace.stderr.splitlines()[0])["stack"], [expected])

    def test_closed_trace_pipe_returns_internal_status(self):
        path = self.write_source(b"print 7;")
        reader, writer = os.pipe()
        os.close(reader)
        with os.fdopen(writer, "wb") as stderr:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tiny_language", "run",
                 "--engine", "vm", "--trace", str(path)],
                cwd=phase2.PROJECT, stdout=subprocess.PIPE, stderr=stderr, timeout=10,
            )
        self.assertEqual((result.returncode, result.stdout), (4, b""))


class BytecodeValidationTests(unittest.TestCase):
    @staticmethod
    def code(*instructions, slots=()):
        return Bytecode(
            tuple(Instruction(op, operand, Position(1, pc + 1, pc, pc if op is Op.HALT else pc + 1))
                  for pc, (op, operand) in enumerate(instructions)),
            slots,
        )

    def assert_internal(self, action, code, pc, message):
        with self.assertRaises(DiagnosticError) as raised:
            action()
        error = raised.exception
        self.assertEqual((error.code, error.phase, error.exit_code), ("E_INTERNAL", "internal", 4))
        position = None if pc is None else code.instructions[pc].position
        self.assertEqual(error.position, position)
        self.assertIn(message, str(error))

    def test_invalid_opcodes_operands_and_ranges(self):
        for op, operand, message in [
            ("PUSH", IntValue(1), "Unknown"),
            (Op.PUSH, 1, "Invalid operand"),
            (Op.PUSH, IntValue(True), "Invalid operand"),
            (Op.PUSH, IntValue(1 << 4096), "Invalid operand"),
            (Op.PUSH, BoolValue(1), "Invalid operand"),
            (Op.UNARY, "+", "Invalid operand"),
            (Op.BINARY, "!", "Invalid operand"),
            (Op.PRINT, 0, "Invalid operand"),
            (Op.LOAD, -1, "Invalid operand"),
            (Op.STORE, 1, "Invalid operand"),
            (Op.STORE, True, "Invalid operand"),
            (Op.JUMP, -1, "Invalid operand"),
            (Op.JUMP_IF_FALSE, 2, "Invalid operand"),
            (Op.JUMP, False, "Invalid operand"),
        ]:
            with self.subTest(op=op, operand=operand):
                code = self.code((op, operand), (Op.HALT, None), slots=(ValueType.INT,))
                self.assert_internal(lambda: validate(code), code, 0, message)
        code = self.code((Op.HALT, None), slots=("int",))
        self.assert_internal(lambda: validate(code), code, None, "slot type")

    def test_stack_underflow_on_pops(self):
        for instruction in [(Op.STORE, 0), (Op.UNARY, "-"), (Op.BINARY, "+"),
                            (Op.JUMP_IF_FALSE, 1), (Op.PRINT, None)]:
            with self.subTest(instruction=instruction):
                code = self.code(instruction, (Op.HALT, None), slots=(ValueType.INT,))
                self.assert_internal(lambda: validate(code), code, 0, "underflow")

    def test_inconsistent_depth_at_forward_merge_and_back_edge(self):
        for target in [0, 4]:
            with self.subTest(target=target):
                code = self.code((Op.PUSH, BoolValue(False)), (Op.JUMP_IF_FALSE, 4),
                                 (Op.PUSH, IntValue(1)), (Op.JUMP, target), (Op.HALT, None))
                self.assert_internal(lambda: validate(code), code, target, "Inconsistent stack depth")

    def test_invalid_termination_and_source_location(self):
        for code, pc, message in [
            (self.code(), None, "Empty"),
            (self.code((Op.PRINT, None)), 0, "at the end"),
            (self.code((Op.HALT, None), (Op.HALT, None)), 0, "at the end"),
            (self.code((Op.HALT, 0)), 0, "Invalid operand"),
            (self.code((Op.PUSH, IntValue(1)), (Op.HALT, None)), 1, "empty stack"),
            (self.code((Op.JUMP, 0), (Op.HALT, None)), 1, "Unreachable"),
            (Bytecode((Instruction(Op.HALT, None, Position(0, 1, 0, 0)),), ()), None,
             "source position"),
        ]:
            with self.subTest(code=code):
                self.assert_internal(lambda: validate(code), code, pc, message)

    def test_halt_requires_an_empty_source_span(self):
        code = Bytecode((Instruction(Op.HALT, None, Position(1, 1, 0, 1)),), ())
        self.assert_internal(lambda: validate(code), code, 0, "empty source span")

    def test_cli_rejects_halt_with_nonempty_source_span(self):
        code = Bytecode((Instruction(Op.HALT, None, Position(1, 1, 0, 1)),), ())
        for flags in [("run", "--engine", "vm", "--trace"), ("--disassemble",)]:
            with (
                self.subTest(flags=flags),
                patch("tiny_language.__main__.compile_program", return_value=code),
                patch("tiny_language.__main__._write_stdout") as output,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(main([*flags, str(phase2.EXAMPLES / "arithmetic.tiny")]), 4)
                output.assert_not_called()
                error = json.loads(stderr.getvalue())
                self.assertIn("empty source span", error.pop("message"))
                self.assertEqual(error, dict(code="E_INTERNAL", phase="internal",
                                             line=1, column=1, span=[0, 1]))

    def test_runtime_internal_checks_keep_instruction_position(self):
        for prefix, pc, message in [
            ([(Op.LOAD, 0), (Op.PRINT, None)], 0, "Uninitialized slot"),
            ([(Op.PUSH, BoolValue(True)), (Op.STORE, 0)], 1, "Incorrect slot type"),
            ([(Op.PUSH, IntValue(1)), (Op.JUMP_IF_FALSE, 2)], 1, "Invalid condition type"),
            ([(Op.PUSH, BoolValue(True)), (Op.UNARY, "-"), (Op.PRINT, None)], 1, "Invalid values"),
            ([(Op.PUSH, BoolValue(True)), (Op.PUSH, IntValue(1)),
              (Op.BINARY, "=="), (Op.PRINT, None)], 2, "Invalid values"),
        ]:
            with self.subTest(prefix=prefix):
                code = self.code(*prefix, (Op.HALT, None), slots=(ValueType.INT,))
                output = []
                self.assert_internal(lambda: execute(code, output.append), code, pc, message)
                self.assertEqual(output, [])

    def test_cli_rejects_invalid_bytecode_before_printing_or_tracing(self):
        code = self.code((Op.PUSH, IntValue(7)), (Op.PRINT, None),
                         (Op.JUMP, 99), (Op.HALT, None))
        for flags in [("run", "--engine", "vm", "--trace"), ("--disassemble",)]:
            with (
                self.subTest(flags=flags),
                patch("tiny_language.__main__.compile_program", return_value=code),
                patch("tiny_language.__main__._write_stdout") as output,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                status = main([*flags, str(phase2.EXAMPLES / "arithmetic.tiny")])
                self.assertEqual(status, 4)
                output.assert_not_called()
                error = json.loads(stderr.getvalue())
                self.assertIn("Invalid operand", error.pop("message"))
                self.assertEqual(error, dict(code="E_INTERNAL", phase="internal",
                                             line=1, column=3, span=[2, 3]))
