#!/usr/bin/env python3
"""Render the actual demo observations as an edited Japanese-caption MP4."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

SCENES = Path(__file__).resolve().parents[2] / "demo/scenes.json"
W, H, FPS = 1920, 1080, 24
DARK = "#10283b"
BLUE = "#17384f"
LIGHT = "#e8f3f7"
WHITE = "#ffffff"
INK = "#102638"
TEAL = "#75ded2"
DIM = "#bbd2df"


def short_command(frame):
    return frame["command"].replace(' -volume "$demo"', '').replace(' -hub "$address"', '')


def receipt_lines(history):
    rows = []
    for row in history:
        e = row["entry"]
        rows.append(f'{row["client_id"]:<9}  {e["path"]:<10}  {row["base_revision"]} -> {e["revision"]}  '
                    f'{row["result"]:<8}  {row["op_id"]}')
    return rows


def panels(evidence, measurement):
    f = evidence["frames"]
    initial, conflict = f["initial"], f["conflict"]
    resolved, offline, deleted = f["resolution"], f["offline"], f["deleted"]
    receive_p, receive_e = (initial["inspection"][f"B/{p}"] for p in ("notes.txt", "empty.txt"))
    local_copy = conflict["status"]["detected_local"]["stage_file"]
    hub_copy = conflict["status"]["hub_candidates"][-1]["stage_file"]
    body = lambda row: json.dumps(row["body"], ensure_ascii=False)
    row_header = "client     path        base -> rev  result    operation ID"
    return {
        "startup": f["startup"]["stdout"],
        "register": ("端末の実行結果・コマンド抜粋", [
            "$ " + short_command(f["seed"]), f'exit {f["seed"]["exit"]}', "",
            "$ " + short_command(f["pull"]), f'exit {f["pull"]["exit"]}', "",
            row_header, *receipt_lines(initial["history"])]),
        "initial": ("Bの status と、本文の独立照合", [
            f'notes.txt   body={body(receive_p)}  size={receive_p["size"]}  revision={receive_p["baseline"]["revision"]}',
            f'  sha256 {receive_p["sha256"]}', "",
            f'empty.txt   body={body(receive_e)}  size={receive_e["size"]}  revision={receive_e["baseline"]["revision"]}',
            f'  sha256 {receive_e["sha256"]}', "",
            "never-created.txt   no file, no hub entry"]),
        "hold": ("CLIの実行結果とSQLite受理履歴の抜粋", [
            "$ " + short_command(f["sync_A"]), f'exit {f["sync_A"]["exit"]}', "",
            "$ " + short_command(f["hold_B"]), f["hold_B"]["stderr"],
            f'exit {f["hold_B"]["exit"]}', "", row_header,
            *receipt_lines([r for r in conflict["history"] if r["entry"]["path"] == "notes.txt"])]),
        "copies": ("Bの conflicts の抜粋と管理コピーの独立照合", [
            f'hub    revision={conflict["hub"]["revision"]}  body={body(conflict["a_file"])}',
            f'B      revision={conflict["status"]["baseline"]["revision"]}  body={body(conflict["b_file"])}'
            f'  state={conflict["status"]["state"]}', "",
            f'.file-sync/stage/{hub_copy}   {body(conflict["q_copy"])}',
            f'  sha256 {conflict["q_copy"]["sha256"]}', "",
            f'.file-sync/stage/{local_copy}   {body(conflict["r_copy"])}',
            f'  sha256 {conflict["r_copy"]["sha256"]}']),
        "resolve": ("端末の実行結果・コマンド抜粋", [
            "$ file-sync resolve -root B -path notes.txt",
            "    --generation \"$generation\" --revision 2 --choose local",
            f'exit {f["resolve_B"]["exit"]}', "", "$ " + short_command(f["receive_A"]),
            f'exit {f["receive_A"]["exit"]}', "",
            f'A  revision={resolved["A_base"]["revision"]}  body={body(resolved["a_file"])}',
            f'B  revision={resolved["B_base"]["revision"]}  body={body(resolved["b_file"])}  state={resolved["state"]}',
            f'hub  revision={resolved["receipt"]["entry"]["revision"]}  op={resolved["receipt"]["op_id"]}']),
        "offline": ("Aの実行結果、ハブの行、復帰前のB", [
            "$ " + short_command(f["send_delete"]), f'exit {f["send_delete"]["exit"]}', "",
            f'hub   revision={offline["hub"]["revision"]}  content_hash=null  deleted=true',
            f'B     revision={offline["b_baseline"]["revision"]}  body={body(offline["b_file"])}',
            f'  sha256 {offline["b_file"]["sha256"]}']),
        "deleted": ("Bの実行結果・退避本文・受理履歴の抜粋", [
            "$ " + short_command(f["receive_delete"]), f'exit {f["receive_delete"]["exit"]}',
            f'B  revision={deleted["B_base"]["revision"]}  no file  status={deleted["plan"]["status"]}', "",
            f'.file-sync/stage/{deleted["plan"]["id"]}.trash   {body(deleted["b_trash"])}',
            f'  sha256 {deleted["b_trash"]["sha256"]}',
            f'hub blob  body={body(deleted["hub_blob"])}  same sha256', "",
            *receipt_lines(deleted["history"])]),
        "measure": ("実測・中央値（ミリ秒）", [
            "files    bytes/file    scan A      seed A      pull B    convergence",
            *[f'{case["files"]:>5}    {case["file_bytes"]:>10}    '
              + "    ".join(f'{case["statistics"][field]["median"]/1e6:>8.1f}'
                            for field in ("scan_a_ns", "seed_a_ns", "pull_b_ns", "convergence_ns"))
              for case in measurement["cases"]], "",
            "5 fresh trials per case / IPv4 loopback HTTP",
            "convergence = seed A + pull B"]),
    }


class Deck:
    def __init__(self, font, mono):
        self.font_path, self.mono_path = font, mono

    def font(self, size, mono=False):
        return ImageFont.truetype(self.mono_path if mono else self.font_path, size)

    def line(self, draw, point, value, size=30, color=INK, mono=False):
        font = self.font(size, mono)
        if point[0] + draw.textlength(value, font) > W - 108:
            raise ValueError(f"line overflows: {value}")
        draw.text(point, value, font=font, fill=color, anchor="lt")

    def render(self, scene, start, total, panels, sha):
        image = Image.new("RGB", (W, H), LIGHT)
        d = ImageDraw.Draw(image)
        self.line(d, (114, 64), scene["title"], 62)
        panel = (114, 164, W - 114, 872)
        d.rounded_rectangle(panel, radius=24, fill=DARK)
        d.rounded_rectangle((146, 202, W - 146, 281), radius=15, fill=BLUE)
        key = scene["id"]
        if key == "intro":
            self.line(d, (188, 224), "構成図と実演の対象コミット", 30, TEAL)
            for x, title, sub in [(278, "クライアント A", "CLI"), (804, "中央ハブ", "HTTP / SQLite"),
                                  (1328, "クライアント B", "CLI")]:
                d.rounded_rectangle((x, 374, x + 310, 531), 18, fill=BLUE, outline=TEAL, width=2)
                self.line(d, (x + 37, 411), title, 32, WHITE)
                self.line(d, (x + 37, 468), sub, 24, DIM, mono=True)
            for x in (597, 1124):
                d.line((x, 455, x + 197, 455), fill=TEAL, width=4)
                d.polygon(((x, 455), (x + 15, 445), (x + 15, 465)), fill=TEAL)
                d.polygon(((x + 197, 455), (x + 182, 445), (x + 182, 465)), fill=TEAL)
            self.line(d, (190, 630), "独立した3プロセス / 127.0.0.1", 35, WHITE)
            self.line(d, (190, 697), panels["startup"], 28, TEAL, mono=True)
            self.line(d, (190, 758), sha, 30, DIM, mono=True)
        elif key in ("recovery", "limits"):
            self.explanation(d, key)
        else:
            label, rows = panels[key]
            self.line(d, (188, 224), label, 30, TEAL)
            for index, row in enumerate(rows):
                color = TEAL if row.startswith("$ ") else WHITE
                self.line(d, (190, 317 + index * 50), row, 28, color, mono=True)

        d.rounded_rectangle((0, 906, W + 32, H + 24), 19, fill=DARK)
        caption = scene["caption"]
        font = self.font(39)
        rows = [""]
        for char in caption:
            if d.textlength(rows[-1] + char, font) > W - 232:
                rows.append("")
            rows[-1] += char
        if len(rows) > 2:
            raise ValueError(f"caption overflows: {caption}")
        for index, row in enumerate(rows):
            self.line(d, (114, 944 + 48 * index), row, 39, WHITE)
        d.rectangle((0, H - 8, W, H - 1), fill=BLUE)
        d.rectangle((0, H - 8, W * (start + scene["seconds"]) / total, H - 1), fill=TEAL)
        return image

    def explanation(self, draw, key):
        if key == "recovery":
            title = "障害試験の結果（実演とは別に実行）"
            points = [
                ("応答消失後の再送", "コミット直後にSIGKILL。同じIDの受理は1件、ハブは hello の版2。"),
                ("古いバックアップから復元", "新しい世代で起動。正しい認証でも旧世代の更新にはHTTP 409。"),
            ]
        else:
            title = "受入で確認した範囲"
            points = [
                ("Linux / WSL2 のLinuxファイルシステム", "2クライアント・1ハブ。1ファイル10 MiB、生存1,000件が上限。"),
                ("プロセス停止・注入試験", "デバイス試料は未実施。実機の電源断は受入範囲外。"),
            ]
        self.line(draw, (188, 224), title, 30, TEAL)
        for index, (heading, body) in enumerate(points):
            y = 350 + 208 * index
            self.line(draw, (190, y), heading, 47, WHITE)
            self.line(draw, (190, y + 79), body, 33, DIM)


def render(args):
    evidence = json.loads(args.evidence.read_text())
    measurement = json.loads(args.measurement.read_text())
    if evidence["result"] != "pass" or measurement["result"] != "pass":
        raise ValueError("verified demo and measurements are required")
    if evidence["product_sha"] != measurement["product_sha"]:
        raise ValueError("evidence and measurements must use the same product SHA")
    scenes = json.loads(SCENES.read_text())
    total = sum(s["seconds"] for s in scenes)
    deck = Deck(args.font, args.mono)
    actual = panels(evidence, measurement)
    with tempfile.TemporaryDirectory(prefix="file-sync-frames-") as temporary:
        root = Path(temporary)
        images = []
        start = 0
        for i, scene in enumerate(scenes):
            frame = deck.render(scene, start, total, actual, evidence["product_sha"])
            name = f"frame-{i:02}.png"
            frame.save(root / name)
            if i == 0:
                frame.resize((960, 540), Image.Resampling.LANCZOS).save(args.poster, quality=89,
                                                                         method=4)
            images.extend([f"file '{name}'", f'duration {scene["seconds"]}'])
            start += scene["seconds"]
        images.append(f"file '{name}'")
        manifest = root / "timeline.txt"
        manifest.write_text("\n".join(images) + "\n")
        subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "concat", "-safe", "1", "-i", manifest,
                        "-vf", f"fps={FPS}", "-t", str(total), "-an", "-c:v", "libx264", "-threads", "4",
                        "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
                        "-movflags", "+faststart", args.output], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poster", type=Path, required=True)
    parser.add_argument("--font", default="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    parser.add_argument("--mono", default="/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    args = parser.parse_args()
    if args.output.exists() or args.poster.exists():
        parser.error("use new output and poster paths")
    render(args)


if __name__ == "__main__":
    main()
