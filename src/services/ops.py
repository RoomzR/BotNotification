"""Вспомогательные сервисы: температура, устройства, задачи."""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import Optional

import httpx

from src.storage.db import Store

logger = logging.getLogger(__name__)


class TemperatureService:
    def __init__(self, store: Store, min_c: float, max_c: float):
        self.store = store
        self.min_c = min_c
        self.max_c = max_c

    def record(self, zone: str, celsius: float, source: str = "manual") -> Optional[str]:
        self.store.add_temperature(zone, celsius, source)
        if celsius < self.min_c:
            return f"🌡️ {zone}: холодно {celsius:.1f}°C (мин {self.min_c})"
        if celsius > self.max_c:
            return f"🌡️ {zone}: жарко {celsius:.1f}°C (макс {self.max_c})"
        return None

    def summary(self) -> str:
        latest = self.store.latest_temps()
        if not latest:
            return "Температуры ещё не вносились. /temp <зона> <C>"
        lines = [f"• {z}: {v:.1f}°C" for z, v in sorted(latest.items())]
        return "Температура по зонам:\n" + "\n".join(lines)


class DeviceService:
    def __init__(self, store: Store):
        self.store = store

    def ping_host(self, host: str) -> bool:
        param = "-n" if platform.system().lower() == "windows" else "-c"
        try:
            r = subprocess.run(
                ["ping", param, "1", host],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ok = r.returncode == 0
        except Exception:
            ok = False
        self.store.add_device_check(host, ok, "ping")
        return ok

    def check_http(self, url: str, name: str) -> bool:
        try:
            r = httpx.get(url, timeout=5)
            ok = r.status_code < 500
            detail = f"http {r.status_code}"
        except Exception as exc:
            ok = False
            detail = str(exc)
        self.store.add_device_check(name, ok, detail)
        return ok

    def mark_gamepad(self, seat: str, ok: bool, note: str = "") -> None:
        self.store.add_device_check(f"gamepad:{seat}", ok, note or ("ok" if ok else "broken"))


class TaskService:
    def __init__(self, store: Store):
        self.store = store

    def create(self, title: str, detail: str, assignee: str = "admin", due_hours: float = 24) -> int:
        return self.store.add_task(title, detail, assignee, due_hours)

    def done(self, task_id: int, result: str = "done") -> bool:
        return self.store.complete_task(task_id, result)

    def report_overdue(self) -> list[str]:
        rows = self.store.overdue_tasks()
        return [f"#{r['id']} {r['title']} → {r['assignee']}" for r in rows]
