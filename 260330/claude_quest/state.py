"""Player state and progress management."""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Set, List

SAVE_FILE = os.path.join(os.path.dirname(__file__), ".save.json")

LEVEL_THRESHOLDS = [0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200, 4000]
LEVEL_NAMES = [
    "見習い", "初心者", "学習者", "探索者", "実践者",
    "熟練者", "上級者", "エキスパート", "マスター", "グランドマスター", "クロードの申し子"
]

@dataclass
class PlayerState:
    name: str = "冒険者"
    xp: int = 0
    level: int = 1
    completed_chapters: List[str] = field(default_factory=list)
    completed_quizzes: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    total_correct: int = 0
    total_questions: int = 0
    streak: int = 0
    max_streak: int = 0

    @property
    def level_name(self) -> str:
        idx = min(self.level - 1, len(LEVEL_NAMES) - 1)
        return LEVEL_NAMES[idx]

    @property
    def xp_for_next_level(self) -> int:
        if self.level >= len(LEVEL_THRESHOLDS):
            return 9999
        return LEVEL_THRESHOLDS[self.level] - self.xp

    @property
    def xp_current_level(self) -> int:
        prev = LEVEL_THRESHOLDS[self.level - 1] if self.level - 1 < len(LEVEL_THRESHOLDS) else 0
        return self.xp - prev

    @property
    def xp_level_range(self) -> int:
        if self.level >= len(LEVEL_THRESHOLDS):
            return 1
        prev = LEVEL_THRESHOLDS[self.level - 1] if self.level - 1 < len(LEVEL_THRESHOLDS) else 0
        return LEVEL_THRESHOLDS[self.level] - prev

    @property
    def accuracy(self) -> float:
        if self.total_questions == 0:
            return 0.0
        return self.total_correct / self.total_questions * 100

    def add_xp(self, amount: int) -> bool:
        """Add XP and return True if leveled up."""
        self.xp += amount
        new_level = 1
        for i, threshold in enumerate(LEVEL_THRESHOLDS):
            if self.xp >= threshold:
                new_level = i + 1
        new_level = min(new_level, len(LEVEL_NAMES))
        leveled_up = new_level > self.level
        self.level = new_level
        return leveled_up

    def answer_question(self, correct: bool):
        self.total_questions += 1
        if correct:
            self.total_correct += 1
            self.streak += 1
            self.max_streak = max(self.streak, self.max_streak)
        else:
            self.streak = 0

    def unlock_achievement(self, achievement: str) -> bool:
        if achievement not in self.achievements:
            self.achievements.append(achievement)
            return True
        return False

    def save(self):
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "PlayerState":
        if not os.path.exists(SAVE_FILE):
            return cls()
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)
        except Exception:
            return cls()

    @classmethod
    def reset(cls) -> "PlayerState":
        if os.path.exists(SAVE_FILE):
            os.remove(SAVE_FILE)
        return cls()
