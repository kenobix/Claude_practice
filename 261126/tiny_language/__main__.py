import argparse
import json
import sys
from pathlib import Path

from .diagnostics import DiagnosticError
from .display import ast_data, token_data
from .interpreter import interpret
from .parser import parse
from .resolver import resolve
from .scanner import scan
from .source import read_source
from .type_checker import check_types


def _show_error(error: DiagnosticError) -> int:
    print(json.dumps(error.as_dict()), file=sys.stderr)
    return error.exit_code


def _write_stdout(text: str) -> None:
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except OSError:
        # Discard any buffered output so interpreter shutdown does not retry the write.
        try:
            sys.stdout.close()
        except OSError:
            pass
        raise DiagnosticError("E_INTERNAL", "internal", "Cannot write CLI output.") from None


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Inspect or run tiny language source.")
    commands = cli.add_mutually_exclusive_group()
    commands.add_argument("--tokens", type=Path, metavar="PATH", help="Show the token list.")
    commands.add_argument("--ast", type=Path, metavar="PATH", help="Show the syntax tree.")
    run = cli.add_subparsers(dest="command").add_parser("run", help="Run a source file.")
    run.add_argument("--engine", choices=("ast",), required=True)
    run.add_argument("path", type=Path, metavar="PATH")
    args = cli.parse_args(argv)
    inspect_path = args.tokens or args.ast
    if args.command == "run" and inspect_path is not None:
        cli.error("run cannot be combined with --tokens or --ast")
    if args.command is None and inspect_path is None:
        cli.error("specify --tokens, --ast, or run")

    try:
        path = args.path if args.command == "run" else inspect_path
        tokens = scan(read_source(path))
        if args.command == "run":
            program = parse(tokens)
            resolution = resolve(program)
            check_types(program, resolution)
            interpret(program, resolution, _write_stdout)
        else:
            result = (
                [token_data(token) for token in tokens]
                if args.tokens is not None else ast_data(parse(tokens))
            )
            _write_stdout(json.dumps(result, indent=2) + "\n")
    except DiagnosticError as error:
        return _show_error(error)
    except Exception:
        return _show_error(DiagnosticError("E_INTERNAL", "internal", "Internal processing error."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
