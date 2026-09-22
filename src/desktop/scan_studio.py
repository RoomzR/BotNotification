"""Отдельное растягиваемое окно live-скана (iPhone Continuity / Mac camera)."""

from __future__ import annotations

import tempfile
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from src.vision.product_scan_coach import (
    ANGLE_LABELS_RU,
    ANGLE_TIPS,
    ProductScanCoach,
)


CX_BG = "#070708"
CX_PANEL = "#121214"
CX_RED = "#FF1E1A"
CX_WHITE = "#FFFFFF"
CX_OK = "#3DDC84"
CX_WARN = "#FFD166"


def _font(size: int = 13, bold: bool = False):
    family = "Helvetica Neue"
    return (family, size, "bold" if bold else "normal")


class ScanStudio(tk.Toplevel):
    """Большое окно камеры с live-подсказками Scan Coach."""

    def __init__(
        self,
        master,
        *,
        get_sku: Callable[[], str],
        get_refs: Callable[[str], list],
        get_angles: Callable[[str], tuple],
        on_capture: Callable[[np.ndarray, Path], None],
        open_camera: Callable[[], Optional[cv2.VideoCapture]],
        on_close: Optional[Callable[[], None]] = None,
    ):
        super().__init__(master)
        self.title("CyberX · Live скан (раздвиньте окно)")
        self.configure(bg=CX_BG)
        self.geometry("1100x780")
        self.minsize(780, 560)
        self._get_sku = get_sku
        self._get_refs = get_refs
        self._get_angles = get_angles
        self._on_capture = on_capture
        self._open_camera = open_camera
        self._on_close_cb = on_close

        self._cap: Optional[cv2.VideoCapture] = None
        self._job = None
        self._frame_i = 0
        self._last_bgr: Optional[np.ndarray] = None
        self._imgtk = None
        self._coach = ProductScanCoach(n_features=2000)
        self._live_hint = tk.StringVar(value="Запуск камеры…")
        self._live_detail = tk.StringVar(value="")
        self._pts_var = tk.StringVar(value="pts: —")
        self._ok_streak = 0
        self._snap_cooldown_until = 0.0
        self._last_live_v = None
        self._hint_lbl = None
        self._detail_lbl = None

        top = tk.Frame(self, bg=CX_PANEL)
        top.pack(fill="x")
        tk.Label(top, text="LIVE СКАН", bg=CX_PANEL, fg=CX_RED, font=_font(16, True)).pack(
            side="left", padx=14, pady=10
        )
        tk.Label(
            top,
            text="раздвиньте края окна · подсказки в прямом эфире",
            bg=CX_PANEL,
            fg="#AAAAAA",
            font=_font(11),
        ).pack(side="left", padx=4)
        tk.Label(top, textvariable=self._pts_var, bg=CX_PANEL, fg=CX_WHITE, font=_font(12)).pack(
            side="right", padx=14
        )

        hint_box = tk.Frame(self, bg="#1A0A0A", highlightthickness=1, highlightbackground=CX_RED)
        hint_box.pack(fill="x", padx=10, pady=(0, 8))
        self._hint_lbl = tk.Label(
            hint_box,
            textvariable=self._live_hint,
            bg="#1A0A0A",
            fg=CX_WARN,
            font=_font(18, True),
            wraplength=980,
            justify="left",
            anchor="w",
        )
        self._hint_lbl.pack(fill="x", padx=14, pady=(12, 4))
        self._detail_lbl = tk.Label(
            hint_box,
            textvariable=self._live_detail,
            bg="#1A0A0A",
            fg=CX_WHITE,
            font=_font(13),
            wraplength=980,
            justify="left",
            anchor="w",
        )
        self._detail_lbl.pack(fill="x", padx=14, pady=(0, 12))

        self._video_box = tk.Frame(self, bg="#000000")
        self._video_box.pack(fill="both", expand=True, padx=10, pady=4)
        self._video = tk.Label(self._video_box, bg="#000000", fg=CX_WHITE, text="Камера…")
        self._video.pack(fill="both", expand=True)

        bar = tk.Frame(self, bg=CX_PANEL)
        bar.pack(fill="x", padx=10, pady=10)
        tk.Button(
            bar,
            text="СНЯТЬ + ПРОВЕРКА",
            command=self._snap,
            bg=CX_RED,
            fg=CX_WHITE,
            activebackground="#C41010",
            font=_font(14, True),
            relief="flat",
            padx=16,
            pady=10,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            bar,
            text="Переподключить камеру",
            command=self._reconnect,
            bg="#242428",
            fg=CX_WHITE,
            font=_font(12),
            relief="flat",
            padx=12,
            pady=10,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            bar,
            text="Закрыть",
            command=self.close,
            bg="#242428",
            fg=CX_WHITE,
            font=_font(12),
            relief="flat",
            padx=12,
            pady=10,
            cursor="hand2",
        ).pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Configure>", self._on_resize)
        self.after(80, self._reconnect)

    def _on_resize(self, _evt=None):
        try:
            w = max(400, int(self.winfo_width()) - 40)
            if self._hint_lbl is not None:
                self._hint_lbl.configure(wraplength=w)
            if self._detail_lbl is not None:
                self._detail_lbl.configure(wraplength=w)
        except Exception:
            pass

    def notify_enrolled(self, next_code: str, tip: str):
        self._ok_streak = 0
        self._snap_cooldown_until = time.time() + 2.0
        self._live_hint.set(f"✓ Сохранено. Дальше: {next_code}")
        self._live_detail.set(tip or "Смените ракурс упаковки.")
        if self._hint_lbl is not None:
            self._hint_lbl.configure(fg=CX_OK)

    def notify_pending(self, msg: str):
        self._ok_streak = 0
        self._snap_cooldown_until = time.time() + 1.2
        self._live_hint.set(f"Кадр на проверке: {msg}")
        if self._hint_lbl is not None:
            self._hint_lbl.configure(fg=CX_WARN)

    def _reconnect(self):
        self._release_cap()
        self._live_hint.set("Подключение камеры (iPhone Continuity / Mac)…")
        if self._hint_lbl is not None:
            self._hint_lbl.configure(fg=CX_WARN)
        cap = self._open_camera()
        if cap is None:
            self._live_hint.set("Камера не найдена")
            self._live_detail.set(
                "Разрешите Камеру в Настройках Mac для Python/Terminal.\n"
                "Continuity: Пункт управления → выберите iPhone как камеру.\n"
                "Или AirDrop: «iPhone скан» → CyberX_Inbox."
            )
            return
        self._cap = cap
        self._frame_i = 0
        self._tick()

    def _release_cap(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def close(self):
        self._release_cap()
        cb = self._on_close_cb
        self._on_close_cb = None
        try:
            self.destroy()
        except Exception:
            pass
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _tick(self):
        if self._cap is None:
            return
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._job = self.after(80, self._tick)
            return
        self._last_bgr = frame
        self._frame_i += 1

        sku = self._get_sku()
        have, nxt, _ = self._get_angles(sku)
        target = nxt or "front"
        label = ANGLE_LABELS_RU.get(target, target)
        tip = ANGLE_TIPS.get(target, "")

        coach = self._coach
        crop, bbox = coach.localize_pack(frame)
        work = crop if crop is not None and crop.size else frame
        kps, _ = coach.keypoints(work)
        cov = coach.coverage_score(work, kps)
        q_ok, q_msg, _issues = coach.quality(work)

        live_v = self._last_live_v
        if self._frame_i % 8 == 0:
            try:
                refs = self._get_refs(sku) if sku else []
                live_v = coach.evaluate_capture(frame, target_angle=target, existing_refs=refs)
                self._last_live_v = live_v
            except Exception:
                live_v = None

        cooling = time.time() < self._snap_cooldown_until

        if live_v is not None and not live_v.ok:
            self._ok_streak = 0
            self._live_hint.set(f"⚠ {live_v.message}")
            self._live_detail.set(f"Нужен ракурс: {label}\n{tip}")
            color_hint = CX_WARN
            bgr_banner = (0, 180, 255)
        elif not q_ok:
            self._ok_streak = 0
            self._live_hint.set(f"Исправьте кадр: {q_msg}")
            self._live_detail.set(f"Нужен: {label} · покрытие {cov:.0%} · точек {len(kps)}\n{tip}")
            color_hint = CX_WARN
            bgr_banner = (0, 180, 255)
        else:
            self._ok_streak += 1
            ready = self._ok_streak >= 18
            self._live_hint.set(
                f"✓ Хорошо — {'СНИМАЮ…' if ready and not cooling else 'можно СНЯТЬ'} ({label})"
            )
            self._live_detail.set(
                f"Точек: {len(kps)} · покрытие {cov:.0%} · стабильность {min(self._ok_streak, 18)}/18\n{tip}"
            )
            color_hint = CX_OK
            bgr_banner = (80, 220, 120)

        if self._hint_lbl is not None:
            self._hint_lbl.configure(fg=color_hint)

        self._pts_var.set(f"pts:{len(kps)}  cov:{cov:.0%}  →{label}")

        drawn = coach.draw_overlay(
            frame,
            target_angle=target,
            have_angles=have,
            verdict=live_v,
        )
        h, w = drawn.shape[:2]
        cv2.rectangle(drawn, (0, h - 78), (w, h), (0, 0, 0), -1)
        msg = self._live_hint.get()[:72]
        cv2.putText(
            drawn,
            msg,
            (12, h - 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            bgr_banner,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            drawn,
            tip[:70],
            (12, h - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

        rgb = cv2.cvtColor(drawn, cv2.COLOR_BGR2RGB)
        box_w = max(self._video_box.winfo_width(), 720)
        box_h = max(self._video_box.winfo_height(), 400)
        scale = min(box_w / w, box_h / h)
        rgb = cv2.resize(
            rgb,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        )
        self._imgtk = ImageTk.PhotoImage(Image.fromarray(rgb))
        self._video.configure(image=self._imgtk, text="")

        # авто-снимок только после стабильного OK + cooldown
        if (
            not cooling
            and self._ok_streak >= 18
            and live_v is not None
            and live_v.ok
            and q_ok
        ):
            self._ok_streak = 0
            self._snap_cooldown_until = time.time() + 2.5
            self._live_detail.set("Авто-съёмка — держите ровно")
            self.after(350, self._snap)

        self._job = self.after(33, self._tick)

    def _snap(self):
        frame = self._last_bgr
        if frame is None:
            self._live_hint.set("Нет кадра — подождите")
            return
        self._snap_cooldown_until = time.time() + 2.0
        self._ok_streak = 0
        tmp = Path(tempfile.gettempdir()) / f"cyberx_studio_{int(time.time())}.jpg"
        cv2.imwrite(str(tmp), frame)
        self._live_hint.set("Кадр отправлен в Desktop…")
        try:
            self._on_capture(frame.copy(), tmp)
        except Exception as exc:
            self._live_hint.set(f"Ошибка: {exc}")
