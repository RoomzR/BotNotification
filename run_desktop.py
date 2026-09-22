"""
CyberX Control — Desktop для сотрудника.
Меню: камеры, «со своим», каталог продукции, алерты + Telegram-бот.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    from src.desktop.app import DesktopApp

    DesktopApp().run()


if __name__ == "__main__":
    main()
