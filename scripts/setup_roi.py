"""
Интерактивная настройка зоны админ-кассы (ROI).
Мышью выдели прямоугольник вокруг места, где стоят люди у кассы.
Enter — сохранить, R — сбросить, Q — выход без сохранения.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.camera import create_source
from src.config import Roi, load_settings, save_roi


def main() -> None:
    settings = load_settings()
    source = create_source(
        settings.source_mode,
        settings.rtsp_url,
        settings.ivms_window_title,
    )

    frame = None
    for _ in range(60):
        frame = source.read()
        if frame is not None:
            break
    if frame is None:
        print("Не удалось получить кадр. Проверь RTSP / окно iVMS.")
        source.release()
        return

    h0, w0 = frame.shape[:2]
    max_w = 1280
    scale = min(1.0, max_w / w0)
    disp_w, disp_h = int(w0 * scale), int(h0 * scale)

    drawing = False
    start = (0, 0)
    end = (0, 0)
    done_box = None  # в координатах исходного кадра

    def to_src(x: int, y: int) -> tuple[int, int]:
        return int(x / scale), int(y / scale)

    def on_mouse(event, x, y, _flags, _param):
        nonlocal drawing, start, end, done_box
        sx, sy = to_src(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            drawing = True
            start = (sx, sy)
            end = (sx, sy)
            done_box = None
        elif event == cv2.EVENT_MOUSEMOVE and drawing:
            end = (sx, sy)
        elif event == cv2.EVENT_LBUTTONUP:
            drawing = False
            end = (sx, sy)
            x1, x2 = sorted([start[0], end[0]])
            y1, y2 = sorted([start[1], end[1]])
            if x2 - x1 > 10 and y2 - y1 > 10:
                done_box = (x1, y1, x2, y2)

    win = "ROI setup — drag rectangle, Enter=save, R=reset, Q=quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, disp_w, disp_h)
    cv2.setMouseCallback(win, on_mouse)

    print("Выдели мышью зону у админ-кассы, где ждут люди.")
    print("Enter — сохранить в config.yaml | R — сброс | Q — выход")

    while True:
        view = frame.copy()
        if drawing:
            cv2.rectangle(view, start, end, (0, 255, 255), 2)
        elif done_box:
            cv2.rectangle(
                view,
                (done_box[0], done_box[1]),
                (done_box[2], done_box[3]),
                (0, 255, 255),
                2,
            )

        show = cv2.resize(view, (disp_w, disp_h)) if scale != 1.0 else view
        cv2.imshow(win, show)
        key = cv2.waitKey(20) & 0xFF

        if key in (ord("q"), ord("Q"), 27):
            print("Выход без сохранения.")
            break
        if key in (ord("r"), ord("R")):
            done_box = None
        if key in (13, 10) and done_box:
            x1, y1, x2, y2 = done_box
            roi = Roi(
                x1=x1 / w0,
                y1=y1 / h0,
                x2=x2 / w0,
                y2=y2 / h0,
            ).clamp()
            save_roi(roi)
            print(
                f"ROI сохранён: ({roi.x1:.3f},{roi.y1:.3f})-({roi.x2:.3f},{roi.y2:.3f})"
            )
            break

        if not drawing and done_box is None:
            fresh = source.read()
            if fresh is not None:
                frame = fresh

    source.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
