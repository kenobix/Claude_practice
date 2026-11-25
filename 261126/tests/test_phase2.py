import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from math import floor
from pathlib import Path

from tiny_language.parser import parse
from tiny_language.resolver import resolve
from tiny_language.scanner import scan

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT / "examples"
MAX_INTEGER = (1 << 4096) - 1


def decimal_bytes(number: int) -> bytes:
    return str(Decimal(number)).encode("ascii")


def diagnostic(
    code: str, phase: str, source: bytes = b"", start: int | None = None, width: int = 0
) -> dict:
    if start is None:
        return dict(code=code, phase=phase, line=None, column=None, span=None)
    prefix = source[:start]
    column = len(prefix.split(b"\n")[-1].decode("utf-8")) + 1
    return dict(
        code=code, phase=phase, line=prefix.count(b"\n") + 1, column=column,
        span=[start, start + width],
    )


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_file(self, path: Path, *python_args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-B", *python_args, "-m", "tiny_language",
             "run", "--engine", "ast", str(path)],
            cwd=PROJECT, capture_output=True, timeout=10,
        )

    def run_source(self, source: bytes, *python_args: str) -> subprocess.CompletedProcess[bytes]:
        path = self.workspace / "source.tiny"
        path.write_bytes(source)
        return self.run_file(path, *python_args)

    def assert_result(
        self, result: subprocess.CompletedProcess[bytes], stdout: bytes = b"", *,
        status: int = 0, error: dict | None = None,
    ) -> None:
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertEqual(result.stdout, stdout)
        if error is None:
            self.assertEqual(result.stderr, b"")
        else:
            self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
            actual = json.loads(result.stderr)
            self.assertTrue(actual.pop("message").strip())
            self.assertEqual(actual, error)

    def assert_error_at(
        self, source: bytes, code: str, phase: str, start: int, width: int, *,
        stdout: bytes = b"", python_args: tuple[str, ...] = (),
    ) -> None:
        status = 3 if phase == "runtime" else 2
        error = diagnostic(code, phase, source, start, width)
        self.assert_result(self.run_source(source, *python_args), stdout, status=status, error=error)

    def test_all_phase0_programs_match_fixed_expectations(self):
        expectations = json.loads((EXAMPLES / "expected.json").read_bytes())
        self.assertEqual(set(expectations), {path.name for path in EXAMPLES.glob("*.tiny")})
        for filename, expected in expectations.items():
            with self.subTest(file=filename):
                error = expected["diagnostic"]
                if "ast_position" in expected:
                    error = {**error, **expected["ast_position"]}
                self.assert_result(
                    self.run_file(EXAMPLES / filename), expected["stdout"].encode("utf-8"),
                    status=expected["exit_code"], error=error,
                )

    def test_declaration_ids_follow_lexical_scope_and_source_order(self):
        source = (
            b"let x: int = 7; { print x; x = 8; let x: bool = true; print x; x = false; } "
            b"{ let x: int = 2; print x; } print x;"
        )
        program = parse(scan(source.decode("ascii")))
        outer, inner_block, sibling_block, outer_print = program.statements
        before, outer_write, inner, after, inner_write = inner_block.statements
        sibling, sibling_print = sibling_block.statements
        binding = resolve(program)
        self.assertEqual(binding.declarations, (outer, inner, sibling))
        occurrences = [
            outer.name, before.value.anchor, outer_write.anchor, inner.name, after.value.anchor,
            inner_write.anchor, sibling.name, sibling_print.value.anchor, outer_print.value.anchor,
        ]
        self.assertEqual(
            [binding.declaration_id(name) for name in occurrences], [0, 0, 0, 1, 1, 1, 2, 2, 0]
        )
        self.assert_result(self.run_source(source), b"7\ntrue\n2\n8\n")

    def test_scope_exit_and_first_initialization_errors(self):
        for source, code, fragment, width in [
            (b"{ let x: int = 1; } print x;", "E_UNDEFINED", b"x;", 1),
            (b"{ print x; let x: int = 1; }", "E_UNDEFINED", b"x;", 1),
            (b"let x: int = x + absent;", "E_SELF_REFERENCE", b"x +", 1),
            (b"let x: int = 1; { let x: int = x; }", "E_SELF_REFERENCE", b"x;", 1),
            (b"let x: int = 1; let x: int = missing;", "E_REDECLARATION", b"x: int = missing", 1),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, code, "resolve", source.index(fragment), width)

    def test_resolver_checks_the_whole_program_before_type_checking(self):
        source = b"print 7; let x: int = true; if (false) { print missing; }"
        self.assert_error_at(source, "E_UNDEFINED", "resolve", source.index(b"missing"), 7)
        source = b"print 7; while (false) { print missing; }"
        self.assert_error_at(source, "E_UNDEFINED", "resolve", source.index(b"missing"), 7)

    def test_name_resolution_order_with_multiple_candidates(self):
        for source, name in [
            (b"target = right;", b"target"),
            (b"print left + right;", b"left"),
            (b"if (condition) { print yes; } else { print no; }", b"condition"),
            (b"if (false) { print yes; } else { print no; }", b"yes"),
            (b"if (true) {} else { print missing; }", b"missing"),
            (b"while (condition) { print body; }", b"condition"),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, "E_UNDEFINED", "resolve", source.index(name), len(name))

    def test_type_mismatches_are_not_coerced(self):
        for source, fragment, width in [
            (b"let x: bool = 1;", b"x:", 1),
            (b"let x: int = 0; x = false;", b"x =", 1),
            (b"let x: int = 0; { let x: bool = true; x = 1; }", b"x =", 1),
            (b"print -true;", b"-", 1),
            (b"print !1;", b"!", 1),
            (b"print false + false;", b"+", 1),
            (b"print 1 / false;", b"/", 1),
            (b"print true < 1;", b"<", 1),
            (b"print true >= false;", b">=", 2),
            (b"print true == 1;", b"==", 2),
            (b"print 0 != false;", b"!=", 2),
            (b"if (0) {}", b"if", 2),
            (b"while (1) {}", b"while", 5),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, "E_TYPE", "type", source.index(fragment), width)

    def test_type_check_order_and_dead_branches(self):
        for source, fragment, width in [
            (b"print (!2) + (-false);", b"!", 1),
            (b"print true + !2;", b"!", 1),
            (b"let x: int = 0; x = !2;", b"!", 1),
            (b"if (1 + true) {}", b"+", 1),
            (b"print 7; if (1) { print !2; } else { print -false; }", b"if", 2),
            (b"if (false) { print !2; } else { print -false; }", b"!", 1),
            (b"if (true) {} else { print -false; }", b"-", 1),
            (b"while (false) { print -false; }", b"-", 1),
            (b"while (1) { print -false; }", b"while", 5),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, "E_TYPE", "type", source.index(fragment), width)
        source = b"print 1 / 0; print -false;"
        self.assert_error_at(source, "E_TYPE", "type", source.index(b"-"), 1)

    def test_binary_evaluation_order_and_partial_output(self):
        large = decimal_bytes(MAX_INTEGER)
        for expression, fragment, code in [
            (b"(8 / 0) + (2 % 0)", b"/", "E_ZERO_DIVISION"),
            (b"8 / (2 % 0)", b"%", "E_ZERO_DIVISION"),
            (b"(" + large + b" + 1) - (2 % 0)", b"+", "E_LIMIT"),
            (b"(2 % 0) - (" + large + b" + 1)", b"%", "E_ZERO_DIVISION"),
        ]:
            source = b"print 7; print " + expression + b"; print 9;"
            with self.subTest(fragment=fragment, code=code):
                self.assert_error_at(
                    source, code, "runtime", source.index(fragment), 1, stdout=b"7\n"
                )

    def test_all_comparisons_tagged_values_and_arithmetic(self):
        source = (
            b"print false == false; print true != false; print 2 == 2; print 2 != 2;"
            b"print 2 < 3; print 2 <= 2; print 3 > 2; print 2 >= 3;"
            b"print !true; print --3; print -0; print 5 + 6 * 7 - 8;"
        )
        expected = b"true\ntrue\ntrue\nfalse\ntrue\ntrue\ntrue\nfalse\nfalse\n3\n0\n39\n"
        self.assert_result(self.run_source(source), expected)

    def test_floor_division_and_remainder_with_both_signs(self):
        cases = [(7, 3), (-7, 3), (7, -3), (-7, -3), (6, -3), (0, -3)]
        cases += [(MAX_INTEGER, -3), (-MAX_INTEGER, 3)]
        source = bytearray()
        expected = bytearray()
        for dividend, divisor in cases:
            left, right = decimal_bytes(dividend), decimal_bytes(divisor)
            source += b"print " + left + b" / " + right + b";"
            source += b"print " + left + b" % " + right + b";"
            quotient = floor(Fraction(dividend, divisor))
            remainder = dividend - quotient * divisor
            expected += decimal_bytes(quotient) + b"\n" + decimal_bytes(remainder) + b"\n"
        self.assert_result(self.run_source(bytes(source), "-X", "int_max_str_digits=640"), bytes(expected))

    def test_loop_reinitializes_local_values_and_updates_outer_values(self):
        source = (
            b"let i: int = 0; let total: int = 0;"
            b"while (i < 3) { let current: int = i; let fresh: bool = true;"
            b"  if (fresh == true) { total = total + current; } else { print 99; }"
            b"  current = 99; fresh = false; i = i + 1; }"
            b"if (false) { print 1 / 0; } else { print total; }"
            b"if (false) { print 99; } while (false) { print 99; } print i;"
        )
        self.assert_result(self.run_source(source), b"3\n3\n")

    def test_lf_output_and_runtime_location_with_crlf_source(self):
        source = "// 日本語\r\nprint 5;\r\n\tprint 3 % 0;\r\nprint 6;".encode("utf-8")
        result = self.run_source(source)
        error = dict(code="E_ZERO_DIVISION", phase="runtime", line=3, column=10, span=[33, 34])
        self.assert_result(result, b"5\n", status=3, error=error)

    def test_integer_literal_limits_through_execution(self):
        python_args = ("-X", "int_max_str_digits=640")
        for number in [(1 << 4095) - 1, MAX_INTEGER]:
            with self.subTest(bits=number.bit_length()):
                digits = decimal_bytes(number)
                program = b"print " + digits + b"; print -" + digits + b";"
                self.assert_result(self.run_source(program, *python_args), digits + b"\n-" + digits + b"\n")
        overflow = decimal_bytes(MAX_INTEGER + 1)
        for prefix, suffix in [(b"print ", b";"), (b"if (false) { print ", b"; }")]:
            source = prefix + overflow + suffix
            with self.subTest(prefix=prefix):
                self.assert_error_at(source, "E_LIMIT", "lex", len(prefix), len(overflow),
                                     python_args=python_args)
        source = b"print " + b"0" * 5000 + b"1;"
        self.assert_result(self.run_source(source, *python_args), b"1\n")

    def test_integer_operation_limits_and_skipped_operations(self):
        for left in [MAX_INTEGER - 2, MAX_INTEGER - 1]:
            with self.subTest(left_offset=MAX_INTEGER - left):
                source = b"print " + decimal_bytes(left) + b" + 1;"
                result = self.run_source(source, "-X", "int_max_str_digits=640")
                self.assert_result(result, decimal_bytes(left + 1) + b"\n")
        largest = decimal_bytes(MAX_INTEGER)
        for expression, operator in [
            (largest + b" + 1", b"+"), (b"-" + largest + b" - 1", b" - "),
            (largest + b" * 2", b"*"),
        ]:
            with self.subTest(operator=operator):
                source = b"print 1; print " + expression + b";"
                start = source.index(operator) + (1 if operator == b" - " else 0)
                self.assert_error_at(source, "E_LIMIT", "runtime", start, 1, stdout=b"1\n")
        source = b"if (false) { print " + largest + b" + 1; }"
        self.assert_result(self.run_source(source))

    def test_output_line_boundaries(self):
        for lines in [1023, 1024, 1025]:
            with self.subTest(lines=lines):
                source = self.output_loop(lines)
                expected = self.number_lines(min(lines, 1024))
                if lines <= 1024:
                    self.assert_result(self.run_source(source), expected)
                else:
                    self.assert_error_at(
                        source, "E_LIMIT", "runtime", source.index(b"print"), 5, stdout=expected
                    )

    @staticmethod
    def output_loop(lines: int) -> bytes:
        return f"let i: int = 0; while (i < {lines}) {{ print i; i = i + 1; }}".encode("ascii")

    @staticmethod
    def number_lines(count: int) -> bytes:
        return "".join(f"{number}\n" for number in range(count)).encode("ascii")

    def test_print_evaluates_expression_before_output_limit(self):
        prefix = self.output_loop(1024) + b"\n"
        expected = self.number_lines(1024)
        source = prefix + b"print 9 / 0; print 0;"
        self.assert_error_at(
            source, "E_ZERO_DIVISION", "runtime", source.index(b"/"), 1, stdout=expected
        )
        source = prefix + b"print " + decimal_bytes(MAX_INTEGER) + b" + 1;"
        self.assert_error_at(source, "E_LIMIT", "runtime", source.rindex(b"+"), 1, stdout=expected)

    def test_finite_step_boundaries(self):
        # Initial let, while and final false condition take 6 steps; each iteration takes 8.
        loop = b"let i: int = 0; while (i < 12499) { i = i + 1; }"
        for blocks in [1, 2, 3]:
            with self.subTest(blocks=blocks):
                source = loop + b"{}" * blocks
                if blocks <= 2:
                    self.assert_result(self.run_source(source))
                else:
                    self.assert_error_at(source, "E_LIMIT", "runtime", len(loop) + 4, 1)

    def test_step_limit_stops_before_the_next_node_and_preserves_output(self):
        loop = b"let i: int = 0; while (i < 12499) { i = i + 1; }"
        source = loop + b" print 7; print 8;"
        self.assert_error_at(source, "E_LIMIT", "runtime", source.rindex(b"print"), 5, stdout=b"7\n")
        source = loop + b"{}print (7);"
        self.assert_error_at(source, "E_LIMIT", "runtime", source.index(b"(7)") + 1, 1)
        source = loop + b"print 1 / 0;"
        self.assert_error_at(source, "E_LIMIT", "runtime", source.index(b"1 /"), 1)

    def test_entry_point_preserves_front_end_precedence_and_locations(self):
        result = self.run_file(self.workspace / "missing.tiny")
        self.assert_result(result, status=2, error=diagnostic("E_INPUT", "input"))
        for source, code, phase, start, width in [
            (b"//\r\n\xff", "E_INPUT", "input", 4, 1),
            (b"print ; @", "E_LEX", "lex", 8, 1),
            (b"print ; @\xff", "E_INPUT", "input", 9, 1),
            (b"print ; print missing;", "E_PARSE", "parse", 6, 1),
            ((EXAMPLES / "lex_character.tiny").read_bytes().replace(b"\n", b"\r\n"),
             "E_LEX", "lex", 21, 3),
        ]:
            with self.subTest(source=source):
                self.assert_error_at(source, code, phase, start, width)
        self.assert_result(self.run_source(b""))
        source = "let if1: int = 4; print if1 / 2 <= 3;//終端".encode("utf-8")
        self.assert_result(self.run_source(source), b"true\n")

    def test_source_and_token_boundaries_through_execution(self):
        for size in [65_535, 65_536]:
            with self.subTest(size=size):
                self.assert_result(self.run_source(b" " * size))
        result = self.run_source(b" " * 65_536 + b"\xff")
        self.assert_result(result, status=2, error=diagnostic("E_LIMIT", "input"))
        for blocks in [4095, 4096]:
            with self.subTest(blocks=blocks):
                self.assert_result(self.run_source(b"{}" * blocks))
        self.assert_error_at(b"{}" * 4097, "E_LIMIT", "lex", 8192, 1)

    def test_parse_depth_boundaries_through_execution(self):
        for depth in [63, 64, 65]:
            with self.subTest(depth=depth):
                source = b"print " + b"(" * depth + b"1" + b")" * depth + b";"
                if depth <= 64:
                    self.assert_result(self.run_source(source), b"1\n")
                else:
                    self.assert_error_at(source, "E_LIMIT", "parse", 70, 1)
        for terms in [62, 63, 64, 65]:
            with self.subTest(terms=terms):
                source = b"print " + b"+".join([b"1"] * terms) + b";"
                if terms <= 63:
                    self.assert_result(self.run_source(source), f"{terms}\n".encode("ascii"))
                else:
                    start, width = (0, 5) if terms == 64 else (133, 1)
                    self.assert_error_at(source, "E_LIMIT", "parse", start, width)
