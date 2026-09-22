"""Источники видео: RTSP камеры Hikvision или захват окна iVMS-4200."""

from __future__ import annotations

import logging
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FrameSource:
    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError

    def release(self) -> None:
        pass


class RtspSource(FrameSource):
    """Поток RTSP с камеры Hikvision."""

    def __init__(self, url: str, reconnect_delay: float = 3.0):
        self.url = url
        self.reconnect_delay = reconnect_delay
        self._cap: Optional[cv2.VideoCapture] = None
        self._open()

    def _open(self) -> None:
        if self._cap is not None:
            self._cap.release()
        # TCP стабильнее UDP в локальной сети
        self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self._cap.isOpened():
            logger.error("Не удалось открыть RTSP: %s", self._mask(self.url))
        else:
            logger.info("RTSP подключён: %s", self._mask(self.url))

    @staticmethod
    def _mask(url: str) -> str:
        # Скрываем пароль в логах
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            if "@" in rest and ":" in rest.split("@", 1)[0]:
                creds, host = rest.split("@", 1)
                user = creds.split(":", 1)[0]
                return f"{prefix}://{user}:***@{host}"
        return url

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None or not self._cap.isOpened():
            time.sleep(self.reconnect_delay)
            self._open()
            return None

        ok, frame = self._cap.read()
        if not ok or frame is None:
            logger.warning("Потерян кадр RTSP, переподключение...")
            time.sleep(self.reconnect_delay)
            self._open()
            return None
        return frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class ScreenSource(FrameSource):
    """Захват области экрана / окна iVMS-4200 (запасной вариант)."""

    def __init__(self, window_title: str = "iVMS-4200", monitor_index: int = 1):
        import mss

        self.window_title = window_title
        self.monitor_index = monitor_index
        self._sct = mss.mss()
        self._region = self._resolve_region()
        logger.info("Захват экрана: %s", self._region)

    def _resolve_region(self) -> dict:
        # Пытаемся найти окно iVMS по заголовку (Windows)
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found = {}

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def enum_proc(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if self.window_title.lower() in title.lower():
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w > 200 and h > 200:
                        found["left"] = rect.left
                        found["top"] = rect.top
                        found["width"] = w
                        found["height"] = h
                        return False
                return True

            user32.EnumWindows(enum_proc, 0)
            if found:
                logger.info("Найдено окно «%s»", self.window_title)
                return found
        except Exception as exc:
            logger.warning("Не удалось найти окно iVMS: %s", exc)

        mon = self._sct.monitors[self.monitor_index]
        logger.warning(
            "Окно iVMS не найдено — захватывается весь монитор %s. "
            "Открой iVMS-4200 с камерой Reception 01_REG на весь экран.",
            self.monitor_index,
        )
        return {
            "left": mon["left"],
            "top": mon["top"],
            "width": mon["width"],
            "height": mon["height"],
        }

    def read(self) -> Optional[np.ndarray]:
        shot = self._sct.grab(self._region)
        frame = np.array(shot)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    def release(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


def create_source(mode: str, rtsp_url: str, window_title: str) -> FrameSource:
    if mode == "screen":
        return ScreenSource(window_title=window_title)
    return RtspSource(rtsp_url)
