"""Compare the two CLIs and independent expectations for a bounded generated corpus."""

import argparse
import hashlib
import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from fractions import Fraction
from itertools import product
from math import floor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 261126
ENGINES = ("ast", "vm")


@dataclass(frozen=True)
class Outcome:
    stdout: str
    exit_code: int = 0
    diagnostic: dict | None = None


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    expected: Outcome


def cases(seed: int) -> list[Case]:
    rng = random.Random(seed)
    corpus = []
    for index in range(24):
        count = index % 7
        initial = index % 5 - 2
        scale = rng.choice([-3, -1, 1, 3])
        bias = rng.randint(-4, 4)
        source = f"""let i: int = 0;
let x: int = {initial};
while (i < {count}) {{
  {{
    x = x + {bias};
    let x: int = -(i * {scale}) + {bias};
    {{
      let flag: bool = !(x >= 0);
      if (flag == true) {{ print -x; }} else {{ print x + i; }}
      x = x - 1;
      print x;
    }}
  }}
  i = i + 1;
}}
print x;
print i == {count};
"""
        lines = []
        for i in range(count):
            inner = bias - i * scale
            lines.extend([-inner if inner < 0 else inner + i, inner - 1])
        expected = "".join(f"{value}\n" for value in [*lines, initial + count * bias])
        corpus.append(Case(f"scope-loop-{index:02}", source, Outcome(expected + "true\n")))

    operands = list(product([-13, -6, 0, 6, 13], [-4, -3, 3, 4]))
    rng.shuffle(operands)
    for index, (a, b) in enumerate(operands):
        source = f"""let a: int = {a};
let b: int = {b};
let q: int = a / b;
let r: int = a % b;
print q;
print r;
print a == q * b + r;
print r == 0;
"""
        q = floor(Fraction(a, b))
        r = a - q * b
        zero = "true" if r == 0 else "false"
        corpus.append(Case(f"divmod-{index:02}", source, Outcome(f"{q}\n{r}\ntrue\n{zero}\n")))

    # Each split marks the first operator that fails by left-to-right evaluation.
    for index in range(16):
        value = rng.randint(-9, 9)
        zero = f"({value} - {value})"
        before, op, after = [
            ("(6 ", "/", f" {zero}) + (3 % {zero})"),
            ("6 / (3 ", "%", f" {zero})"),
            ("(6 / 2) - (3 ", "%", f" {zero})"),
            ("(6 % 2) + (3 ", "/", f" {zero})"),
        ][index % 4]
        prefix = f"// 比較\r\nprint {value};\r\nif (true) {{\r\n  print {before}"
        source = prefix + op + after + ";\r\n} else { print 1 / 0; }\r\nprint 99;\r\n"
        start = len(prefix.encode("utf-8"))
        diagnostic = {
            "code": "E_ZERO_DIVISION", "phase": "runtime",
            "line": 4, "column": len(prefix.rsplit("\n", 1)[1]) + 1, "span": [start, start + 1],
        }
        corpus.append(Case(f"zero-divisor-{index:02}", source, Outcome(f"{value}\n", 3, diagnostic)))
    return corpus


def run_cli(engine: str, path: Path) -> Outcome:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tiny_language", "run", "--engine", engine, str(path)],
        cwd=PROJECT, capture_output=True, timeout=15, check=False,
    )
    diagnostic = None
    if result.stderr:
        try:
            payload = json.loads(result.stderr)
            if payload.pop("message"):
                diagnostic = payload
            else:
                diagnostic = {"invalid_stderr": result.stderr.decode("utf-8", errors="replace")}
        except (ValueError, TypeError, KeyError, AttributeError):
            diagnostic = {"invalid_stderr": result.stderr.decode("utf-8", errors="replace")}
    # Decoding bytes directly preserves the distinction between LF and CRLF.
    return Outcome(result.stdout.decode("utf-8", errors="backslashreplace"), result.returncode, diagnostic)


def compare(corpus: list[Case], seed: int, failure_dir: Path) -> dict:
    failures = []
    fingerprint = hashlib.sha256()
    with tempfile.TemporaryDirectory() as temp:
        for case in corpus:
            source = case.source.encode("utf-8")
            fingerprint.update(case.name.encode("ascii") + b"\0" + source + b"\0")
            path = Path(temp, f"{case.name}.tiny")
            path.write_bytes(source)
            actual = {}
            for engine in ENGINES:
                try:
                    actual[engine] = run_cli(engine, path)
                except subprocess.TimeoutExpired:
                    actual[engine] = Outcome("", -1, {"timeout": True})
            if actual["ast"] == actual["vm"] == case.expected:
                continue
            failures.append(case.name)
            failure_dir.mkdir(parents=True, exist_ok=True)
            stem = failure_dir / f"{seed}-{case.name}"
            stem.with_suffix(".tiny").write_bytes(source)
            report = {
                "seed": seed, "case": case.name, "expected": asdict(case.expected),
                **{engine: asdict(outcome) for engine, outcome in actual.items()},
            }
            stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {
        "seed": seed,
        "total": len(corpus),
        "families": dict(Counter(case.name.rsplit("-", 1)[0] for case in corpus)),
        "normal": sum(case.expected.exit_code == 0 for case in corpus),
        "runtime_error": sum(case.expected.exit_code == 3 for case in corpus),
        "source_sha256": fingerprint.hexdigest(),
        "passed": len(corpus) - len(failures),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--case", help="Run only the named case.")
    parser.add_argument("--failure-dir", type=Path, default=Path(tempfile.gettempdir(), "tiny-language-failures"))
    args = parser.parse_args()
    corpus = cases(args.seed)
    if args.case is not None:
        corpus = [case for case in corpus if case.name == args.case]
        if not corpus:
            parser.error(f"unknown case {args.case!r}")
    report = compare(corpus, args.seed, args.failure_dir)
    print(json.dumps(report, indent=2))
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
