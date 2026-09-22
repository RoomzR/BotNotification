"""Ждёт сообщение боту и сохраняет chat_id в .env."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def main() -> None:
    load_dotenv(ENV_PATH)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Нет TELEGRAM_BOT_TOKEN в .env")
        sys.exit(1)

    print("Бот: @CyberXNotification_bot")
    print("1) Открой Telegram")
    print("2) Найди @CyberXNotification_bot")
    print("3) Нажми Start / напиши привет")
    print("Жду сообщение", end="", flush=True)

    api = f"https://api.telegram.org/bot{token}"
    with httpx.Client(timeout=30) as client:
        # сбросим старые апдейты
        client.get(f"{api}/getUpdates", params={"offset": -1})
        for _ in range(120):  # ~4 минуты
            print(".", end="", flush=True)
            r = client.get(f"{api}/getUpdates", params={"timeout": 2})
            data = r.json()
            for upd in data.get("result", []):
                msg = upd.get("message") or upd.get("my_chat_member") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if chat_id is not None:
                    text = ENV_PATH.read_text(encoding="utf-8")
                    if re.search(r"^TELEGRAM_CHAT_ID=.*$", text, re.M):
                        text = re.sub(
                            r"^TELEGRAM_CHAT_ID=.*$",
                            f"TELEGRAM_CHAT_ID={chat_id}",
                            text,
                            flags=re.M,
                        )
                    else:
                        text += f"\nTELEGRAM_CHAT_ID={chat_id}\n"
                    ENV_PATH.write_text(text, encoding="utf-8")
                    print(f"\nOK: chat_id={chat_id} сохранён в .env")
                    # тестовое сообщение
                    client.post(
                        f"{api}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": "✅ Бот подключён. Уведомления с админ-кассы будут приходить сюда.",
                        },
                    )
                    return
            time.sleep(1)
    print("\nНе дождался сообщения. Запусти скрипт ещё раз после /start боту.")
    sys.exit(1)


if __name__ == "__main__":
    main()
