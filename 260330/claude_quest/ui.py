"""UI components using Rich library."""
import time
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
from rich.align import Align
from rich import box
from rich.live import Live
from rich.spinner import Spinner
from rich.rule import Rule
import random

console = Console()

LOGO = r"""
   _____ _                 _        _____          _
  / ____| |               | |      / ____|        | |
 | |    | | __ _ _   _  __| | ___ | |     ___   __| | ___
 | |    | |/ _` | | | |/ _` |/ _ \| |    / _ \ / _` |/ _ \
 | |____| | (_| | |_| | (_| |  __/| |___| (_) | (_| |  __/
  \_____|_|\__,_|\__,_|\__,_|\___| \_____\___/ \__,_|\___|

   ██████╗ ██╗   ██╗███████╗███████╗████████╗
  ██╔═══██╗██║   ██║██╔════╝██╔════╝╚══██╔══╝
  ██║   ██║██║   ██║█████╗  ███████╗   ██║
  ██║▄▄ ██║██║   ██║██╔══╝  ╚════██║   ██║
  ╚██████╔╝╚██████╔╝███████╗███████║   ██║
   ╚══▀▀═╝  ╚═════╝ ╚══════╝╚══════╝   ╚═╝
"""

SMALL_LOGO = "⚡ [bold cyan]Claude Code Quest[/bold cyan] ⚡"


def clear_screen():
    console.clear()


def show_logo():
    console.print(Panel(
        Align.center(Text(LOGO, style="bold cyan")),
        border_style="cyan",
        padding=(0, 2),
    ))


def show_title_screen():
    clear_screen()
    console.print()
    console.print(Align.center(Text(LOGO, style="bold cyan")))
    console.print()
    console.print(Align.center(
        Text("Claude Code を ゲームで学ぶ 究極チュートリアル", style="bold yellow")
    ))
    console.print(Align.center(
        Text("v1.0 — Learn by Playing!", style="dim")
    ))
    console.print()


def show_xp_gain(amount: int, leveled_up: bool = False, level: int = 1, level_name: str = ""):
    console.print(f"  [bold yellow]+{amount} XP[/bold yellow] 獲得！", end="")
    if leveled_up:
        console.print(f"  [bold magenta]★ レベルアップ！ Lv.{level} [{level_name}] に到達！ ★[/bold magenta]")
    else:
        console.print()


def show_achievement(name: str, description: str, emoji: str, xp: int):
    console.print()
    console.print(Panel(
        f"{emoji} [bold yellow]実績解除！[/bold yellow]\n"
        f"  [bold white]{name}[/bold white]\n"
        f"  [dim]{description}[/dim]\n"
        f"  [yellow]+{xp} XP[/yellow]",
        border_style="yellow",
        title="[bold yellow]ACHIEVEMENT UNLOCKED[/bold yellow]",
        padding=(0, 2),
    ))


def show_status_bar(state):
    bar_filled = int((state.xp_current_level / max(state.xp_level_range, 1)) * 20)
    bar = "[cyan]" + "█" * bar_filled + "[/cyan]" + "░" * (20 - bar_filled)
    console.print(
        f"  [bold]Lv.{state.level}[/bold] [{state.level_name}]  "
        f"XP: {bar} {state.xp_current_level}/{state.xp_level_range}  "
        f"[yellow]Total: {state.xp}[/yellow]"
    )


def show_chapter_header(chapter: dict, state):
    clear_screen()
    console.print()
    console.print(Panel(
        f"{chapter['emoji']} [bold white]{chapter['title']}[/bold white]\n\n"
        f"[dim]{chapter['description']}[/dim]",
        border_style="cyan",
        padding=(1, 3),
    ))
    show_status_bar(state)
    console.print()


def show_lesson(lesson: dict, lesson_num: int, total: int):
    console.print(Rule(
        f"[bold cyan]レッスン {lesson_num}/{total}: {lesson['title']}[/bold cyan]",
        style="cyan"
    ))
    console.print()
    console.print(Panel(
        lesson["content"],
        border_style="blue",
        padding=(1, 3),
    ))
    console.print()


def show_quiz_question(question: dict, q_num: int, total: int) -> int:
    """Display quiz question and get answer. Returns chosen index (0-based)."""
    console.print()
    console.print(Rule(
        f"[bold yellow]クイズ {q_num}/{total}[/bold yellow]",
        style="yellow"
    ))
    console.print()
    console.print(Panel(
        f"[bold white]{question['question']}[/bold white]",
        border_style="yellow",
        padding=(1, 3),
    ))

    for i, choice in enumerate(question["choices"]):
        console.print(f"  [bold cyan]{i + 1}.[/bold cyan] {choice}")

    console.print()

    while True:
        try:
            raw = console.input("[bold yellow]答えを入力 (番号): [/bold yellow]").strip()
            num = int(raw)
            if 1 <= num <= len(question["choices"]):
                return num - 1
            console.print(f"  [red]1〜{len(question['choices'])}の数字を入力してください[/red]")
        except (ValueError, KeyboardInterrupt):
            console.print("  [red]数字を入力してください[/red]")


def show_quiz_result(correct: bool, explanation: str, xp: int):
    if correct:
        console.print()
        console.print(Panel(
            f"[bold green]✓ 正解！[/bold green]\n\n{explanation}",
            border_style="green",
            padding=(1, 2),
        ))
        console.print(f"  [bold yellow]+{xp} XP[/bold yellow]")
    else:
        console.print()
        console.print(Panel(
            f"[bold red]✗ 不正解...[/bold red]\n\n{explanation}",
            border_style="red",
            padding=(1, 2),
        ))
    console.print()


def show_chapter_complete(chapter: dict, score: int, total: int, xp_gained: int):
    percent = int(score / total * 100) if total > 0 else 0
    stars = "★" * min(3, score) + "☆" * max(0, 3 - score)

    if percent == 100:
        grade = "[bold green]完璧！[/bold green]"
        msg = "全問正解！素晴らしい！"
    elif percent >= 70:
        grade = "[bold yellow]合格！[/bold yellow]"
        msg = "よくできました！"
    else:
        grade = "[bold red]もう少し！[/bold red]"
        msg = "復習してもう一度チャレンジ！"

    console.print()
    console.print(Panel(
        f"{chapter['emoji']} [bold white]{chapter['title']}[/bold white] — クリア！\n\n"
        f"  スコア: [bold]{score}/{total}[/bold] ({percent}%)\n"
        f"  評価: {grade} {msg}\n"
        f"  [bold yellow]+{xp_gained} XP 獲得！[/bold yellow]",
        border_style="magenta",
        title="[bold magenta]CHAPTER COMPLETE[/bold magenta]",
        padding=(1, 3),
    ))


def show_world_map(chapters: list, completed: list, state):
    clear_screen()
    console.print()
    console.print(Panel(
        SMALL_LOGO,
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()
    show_status_bar(state)
    console.print()
    console.print(Rule("[bold cyan]ワールドマップ[/bold cyan]", style="cyan"))
    console.print()

    table = Table(box=box.ROUNDED, border_style="dim", show_header=True,
                  header_style="bold cyan", padding=(0, 1))
    table.add_column("#", width=3, justify="center")
    table.add_column("章", min_width=30)
    table.add_column("状態", width=10, justify="center")
    table.add_column("報酬XP", width=8, justify="right")

    for i, ch in enumerate(chapters):
        done = ch["id"] in completed
        status = "[green]✓ クリア[/green]" if done else "[yellow]未クリア[/yellow]"
        xp_text = f"[yellow]{ch['xp_reward']}[/yellow]"
        title_str = f"{ch['emoji']} {ch['title']}"
        table.add_row(str(i), title_str, status, xp_text)

    console.print(table)
    console.print()


def show_final_results(state):
    clear_screen()
    console.print()
    console.print(Panel(
        Align.center(
            f"[bold yellow]★ おめでとうございます！ ★[/bold yellow]\n\n"
            f"[bold white]全章クリア達成！[/bold white]\n\n"
            f"[cyan]{state.name}[/cyan] は [bold magenta]{state.level_name}[/bold magenta] になった！\n\n"
            f"[dim]最終スコア:[/dim]\n"
            f"  総XP: [yellow]{state.xp}[/yellow]\n"
            f"  レベル: [bold]{state.level}[/bold]\n"
            f"  正解率: [green]{state.accuracy:.1f}%[/green] ({state.total_correct}/{state.total_questions})\n"
            f"  最大連続正解: [bold]{state.max_streak}[/bold]\n"
            f"  実績: [bold]{len(state.achievements)}[/bold] 個解除\n"
        ),
        border_style="yellow",
        title="[bold yellow]GAME COMPLETE[/bold yellow]",
        padding=(1, 4),
    ))


def show_stats(state):
    clear_screen()
    console.print()
    console.print(Panel(
        f"[bold cyan]プレイヤー統計[/bold cyan]",
        border_style="cyan",
    ))

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("項目", style="dim", min_width=20)
    table.add_column("値", style="bold white")

    table.add_row("プレイヤー名", state.name)
    table.add_row("レベル", f"Lv.{state.level} [{state.level_name}]")
    table.add_row("総XP", str(state.xp))
    table.add_row("次のレベルまで", f"{state.xp_for_next_level} XP")
    table.add_row("正解数", f"{state.total_correct} / {state.total_questions}")
    table.add_row("正解率", f"{state.accuracy:.1f}%")
    table.add_row("最大連続正解", str(state.max_streak))
    table.add_row("クリア章数", str(len(state.completed_chapters)))
    table.add_row("獲得実績数", str(len(state.achievements)))

    console.print(table)

    if state.achievements:
        console.print()
        console.print("[bold yellow]獲得実績:[/bold yellow]")
        for ach in state.achievements:
            console.print(f"  • [yellow]{ach}[/yellow]")

    console.print()


def press_enter(message: str = "Enterキーで続ける..."):
    console.input(f"[dim]{message}[/dim]")


def loading_animation(message: str, duration: float = 0.8):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    end_time = time.time() + duration
    i = 0
    while time.time() < end_time:
        console.print(f"\r[cyan]{frames[i % len(frames)]}[/cyan] {message}", end="")
        time.sleep(0.08)
        i += 1
    console.print(f"\r[green]✓[/green] {message}          ")
