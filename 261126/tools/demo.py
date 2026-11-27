"""Render the three-minute captioned demo from CLI and benchmark evidence."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .compare import PROJECT

JP_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
CODE_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")


def cli(*args: str, status: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tiny_language", *args],
        cwd=PROJECT, capture_output=True, encoding="utf-8", check=False, timeout=15,
    )
    if result.returncode != status:
        raise RuntimeError(f"{args!r} returned {result.returncode}: {result.stderr}")
    return result


def source(name: str) -> str:
    return (PROJECT / "examples" / name).read_text(encoding="utf-8").rstrip()


def value(item: dict) -> str:
    return json.dumps(item["value"]) if item["type"] == "bool" else item["value"]


def instruction_row(instruction: dict) -> str:
    operand = instruction["operand"]
    if isinstance(operand, dict):
        operand = value(operand)
    return (
        f'{instruction["pc"]:>2}  {instruction["op"]:<14} '
        f'{"" if operand is None else operand!s:<4} '
        f'{instruction["line"]:>2}:{instruction["column"]}'
    )


def trace_rows(rows: list[dict]) -> str:
    lines = ["step  pc -> next   stack after"]
    for row in rows:
        stack = ", ".join(value(item) for item in row["stack"])
        lines.append(f'{row["step"]:>4} {row["pc"]:>3} -> {row["next_pc"]:>3}    [{stack}]')
        if row["store"] is not None:
            store = row["store"]
            lines.append(f'                    slot {store["slot"]} = {value(store)}')
    return "\n".join(lines)


def capture(comparison: dict, benchmark: dict) -> list[tuple[str, str, str, str]]:
    program = "examples/sum_loop.tiny"
    ast_run = cli("run", "--engine", "ast", program)
    vm_run = cli("run", "--engine", "vm", "--trace", program)
    if ast_run.stdout != vm_run.stdout:
        raise RuntimeError("The demo's AST and VM output differ.")
    raw_ast = json.loads(cli("--ast", program).stdout)
    loop = raw_ast["statements"][2]
    condition = loop["condition"]
    token = condition["token"]
    ast_lines = [
        loop["kind"],
        f'  condition: {condition["kind"]} {token["lexeme"]}',
        f'    {condition["left"]["kind"]} {condition["left"]["token"]["lexeme"]}',
        f'    {condition["right"]["kind"]} {condition["right"]["value"]}',
        f'  body: {loop["body"]["kind"]}',
        *(f'    {statement["kind"]}' for statement in loop["body"]["statements"]),
        "", f'operator: {token["line"]}:{token["column"]}, bytes {token["span"]}',
    ]
    code = json.loads(cli("--disassemble", program).stdout)
    trace = [json.loads(line) for line in vm_run.stderr.splitlines()]
    first_branch, *_, last_branch = [row for row in trace if row["op"] == "JUMP_IF_FALSE"]
    first = trace[first_branch["step"] - 5:first_branch["step"] + 2]
    last = trace[last_branch["step"] - 5:last_branch["step"]]
    typed = cli("run", "--engine", "vm", "examples/type_dead_branch.tiny", status=2)
    error = json.loads(typed.stderr)
    shadow = cli("run", "--engine", "vm", "examples/shadowing.tiny")
    fixed = json.loads((PROJECT / "examples" / "expected.json").read_bytes())
    fixed_ok = sum(case["exit_code"] == 0 for case in fixed.values())

    executions, totals = [], []
    for name, workload in benchmark["workloads"].items():
        ast, vm = (workload["median"][engine] for engine in ("ast", "vm"))
        executions.append(f'{name:<16} {ast["execute_ns"] / 1e6:>6.2f}  {vm["execute_ns"] / 1e6:>6.2f}')
        totals.append(f'{name:<16} {ast["total_ns"] / 1e6:>6.2f}  {vm["total_ns"] / 1e6:>6.2f}')
    benchmark_note = (
        f'{benchmark["warmups"]} warmups, {benchmark["repeats"]} trials\n\n'
        "Core language: int / bool\n"
        "No functions, arrays, strings or JIT"
    )
    return [
        ("examples/sum_loop.tiny", source("sum_loop.tiny"), "CLI  /  AST", (
            "$ python -m tiny_language run \\\n  --engine ast examples/sum_loop.tiny\n\n"
            f"stdout\n{ast_run.stdout}\nexit {ast_run.returncode}"
        )),
        ("examples/sum_loop.tiny  /  excerpt", "\n".join(source("sum_loop.tiny").splitlines()[2:8]),
         "CLI  /  --ast excerpt", "\n".join(ast_lines)),
        ("PC   OPCODE          ARG   LINE:COL", "\n".join(
            instruction_row(row) for row in code[4:10]
        ), "LOOP BACK EDGE AND EXIT", "\n".join(instruction_row(row) for row in code[19:])),
        ("FIRST ITERATION  /  --trace", trace_rows(first), "EXIT  /  --trace", trace_rows(last)),
        ("examples/type_dead_branch.tiny", source("type_dead_branch.tiny"), "CLI  /  VM", (
            f"exit   {typed.returncode}\nstdout {typed.stdout!r}\n\n"
            f'{error["code"]} / {error["phase"]}\n'
            f'line {error["line"]}, column {error["column"]}\nbytes {error["span"]}'
        )),
        ("examples/shadowing.tiny", source("shadowing.tiny"), "CLI  /  VM", (
            f"stdout\n{shadow.stdout}\nexit {shadow.returncode}"
        )),
        ("GENERATED  /  tools.compare", (
            f'seed            {comparison["seed"]}\n'
            f'passed          {comparison["passed"]} / {comparison["total"]}\n'
            f'normal          {comparison["normal"]}\n'
            f'runtime error   {comparison["runtime_error"]}\n'
            f'failures        {len(comparison["failures"])}'
        ), "FIXED EXAMPLES  /  expected.json", (
            f"normal          {fixed_ok}\nerror           {len(fixed) - fixed_ok}\n\n"
            "Compared per engine\nstdout, exit code, code / phase,\nline / column / byte span"
        )),
        ("EXECUTION  /  MEDIAN ms", "                  AST      VM\n" + "\n".join(executions)
         + "\n\nPIPELINE TOTAL  /  MEDIAN ms\n                  AST      VM\n" + "\n".join(totals),
         "MEASUREMENT AND SCOPE", benchmark_note),
    ]


def frame(work: Path, number: int, title: str, timing: str, caption: str,
          contents: tuple[str, str, str, str]) -> None:
    filters = [
        "drawbox=x=0:y=0:w=iw:h=8:color=0x55C6AF:t=fill",
        "drawbox=x=54:y=130:w=570:h=440:color=0x162634:t=fill",
        "drawbox=x=654:y=130:w=572:h=440:color=0x162634:t=fill",
        "drawbox=x=54:y=598:w=1172:h=2:color=0x355467:t=fill",
    ]

    def draw(label: str, text: str, x: int, y: int, size: int, color: str,
             font: Path = CODE_FONT) -> None:
        filename = f"text-{label}.txt"
        (work / filename).write_text(text, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={font}:textfile={filename}:expansion=none:"
            f"fontsize={size}:line_spacing=6:fontcolor={color}:x={x}:y={y}"
        )

    left_title, left_text, right_title, right_text = contents
    draw("time", timing, 56, 30, 17, "0x76D8C4")
    draw("title", title, 54, 63, 39, "white", JP_FONT)
    draw("left-label", left_title, 77, 151, 18, "0x76D8C4")
    draw("right-label", right_title, 678, 151, 18, "0x76D8C4")
    draw("left", left_text, 77, 198, 21, "0xE7F2FF")
    draw("right", right_text, 678, 198, 21, "0xE7F2FF")
    draw("caption", caption, 54, 623, 22, "white", JP_FONT)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x0D1922:s=1280x720", "-vf", ",".join(filters),
        "-frames:v", "1", "-threads", "1", "-update", "1", f"slide-{number:02}.png",
    ], cwd=work, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not JP_FONT.is_file() or not CODE_FONT.is_file():
        parser.error("Install Noto Sans CJK and DejaVu Sans Mono before rendering.")
    comparison = json.loads((PROJECT / "results/comparison.json").read_bytes())
    benchmark = json.loads((PROJECT / "results/benchmark.json").read_bytes())
    storyboard = (PROJECT / "demo/storyboard.md").read_text(encoding="utf-8")
    scenes = re.findall(r"^## (\d\d:\d\d)–(\d\d:\d\d) (.+)\n\n([^\n]+)", storyboard, re.M)
    panels = capture(comparison, benchmark)
    files = ["ffconcat version 1.0"]
    output = args.output.resolve()

    def seconds(timecode: str) -> int:
        minutes, sec = map(int, timecode.split(":"))
        return minutes * 60 + sec

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        for number, ((start, end, title, caption), contents) in enumerate(zip(scenes, panels, strict=True)):
            caption = caption.replace("`", "").replace("。", "。\n", 1)
            frame(work, number, title, start + " - " + end, caption, contents)
            files.extend([f"file slide-{number:02}.png", f"duration {seconds(end) - seconds(start)}"])
        files.append(f"file slide-{len(scenes) - 1:02}.png")
        (work / "frames.ffconcat").write_text("\n".join(files) + "\n", encoding="utf-8")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-i", "frames.ffconcat",
            "-t", "180", "-r", "24", "-c:v", "libx264", "-crf", "21", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ], cwd=work, check=True)


if __name__ == "__main__":
    main()
