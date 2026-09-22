"""Проверка Telegram-бота: отправляет тестовое сообщение."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.telegram_notifier import TelegramNotifier


def main() -> None:
    settings = load_settings()
    notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
    ok = notifier.test()
    if ok:
        print("OK: сообщение ушло в Telegram. Проверь телефон.")
    else:
        print("FAIL: не удалось отправить. Проверь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
        sys.exit(1)


if __name__ == "__main__":
    main()
