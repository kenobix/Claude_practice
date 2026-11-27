import argparse
import json
import sys
from pathlib import Path

from .compiler import compile_program
from .diagnostics import DiagnosticError
from .display import ast_data, disassembly_data, token_data
from .interpreter import interpret
from .parser import parse
from .resolver import resolve
from .scanner import scan
from .source import read_source
from .type_checker import check_types
from .vm import execute


def _show_error(error: DiagnosticError) -> int:
    try:
        print(json.dumps(error.as_dict()), file=sys.stderr, flush=True)
    except OSError:
        # A broken trace/diagnostic stream cannot carry another diagnostic.
        try:
            sys.stderr.close()
        except OSError:
            pass
        return 4
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


def _show_trace(data: dict[str, object]) -> None:
    print(json.dumps(data), file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Inspect or run tiny language source.")
    commands = cli.add_mutually_exclusive_group()
    commands.add_argument("--tokens", type=Path, metavar="PATH", help="Show the token list.")
    commands.add_argument("--ast", type=Path, metavar="PATH", help="Show the syntax tree.")
    commands.add_argument("--disassemble", type=Path, metavar="PATH", help="Show the bytecode.")
    run = cli.add_subparsers(dest="command").add_parser("run", help="Run a source file.")
    run.add_argument("--engine", choices=("ast", "vm"), required=True)
    run.add_argument("--trace", action="store_true", help="Show VM instructions and state on stderr.")
    run.add_argument("path", type=Path, metavar="PATH")
    args = cli.parse_args(argv)
    inspect_path = args.tokens or args.ast or args.disassemble
    if args.command == "run" and inspect_path is not None:
        cli.error("run cannot be combined with --tokens, --ast, or --disassemble")
    if args.command is None and inspect_path is None:
        cli.error("specify --tokens, --ast, --disassemble, or run")
    if args.command == "run" and args.trace and args.engine != "vm":
        run.error("--trace requires --engine vm")

    try:
        path = args.path if args.command == "run" else inspect_path
        tokens = scan(read_source(path))
        if args.tokens is not None:
            _write_stdout(json.dumps([token_data(token) for token in tokens], indent=2) + "\n")
            return 0
        program = parse(tokens)
        if args.ast is not None:
            _write_stdout(json.dumps(ast_data(program), indent=2) + "\n")
            return 0

        resolution = resolve(program)
        check_types(program, resolution)
        if args.disassemble is None and args.engine == "ast":
            interpret(program, resolution, _write_stdout)
        else:
            code = compile_program(program, resolution, tokens[-1].position)
            if args.disassemble is not None:
                _write_stdout(json.dumps(disassembly_data(code), indent=2) + "\n")
            else:
                execute(code, _write_stdout, _show_trace if args.trace else None)
    except DiagnosticError as error:
        return _show_error(error)
    except Exception:
        return _show_error(DiagnosticError("E_INTERNAL", "internal", "Internal processing error."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
