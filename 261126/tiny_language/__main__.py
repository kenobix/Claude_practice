import argparse
import json
import sys
from pathlib import Path

from .diagnostics import DiagnosticError
from .display import ast_data, token_data
from .parser import parse
from .scanner import scan
from .source import read_source


def _show_error(error: DiagnosticError) -> int:
    print(json.dumps(error.as_dict()), file=sys.stderr)
    return error.exit_code


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Inspect tiny language source.")
    commands = cli.add_mutually_exclusive_group(required=True)
    commands.add_argument("--tokens", type=Path, metavar="PATH", help="Show the token list.")
    commands.add_argument("--ast", type=Path, metavar="PATH", help="Show the syntax tree.")
    args = cli.parse_args(argv)

    try:
        tokens = scan(read_source(args.tokens or args.ast))
        if args.tokens is not None:
            result = [token_data(token) for token in tokens]
        else:
            result = ast_data(parse(tokens))
        output = json.dumps(result, indent=2)
    except DiagnosticError as error:
        return _show_error(error)
    except Exception:
        return _show_error(DiagnosticError("E_INTERNAL", "internal", "Internal front-end error."))

    try:
        print(output, flush=True)
    except OSError:
        # Discard any buffered output so interpreter shutdown does not retry the write.
        try:
            sys.stdout.close()
        except OSError:
            pass
        return _show_error(DiagnosticError("E_INTERNAL", "internal", "Cannot write CLI output."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
