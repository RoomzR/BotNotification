"""
CyberX Control — платформа мониторинга клуба.
Главный поток: экран камер + детекторы.
Фон: Telegram-бот (/help).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cyberx")


def main() -> None:
    from src.bot.admin_bot import AdminBot
    from src.platform.orchestrator import Platform

    platform = Platform()

    def bot_thread():
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            AdminBot().run_polling()
        except Exception:
            logger.exception("Telegram bot stopped")

    t = threading.Thread(target=bot_thread, name="admin-bot", daemon=True)
    t.start()
    logger.info("Telegram bot в фоне. На экране — мозаика камер. Q = выход.")
    # OpenCV GUI только в главном потоке
    platform.run()


if __name__ == "__main__":
    main()
