from . import ast_nodes as ast
from .diagnostics import DiagnosticError
from .scanner import Token

MAX_DEPTH = 64


def parse(tokens: list[Token]) -> ast.Program:
    """Parse a complete scanner result, including its EOF token."""
    return _Parser(tokens).program()


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def _take(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def _expect(self, *kinds: str) -> Token:
        if self.current.kind not in kinds:
            raise self._unexpected(" or ".join(repr(kind) for kind in kinds))
        return self._take()

    def _unexpected(self, expected: str) -> DiagnosticError:
        token = self.current
        return DiagnosticError(
            "E_PARSE", "parse", f"Expected {expected}, found {token.kind!r}.", token.position
        )

    def _enter(self, token: Token, nesting: int) -> int:
        if nesting == MAX_DEPTH:
            raise DiagnosticError(
                "E_LIMIT", "parse", f"Syntax nesting exceeds {MAX_DEPTH}.", token.position
            )
        return nesting + 1

    def _node_depth(self, anchor: Token, *children: ast.Node) -> int:
        depth = 1 + max((child.depth for child in children), default=0)
        if depth > MAX_DEPTH:
            raise DiagnosticError(
                "E_LIMIT", "parse", f"AST depth exceeds {MAX_DEPTH}.", anchor.position
            )
        return depth

    def program(self) -> ast.Program:
        return ast.Program(self._statements("EOF", nesting=0))

    def _statements(self, end: str, nesting: int) -> tuple[ast.Statement, ...]:
        statements = []
        while self.current.kind not in (end, "EOF"):
            statements.append(self._statement(nesting))
        return tuple(statements)

    def _statement(self, nesting: int) -> ast.Statement:
        match self.current.kind:
            case "let":
                keyword = self._take()
                name = self._expect("IDENT")
                self._expect(":")
                annotation = self._expect("int", "bool")
                self._expect("=")
                initializer = self._expression(nesting)
                self._expect(";")
                return ast.Let(
                    keyword, name, annotation, initializer,
                    depth=self._node_depth(keyword, initializer),
                )
            case "IDENT" | "print":
                anchor = self._take()
                if anchor.kind == "IDENT":
                    self._expect("=")
                value = self._expression(nesting)
                self._expect(";")
                statement = ast.Assignment if anchor.kind == "IDENT" else ast.Print
                return statement(anchor, value, depth=self._node_depth(anchor, value))
            case "{":
                return self._block(nesting)
            case "if":
                return self._if_statement(nesting)
            case "while":
                keyword = self._take()
                condition = self._condition(nesting)
                body = self._block(nesting)
                return ast.While(
                    keyword, condition, body, depth=self._node_depth(keyword, condition, body)
                )
            case _:
                raise self._unexpected("a declaration or statement")

    def _block(self, nesting: int) -> ast.Block:
        opening = self._expect("{")
        statements = self._statements("}", self._enter(opening, nesting))
        self._expect("}")
        return ast.Block(opening, statements, depth=self._node_depth(opening, *statements))

    def _if_statement(self, nesting: int) -> ast.If:
        keyword = self._expect("if")
        condition = self._condition(nesting)
        then_block = self._block(nesting)
        children = [condition, then_block]
        else_block = None
        if self.current.kind == "else":
            self._take()
            else_block = self._block(nesting)
            children.append(else_block)
        return ast.If(
            keyword, condition, then_block, else_block, depth=self._node_depth(keyword, *children)
        )

    def _condition(self, nesting: int) -> ast.Expression:
        self._expect("(")
        condition = self._expression(nesting)
        self._expect(")")
        return condition

    def _expression(self, nesting: int) -> ast.Expression:
        left = self._comparison(nesting)
        if self.current.kind in ("==", "!="):
            operator = self._take()
            return self._binary(operator, left, self._comparison(nesting))
        return left

    def _comparison(self, nesting: int) -> ast.Expression:
        left = self._term(nesting)
        if self.current.kind in ("<", "<=", ">", ">="):
            operator = self._take()
            return self._binary(operator, left, self._term(nesting))
        return left

    def _term(self, nesting: int) -> ast.Expression:
        left = self._factor(nesting)
        while self.current.kind in ("+", "-"):
            operator = self._take()
            left = self._binary(operator, left, self._factor(nesting))
        return left

    def _factor(self, nesting: int) -> ast.Expression:
        left = self._unary(nesting)
        while self.current.kind in ("*", "/", "%"):
            operator = self._take()
            left = self._binary(operator, left, self._unary(nesting))
        return left

    def _binary(self, operator: Token, left: ast.Expression, right: ast.Expression) -> ast.Binary:
        return ast.Binary(operator, left, right, depth=self._node_depth(operator, left, right))

    def _unary(self, nesting: int) -> ast.Expression:
        if self.current.kind in ("!", "-"):
            operator = self._take()
            operand = self._unary(self._enter(operator, nesting))
            return ast.Unary(operator, operand, depth=self._node_depth(operator, operand))
        return self._primary(nesting)

    def _primary(self, nesting: int) -> ast.Expression:
        token = self.current
        match token.kind:
            case "INTEGER":
                self._take()
                assert token.integer is not None
                return ast.IntegerLiteral(token, token.integer)
            case "true" | "false":
                self._take()
                return ast.BooleanLiteral(token, token.kind == "true")
            case "IDENT":
                self._take()
                return ast.Variable(token)
            case "(":
                self._take()
                expression = self._expression(self._enter(token, nesting))
                self._expect(")")
                return expression
            case _:
                raise self._unexpected("an expression")
