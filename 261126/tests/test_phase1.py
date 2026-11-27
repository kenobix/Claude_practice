import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tiny_language.ast_nodes import Binary, IntegerLiteral
from tiny_language.parser import parse
from tiny_language.scanner import scan

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT / "examples"


def decimal_bytes(number: int) -> bytes:
    return str(Decimal(number)).encode("ascii")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_file(self, option: str, path: Path, *python_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", *python_args, "-m", "tiny_language", option, str(path)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

    def run_source(
        self, option: str, source: bytes, *python_args: str
    ) -> subprocess.CompletedProcess[str]:
        path = self.workspace / "source.tiny"
        path.write_bytes(source)
        return self.run_file(option, path, *python_args)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_diagnostic(self, result, code, phase, line=None, column=None, span=None, status=2):
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        error = json.loads(result.stderr)
        self.assertTrue(error.pop("message").strip())
        self.assertEqual(
            error, {"code": code, "phase": phase, "line": line, "column": column, "span": span}
        )

    def test_phase0_fixtures(self):
        expectations = json.loads((EXAMPLES / "expected.json").read_bytes())
        self.assertEqual(set(expectations), {path.name for path in EXAMPLES.glob("*.tiny")})
        for filename, expected in expectations.items():
            diagnostic = expected["diagnostic"]
            for option, phases in [
                ("--tokens", {"input", "lex"}),
                ("--ast", {"input", "lex", "parse"}),
            ]:
                with self.subTest(file=filename, option=option):
                    result = self.run_file(option, EXAMPLES / filename)
                    if diagnostic and diagnostic["phase"] in phases:
                        self.assertEqual(result.stdout, expected["stdout"])
                        self.assert_diagnostic(result, **diagnostic, status=expected["exit_code"])
                    else:
                        output = self.assert_success(result)
                        if option == "--tokens":
                            self.assertEqual(output[-1]["kind"], "EOF")
                        else:
                            self.assertEqual(output["kind"], "Program")

    def test_ast_precedence_and_left_associativity(self):
        source = (
            b"print 1+2*3; print (1+2)*3; print 10-3-2; print 20/3*2%5; "
            b"print 1 < 2 == true; print 1<2 != 3<4;"
        )
        tree = self.assert_success(self.run_source("--ast", source))

        def shape(node):
            if node["kind"] == "Binary":
                return node["token"]["lexeme"], shape(node["left"]), shape(node["right"])
            return node["value"]

        self.assertEqual([shape(node["value"]) for node in tree["statements"]], [
            ("+", "1", ("*", "2", "3")), ("*", ("+", "1", "2"), "3"),
            ("-", ("-", "10", "3"), "2"), ("%", ("*", ("/", "20", "3"), "2"), "5"),
            ("==", ("<", "1", "2"), True), ("!=", ("<", "1", "2"), ("<", "3", "4")),
        ])

    def test_unary_nodes_and_operator_locations(self):
        source = b"print !-!2 * 3;"
        result = self.assert_success(self.run_source("--ast", source))
        product = result["statements"][0]["value"]
        self.assertEqual(product["token"], self.token("*", "*", 1, 12, 11, 12))
        operand = product["left"]
        for operator, start in [("!", 6), ("-", 7), ("!", 8)]:
            self.assertEqual(operand["kind"], "Unary")
            expected = self.token(operator, operator, 1, start + 1, start, start + 1)
            self.assertEqual(operand["token"], expected)
            operand = operand["operand"]
        self.assertEqual(operand["value"], "2")

    def test_ast_statement_shapes(self):
        source = b"let x: int = 03; x = y; { if (true) {} else {} while (false) {} if (x) {} }"
        program = self.assert_success(self.run_source("--ast", source))
        declaration, assignment, block = program["statements"]
        self.assertEqual(declaration["kind"], "Let")
        self.assertEqual(declaration["name"], self.token("IDENT", "x", 1, 5, 4, 5))
        self.assertEqual(declaration["annotation"], self.token("int", "int", 1, 8, 7, 10))
        self.assertEqual(declaration["initializer"]["value"], "3")
        self.assertEqual(assignment["kind"], "Assignment")
        self.assertEqual(assignment["token"]["lexeme"], "x")
        self.assertEqual(assignment["value"]["kind"], "Variable")
        self.assertEqual(assignment["value"]["token"]["lexeme"], "y")
        self.assertEqual(block["kind"], "Block")
        choice, loop, without_else = block["statements"]
        self.assertEqual(choice["kind"], "If")
        self.assertIs(choice["condition"]["value"], True)
        self.assertEqual(choice["then_block"]["statements"], [])
        self.assertEqual(choice["else_block"]["statements"], [])
        self.assertEqual(loop["kind"], "While")
        self.assertIs(loop["condition"]["value"], False)
        self.assertEqual(loop["body"]["statements"], [])
        self.assertIsNone(without_else["else_block"])

    def test_longest_match_keywords_and_identifiers(self):
        source = (
            b"let int bool print if else while true false if1 _if If true2 12abc "
            b"== != <= >= = ! < > /%// ignored"
        )
        tokens = self.assert_success(self.run_source("--tokens", source))
        self.assertEqual([(token["kind"], token["lexeme"]) for token in tokens], [
            ("let", "let"), ("int", "int"), ("bool", "bool"), ("print", "print"),
            ("if", "if"), ("else", "else"), ("while", "while"), ("true", "true"), ("false", "false"),
            ("IDENT", "if1"), ("IDENT", "_if"), ("IDENT", "If"), ("IDENT", "true2"),
            ("INTEGER", "12"), ("IDENT", "abc"), ("==", "=="), ("!=", "!="),
            ("<=", "<="), (">=", ">="), ("=", "="), ("!", "!"), ("<", "<"), (">", ">"),
            ("/", "/"), ("%", "%"), ("EOF", ""),
        ])

    @staticmethod
    def token(kind, lexeme, line, column, start, end):
        return {"kind": kind, "lexeme": lexeme, "line": line, "column": column, "span": [start, end]}

    def test_utf8_comments_tab_lf_crlf_and_eof_positions(self):
        source = "// é😀\rX\r\n\tprint 09;\r\n//終".encode("utf-8")
        for newline, expected in [
            (b"\r\n", [(14, 19), (20, 22), (22, 23), (30, 30)]),
            (b"\n", [(13, 18), (19, 21), (21, 22), (28, 28)]),
        ]:
            with self.subTest(newline=newline):
                result = self.run_source("--tokens", source.replace(b"\r\n", newline))
                tokens = self.assert_success(result)
                self.assertEqual(tokens, [
                    self.token("print", "print", 2, 2, *expected[0]),
                    self.token("INTEGER", "09", 2, 8, *expected[1]),
                    self.token(";", ";", 2, 10, *expected[2]),
                    self.token("EOF", "", 3, 4, *expected[3]),
                ])

    def test_empty_source_and_comment_at_eof(self):
        tokens = self.assert_success(self.run_source("--tokens", b""))
        self.assertEqual(tokens, [self.token("EOF", "", 1, 1, 0, 0)])
        for source in [b"", b" \t\r\n", b"//\rcomment"]:
            with self.subTest(source=source):
                self.assertEqual(self.assert_success(self.run_source("--ast", source)), {
                    "kind": "Program", "statements": [],
                })

    def test_illegal_characters_and_utf8_byte_widths(self):
        cases = [("\r", 1), ("\ufeff", 3), ("\v", 1), ("é", 2), ("😀", 4), ("٢", 2)]
        for character, length in cases:
            with self.subTest(character=character):
                result = self.run_source("--tokens", character.encode("utf-8"))
                self.assert_diagnostic(result, "E_LEX", "lex", 1, 1, [0, length])
        fixture = (EXAMPLES / "lex_character.tiny").read_bytes().replace(b"\n", b"\r\n")
        self.assert_diagnostic(self.run_source("--ast", fixture), "E_LEX", "lex", 2, 8, [21, 24])

    def test_unreadable_source(self):
        for path in [self.workspace / "missing.tiny", self.workspace]:
            with self.subTest(path=path):
                self.assert_diagnostic(self.run_file("--tokens", path), "E_INPUT", "input")

    def test_invalid_utf8_reports_first_byte_of_sequence(self):
        cases = [
            (b"//\r\n\xff", 2, 1, [4, 5]),
            (b"//\xc3\xa9\r\n\tA\xe2\x82", 2, 3, [8, 9]),
            (b"//\xc3x\xff", 1, 3, [2, 3]),
        ]
        for source, line, column, span in cases:
            with self.subTest(source=source):
                result = self.run_source("--ast", source)
                self.assert_diagnostic(result, "E_INPUT", "input", line, column, span)

    def test_rejects_comparison_and_equality_chains_at_extra_operator(self):
        for source, start, end in [
            (b"print 1 < 2 >= 3;", 12, 14),
            (b"print 1 == 2 != false;", 13, 15),
            (b"if (true != false == false) {}", 18, 20),
        ]:
            with self.subTest(source=source):
                result = self.run_source("--ast", source)
                self.assert_diagnostic(result, "E_PARSE", "parse", 1, start + 1, [start, end])

    def test_expected_tokens_and_eof(self):
        for source, line, column, span in [
            (b"let true: int = 1;", 1, 5, [4, 8]),
            (b"let x: int = 1", 1, 15, [14, 14]),
            (b"}", 1, 1, [0, 1]),
            (b"{\r\n", 2, 1, [3, 3]),
            (b"if (true) print 1;", 1, 11, [10, 15]),
        ]:
            with self.subTest(source=source):
                result = self.run_source("--ast", source)
                self.assert_diagnostic(result, "E_PARSE", "parse", line, column, span)

    def test_source_size_boundaries(self):
        for size in [65_535, 65_536]:
            with self.subTest(size=size):
                pairs, remainder = divmod(size - 2, 2)
                source = b"//" + b"\xc3\xa9" * pairs + b" " * remainder
                tokens = self.assert_success(self.run_source("--tokens", source))
                self.assertEqual(tokens[-1]["span"], [size, size])
                program = self.assert_success(self.run_source("--ast", b" " * size))
                self.assertEqual(program["statements"], [])
        self.assert_diagnostic(self.run_source("--ast", b" " * 65_537), "E_LIMIT", "input")

    def test_token_count_boundaries(self):
        for source, count in [(b"{}" * 4095 + b"{", 8191), (b"{}" * 4096, 8192)]:
            with self.subTest(count=count):
                tokens = self.assert_success(self.run_source("--tokens", source))
                self.assertEqual(len(tokens), count + 1)
                self.assertEqual(tokens[-1], self.token("EOF", "", 1, count + 1, count, count))
        source = b"{}" * 4097
        self.assert_diagnostic(self.run_source("--ast", source), "E_LIMIT", "lex", 1, 8193, [8192, 8193])
        program = self.assert_success(self.run_source("--ast", b"{}" * 4096))
        self.assertEqual(len(program["statements"]), 4096)

    def test_group_nesting_boundaries(self):
        for depth in [63, 64, 65]:
            with self.subTest(depth=depth):
                result = self.run_source("--ast", b"print " + b"(" * depth + b"1" + b")" * depth + b";")
                if depth == 65:
                    self.assert_diagnostic(result, "E_LIMIT", "parse", 1, 71, [70, 71])
                else:
                    literal = self.assert_success(result)["statements"][0]["value"]
                    self.assertEqual(literal["kind"], "IntegerLiteral")
                    self.assertEqual(literal["token"]["span"], [6 + depth, 7 + depth])

    def test_block_nesting_boundaries(self):
        for depth in [63, 64, 65]:
            with self.subTest(depth=depth):
                result = self.run_source("--ast", b"{" * depth + b"}" * depth)
                if depth == 65:
                    self.assert_diagnostic(result, "E_LIMIT", "parse", 1, 65, [64, 65])
                else:
                    statements = self.assert_success(result)["statements"]
                    for _ in range(depth):
                        self.assertEqual(statements[0]["kind"], "Block")
                        statements = statements[0]["statements"]
                    self.assertEqual(statements, [])

    def test_unary_and_block_depth_interact(self):
        for count, span in [(63, [0, 5]), (64, [6, 7]), (65, [70, 71])]:
            with self.subTest(count=count):
                result = self.run_source("--ast", b"print " + b"-" * count + b"1;")
                self.assert_diagnostic(result, "E_LIMIT", "parse", 1, span[0] + 1, span)
        for keyword in [b"if", b"while"]:
            with self.subTest(keyword=keyword):
                source = b"{" * 62 + keyword + b"(((!true))){}" + b"}" * 62
                start = 62 + len(keyword) + 3
                result = self.run_source("--ast", source)
                self.assert_diagnostic(result, "E_LIMIT", "parse", 1, start + 1, [start, start + 1])
                self.assert_success(self.run_source("--ast", source.replace(b"!", b"")))

    def test_ast_depth_boundaries(self):
        for count in [62, 63, 64, 65]:
            with self.subTest(count=count):
                result = self.run_source("--ast", b"print " + b"+".join([b"1"] * count) + b";")
                if count <= 63:
                    self.assert_success(result)
                elif count == 64:
                    self.assert_diagnostic(result, "E_LIMIT", "parse", 1, 1, [0, 5])
                else:
                    self.assert_diagnostic(result, "E_LIMIT", "parse", 1, 134, [133, 134])
        source = b"if(true){" * 32 + b"print 1;" + b"}" * 32
        self.assert_diagnostic(self.run_source("--ast", source), "E_LIMIT", "parse", 1, 9, [8, 9])

    def test_integer_bit_limit_under_minimum_host_digit_limit(self):
        for number in [(1 << 4095) - 1, (1 << 4096) - 1, 1 << 4096]:
            with self.subTest(bits=number.bit_length()):
                digits = decimal_bytes(number)
                result = self.run_source(
                    "--ast", b"print " + digits + b";", "-X", "int_max_str_digits=640"
                )
                if number.bit_length() == 4097:
                    self.assert_diagnostic(result, "E_LIMIT", "lex", 1, 7, [6, 6 + len(digits)])
                else:
                    literal = self.assert_success(result)["statements"][0]["value"]
                    self.assertEqual(literal["value"], digits.decode("ascii"))
        digits = b"9" * 5000
        result = self.run_source("--tokens", b"print " + digits + b";")
        self.assert_diagnostic(result, "E_LIMIT", "lex", 1, 7, [6, 5006])

    def test_leading_zeros_and_unexecuted_literal_limits(self):
        largest = decimal_bytes((1 << 4096) - 1)
        for ending, value in [(b"", "0"), (b"1", "1"), (largest, largest.decode("ascii"))]:
            with self.subTest(ending_length=len(ending)):
                digits = b"0" * 5000 + ending
                result = self.run_source(
                    "--ast", b"print " + digits + b";", "-X", "int_max_str_digits=640"
                )
                literal = self.assert_success(result)["statements"][0]["value"]
                self.assertEqual(literal["token"]["lexeme"], digits.decode("ascii"))
                self.assertEqual(literal["value"], value)
        digits = b"0" * 5000 + decimal_bytes(1 << 4096)
        prefix = b"if(false){print "
        result = self.run_source("--ast", prefix + digits + b";}")
        self.assert_diagnostic(result, "E_LIMIT", "lex", 1, 17, [16, 16 + len(digits)])

    def test_input_lex_parse_priority_and_mode_scope(self):
        for source, diagnosis in [
            (b"@" + b" " * 65_535 + b"\xff", ("E_LIMIT", "input", None, None, None)),
            (b"print ; @\xff", ("E_INPUT", "input", 1, 10, [9, 10])),
            (b"print ; @", ("E_LEX", "lex", 1, 9, [8, 9])),
            (b"print ;", ("E_PARSE", "parse", 1, 7, [6, 7])),
        ]:
            with self.subTest(source_length=len(source), diagnosis=diagnosis):
                self.assert_diagnostic(self.run_source("--ast", source), *diagnosis)
        self.assert_success(self.run_source("--tokens", b"print ;"))
        digits = decimal_bytes(1 << 4096)
        result = self.run_source("--tokens", b"{}" * 4096 + digits)
        self.assert_diagnostic(result, "E_LIMIT", "lex", 1, 8193, [8192, 8192 + len(digits)])
        self.assertIn("tokens", json.loads(result.stderr)["message"])

    def test_closed_output_pipe_reports_an_internal_diagnostic(self):
        reader, writer = os.pipe()
        os.close(reader)
        with os.fdopen(writer, "wb") as pipe:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tiny_language", "--ast", "examples/arithmetic.tiny"],
                cwd=PROJECT,
                stdout=pipe,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                timeout=10,
            )
        self.assertEqual(result.returncode, 4, result.stderr)
        error = json.loads(result.stderr)
        self.assertTrue(error.pop("message").strip())
        self.assertEqual(
            error, {"code": "E_INTERNAL", "phase": "internal",
                    "line": None, "column": None, "span": None}
        )


class FrontEndDataTests(unittest.TestCase):
    def test_ast_keeps_integer_value_and_operator_source_token(self):
        expression = parse(scan("print ((00042)) + 1;")).statements[0].value
        self.assertIsInstance(expression, Binary)
        self.assertEqual(expression.anchor.lexeme, "+")
        self.assertEqual(
            expression.anchor.position.as_dict(), {"line": 1, "column": 17, "span": [16, 17]}
        )
        self.assertIsInstance(expression.left, IntegerLiteral)
        self.assertEqual(expression.left.value, 42)
        self.assertEqual(expression.left.anchor.lexeme, "00042")
        self.assertEqual(expression.depth, 2)
