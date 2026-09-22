"""Проверка, что камера / RTSP / окно iVMS отдаёт кадры."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.camera import create_source
from src.config import load_settings


def main() -> None:
    settings = load_settings()
    print(f"Режим: {settings.source_mode}")
    if settings.source_mode == "rtsp":
        masked = settings.rtsp_url
        if "@" in masked:
            # не печатаем пароль
            left, right = masked.split("@", 1)
            user = left.split("://", 1)[-1].split(":", 1)[0]
            proto = masked.split("://", 1)[0]
            masked = f"{proto}://{user}:***@{right}"
        print(f"RTSP: {masked}")

    source = create_source(
        settings.source_mode,
        settings.rtsp_url,
        settings.ivms_window_title,
    )

    ok_frames = 0
    for i in range(90):
        frame = source.read()
        if frame is None:
            continue
        ok_frames += 1
        h, w = frame.shape[:2]
        cv2.putText(
            frame,
            f"{w}x{h} frame#{ok_frames}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
        )
        show = frame
        if w > 1280:
            scale = 1280 / w
            show = cv2.resize(frame, (int(w * scale), int(h * scale)))
        cv2.imshow("Camera test — Q to quit", show)
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
            break

    source.release()
    cv2.destroyAllWindows()
    print(f"Получено кадров: {ok_frames}")
    if ok_frames == 0:
        print("FAIL: кадры не пришли. Проверь IP/логин/пароль камеры или окно iVMS.")
        sys.exit(1)
    print("OK: камера работает.")


if __name__ == "__main__":
    main()
