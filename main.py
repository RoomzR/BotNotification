"""
Мониторинг зоны админ-кассы по камере Hikvision / iVMS-4200.
При появлении людей в ROI — уведомление в Telegram.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.camera import create_source
from src.config import load_settings
from src.detector import PersonDetector, draw_overlay
from src.telegram_notifier import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("admin-desk")


def main() -> None:
    settings = load_settings()
    source = create_source(
        settings.source_mode,
        settings.rtsp_url,
        settings.ivms_window_title,
    )
    detector = PersonDetector(settings.model, settings.confidence)
    notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)

    presence_started: float | None = None
    last_alert_at = 0.0
    frame_i = 0
    last_count = 0
    last_dets = []

    logger.info(
        "Старт. Режим=%s | presence=%ss | cooldown=%ss | ROI=(%.2f,%.2f)-(%.2f,%.2f)",
        settings.source_mode,
        settings.presence_seconds,
        settings.cooldown_seconds,
        settings.roi.x1,
        settings.roi.y1,
        settings.roi.x2,
        settings.roi.y2,
    )
    logger.info("Нажми Q в окне превью, чтобы выйти.")

    try:
        while True:
            frame = source.read()
            if frame is None:
                continue

            frame_i += 1
            if frame_i % settings.frame_skip == 0:
                last_dets = detector.detect(frame, settings.roi)
                last_count = len(last_dets)

            now = time.time()
            if last_count > 0:
                if presence_started is None:
                    presence_started = now
                waited = now - presence_started
                status = f"People: {last_count} | waiting {waited:.0f}/{settings.presence_seconds:.0f}s"
                ready = waited >= settings.presence_seconds
                cooled = (now - last_alert_at) >= settings.cooldown_seconds
                if ready and cooled:
                    text = settings.message_template.format(count=last_count)
                    preview = draw_overlay(frame, settings.roi, last_dets, status)
                    photo = preview if settings.send_photo else None
                    if notifier.send_alert(text, photo):
                        last_alert_at = now
                        logger.info("Уведомление отправлено (%s чел.)", last_count)
            else:
                presence_started = None
                status = "Empty | watching admin desk"

            if settings.show_preview:
                preview = draw_overlay(frame, settings.roi, last_dets, status)
                # Уменьшаем большое окно для удобства
                h, w = preview.shape[:2]
                max_w = 1280
                if w > max_w:
                    scale = max_w / w
                    preview = cv2.resize(preview, (int(w * scale), int(h * scale)))
                cv2.imshow("Admin desk monitor — Q to quit", preview)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    break
            else:
                time.sleep(0.05)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    finally:
        source.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
