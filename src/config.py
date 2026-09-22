"""Загрузка настроек из .env и config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Roi:
    x1: float
    y1: float
    x2: float
    y2: float

    def clamp(self) -> "Roi":
        return Roi(
            x1=max(0.0, min(1.0, self.x1)),
            y1=max(0.0, min(1.0, self.y1)),
            x2=max(0.0, min(1.0, self.x2)),
            y2=max(0.0, min(1.0, self.y2)),
        )

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        r = self.clamp()
        return (
            int(r.x1 * width),
            int(r.y1 * height),
            int(r.x2 * width),
            int(r.y2 * height),
        )


@dataclass
class Settings:
    telegram_token: str
    telegram_chat_id: str
    source_mode: str
    rtsp_url: str
    ivms_window_title: str
    roi: Roi
    model: str
    confidence: float
    presence_seconds: float
    cooldown_seconds: float
    frame_skip: int
    show_preview: bool
    message_template: str
    send_photo: bool


def _build_rtsp_url() -> str:
    explicit = os.getenv("RTSP_URL", "").strip()
    if explicit:
        return explicit

    user = os.getenv("CAMERA_USER", "admin")
    password = os.getenv("CAMERA_PASSWORD", "")
    ip = os.getenv("CAMERA_IP", "")
    channel = os.getenv("CAMERA_CHANNEL", "102")
    if not ip or not password:
        return ""
    return f"rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{channel}"


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")

    config_path = ROOT / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    roi_raw = raw.get("roi", {})
    det = raw.get("detection", {})
    tg = raw.get("telegram", {})

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    source_mode = os.getenv("SOURCE_MODE", "rtsp").strip().lower()
    rtsp_url = _build_rtsp_url()

    if not token:
        raise SystemExit("Заполни TELEGRAM_BOT_TOKEN в файле .env")
    if not chat_id:
        raise SystemExit(
            "Нет TELEGRAM_CHAT_ID. Запусти: python scripts/wait_chat_id.py "
            "и напиши боту /start в Telegram"
        )
    if source_mode == "rtsp" and not rtsp_url:
        raise SystemExit(
            "Для SOURCE_MODE=rtsp укажи RTSP_URL или CAMERA_IP + CAMERA_PASSWORD в .env"
        )

    return Settings(
        telegram_token=token,
        telegram_chat_id=chat_id,
        source_mode=source_mode,
        rtsp_url=rtsp_url,
        ivms_window_title=os.getenv("IVMS_WINDOW_TITLE", "iVMS-4200"),
        roi=Roi(
            x1=float(roi_raw.get("x1", 0.25)),
            y1=float(roi_raw.get("y1", 0.20)),
            x2=float(roi_raw.get("x2", 0.75)),
            y2=float(roi_raw.get("y2", 0.90)),
        ),
        model=str(det.get("model", "yolov8n.pt")),
        confidence=float(det.get("confidence", 0.45)),
        presence_seconds=float(det.get("presence_seconds", 30)),
        cooldown_seconds=float(det.get("cooldown_seconds", 120)),
        frame_skip=max(1, int(det.get("frame_skip", 2))),
        show_preview=bool(det.get("show_preview", True)),
        message_template=str(
            tg.get(
                "message",
                "⚠️ Возле админ-кассы стоят люди ({count}). Нужно подойти!",
            )
        ),
        send_photo=bool(tg.get("send_photo", True)),
    )


def save_roi(roi: Roi) -> None:
    config_path = ROOT / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw["roi"] = {
        "x1": round(roi.x1, 4),
        "y1": round(roi.y1, 4),
        "x2": round(roi.x2, 4),
        "y2": round(roi.y2, 4),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
