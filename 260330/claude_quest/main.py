#!/usr/bin/env python3
"""Claude Code Quest - Learn Claude Code through a game!

Usage:
    python3 main.py
"""
import sys
import os

# Add the directory to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game import Game


def main():
    try:
        game = Game()
        game.run()
    except KeyboardInterrupt:
        print("\n\nまた遊んでね！ (Ctrl+C で終了)")
        sys.exit(0)


if __name__ == "__main__":
    main()
