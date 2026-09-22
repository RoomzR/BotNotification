"""Отправка уведомлений в Telegram."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import cv2
import numpy as np
from telegram import Bot
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        request = HTTPXRequest(connection_pool_size=4, read_timeout=30, write_timeout=30)
        self.bot = Bot(token=token, request=request)
        self.chat_id = chat_id
        self._loop = asyncio.new_event_loop()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def send_alert(
        self,
        text: str,
        frame: Optional[np.ndarray] = None,
    ) -> bool:
        try:
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok:
                    self._run(
                        self.bot.send_photo(
                            chat_id=self.chat_id,
                            photo=buf.tobytes(),
                            caption=text,
                        )
                    )
                    logger.info("Telegram: фото+текст отправлены")
                    return True

            self._run(self.bot.send_message(chat_id=self.chat_id, text=text))
            logger.info("Telegram: текст отправлен")
            return True
        except Exception as exc:
            logger.exception("Ошибка Telegram: %s", exc)
            return False

    def test(self) -> bool:
        return self.send_alert("✅ Бот админ-кассы подключён. Уведомления будут приходить сюда.")
