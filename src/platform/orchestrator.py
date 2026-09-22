"""Оркестратор мульти-камерного мониторинга CyberX."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml
from dotenv import load_dotenv

from src.camera import RtspSource
from src.langame.client import LangameClient
from src.platform.dashboard import CamTile, build_dashboard
from src.services.ops import DeviceService, TaskService, TemperatureService
from src.storage.db import Store
from src.telegram_notifier import TelegramNotifier
from src.vision.analyzers import FrameAnalyzer, Finding, draw_findings
from src.vision.engine import YoloEngine

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("cyberx.platform")


class AlertGate:
    """Confirm требует непрерывного присутствия: при пропуске цикла таймер сбрасывается."""

    def __init__(self, cooldown: float, confirm: float, gap_reset: float = 8.0):
        self.cooldown = cooldown
        self.confirm = confirm
        self.gap_reset = gap_reset
        self._seen: Dict[str, float] = {}
        self._last_hit: Dict[str, float] = {}
        self._last_sent: Dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last_hit = self._last_hit.get(key)
        if last_hit is not None and (now - last_hit) > self.gap_reset:
            self._seen.pop(key, None)
        self._last_hit[key] = now

        started = self._seen.get(key)
        if started is None:
            self._seen[key] = now
            return False
        if now - started < self.confirm:
            return False
        last = self._last_sent.get(key, 0)
        if now - last < self.cooldown:
            return False
        self._last_sent[key] = now
        self._seen.pop(key, None)
        return True

    def note_absent(self, active_keys: set) -> None:
        """Сброс таймеров для ключей, которых нет в текущем цикле."""
        now = time.time()
        for key in list(self._seen.keys()):
            if key in active_keys:
                continue
            last_hit = self._last_hit.get(key, 0)
            if now - last_hit > self.gap_reset:
                self._seen.pop(key, None)

    def note_absent_for_camera(self, camera: str, active_keys: set) -> None:
        """Как note_absent, но только для ключей вида *:camera:*."""
        now = time.time()
        needle = f":{camera}:"
        for key in list(self._seen.keys()):
            if needle not in key:
                continue
            if key in active_keys:
                continue
            last_hit = self._last_hit.get(key, 0)
            if now - last_hit > self.gap_reset:
                self._seen.pop(key, None)

    def allow_cooldown(self, key: str, now: float) -> bool:
        """Только cooldown (для очереди Reception, где уже есть presence_seconds)."""
        last = self._last_sent.get(key, 0)
        if now - last < self.cooldown:
            return False
        self._last_sent[key] = now
        return True


class Platform:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        with open(ROOT / "platform.yaml", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

        self.store = Store()
        self.notifier = TelegramNotifier(
            os.environ["TELEGRAM_BOT_TOKEN"].strip(),
            os.environ["TELEGRAM_CHAT_ID"].strip(),
        )
        vision = self.cfg.get("vision", {})
        self.engine = YoloEngine(vision.get("model", "yolov8n.pt"), float(vision.get("confidence", 0.4)))
        self.analyzer = FrameAnalyzer(self.engine)
        self.gate = AlertGate(
            float(vision.get("cooldown_seconds", 180)),
            float(vision.get("confirm_seconds", 8)),
        )
        alerts = self.cfg.get("alerts", {})
        self.soft_gate = AlertGate(
            float(alerts.get("soft_cooldown_seconds", 600)),
            float(alerts.get("soft_confirm_seconds", 25)),
        )
        lg = self.cfg.get("langame", {})
        self.langame = LangameClient(
            lg.get("mode", "stub"),
            lg.get("base_url", ""),
            lg.get("api_token", "") or os.getenv("LANGAME_API_TOKEN", ""),
            self.store,
            lg.get("pc_agent_url", "") or os.getenv("LANGAME_PC_AGENT_URL", ""),
        )
        tcfg = self.cfg.get("temperature", {})
        self.temps = TemperatureService(self.store, float(tcfg.get("min_c", 18)), float(tcfg.get("max_c", 27)))
        self.devices = DeviceService(self.store)
        self.tasks = TaskService(self.store)
        self.modules = self.cfg.get("modules", {})
        self._sources: Dict[str, RtspSource] = {}
        self._reception_wait: Dict[str, float] = {}
        self._cam_index = 0
        self.tiles: Dict[str, CamTile] = {}
        self._alert_lines: List[str] = []
        self._paused = False
        self._roi = self._load_reception_roi()
        self._presence_need = self._load_presence_seconds()

    def _load_reception_roi(self) -> Tuple[float, float, float, float]:
        try:
            with open(ROOT / "config.yaml", encoding="utf-8") as f:
                roi_cfg = (yaml.safe_load(f) or {}).get("roi", {})
            return (
                float(roi_cfg.get("x1", 0.4)),
                float(roi_cfg.get("y1", 0.35)),
                float(roi_cfg.get("x2", 0.98)),
                float(roi_cfg.get("y2", 0.98)),
            )
        except Exception:
            return (0.4, 0.35, 0.98, 0.98)

    def _load_presence_seconds(self) -> float:
        # приоритет: config.yaml (как в старом боте) → platform.yaml
        try:
            with open(ROOT / "config.yaml", encoding="utf-8") as f:
                det = (yaml.safe_load(f) or {}).get("detection", {})
            if "presence_seconds" in det:
                return float(det["presence_seconds"])
        except Exception:
            pass
        return float(self.cfg.get("vision", {}).get("reception_presence_seconds", 25))

    def _rtsp(self, channel_num: int, *, force_main: bool = False) -> str:
        nvr = self.cfg["nvr"]
        user = os.getenv("CAMERA_USER", nvr.get("user", "admin"))
        password = os.getenv("CAMERA_PASSWORD", "")
        ip = os.getenv("CAMERA_IP", nvr.get("ip"))
        use_sub = bool(nvr.get("use_substream", True)) and not force_main
        stream = 2 if use_sub else 1
        ch = int(channel_num) * 100 + stream
        return f"rtsp://{user}:{password}@{ip}:{nvr.get('rtsp_port', 554)}/Streaming/Channels/{ch}"

    def _source(self, cam: dict) -> RtspSource:
        cid = cam["id"]
        vision = self.cfg.get("vision", {}) or {}
        prefer_main = bool(vision.get("prefer_mainstream_for_own", False))
        force_main = prefer_main and "own_products" in set(cam.get("modules") or [])
        # ключ источника зависит от stream, чтобы можно было переключить
        key = f"{cid}:{'main' if force_main else 'sub'}"
        if key not in self._sources:
            self._sources[key] = RtspSource(self._rtsp(cam["channel"], force_main=force_main))
        return self._sources[key]

    def _emit(self, finding: Finding, camera: str, frame) -> None:
        key = f"{finding.kind}:{camera}:{finding.title}"
        now = time.time()
        alerts_cfg = self.cfg.get("alerts", {}) or {}
        soft_mode = bool(alerts_cfg.get("soft_mode", True))
        is_soft_own = (
            soft_mode
            and finding.kind == "own_products"
            and finding.severity in ("medium", "low")
        )

        if finding.kind == "reception_queue":
            ok = self.gate.allow_cooldown(key, now)
        elif is_soft_own:
            ok = self.soft_gate.allow(key, now)
        else:
            ok = self.gate.allow(key, now)
        if not ok:
            return

        # подозрительные: можно только в журнал UI, без Telegram
        send_tg = True
        if is_soft_own and not bool(alerts_cfg.get("soft_telegram", True)):
            send_tg = False

        self.store.add_alert(
            finding.kind,
            finding.title,
            finding.detail,
            camera=camera,
            severity=finding.severity,
        )
        photo = None
        if send_tg and self.cfg.get("alerts", {}).get("send_photo", True) and frame is not None:
            photo = draw_findings(frame, [finding])
        icon = {"critical": "!", "high": "!", "medium": "?", "low": ".", "info": "i"}.get(
            finding.severity, "-"
        )
        prefix = "ПРОВЕРЬТЕ: " if is_soft_own else ""
        text = f"{icon} [{finding.severity}] {prefix}{finding.title}\n[{camera}]\n{finding.detail}"
        if send_tg:
            self.notifier.send_alert(text, photo)
        line = f"{finding.severity}: {finding.title} @ {camera}"
        self._alert_lines = [line, *self._alert_lines][:10]
        logger.info("ALERT%s %s | %s", "" if send_tg else "(ui-only)", camera, finding.title)

    def _ensure_tile(self, cam: dict) -> CamTile:
        cid = cam["id"]
        if cid not in self.tiles:
            self.tiles[cid] = CamTile(
                cam_id=cid,
                name=cam["name"],
                is_reception=("reception_queue" in cam.get("modules", [])),
                roi=self._roi if "reception_queue" in cam.get("modules", []) else None,
                reception_need=self._presence_need,
            )
        return self.tiles[cid]

    def _process_camera(self, cam: dict) -> None:
        enabled_mods = set(cam.get("modules", []))
        tile = self._ensure_tile(cam)
        src = self._source(cam)
        frame = src.read()
        name = cam["name"]
        cid = cam["id"]

        if frame is None:
            tile.ok = False
            tile.status = "нет кадра"
            tile.last_check = time.time()
            return

        findings: List[Finding] = []
        status_bits: List[str] = []

        if self.modules.get("own_products") and "own_products" in enabled_mods:
            own = self.analyzer.analyze_own_products(frame, name)
            findings.extend(own)
            if own:
                status_bits.append(f"своя еда:{len(own)}")

        if self.modules.get("cleanliness") and "cleanliness" in enabled_mods:
            cl = self.analyzer.analyze_cleanliness(frame, name)
            findings.extend(cl)
            if cl:
                status_bits.append("чистота!")

        if self.modules.get("fridge_stock") and "fridge_stock" in enabled_mods:
            fr = self.analyzer.analyze_fridge(frame, name)
            findings.extend(fr)
            if fr:
                status_bits.append("витрина!")

        if self.modules.get("incidents") and "incidents" in enabled_mods:
            inc = self.analyzer.analyze_incidents(frame, name)
            findings.extend(inc)
            if inc:
                status_bits.append("инцидент!")

        if self.modules.get("staff_presence") and "staff_presence" in enabled_mods:
            staff = self.analyzer.analyze_staff(frame, name)
            for f in staff:
                if f.severity != "info":
                    findings.append(f)
                self.store.add_staff_event(name, f.severity == "info", f.detail)
            if any(f.severity == "info" for f in staff):
                status_bits.append("сотрудник+")
            elif staff:
                status_bits.append("сотрудник-")

        # Очередь у админ-стойки — обязательно для reception
        if self.modules.get("reception_queue") and "reception_queue" in enabled_mods:
            self._roi = self._load_reception_roi()
            self._presence_need = self._load_presence_seconds()
            tile.roi = self._roi
            tile.reception_need = self._presence_need
            count = self.analyzer.count_people_in_roi(frame, self._roi)
            now = time.time()
            key = f"reception:{cid}"
            tile.reception_count = count
            if count > 0:
                self._reception_wait.setdefault(key, now)
                waited = now - self._reception_wait[key]
                tile.reception_waited = waited
                status_bits.append(f"очередь {count} {waited:.0f}/{self._presence_need:.0f}s")
                if waited >= self._presence_need:
                    findings.append(
                        Finding(
                            kind="reception_queue",
                            severity="high",
                            title="Гости у админ-кассы",
                            detail=(
                                f"{name}: {count} чел. ждут ≥{self._presence_need:.0f}с — подойти!"
                            ),
                            boxes=[],
                        )
                    )
            else:
                self._reception_wait.pop(key, None)
                tile.reception_waited = 0.0
                status_bits.append("очередь пусто")

        tile.frame = frame
        tile.findings = findings
        tile.ok = True
        tile.last_check = time.time()
        tile.status = ", ".join(status_bits) if status_bits else "OK — проверка"

        seen = set()
        active_keys = set()
        for f in findings:
            sig = (f.kind, f.title)
            if sig in seen:
                continue
            seen.add(sig)
            if f.kind == "staff_presence" and f.severity == "info":
                continue
            key = f"{f.kind}:{name}:{f.title}"
            active_keys.add(key)
            self._emit(f, name, frame)
        # сброс только ключей этой камеры (round-robin не должен бить другие)
        self.gate.note_absent_for_camera(name, active_keys)
        self.soft_gate.note_absent_for_camera(name, active_keys)

    def tick_background(self) -> None:
        # Авто-алерты только по камерам (своя еда / очередь).
        # Задачи/температура/телефоны — без пуша, чтобы не спамить.
        return

    def _pick_batch(self, cams: List[dict], per: int) -> List[dict]:
        """Reception каждый цикл + остальные по кругу."""
        if not cams:
            return []
        reception = [c for c in cams if "reception_queue" in c.get("modules", [])]
        others = [c for c in cams if c not in reception]
        batch: List[dict] = list(reception[:1])  # всегда стойка админа
        need = max(0, per - len(batch))
        for _ in range(need):
            if not others:
                break
            batch.append(others[self._cam_index % len(others)])
            self._cam_index += 1
        # если reception нет в списке — обычный round-robin
        if not batch:
            for _ in range(per):
                batch.append(cams[self._cam_index % len(cams)])
                self._cam_index += 1
        return batch

    def reload_catalog(self) -> None:
        try:
            self.analyzer.reload_menu()
        except Exception:
            logger.exception("reload catalog failed")

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    def snapshot_dashboard(self, active_id: str = "") -> Optional[np.ndarray]:
        cams = self.cfg.get("cameras", [])
        if not cams:
            return None
        order = [c["id"] for c in cams]
        aid = active_id or (order[0] if order else "")
        cols = int(self.cfg.get("vision", {}).get("dashboard_cols", 5))
        checked = sum(1 for t in self.tiles.values() if t.ok)
        footer = (
            f"Онлайн: {checked}/{len(cams)} | "
            f"очередь: {self._presence_need:.0f}с | "
            f"{'ПАУЗА' if self._paused else 'SCAN'}"
        )
        return build_dashboard(
            self.tiles, order, aid, self._alert_lines, footer, cols=cols
        )

    def process_once(self) -> str:
        """Один цикл сканирования камер. Возвращает active camera id."""
        cams = self.cfg.get("cameras", [])
        per = max(1, int(self.cfg.get("vision", {}).get("cameras_per_cycle", 2)))
        active_id = cams[0]["id"] if cams else ""
        if self._paused or not cams:
            return active_id
        batch = self._pick_batch(cams, per)
        for cam in batch:
            active_id = cam["id"]
            try:
                self._process_camera(cam)
            except Exception as exc:
                logger.exception("camera fail %s", cam.get("id"))
                tile = self._ensure_tile(cam)
                tile.ok = False
                tile.status = f"ошибка: {exc}"[:40]
                tile.last_check = time.time()
        return active_id

    def run_loop(
        self,
        stop_flag=None,
        show_opencv_dashboard: Optional[bool] = None,
        on_cycle=None,
        announce: bool = True,
    ) -> None:
        """
        Основной цикл. stop_flag — объект с .is_set() (threading.Event).
        on_cycle(active_id, dashboard_bgr) — колбэк для Desktop UI.
        """
        import threading

        cams = self.cfg.get("cameras", [])
        vision = self.cfg.get("vision", {})
        show_dash = (
            bool(vision.get("show_dashboard", True))
            if show_opencv_dashboard is None
            else bool(show_opencv_dashboard)
        )

        for cam in cams:
            self._ensure_tile(cam)

        logger.info(
            "CyberX Control start | cameras=%s | dashboard=%s | reception_wait=%ss",
            len(cams),
            show_dash,
            self._presence_need,
        )
        if announce:
            try:
                self.notifier.send_alert(
                    "CyberX Control запущен\n"
                    f"Клуб: {self.cfg.get('club', {}).get('name')}\n"
                    f"Камер: {len(cams)}\n"
                    f"Очередь у админки: {self._presence_need:.0f}с\n"
                    "Desktop / мозаика камер. В боте: /help"
                )
            except Exception:
                logger.exception("startup telegram failed")

        last_bg = 0.0
        active_id = cams[0]["id"] if cams else ""
        win = "CyberX Control — камеры и детекторы"
        if show_dash:
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(win, 1280, 800)

        stop = stop_flag if stop_flag is not None else threading.Event()

        # Сразу отдать пустую/стартовую мозаику в Desktop, не дожидаясь первого RTSP
        if on_cycle is not None:
            try:
                on_cycle(active_id, self.snapshot_dashboard(active_id))
            except Exception:
                logger.exception("on_cycle initial failed")

        try:
            while not stop.is_set():
                active_id = self.process_once()

                now = time.time()
                if now - last_bg > 60:
                    try:
                        self.tick_background()
                    except Exception:
                        logger.exception("background fail")
                    last_bg = now

                dash = None
                if show_dash or on_cycle is not None:
                    dash = self.snapshot_dashboard(active_id)

                if on_cycle is not None:
                    try:
                        on_cycle(active_id, dash)
                    except Exception:
                        logger.exception("on_cycle failed")

                if show_dash and dash is not None:
                    h, w = dash.shape[:2]
                    max_w = 1600
                    if w > max_w:
                        scale = max_w / w
                        dash = cv2.resize(dash, (int(w * scale), int(h * scale)))
                    cv2.imshow(win, dash)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), ord("Q"), 27):
                        break
                    if key == ord(" "):
                        self._paused = not self._paused
                else:
                    time.sleep(0.12)
        except KeyboardInterrupt:
            logger.info("stop")
        finally:
            stop.set()
            for s in self._sources.values():
                s.release()
            if show_dash:
                cv2.destroyAllWindows()

    def run(self) -> None:
        self.run_loop(show_opencv_dashboard=None, on_cycle=None, announce=True)
