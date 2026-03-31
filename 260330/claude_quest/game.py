"""Main game logic."""
import time
from rich.console import Console
from rich.prompt import Prompt, Confirm

from state import PlayerState, LEVEL_THRESHOLDS
from content import CHAPTERS, ACHIEVEMENTS
import ui

console = Console()


class Game:
    def __init__(self):
        self.state = PlayerState.load()

    def run(self):
        """Main game loop."""
        ui.show_title_screen()
        time.sleep(0.5)

        # First time setup
        if not self.state.name or self.state.name == "冒険者":
            self._first_time_setup()

        while True:
            action = self._show_main_menu()
            if action == "play":
                self._chapter_select()
            elif action == "stats":
                ui.show_stats(self.state)
                ui.press_enter()
            elif action == "reset":
                if Confirm.ask("[red]本当にリセットしますか？全進捗が消えます[/red]"):
                    self.state = PlayerState.reset()
                    console.print("[yellow]リセットしました[/yellow]")
                    time.sleep(1)
            elif action == "quit":
                console.print("\n[cyan]また遊んでね！Claude Code を楽しんでください！[/cyan]\n")
                break

    def _first_time_setup(self):
        console.print()
        console.print("[bold cyan]ようこそ！Claude Code Quest へ！[/bold cyan]")
        console.print("[dim]Claude Code の基礎をゲーム感覚で学べるチュートリアルです。[/dim]")
        console.print()
        name = Prompt.ask("[bold yellow]冒険者よ、名を名乗れ[/bold yellow]", default="勇者")
        self.state.name = name
        self.state.save()
        console.print(f"\n[bold green]{name}よ、旅立ちの時だ！[/bold green]")
        time.sleep(1)

    def _show_main_menu(self) -> str:
        ui.clear_screen()
        console.print()
        from rich.panel import Panel
        from rich.align import Align
        console.print(Panel(
            Align.center(
                f"⚡ [bold cyan]Claude Code Quest[/bold cyan] ⚡\n\n"
                f"[bold white]{self.state.name}[/bold white] | "
                f"Lv.[bold]{self.state.level}[/bold] [{self.state.level_name}] | "
                f"[yellow]{self.state.xp} XP[/yellow]"
            ),
            border_style="cyan",
            padding=(1, 3),
        ))
        console.print()

        completed = len(self.state.completed_chapters)
        total = len(CHAPTERS)
        console.print(f"  [dim]クリア章数: {completed}/{total}[/dim]")
        console.print()
        console.print("  [bold cyan]1.[/bold cyan] ゲームをプレイ（章を選ぶ）")
        console.print("  [bold cyan]2.[/bold cyan] 統計・実績を見る")
        console.print("  [bold cyan]3.[/bold cyan] データをリセット")
        console.print("  [bold cyan]4.[/bold cyan] 終了")
        console.print()

        choices = {"1": "play", "2": "stats", "3": "reset", "4": "quit"}
        while True:
            raw = console.input("[bold yellow]選択 (1-4): [/bold yellow]").strip()
            if raw in choices:
                return choices[raw]
            console.print("[red]1〜4を入力してください[/red]")

    def _chapter_select(self):
        ui.show_world_map(CHAPTERS, self.state.completed_chapters, self.state)

        console.print("  [bold cyan]番号[/bold cyan] を入力して章を選ぶ  |  [bold]B[/bold] で戻る")
        console.print()

        while True:
            raw = console.input("[bold yellow]選択: [/bold yellow]").strip().upper()
            if raw == "B":
                return
            try:
                idx = int(raw)
                if 0 <= idx < len(CHAPTERS):
                    self._play_chapter(CHAPTERS[idx])
                    return
                console.print(f"[red]0〜{len(CHAPTERS)-1}を入力してください[/red]")
            except ValueError:
                console.print("[red]番号またはBを入力してください[/red]")

    def _play_chapter(self, chapter: dict):
        ui.show_chapter_header(chapter, self.state)

        console.print("[dim]このチャプターは レッスン + クイズ で構成されています。[/dim]")
        console.print()
        ui.press_enter("Enterで開始...")

        # Lessons
        lessons = chapter["lessons"]
        for i, lesson in enumerate(lessons, 1):
            ui.show_lesson(lesson, i, len(lessons))
            ui.press_enter()

        # Quiz section
        console.print()
        from rich.rule import Rule
        console.print(Rule("[bold yellow]クイズタイム！[/bold yellow]", style="yellow"))
        console.print()
        console.print("[dim]レッスンの内容を確認するクイズです。頑張れ！[/dim]")
        console.print()
        ui.press_enter("Enterでクイズ開始...")

        quizzes = chapter["quiz"]
        score = 0
        xp_from_quiz = 0
        chapter_all_correct = True

        for i, q in enumerate(quizzes, 1):
            if q["id"] in self.state.completed_quizzes:
                # Already answered before - show again but don't double-count
                pass

            start_time = time.time()
            chosen = ui.show_quiz_question(q, i, len(quizzes))
            elapsed = time.time() - start_time

            correct = chosen == q["answer"]
            self.state.answer_question(correct)

            if correct:
                score += 1
                xp_gain = q["xp"]
                xp_from_quiz += xp_gain
                leveled_up = self.state.add_xp(xp_gain)
                ui.show_quiz_result(True, q["explanation"], xp_gain)

                if q["id"] not in self.state.completed_quizzes:
                    self.state.completed_quizzes.append(q["id"])

                # Achievements
                if len(self.state.completed_quizzes) == 1:
                    self._unlock_achievement("first_quiz")

                if self.state.streak >= 5:
                    self._unlock_achievement("streak_5")

                if elapsed <= 3.0:
                    self._unlock_achievement("speed_demon")

                if leveled_up:
                    console.print(
                        f"\n  [bold magenta]★ LEVEL UP! Lv.{self.state.level} "
                        f"[{self.state.level_name}] ★[/bold magenta]"
                    )
            else:
                chapter_all_correct = False
                ui.show_quiz_result(False, q["explanation"], 0)

            self.state.save()
            ui.press_enter()

        # Chapter XP bonus
        xp_bonus = chapter["xp_reward"]
        leveled_up = self.state.add_xp(xp_bonus)
        xp_from_quiz += xp_bonus

        if chapter["id"] not in self.state.completed_chapters:
            self.state.completed_chapters.append(chapter["id"])

        if chapter_all_correct:
            self._unlock_achievement("perfect_chapter")

        if len(self.state.completed_chapters) == len(CHAPTERS):
            self._unlock_achievement("all_chapters")

        self.state.save()

        ui.show_chapter_complete(chapter, score, len(quizzes), xp_from_quiz)
        if leveled_up:
            console.print(
                f"  [bold magenta]★ LEVEL UP! Lv.{self.state.level} "
                f"[{self.state.level_name}] に到達！ ★[/bold magenta]"
            )

        console.print()
        ui.press_enter("Enterでワールドマップに戻る...")

    def _unlock_achievement(self, key: str):
        if key not in ACHIEVEMENTS:
            return
        ach = ACHIEVEMENTS[key]
        if self.state.unlock_achievement(key):
            xp_gain = ach["xp"]
            self.state.add_xp(xp_gain)
            ui.show_achievement(ach["name"], ach["description"], ach["emoji"], xp_gain)
            self.state.save()
            time.sleep(0.5)
