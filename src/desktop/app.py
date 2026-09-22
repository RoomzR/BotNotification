"""
CyberX Desktop — панель сотрудника.
Стиль: чёрный / красный / белый. Высокий контраст (macOS-friendly).
Скан на ноуте → экспорт ZIP → Telegram → импорт на ПК клуба.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("cyberx.desktop")

# --- CyberX palette (высокий контраст) ---
CX_BG = "#070708"
CX_PANEL = "#121214"
CX_PANEL2 = "#1A1A1E"
CX_PANEL3 = "#242428"
CX_RED = "#FF1E1A"
CX_RED_DARK = "#C41010"
CX_RED_DEEP = "#7A0A0A"
CX_WHITE = "#FFFFFF"
CX_OFFWHITE = "#E8E8EA"
CX_MUTED = "#E6E6EC"
CX_OK = "#3DDC84"
CX_LINE = "#3A3A42"
CX_BLACK = "#000000"
CX_SUB = "#FFFFFF"  # подпись под логотипом и вторичный текст — максимальный контраст

MIN_SCAN_REFS = 3  # сколько ракурсов нужно для «полного» скана

# Подсказки шагов скана (коротко в каталоге + подробно в «Как работать»)
SCAN_STEP_HELP = {
    "sku": (
        "SKU + название",
        "SKU — короткий код товара в системе (например TUC-01, COCA-05). "
        "Название — как его видит сотрудник (TUC солёный, Coca-Cola 0.5). "
        "Сначала заполните оба поля.",
    ),
    "saved": (
        "Товар в базе",
        "После «Сохранить» товар появляется в списке слева и в меню клуба. "
        "Без этого нельзя привязать фото-эталоны.",
    ),
    "photo": (
        "Фото",
        "Снимок упаковки с веб-камеры ноутбука или файл с телефона. "
        "На ПК админки камеры часто нет — сканируйте на ноуте.",
    ),
    "quality": (
        "Качество",
        "Автопроверка кадра: не размыто, не слишком темно/ярко, товар крупно в кадре. "
        "Если качество плохое — система предупредит и попросит переснять.",
    ),
    "ocr": (
        "OCR этикетки",
        "OCR = программа читает текст с этикетки (бренд, название). "
        "Нужно, чтобы система отличала товар бара от «своего». Держите этикетку ровно, без бликов.",
    ),
    "refs": (
        f"Эталоны {MIN_SCAN_REFS} шт",
        f"Эталон — сохранённое фото «как выглядит наш товар». "
        f"Нужно минимум {MIN_SCAN_REFS} ракурса: лицо, бок, чуть дальше. "
        f"Пока не ✓ {MIN_SCAN_REFS}/{MIN_SCAN_REFS} — скан неполный.",
    ),
    "aliases": (
        "Aliases",
        "Дополнительные слова для узнавания: TUC, ТУК, хрустим, Coca, кока и т.д. "
        "Через запятую. Часто подставляются из OCR автоматически — проверьте и дополните.",
    ),
}

# Секции инструкции («Как работать») — не txt, а карточки
HELP_SECTIONS: list[tuple[str, str, list[dict]]] = [
    (
        "about",
        "О программе",
        [
            {
                "title": "Что делает CyberX Control",
                "body": "Панель сотрудника клуба CyberX Gomel. Смотрит камеры и пишет в Telegram, когда нужно вмешаться.",
                "bullets": [
                    "Очередь у админки / reception дольше ~30 секунд → алерт",
                    "Еда или напиток НЕ из меню бара («со своим») → алерт",
                    "Каталог товаров бара: фото-эталоны + OCR + объём бутылки (0.33/0.45/0.5 ≠ 1л/2л)",
                ],
            },
            {
                "title": "Где что находится",
                "body": "Верхние красные кнопки — мониторинг. Вкладки ниже — разные рабочие экраны.",
                "bullets": [
                    "КАК РАБОТАТЬ — эта инструкция",
                    "КАМЕРЫ — живая мозаика после старта",
                    "КОНТРОЛЬ — очередь и «своё» по камерам",
                    "КАТАЛОГ — скан товаров, экспорт/импорт ZIP",
                    "АЛЕРТЫ — последние события (те же уходят в Telegram)",
                ],
            },
        ],
    ),
    (
        "buttons",
        "Кнопки сверху",
        [
            {
                "title": "СТАРТ МОНИТОРИНГА",
                "body": "Главная кнопка смены. Запускает анализ камер и Telegram-бота.",
                "bullets": [
                    "После нажатия откроется вкладка КАМЕРЫ",
                    "Статус станет «МОНИТОРИНГ АКТИВЕН»",
                    "Первые кадры могут появиться через несколько секунд (подключение RTSP)",
                ],
            },
            {
                "title": "ПАУЗА",
                "body": "Временно останавливает проверки, камеры остаются подключёнными.",
                "bullets": [
                    "Удобно, если нужно отойти и не слать ложные алерты",
                    "Повторное нажатие снимает паузу",
                ],
            },
            {
                "title": "СТОП",
                "body": "Полная остановка мониторинга в конце смены.",
                "bullets": [
                    "После стопа снова нужно нажать СТАРТ",
                    "Каталог и сканы при этом не удаляются",
                ],
            },
        ],
    ),
    (
        "shift",
        "Каждая смена",
        [
            {
                "title": "Чеклист сотрудника",
                "body": "Делайте в таком порядке — так меньше ошибок.",
                "bullets": [
                    "1. Откройте CyberX Desktop",
                    "2. Нажмите СТАРТ МОНИТОРИНГА",
                    "3. Вкладка КАМЕРЫ — убедитесь, что мозаика живая",
                    "4. Вкладка КОНТРОЛЬ — смотрите очередь и «своё»",
                    "5. На алерты реагируйте: подойти / напомнить про бар",
                    "6. В конце смены — СТОП",
                ],
            },
        ],
    ),
    (
        "cameras",
        "Камеры",
        [
            {
                "title": "Что вы видите",
                "body": "Мозаика всех камер клуба. Красная рамка — камера, которую система сейчас проверяет.",
                "bullets": [
                    "КЛУБ — товар похож на продукцию бара",
                    "СВОЁ — похоже на чужой бренд → Telegram",
                    "Очередь — люди ждут у стойки дольше порога",
                    "Если долго «Подключение…» — проверьте сеть/NVR/.env",
                ],
            },
        ],
    ),
    (
        "control",
        "Контроль",
        [
            {
                "title": "Таблица статуса",
                "body": "Сводка по каждой камере без нужды смотреть все кадры.",
                "bullets": [
                    "Очередь — сколько секунд ждут / порог и число людей",
                    "Своё — сколько срабатываний «не из бара»",
                    "Легенда справа — что означают подписи",
                ],
            },
            {
                "title": "КЛУБ — товар бара CyberX",
                "body": "На камерах: КЛУБ = из меню. СВОЁ = точно чужое. ?? = подозрительно (мягкий режим).",
                "bullets": [
                    "Точно своё (high) → Telegram сразу после confirm",
                    "Подозрительно (medium) → дольше в кадре, реже в Telegram (soft_mode)",
                    "В platform.yaml: soft_telegram: false — подозрительные только в UI",
                ],
            },
        ],
    ),
    (
        "catalog",
        "Каталог и скан",
        [
            {
                "title": "Зачем каталог",
                "body": "Чтобы камеры отличали продукцию CyberX от еды/напитков «со своим».",
                "bullets": [
                    "Авто-доскан: если качество OK — ракурс сохраняется сам",
                    "На экране камеры — рамка и подсказка ракурса (ЛИЦО / БОК / ДАЛЬШЕ) + счётчик 0/3",
                    "Колонка «Скан» в таблице: ○ 0/3 → ! 1/3 → ✓ 3/3",
                ],
            },
            {
                "title": "Кнопки каталога",
                "body": "Что делает каждая кнопка на вкладке КАТАЛОГ.",
                "bullets": [
                    "Обновить — перечитать список товаров из базы",
                    "Синхр. меню — обновить club_menu.yaml для детекторов",
                    "ЭКСПОРТ ZIP — упаковать товары+фото для переноса",
                    "ИМПОРТ ZIP — принять пакет с ноутбука на ПК клуба",
                    "Сохранить — записать SKU/название/aliases в базу",
                    "Выключить — скрыть товар из активного меню",
                    "iPhone — скан с телефона (QR / Safari), основной способ",
                    "СНИМОК Mac — кадр с камеры ноутбука / Continuity",
                    "Камера Mac — включить превью на Mac",
                    "Файл… — фото с AirDrop / галереи",
                    "Сохранить этот ракурс в эталон — записать front/side/far",
                ],
            },
            {
                "title": "Порядок скана одного товара",
                "body": "Повторяйте, пока прогресс не станет 100% и в «Скан» не будет ✓.",
                "bullets": [
                    "1. Выберите товар слева или введите новый SKU + название",
                    "2. Нажмите Сохранить",
                    "3. Кнопка iPhone → откройте QR/ссылку в Safari на телефоне",
                    "4. СНЯТЬ на iPhone — фото придёт на Mac (Scan Coach проверит ракурс)",
                    "5. Повторите для БОК и ДАЛЬШЕ — пока ✓ 3/3",
                    "6. ЭКСПОРТ ZIP → на ПК клуба ИМПОРТ ZIP → Синхр. меню",
                ],
            },
        ],
    ),
    (
        "volumes",
        "Объём бутылок",
        [
            {
                "title": "Почему это важно",
                "body": "В баре CyberX напитки только малого формата. Гость может принести ту же Coca, но 1л или 2л — это уже «со своим».",
                "bullets": [
                    "В баре: 0.33 л, 0.45 л, 0.5 л",
                    "Чужое: 1 л, 1.5 л, 2 л и крупный PET",
                    "Даже если бренд знакомый (Coca / Fanta / Bon Aqua) — крупный объём = СВОЁ → Telegram",
                ],
            },
            {
                "title": "Как система отличает",
                "body": "Три сигнала вместе — бренд, объём на этикетке, размер в кадре.",
                "bullets": [
                    "OCR читает «0.5 л / 500 ml / 1л / 2л» с этикетки",
                    "Сверяет с полем «Объём» у SKU в каталоге (например COCA-033 → 0.33 л)",
                    "Если бутылка очень крупная относительно человека — тоже считает чужой формат",
                    "В каталоге у напитков обязательно указывайте объём: 0.33 л / 0.45 л / 0.5 л",
                ],
            },
            {
                "title": "Что делать сотруднику",
                "body": "При скане напитков пишите объём в карточке товара.",
                "bullets": [
                    "Не заводите в каталог 1л/2л — их быть не должно",
                    "На эталонных фото желательно, чтобы на этикетке был виден объём",
                    "Пиво 0.5 — SKU BEER-05, вес/объём «0.5 л»",
                ],
            },
        ],
    ),
    (
        "scan_terms",
        "Что значит прогресс скана",
        [
            {
                "title": "1. SKU + название",
                "body": SCAN_STEP_HELP["sku"][1],
                "bullets": [
                    "Пример SKU: TUC-01, GORILLA-05, BEER-05",
                    "Название пишите по-человечески — его видит персонал",
                ],
            },
            {
                "title": "2. Товар в базе",
                "body": SCAN_STEP_HELP["saved"][1],
                "bullets": ["Без сохранения фото нельзя привязать к товару"],
            },
            {
                "title": "3. Фото",
                "body": SCAN_STEP_HELP["photo"][1],
                "bullets": ["Лучше на белом/ровном фоне, этикетка к камере"],
            },
            {
                "title": "4. Качество",
                "body": SCAN_STEP_HELP["quality"][1],
                "bullets": ["Размыто / темно / мелко → переснимите"],
            },
            {
                "title": "5. OCR этикетки",
                "body": SCAN_STEP_HELP["ocr"][1],
                "bullets": ["Если OCR пустой — подойдите ближе, уберите блик"],
            },
            {
                "title": f"6. Эталоны {MIN_SCAN_REFS} шт",
                "body": SCAN_STEP_HELP["refs"][1],
                "bullets": [
                    "Ракурс 1 — лицо упаковки",
                    "Ракурс 2 — бок / угол",
                    "Ракурс 3 — чуть дальше в кадре",
                ],
            },
            {
                "title": "7. Aliases",
                "body": SCAN_STEP_HELP["aliases"][1],
                "bullets": ["Пишите варианты как на этикетке и как говорят гости"],
            },
        ],
    ),
    (
        "transfer",
        "ЭКСПОРТ / ИМПОРТ",
        [
            {
                "title": "Зачем ZIP",
                "body": "Скан удобнее на ноуте. ПК клуба должен получить те же эталоны.",
                "bullets": [
                    "На ноуте: после ✓ скана → ЭКСПОРТ ZIP",
                    "Перенос: Telegram / флешка / AirDrop",
                    "На ПК клуба: КАТАЛОГ → ИМПОРТ ZIP",
                    "После импорта: Синхр. меню (пересоберёт embedding-галерею)",
                ],
            },
            {
                "title": "Чеклист детектора «со своим»",
                "body": "Без полного каталога нейросеть не отличит бар от чужого.",
                "bullets": [
                    "Отсканируйте ВСЕ SKU бара (3 ракурса: лицо / бок / дальше)",
                    "Жёлтая строка над списком = SKU без эталонов — детектор слаб",
                    "sanitize_aliases.py --from-yaml (убрать мусор intel/hyperx из aliases)",
                    "build_embedding_gallery.py на ПК клуба после импорта",
                    "1–2 камеры own_products временно на main stream (уже в platform.yaml)",
                    "Соберите 20–30 скринов club/own → scripts/eval_own_detector.py",
                ],
            },
        ],
    ),
    (
        "alerts",
        "Алерты",
        [
            {
                "title": "Что здесь",
                "body": "Журнал последних событий. Те же сообщения уходят в Telegram-бот админки.",
                "bullets": [
                    "Обновить — перечитать список",
                    "Модуль / камера / важность / заголовок — чтобы быстро понять, куда идти",
                ],
            },
        ],
    ),
    (
        "fix",
        "Если не работает",
        [
            {
                "title": "Типовые проблемы",
                "body": "Сначала проверьте это — чаще всего помогает.",
                "bullets": [
                    "Нет мозаики после старта — сеть NVR, логин камер, .env",
                    "Нет Telegram — TELEGRAM_BOT_TOKEN и CHAT_ID",
                    "Не узнаёт товар бара — добавьте ещё ракурсы и aliases",
                    "Веб-камера не открывается — используйте «iPhone» или «Файл…»",
                    "Скан «неполный» — смотрите красную подсказку «Дальше: …»",
                ],
            },
        ],
    ),
]


def _font(size: int = 12, bold: bool = False) -> tuple:
    return ("Helvetica Neue", size, "bold" if bold else "normal")


class CxButton(tk.Frame):
    """Кнопка-лейбл: на macOS системный tk.Button часто рисует белый текст на белом."""

    def __init__(
        self,
        parent,
        text: str,
        command: Callable,
        *,
        primary: bool = False,
        danger: bool = False,
        ghost: bool = False,
        wide: bool = False,
    ):
        if primary:
            bg, hover, fg = CX_RED, "#FF4A45", CX_WHITE
        elif danger:
            bg, hover, fg = CX_RED_DEEP, CX_RED_DARK, CX_WHITE
        elif ghost:
            bg, hover, fg = CX_PANEL3, CX_RED_DARK, CX_WHITE
        else:
            bg, hover, fg = CX_PANEL3, "#35353C", CX_WHITE

        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=CX_LINE)
        self._bg = bg
        self._hover = hover
        self._command = command
        self._label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=_font(11, True),
            padx=16 if not wide else 22,
            pady=9,
            cursor="hand2",
        )
        self._label.pack()
        for w in (self, self._label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _click(self, _e=None):
        if self._command:
            self._command()

    def _enter(self, _e=None):
        self.configure(bg=self._hover, highlightbackground=CX_RED)
        self._label.configure(bg=self._hover)

    def _leave(self, _e=None):
        self.configure(bg=self._bg, highlightbackground=CX_LINE)
        self._label.configure(bg=self._bg)


class DesktopApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CyberX Control")
        self.root.geometry("1380x880")
        self.root.minsize(1120, 740)
        self.root.configure(bg=CX_BG)

        self.platform = None
        self.catalog = None
        self.store = None
        self._stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._photo_imgtk = None
        self._dash_imgtk = None
        self._webcam = None
        self._webcam_job = None
        self._pending_photo: Optional[Path] = None
        self._pending_bgr: Optional[np.ndarray] = None
        self._status_var = tk.StringVar(value="Готово · нажмите СТАРТ МОНИТОРИНГА")
        self._monitor_running = False
        self._tabs: dict[str, tk.Frame] = {}
        self._tab_btns: dict[str, tk.Label] = {}
        self._active_tab = "help"
        self._last_ocr = ""
        self._last_quality_ok = False
        self._last_quality_msg = ""
        self._scan_steps: dict[str, tk.Label] = {}
        self._scan_pct_var = tk.StringVar(value="0%")
        self._scan_title_var = tk.StringVar(value="Скан не начат")
        self._scan_hint_var = tk.StringVar(
            value="Выберите товар → сделайте фото → сохраните эталон (нужно 3 ракурса)"
        )
        self._cam_got_frame = False
        self._monitor_started_at = 0.0
        self._help_nav: dict[str, tk.Label] = {}
        self._help_body: Optional[tk.Frame] = None
        self._help_active = "about"
        self._step_desc_var = tk.StringVar(
            value="Нажмите на шаг прогресса — здесь появится, что он значит"
        )
        self._scan_local_var = tk.StringVar(
            value="1) Выберите SKU  2) кнопка iPhone → Safari на телефоне  3) СНЯТЬ ракурсы\n"
            "Нужно ЛИЦО / БОК / ДАЛЬШЕ. Mac и iPhone — одна Wi‑Fi."
        )
        self._angle_hint_var = tk.StringVar(value="РАКУРС 1/3 — ЛИЦО\nДержите этикетку к камере ровно, крупно в рамке")
        self._angle_count_var = tk.StringVar(value="0/3")
        self._auto_enroll = tk.BooleanVar(value=True)
        self._webcam_live_job = None
        self._scan_coach = None
        self._last_coach_verdict = None
        self._phone_server = None
        self._phone_qr_imgtk = None
        self._phone_url_var = tk.StringVar(value="")
        self._inbox_watcher = None
        self._webcam_frame_i = 0
        self._scan_studio = None

        self._setup_ttk()
        self._build_ui()
        self._bootstrap()
        self._bind_scan_traces()
        self._update_scan_progress()

    def _setup_ttk(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "CX.Treeview",
            background=CX_PANEL2,
            foreground=CX_WHITE,
            fieldbackground=CX_PANEL2,
            borderwidth=0,
            rowheight=30,
            font=_font(12),
        )
        style.configure(
            "CX.Treeview.Heading",
            background=CX_RED_DARK,
            foreground=CX_WHITE,
            font=_font(11, True),
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "CX.Treeview",
            background=[("selected", CX_RED)],
            foreground=[("selected", CX_WHITE)],
        )
        style.map(
            "CX.Treeview.Heading",
            background=[("active", CX_RED)],
            foreground=[("active", CX_WHITE)],
        )
        style.configure("CX.Vertical.TScrollbar", background=CX_PANEL3, troughcolor=CX_PANEL, arrowcolor=CX_WHITE)

    def _btn(self, parent, text, command, primary=False, danger=False, ghost=False):
        return CxButton(parent, text, command, primary=primary, danger=danger, ghost=ghost)

    def _entry(self, parent, var: tk.StringVar) -> tk.Entry:
        e = tk.Entry(
            parent,
            textvariable=var,
            bg=CX_BLACK,
            fg=CX_WHITE,
            insertbackground=CX_RED,
            relief="flat",
            font=_font(12),
            highlightthickness=1,
            highlightbackground=CX_LINE,
            highlightcolor=CX_RED,
        )
        return e

    def _bootstrap(self):
        try:
            from src.catalog.service import CatalogService
            from src.storage.db import Store

            self.store = Store()
            self.catalog = CatalogService(self.store)
            self.catalog.sync_club_menu()
            self._refresh_catalog()
            self._refresh_alerts()
            n = len(self.catalog.list_items())
            self._status_var.set(f"Каталог: {n} позиций · нажмите СТАРТ МОНИТОРИНГА")
        except Exception as exc:
            logger.exception("bootstrap")
            messagebox.showerror("Ошибка", f"Не удалось инициализировать каталог:\n{exc}")

    def _build_ui(self):
        # Top brand bar
        header = tk.Frame(self.root, bg=CX_BG, height=92)
        header.pack(fill="x")
        header.pack_propagate(False)

        accent = tk.Frame(header, bg=CX_RED, width=6)
        accent.pack(side="left", fill="y")

        brand = tk.Frame(header, bg=CX_BG)
        brand.pack(side="left", padx=16, pady=12)
        row = tk.Frame(brand, bg=CX_BG)
        row.pack(anchor="w")
        tk.Label(row, text="CYBERX", bg=CX_BG, fg=CX_RED, font=_font(24, True)).pack(side="left")
        tk.Label(row, text=" CONTROL", bg=CX_BG, fg=CX_WHITE, font=_font(24, True)).pack(side="left")
        tk.Label(
            brand,
            text="Gomel · Internatsionalnaya 13  ·  панель сотрудника",
            bg=CX_BG,
            fg=CX_SUB,
            font=_font(13),
        ).pack(anchor="w", pady=(6, 0))

        actions = tk.Frame(header, bg=CX_BG)
        actions.pack(side="right", padx=18)
        self._btn(actions, "СТАРТ МОНИТОРИНГА", self.start_monitor, primary=True).pack(side="left", padx=4)
        self._btn(actions, "ПАУЗА", self.toggle_pause, ghost=True).pack(side="left", padx=4)
        self._btn(actions, "СТОП", self.stop_monitor, danger=True).pack(side="left", padx=4)

        # Status
        strip = tk.Frame(self.root, bg=CX_RED, height=38)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        tk.Label(
            strip,
            textvariable=self._status_var,
            bg=CX_RED,
            fg=CX_WHITE,
            font=_font(12, True),
            anchor="w",
        ).pack(fill="both", padx=20)

        # Custom tabs
        tabbar = tk.Frame(self.root, bg=CX_PANEL, height=52)
        tabbar.pack(fill="x")
        tabbar.pack_propagate(False)
        tk.Frame(tabbar, bg=CX_RED, height=2).pack(fill="x", side="bottom")

        self._content = tk.Frame(self.root, bg=CX_BG)
        self._content.pack(fill="both", expand=True, padx=12, pady=12)

        for key, title, builder in (
            ("help", "КАК РАБОТАТЬ", self._build_help_tab),
            ("cam", "КАМЕРЫ", self._build_cameras_tab),
            ("own", "КОНТРОЛЬ", self._build_own_tab),
            ("cat", "КАТАЛОГ", self._build_catalog_tab),
            ("alerts", "АЛЕРТЫ", self._build_alerts_tab),
        ):
            page = tk.Frame(self._content, bg=CX_BG)
            self._tabs[key] = page
            builder(page)
            btn = tk.Label(
                tabbar,
                text=f"  {title}  ",
                bg=CX_PANEL,
                fg=CX_MUTED,
                font=_font(12, True),
                pady=14,
                cursor="hand2",
            )
            btn.pack(side="left", padx=2)
            btn.bind("<Button-1>", lambda _e, k=key: self._show_tab(k))
            btn.bind("<Enter>", lambda _e, b=btn, k=key: self._tab_hover(b, k, True))
            btn.bind("<Leave>", lambda _e, b=btn, k=key: self._tab_hover(b, k, False))
            self._tab_btns[key] = btn

        self._show_tab("help")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(400, self._poll_ui)

    def _tab_hover(self, btn: tk.Label, key: str, enter: bool):
        if key == self._active_tab:
            return
        btn.configure(fg=CX_WHITE if enter else CX_MUTED)

    def _show_tab(self, key: str):
        self._active_tab = key
        for k, page in self._tabs.items():
            page.pack_forget()
        self._tabs[key].pack(fill="both", expand=True)
        for k, btn in self._tab_btns.items():
            if k == key:
                btn.configure(bg=CX_RED, fg=CX_WHITE)
            else:
                btn.configure(bg=CX_PANEL, fg=CX_MUTED)

    # ---------- pages ----------
    def _build_help_tab(self, frame: tk.Frame):
        shell = tk.Frame(frame, bg=CX_BG)
        shell.pack(fill="both", expand=True)

        # left nav
        nav = tk.Frame(shell, bg=CX_PANEL, width=240, highlightthickness=1, highlightbackground=CX_LINE)
        nav.pack(side="left", fill="y", padx=(0, 12))
        nav.pack_propagate(False)
        tk.Frame(nav, bg=CX_RED, height=4).pack(fill="x")
        tk.Label(
            nav,
            text="РАЗДЕЛЫ",
            bg=CX_PANEL,
            fg=CX_RED,
            font=_font(12, True),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 10))

        self._help_nav = {}
        for key, title, _cards in HELP_SECTIONS:
            lbl = tk.Label(
                nav,
                text=f"  {title}",
                bg=CX_PANEL,
                fg=CX_OFFWHITE,
                font=_font(12),
                anchor="w",
                pady=11,
                cursor="hand2",
            )
            lbl.pack(fill="x", padx=8, pady=2)
            lbl.bind("<Button-1>", lambda _e, k=key: self._show_help_section(k))
            lbl.bind("<Enter>", lambda _e, b=lbl, k=key: self._help_nav_hover(b, k, True))
            lbl.bind("<Leave>", lambda _e, b=lbl, k=key: self._help_nav_hover(b, k, False))
            self._help_nav[key] = lbl

        # right content
        right = tk.Frame(shell, bg=CX_PANEL, highlightthickness=1, highlightbackground=CX_LINE)
        right.pack(side="left", fill="both", expand=True)
        tk.Frame(right, bg=CX_RED, height=4).pack(fill="x")

        head = tk.Frame(right, bg=CX_PANEL)
        head.pack(fill="x", padx=22, pady=(18, 8))
        self._help_title_var = tk.StringVar(value="")
        tk.Label(
            head,
            textvariable=self._help_title_var,
            bg=CX_PANEL,
            fg=CX_WHITE,
            font=_font(20, True),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            head,
            text="Выберите раздел слева. Все кнопки и шаги скана объяснены простым языком.",
            bg=CX_PANEL,
            fg=CX_SUB,
            font=_font(13),
            wraplength=900,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))

        # scrollable cards
        canvas_wrap = tk.Frame(right, bg=CX_PANEL)
        canvas_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        scroll = tk.Scrollbar(canvas_wrap, bg=CX_PANEL3, troughcolor=CX_PANEL, activebackground=CX_RED)
        scroll.pack(side="right", fill="y")
        self._help_canvas = tk.Canvas(
            canvas_wrap,
            bg=CX_PANEL,
            highlightthickness=0,
            yscrollcommand=scroll.set,
        )
        self._help_canvas.pack(side="left", fill="both", expand=True)
        scroll.config(command=self._help_canvas.yview)

        self._help_body = tk.Frame(self._help_canvas, bg=CX_PANEL)
        self._help_win = self._help_canvas.create_window((0, 0), window=self._help_body, anchor="nw")

        def _on_body_configure(_e=None):
            self._help_canvas.configure(scrollregion=self._help_canvas.bbox("all"))

        def _on_canvas_configure(e):
            self._help_canvas.itemconfigure(self._help_win, width=e.width)

        self._help_body.bind("<Configure>", _on_body_configure)
        self._help_canvas.bind("<Configure>", _on_canvas_configure)

        def _wheel(e):
            # macOS / Windows / Linux
            if getattr(e, "delta", 0):
                self._help_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            elif getattr(e, "num", None) == 4:
                self._help_canvas.yview_scroll(-1, "units")
            elif getattr(e, "num", None) == 5:
                self._help_canvas.yview_scroll(1, "units")

        self._help_canvas.bind("<MouseWheel>", _wheel)
        self._help_canvas.bind("<Button-4>", _wheel)
        self._help_canvas.bind("<Button-5>", _wheel)
        self._help_body.bind("<MouseWheel>", _wheel)

        self._show_help_section("about")

    def _help_nav_hover(self, btn: tk.Label, key: str, enter: bool):
        if key == self._help_active:
            return
        btn.configure(fg=CX_WHITE if enter else CX_OFFWHITE, bg=CX_PANEL2 if enter else CX_PANEL)

    def _show_help_section(self, key: str):
        section = next((s for s in HELP_SECTIONS if s[0] == key), None)
        if not section or self._help_body is None:
            return
        self._help_active = key
        _k, title, cards = section
        self._help_title_var.set(title)

        for k, btn in self._help_nav.items():
            if k == key:
                btn.configure(bg=CX_RED_DARK, fg=CX_WHITE, font=_font(12, True))
            else:
                btn.configure(bg=CX_PANEL, fg=CX_OFFWHITE, font=_font(12))

        for child in self._help_body.winfo_children():
            child.destroy()

        for card in cards:
            box = tk.Frame(
                self._help_body,
                bg=CX_PANEL2,
                highlightthickness=1,
                highlightbackground=CX_LINE,
            )
            box.pack(fill="x", padx=10, pady=8)
            tk.Frame(box, bg=CX_RED, width=5).pack(side="left", fill="y")
            inner = tk.Frame(box, bg=CX_PANEL2)
            inner.pack(side="left", fill="both", expand=True, padx=16, pady=14)
            tk.Label(
                inner,
                text=card["title"],
                bg=CX_PANEL2,
                fg=CX_RED,
                font=_font(15, True),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                inner,
                text=card.get("body", ""),
                bg=CX_PANEL2,
                fg=CX_WHITE,
                font=_font(13),
                wraplength=820,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(8, 6))
            for bullet in card.get("bullets") or []:
                row = tk.Frame(inner, bg=CX_PANEL2)
                row.pack(fill="x", pady=3)
                tk.Label(row, text="▸", bg=CX_PANEL2, fg=CX_RED, font=_font(12, True)).pack(
                    side="left", padx=(0, 8)
                )
                tk.Label(
                    row,
                    text=bullet,
                    bg=CX_PANEL2,
                    fg=CX_OFFWHITE,
                    font=_font(13),
                    wraplength=780,
                    justify="left",
                    anchor="w",
                ).pack(side="left", fill="x", expand=True)

        self._help_canvas.yview_moveto(0)

    def _build_cameras_tab(self, frame: tk.Frame):
        tip = tk.Label(
            frame,
            text="После СТАРТА здесь мозаика камер. Красная рамка = активная проверка.",
            bg=CX_BG,
            fg=CX_SUB,
            font=_font(13),
            anchor="w",
        )
        tip.pack(fill="x", pady=(0, 8))
        self.cam_label = tk.Label(
            frame,
            bg=CX_PANEL,
            text="Ожидание старта…\nНажмите красную кнопку «СТАРТ МОНИТОРИНГА» сверху",
            fg=CX_SUB,
            font=_font(16, True),
            highlightthickness=1,
            highlightbackground=CX_LINE,
            justify="center",
        )
        self.cam_label.pack(fill="both", expand=True)

    def _build_own_tab(self, frame: tk.Frame):
        left = tk.Frame(frame, bg=CX_BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = tk.Frame(frame, bg=CX_PANEL, width=300, highlightthickness=1, highlightbackground=CX_LINE)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(left, text="Живой статус камер", bg=CX_BG, fg=CX_WHITE, font=_font(15, True)).pack(
            anchor="w", pady=(0, 8)
        )
        cols = ("cam", "status", "queue", "own")
        self.own_tree = ttk.Treeview(left, columns=cols, show="headings", height=20, style="CX.Treeview")
        for c, t, w in (
            ("cam", "Камера", 180),
            ("status", "Статус", 340),
            ("queue", "Очередь", 150),
            ("own", "Своё", 70),
        ):
            self.own_tree.heading(c, text=t)
            self.own_tree.column(c, width=w)
        self.own_tree.pack(fill="both", expand=True)

        tk.Frame(right, bg=CX_RED, height=4).pack(fill="x")
        tk.Label(right, text="ЛЕГЕНДА", bg=CX_PANEL, fg=CX_RED, font=_font(13, True)).pack(
            anchor="w", padx=16, pady=(16, 10)
        )
        for line, color in (
            ("КЛУБ — товар бара CyberX", CX_OK),
            ("СВОЁ — точно чужое → Telegram", CX_RED),
            ("?? — подозрительно (мягкий режим)", "#FFD166"),
            ("Очередь — ждут у админки", CX_OFFWHITE),
            ("", CX_SUB),
            ("Реагируйте на алерты:", CX_WHITE),
            ("• подойти к стойке", CX_SUB),
            ("• напомнить: своя еда нельзя", CX_SUB),
        ):
            tk.Label(right, text=line or " ", bg=CX_PANEL, fg=color, font=_font(12), anchor="w").pack(
                fill="x", padx=16, pady=2
            )

    def _build_catalog_tab(self, frame: tk.Frame):
        banner = tk.Frame(frame, bg=CX_RED)
        banner.pack(fill="x", pady=(0, 10))
        tk.Label(
            banner,
            text="  Скан на ноуте  →  ЭКСПОРТ ZIP  →  Telegram  →  ИМПОРТ на ПК клуба  ",
            bg=CX_RED,
            fg=CX_WHITE,
            font=_font(13, True),
            pady=10,
        ).pack()

        # --- прогресс скана (всегда виден) ---
        prog = tk.Frame(frame, bg=CX_PANEL, highlightthickness=1, highlightbackground=CX_RED)
        prog.pack(fill="x", pady=(0, 10))
        top = tk.Frame(prog, bg=CX_PANEL)
        top.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(top, text="ПРОГРЕСС СКАНА", bg=CX_PANEL, fg=CX_RED, font=_font(12, True)).pack(side="left")
        tk.Label(top, textvariable=self._scan_pct_var, bg=CX_PANEL, fg=CX_WHITE, font=_font(16, True)).pack(
            side="right"
        )
        tk.Label(
            prog,
            textvariable=self._scan_title_var,
            bg=CX_PANEL,
            fg=CX_WHITE,
            font=_font(13, True),
            anchor="w",
        ).pack(fill="x", padx=14)

        # progress bar canvas
        self._scan_bar_bg = tk.Frame(prog, bg=CX_BLACK, height=14)
        self._scan_bar_bg.pack(fill="x", padx=14, pady=8)
        self._scan_bar_fg = tk.Frame(self._scan_bar_bg, bg=CX_RED, height=14, width=0)
        self._scan_bar_fg.place(x=0, y=0, relheight=1.0)

        steps_row = tk.Frame(prog, bg=CX_PANEL)
        steps_row.pack(fill="x", padx=10, pady=(0, 6))
        self._scan_steps = {}
        for key, title in (
            ("sku", "1. SKU + название"),
            ("saved", "2. Товар в базе"),
            ("photo", "3. Фото"),
            ("quality", "4. Качество"),
            ("ocr", "5. OCR этикетки"),
            ("refs", f"6. Эталоны {MIN_SCAN_REFS} шт"),
            ("aliases", "7. Aliases"),
        ):
            cell = tk.Frame(steps_row, bg=CX_PANEL2, highlightthickness=1, highlightbackground=CX_LINE)
            cell.pack(side="left", fill="both", expand=True, padx=3, pady=2)
            lbl = tk.Label(
                cell,
                text=f"○  {title}",
                bg=CX_PANEL2,
                fg=CX_SUB,
                font=_font(10),
                pady=8,
                cursor="hand2",
            )
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda _e, k=key: self._explain_scan_step(k))
            cell.bind("<Button-1>", lambda _e, k=key: self._explain_scan_step(k))
            self._scan_steps[key] = lbl

        tk.Label(
            prog,
            textvariable=self._step_desc_var,
            bg=CX_PANEL,
            fg=CX_OFFWHITE,
            font=_font(12),
            wraplength=1200,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 4))

        tk.Label(
            prog,
            textvariable=self._scan_hint_var,
            bg=CX_PANEL,
            fg=CX_SUB,
            font=_font(12),
            wraplength=1200,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(4, 12))
        link = tk.Label(
            prog,
            text="Подробнее про все шаги → вкладка «Как работать» → «Что значит прогресс скана»",
            bg=CX_PANEL,
            fg=CX_RED,
            font=_font(11, True),
            cursor="hand2",
            anchor="w",
        )
        link.pack(fill="x", padx=14, pady=(0, 12))
        link.bind("<Button-1>", lambda _e: self._open_help_scan_terms())

        body = tk.Frame(frame, bg=CX_BG)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=CX_BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right_shell = tk.Frame(body, bg=CX_BG, width=560)
        right_shell.pack(side="right", fill="both")
        right_shell.pack_propagate(False)

        toolbar = tk.Frame(left, bg=CX_BG)
        toolbar.pack(fill="x", pady=(0, 8))
        self._btn(toolbar, "Обновить", self._refresh_catalog, ghost=True).pack(side="left", padx=3)
        self._btn(toolbar, "Синхр. меню", self._sync_menu, ghost=True).pack(side="left", padx=3)
        self._btn(toolbar, "ЭКСПОРТ ZIP", self._export_pack, primary=True).pack(side="left", padx=6)
        self._btn(toolbar, "ИМПОРТ ZIP", self._import_pack, primary=True).pack(side="left", padx=3)

        self._unscanned_var = tk.StringVar(value="")
        tk.Label(
            left,
            textvariable=self._unscanned_var,
            bg=CX_BG,
            fg="#FFD166",
            font=_font(11, True),
            wraplength=700,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        tree_wrap = tk.Frame(left, bg=CX_BG)
        tree_wrap.pack(fill="both", expand=True)
        cols = ("sku", "name", "category", "weight", "aliases", "refs")
        self.cat_tree = ttk.Treeview(
            tree_wrap, columns=cols, show="headings", height=16, style="CX.Treeview"
        )
        for c, t, w in (
            ("sku", "SKU", 100),
            ("name", "Название", 180),
            ("category", "Категория", 80),
            ("weight", "Объём", 70),
            ("aliases", "Aliases", 220),
            ("refs", "Скан", 90),
        ):
            self.cat_tree.heading(c, text=t)
            self.cat_tree.column(c, width=w)
        cat_scroll = ttk.Scrollbar(
            tree_wrap, orient="vertical", command=self.cat_tree.yview, style="CX.Vertical.TScrollbar"
        )
        self.cat_tree.configure(yscrollcommand=cat_scroll.set)
        self.cat_tree.pack(side="left", fill="both", expand=True)
        cat_scroll.pack(side="right", fill="y")
        self.cat_tree.bind("<<TreeviewSelect>>", self._on_cat_select)

        def _tree_wheel(e):
            if getattr(e, "delta", 0):
                self.cat_tree.yview_scroll(-1 if e.delta > 0 else 1, "units")
            elif getattr(e, "num", None) == 4:
                self.cat_tree.yview_scroll(-1, "units")
            elif getattr(e, "num", None) == 5:
                self.cat_tree.yview_scroll(1, "units")

        self.cat_tree.bind("<MouseWheel>", _tree_wheel)
        self.cat_tree.bind("<Button-4>", _tree_wheel)
        self.cat_tree.bind("<Button-5>", _tree_wheel)

        # правая колонка со скроллом (форма + скан целиком видны)
        right_scroll = ttk.Scrollbar(right_shell, orient="vertical", style="CX.Vertical.TScrollbar")
        right_scroll.pack(side="right", fill="y")
        right_canvas = tk.Canvas(
            right_shell,
            bg=CX_BG,
            highlightthickness=0,
            yscrollcommand=right_scroll.set,
            width=540,
        )
        right_canvas.pack(side="left", fill="both", expand=True)
        right_scroll.config(command=right_canvas.yview)

        right = tk.Frame(right_canvas, bg=CX_BG)
        right_win = right_canvas.create_window((0, 0), window=right, anchor="nw")

        def _right_body_cfg(_e=None):
            right_canvas.configure(scrollregion=right_canvas.bbox("all"))

        def _right_canvas_cfg(e):
            right_canvas.itemconfigure(right_win, width=e.width)

        right.bind("<Configure>", _right_body_cfg)
        right_canvas.bind("<Configure>", _right_canvas_cfg)

        def _right_wheel(e):
            if getattr(e, "delta", 0):
                right_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            elif getattr(e, "num", None) == 4:
                right_canvas.yview_scroll(-1, "units")
            elif getattr(e, "num", None) == 5:
                right_canvas.yview_scroll(1, "units")

        def _bind_right_wheel(widget):
            widget.bind("<MouseWheel>", _right_wheel)
            widget.bind("<Button-4>", _right_wheel)
            widget.bind("<Button-5>", _right_wheel)
            for child in widget.winfo_children():
                _bind_right_wheel(child)

        form = tk.Frame(right, bg=CX_PANEL, highlightthickness=1, highlightbackground=CX_LINE)
        form.pack(fill="x", pady=(0, 10))
        tk.Frame(form, bg=CX_RED, height=3).pack(fill="x")
        tk.Label(form, text="ТОВАР", bg=CX_PANEL, fg=CX_RED, font=_font(12, True)).pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        tk.Label(
            form,
            text="SKU = код · для напитков в «Объём» пишите 0.33 л / 0.45 л / 0.5 л (1л и 2л = своё)",
            bg=CX_PANEL,
            fg=CX_OFFWHITE,
            font=_font(10),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 8))

        self.f_sku = tk.StringVar()
        self.f_name = tk.StringVar()
        self.f_cat = tk.StringVar(value="snack")
        self.f_weight = tk.StringVar()
        self.f_aliases = tk.StringVar()
        for label, var in (
            ("SKU", self.f_sku),
            ("Название", self.f_name),
            ("Категория", self.f_cat),
            ("Объём", self.f_weight),
            ("Aliases", self.f_aliases),
        ):
            row = tk.Frame(form, bg=CX_PANEL)
            row.pack(fill="x", padx=12, pady=4)
            tk.Label(row, text=label, width=10, anchor="w", bg=CX_PANEL, fg=CX_SUB, font=_font(11)).pack(
                side="left"
            )
            self._entry(row, var).pack(side="left", fill="x", expand=True, ipady=6)

        btns = tk.Frame(form, bg=CX_PANEL)
        btns.pack(fill="x", padx=12, pady=12)
        self._btn(btns, "Сохранить", self._save_item, primary=True).pack(side="left", padx=3)
        self._btn(btns, "Выключить", self._deactivate_item, danger=True).pack(side="left", padx=3)

        scan = tk.Frame(right, bg=CX_PANEL, highlightthickness=1, highlightbackground=CX_LINE)
        scan.pack(fill="x", expand=False)
        tk.Frame(scan, bg=CX_RED, height=3).pack(fill="x")
        tk.Label(scan, text="СКАН / ЭТАЛОН", bg=CX_PANEL, fg=CX_RED, font=_font(14, True)).pack(
            anchor="w", padx=14, pady=(12, 6)
        )
        tk.Label(
            scan,
            text=f"Скан: «iPhone как камера» → большое окно Live (раздвиньте) с подсказками.\n"
            f"Или «iPhone скан» → AirDrop в CyberX_Inbox. Нужно {MIN_SCAN_REFS} ракурса: ЛИЦО/БОК/ДАЛЬШЕ.",
            bg=CX_PANEL,
            fg=CX_SUB,
            font=_font(11),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 8))
        rowb = tk.Frame(scan, bg=CX_PANEL)
        rowb.pack(fill="x", padx=12, pady=(0, 6))
        self._btn(rowb, "iPhone скан", self._toggle_phone_scan, primary=True).pack(side="left", padx=2)
        self._btn(rowb, "iPhone Live окно", self._start_continuity_camera, primary=True).pack(
            side="left", padx=2
        )
        self._btn(rowb, "СНИМОК", self._capture_webcam, ghost=True).pack(side="left", padx=2)
        self._btn(rowb, "Файл…", self._pick_photo, ghost=True).pack(side="left", padx=2)

        # QR / ссылка для iPhone
        self._phone_box = tk.Frame(scan, bg=CX_BLACK, highlightthickness=1, highlightbackground=CX_RED)
        self._phone_box.pack(fill="x", padx=14, pady=(0, 8))
        self._phone_box.pack_forget()
        ph_top = tk.Frame(self._phone_box, bg=CX_BLACK)
        ph_top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(
            ph_top,
            text="СКАН С iPhone",
            bg=CX_BLACK,
            fg=CX_RED,
            font=_font(12, True),
        ).pack(side="left")
        self._btn(ph_top, "Стоп", self._stop_phone_scan, ghost=True).pack(side="right", padx=4)
        self._btn(ph_top, "Папка AirDrop", self._open_airdrop_inbox, ghost=True).pack(side="right", padx=4)
        self._btn(ph_top, "Копировать ссылку", self._copy_phone_url, ghost=True).pack(side="right", padx=4)
        ph_body = tk.Frame(self._phone_box, bg=CX_BLACK)
        ph_body.pack(fill="x", padx=10, pady=(0, 10))
        self._phone_qr_label = tk.Label(ph_body, bg=CX_BLACK)
        self._phone_qr_label.pack(side="left", padx=(0, 12))
        tk.Label(
            ph_body,
            textvariable=self._phone_url_var,
            bg=CX_BLACK,
            fg=CX_WHITE,
            font=_font(12),
            wraplength=320,
            justify="left",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        auto_row = tk.Frame(scan, bg=CX_PANEL)
        auto_row.pack(fill="x", padx=14, pady=(0, 6))
        tk.Checkbutton(
            auto_row,
            text="Авто-доскан: сохранять ракурс сразу, если качество OK",
            variable=self._auto_enroll,
            bg=CX_PANEL,
            fg=CX_WHITE,
            selectcolor=CX_BLACK,
            activebackground=CX_PANEL,
            activeforeground=CX_WHITE,
            font=_font(11),
            highlightthickness=0,
        ).pack(anchor="w")

        # крупная подсказка ракурса
        angle_box = tk.Frame(scan, bg=CX_BLACK, highlightthickness=1, highlightbackground=CX_RED)
        angle_box.pack(fill="x", padx=14, pady=(2, 10))
        top_a = tk.Frame(angle_box, bg=CX_BLACK)
        top_a.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top_a, text="СЛЕДУЮЩИЙ РАКУРС", bg=CX_BLACK, fg=CX_RED, font=_font(11, True)).pack(
            side="left"
        )
        tk.Label(
            top_a,
            textvariable=self._angle_count_var,
            bg=CX_BLACK,
            fg=CX_WHITE,
            font=_font(22, True),
        ).pack(side="right")
        tk.Label(
            angle_box,
            textvariable=self._angle_hint_var,
            bg=CX_BLACK,
            fg=CX_WHITE,
            font=_font(14, True),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 12))

        # статус скана прямо у камеры — чтобы было видно без прокрутки наверх
        self._scan_local_box = tk.Frame(scan, bg=CX_RED_DEEP, highlightthickness=1, highlightbackground=CX_RED)
        self._scan_local_box.pack(fill="x", padx=14, pady=(4, 10))
        tk.Label(
            self._scan_local_box,
            text="СТАТУС СКАНА ПРОДУКЦИИ",
            bg=CX_RED_DEEP,
            fg=CX_WHITE,
            font=_font(11, True),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(
            self._scan_local_box,
            textvariable=self._scan_local_var,
            bg=CX_RED_DEEP,
            fg=CX_WHITE,
            font=_font(12),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))

        tk.Label(scan, text="ЖИВАЯ КАМЕРА", bg=CX_PANEL, fg=CX_RED, font=_font(11, True)).pack(
            anchor="w", padx=14, pady=(4, 2)
        )
        self._webcam_box = tk.Frame(scan, bg=CX_BLACK, height=320)
        self._webcam_box.pack(fill="x", padx=14, pady=(0, 10))
        self._webcam_box.pack_propagate(False)
        self.webcam_label = tk.Label(
            self._webcam_box,
            bg=CX_BLACK,
            text="Камера Mac выкл.\nДля скана нажмите «iPhone»\nили «Камера Mac» / «Файл…»",
            fg=CX_SUB,
            font=_font(14),
            justify="center",
        )
        self.webcam_label.pack(fill="both", expand=True)

        self._btn(scan, "Сохранить этот ракурс в эталон", self._enroll_photo, primary=True).pack(
            padx=14, pady=8, anchor="w"
        )

        tk.Label(scan, text="ПРЕВЬЮ ЭТАЛОНА", bg=CX_PANEL, fg=CX_RED, font=_font(11, True)).pack(
            anchor="w", padx=14, pady=(4, 2)
        )
        self._photo_box = tk.Frame(scan, bg=CX_BLACK, height=360)
        self._photo_box.pack(fill="x", padx=14, pady=(0, 10))
        self._photo_box.pack_propagate(False)
        self.photo_preview = tk.Label(
            self._photo_box,
            bg=CX_BLACK,
            text="Превью эталона\nСделайте снимок или выберите файл",
            fg=CX_SUB,
            font=_font(14),
            justify="center",
        )
        self.photo_preview.pack(fill="both", expand=True)

        self.ocr_preview = tk.StringVar(value="OCR: —")
        tk.Label(
            scan,
            textvariable=self.ocr_preview,
            bg=CX_PANEL,
            fg=CX_WHITE,
            wraplength=500,
            justify="left",
            anchor="w",
            font=_font(12),
        ).pack(fill="x", padx=14, pady=(0, 20))

        _bind_right_wheel(right)
        right_canvas.bind("<MouseWheel>", _right_wheel)
        right_canvas.bind("<Button-4>", _right_wheel)
        right_canvas.bind("<Button-5>", _right_wheel)
        self._cat_right_canvas = right_canvas

    def _build_alerts_tab(self, frame: tk.Frame):
        bar = tk.Frame(frame, bg=CX_BG)
        bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "Обновить", self._refresh_alerts, ghost=True).pack(side="left")
        tk.Label(bar, text="Те же события уходят в Telegram-бот", bg=CX_BG, fg=CX_SUB, font=_font(13)).pack(
            side="left", padx=14
        )
        cols = ("ts", "module", "camera", "severity", "title")
        self.alert_tree = ttk.Treeview(frame, columns=cols, show="headings", height=24, style="CX.Treeview")
        for c, t, w in (
            ("ts", "Время", 160),
            ("module", "Модуль", 120),
            ("camera", "Камера", 170),
            ("severity", "Уровень", 90),
            ("title", "Событие", 440),
        ):
            self.alert_tree.heading(c, text=t)
            self.alert_tree.column(c, width=w)
        self.alert_tree.pack(fill="both", expand=True)

    # ---- catalog actions ----
    def _explain_scan_step(self, key: str):
        title, text = SCAN_STEP_HELP.get(key, (key, ""))
        self._step_desc_var.set(f"{title}: {text}")

    def _open_help_scan_terms(self):
        self._show_tab("help")
        self._show_help_section("scan_terms")

    def _bind_scan_traces(self):
        for var in (self.f_sku, self.f_name, self.f_aliases):
            try:
                var.trace_add("write", lambda *_: self._update_scan_progress())
            except Exception:
                pass

    def _set_step(self, key: str, ok: bool, warn: bool = False, title: str = ""):
        lbl = self._scan_steps.get(key)
        if not lbl:
            return
        base = title or lbl.cget("text").split("  ", 1)[-1]
        # strip old prefix marks
        for p in ("✓  ", "○  ", "!  "):
            if base.startswith(p):
                base = base[len(p) :]
        if "  " in lbl.cget("text"):
            # keep original titles from construction
            parts = lbl.cget("text").split("  ", 1)
            if len(parts) == 2 and parts[0] in ("✓", "○", "!"):
                base = parts[1]
        if ok:
            lbl.configure(text=f"✓  {base}", fg=CX_OK, bg="#132818")
            lbl.master.configure(bg="#132818", highlightbackground=CX_OK)
        elif warn:
            lbl.configure(text=f"!  {base}", fg="#FFD166", bg="#2A2208")
            lbl.master.configure(bg="#2A2208", highlightbackground="#FFD166")
        else:
            lbl.configure(text=f"○  {base}", fg=CX_SUB, bg=CX_PANEL2)
            lbl.master.configure(bg=CX_PANEL2, highlightbackground=CX_LINE)

    def _get_scan_coach(self):
        if self._scan_coach is None:
            from src.vision.product_scan_coach import ProductScanCoach

            self._scan_coach = ProductScanCoach()
        return self._scan_coach

    def _sku_angle_state(self, sku: str = ""):
        """have_angles, next_angle, refs_n."""
        if not self.catalog or not sku:
            return [], "front", 0
        refs = self.catalog.list_refs(sku)
        have = sorted(self.catalog.angles_present(sku))
        nxt = self.catalog.next_angle_slot(sku)
        return have, nxt, len(refs)

    def _photo_quality(self, bgr: np.ndarray) -> tuple[bool, str, list[str]]:
        """Проверка кадра перед сохранением эталона (через Scan Coach)."""
        try:
            coach = self._get_scan_coach()
            crop, _ = coach.localize_pack(bgr)
            return coach.quality(crop if crop is not None and crop.size else bgr)
        except Exception:
            issues: list[str] = []
            h, w = bgr.shape[:2]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            mean = float(gray.mean())
            if min(h, w) < 140:
                issues.append("фото слишком мелкое — подойдите ближе")
            if blur < 45:
                issues.append("размыто — держите камеру ровнее / добавьте света")
            if mean < 40:
                issues.append("слишком темно")
            if mean > 225:
                issues.append("засвет / слишком ярко")
            ok = len(issues) == 0
            msg = "качество OK" if ok else "; ".join(issues)
            return ok, msg, issues

    def _scan_missing_steps(self) -> tuple[list[str], int, int, int]:
        """Что ещё нужно для полного скана. (missing, done, total, refs_n)."""
        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        name = self.f_name.get().strip() if hasattr(self, "f_name") else ""
        aliases = [
            a.strip()
            for a in (self.f_aliases.get().split(",") if hasattr(self, "f_aliases") else [])
            if a.strip()
        ]
        step_sku = bool(sku and name)
        step_saved = bool(self.catalog and sku and self.catalog.get_item(sku))
        have, nxt, refs_n = self._sku_angle_state(sku) if sku else ([], "front", 0)
        angles_n = len(have)
        has_pending = self._pending_photo is not None and self._pending_bgr is not None
        step_photo = has_pending or angles_n > 0
        step_quality = bool(self._last_quality_ok) if has_pending else (angles_n > 0)
        step_ocr = len(self._last_ocr) >= 3 or (angles_n > 0 and len(aliases) >= 1)
        step_refs = bool(self.catalog and sku and self.catalog.sku_scan_complete(sku))
        step_aliases = len(aliases) >= 1
        flags = [step_sku, step_saved, step_photo, step_quality, step_ocr, step_refs, step_aliases]
        done = sum(1 for x in flags if x)
        total = len(flags)

        from src.vision.product_scan_coach import ANGLE_LABELS_RU, ANGLE_SLOTS

        missing: list[str] = []
        if not step_sku:
            missing.append("укажите SKU и название товара")
        elif not step_saved:
            missing.append("нажмите «Сохранить» товар в базе")

        if not step_refs:
            need_slots = [ANGLE_LABELS_RU[a] for a in ANGLE_SLOTS if a not in set(have)]
            next_sides = ", ".join(need_slots) if need_slots else "ракурсы"
            if has_pending and not self._last_quality_ok:
                missing.append(f"качество плохое — переснимите: {self._last_quality_msg}")
            elif has_pending and getattr(self, "_last_coach_verdict", None) and not self._last_coach_verdict.ok:
                missing.append(self._last_coach_verdict.message)
            elif has_pending and self._last_quality_ok:
                missing.append(
                    f"нажмите «Сохранить этот ракурс в эталон» — ещё нужно: {next_sides}"
                )
            else:
                missing.append(
                    f"сделайте «СНИМОК + ПРОВЕРКА» — нужны ракурсы: {next_sides}"
                )

        if not step_aliases:
            missing.append("добавьте aliases (слова с этикетки через запятую)")
        return missing, done, total, angles_n

    def _refresh_scan_local_banner(self, live_note: str = ""):
        if not hasattr(self, "_scan_local_var"):
            return
        missing, done, total, refs_n = self._scan_missing_steps()
        pct = int(round(100 * done / total)) if total else 0
        box = getattr(self, "_scan_local_box", None)
        if pct >= 100:
            msg = (
                f"✓ СКАН ПОЛНЫЙ · эталоны {refs_n}/{MIN_SCAN_REFS} · {pct}%\n"
                f"Можно ЭКСПОРТ ZIP и перенос на ПК клуба."
            )
            bg, border = "#0E2A18", CX_OK
        else:
            lines = "\n".join(f"• {m}" for m in (missing[:4] or ["продолжите скан"]))
            msg = (
                f"⚠ СКАН НЕ ПОЛНЫЙ · {done}/{total} шагов · эталоны {refs_n}/{MIN_SCAN_REFS} · {pct}%\n"
            )
            if live_note:
                msg += live_note + "\n"
            msg += "Что дальше:\n" + lines
            bg, border = CX_RED_DEEP, CX_RED
        self._scan_local_var.set(msg)
        if box is not None:
            try:
                box.configure(bg=bg, highlightbackground=border)
                for ch in box.winfo_children():
                    ch.configure(bg=bg, fg=CX_WHITE)
            except Exception:
                pass

    def _update_scan_progress(self):
        if not getattr(self, "_scan_steps", None):
            return
        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        name = self.f_name.get().strip() if hasattr(self, "f_name") else ""
        aliases = [
            a.strip()
            for a in (self.f_aliases.get().split(",") if hasattr(self, "f_aliases") else [])
            if a.strip()
        ]

        step_sku = bool(sku and name)
        step_saved = bool(self.catalog and sku and self.catalog.get_item(sku))
        refs_n = len(self.catalog.list_refs(sku)) if self.catalog and sku else 0
        has_pending = self._pending_photo is not None and self._pending_bgr is not None
        step_photo = has_pending or refs_n > 0
        step_quality = bool(self._last_quality_ok) if has_pending else (refs_n > 0)
        step_ocr = len(self._last_ocr) >= 3 or (refs_n > 0 and len(aliases) >= 1)
        step_refs = refs_n >= MIN_SCAN_REFS
        step_aliases = len(aliases) >= 1

        titles = {
            "sku": "1. SKU + название",
            "saved": "2. Товар в базе",
            "photo": "3. Фото",
            "quality": "4. Качество",
            "ocr": "5. OCR этикетки",
            "refs": f"6. Эталоны {refs_n}/{MIN_SCAN_REFS}",
            "aliases": "7. Aliases",
        }
        flags = {
            "sku": step_sku,
            "saved": step_saved,
            "photo": step_photo,
            "quality": step_quality,
            "ocr": step_ocr,
            "refs": step_refs,
            "aliases": step_aliases,
        }
        warns = {
            "quality": has_pending and not step_quality,
            "ocr": has_pending and not (len(self._last_ocr) >= 3),
            "refs": step_saved and refs_n > 0 and not step_refs,
        }

        done = 0
        total = len(flags)
        for k, ok in flags.items():
            self._set_step(k, ok, warn=warns.get(k, False), title=titles[k])
            if ok:
                done += 1

        pct = int(round(100 * done / total)) if total else 0
        self._scan_pct_var.set(f"{pct}%")
        try:
            self._scan_bar_bg.update_idletasks()
            full_w = max(self._scan_bar_bg.winfo_width(), 100)
            self._scan_bar_fg.configure(width=max(4, int(full_w * pct / 100)))
            self._scan_bar_fg.configure(bg=CX_OK if pct >= 100 else CX_RED)
        except Exception:
            pass

        missing, _, _, refs_n = self._scan_missing_steps()
        if pct >= 100:
            self._scan_title_var.set("✓ СКАН ЗАВЕРШЁН — можно ЭКСПОРТ ZIP")
            self._scan_hint_var.set(
                "Товар полностью отсканирован. На ноуте сделайте ЭКСПОРТ ZIP и перенесите на ПК клуба."
            )
        else:
            self._scan_title_var.set(
                f"Скан не завершён · {done}/{total} шагов · эталоны {refs_n}/{MIN_SCAN_REFS}"
            )
            self._scan_hint_var.set("Дальше: " + " → ".join(missing[:3]))

        self._refresh_scan_local_banner()
        self._refresh_angle_hint()

    def _angle_guide(self, refs_n=None):
        """Номер следующего ракурса (1..3), код, инструкция."""
        from src.vision.product_scan_coach import ANGLE_LABELS_RU, ANGLE_SLOTS, ANGLE_TIPS

        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        have, nxt, n = self._sku_angle_state(sku)
        if refs_n is not None and not sku:
            n = refs_n
        if not nxt:
            return MIN_SCAN_REFS, "ГОТОВО", "Все ракурсы есть. Можно ЭКСПОРТ ZIP."
        idx = ANGLE_SLOTS.index(nxt) + 1
        return idx, ANGLE_LABELS_RU[nxt], ANGLE_TIPS[nxt]

    def _refresh_angle_hint(self):
        if not hasattr(self, "_angle_hint_var"):
            return
        from src.vision.product_scan_coach import ProductScanCoach, ANGLE_LABELS_RU, ANGLE_TIPS

        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        have, nxt, refs_n = self._sku_angle_state(sku)
        self._angle_count_var.set(f"{len(have)}/{MIN_SCAN_REFS}")
        prog = ProductScanCoach.progress_text(have, nxt)
        if not nxt:
            self._angle_hint_var.set(f"✓ СКАН ПОЛНЫЙ ({len(have)}/{MIN_SCAN_REFS})\n{prog}")
        else:
            tip = ANGLE_TIPS.get(nxt, ANGLE_LABELS_RU.get(nxt, nxt))
            self._angle_hint_var.set(f"Нужен ракурс: {ANGLE_LABELS_RU.get(nxt, nxt)}\n{tip}\n{prog}")

    def _draw_angle_overlay(self, rgb: np.ndarray, refs_n: int) -> np.ndarray:
        """rgb array in RGB — convert for coach (BGR) then back."""
        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        have, nxt, _ = self._sku_angle_state(sku)
        target = nxt or "front"
        try:
            coach = self._get_scan_coach()
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            drawn = coach.draw_overlay(
                bgr,
                target_angle=target,
                have_angles=have,
                verdict=getattr(self, "_last_coach_verdict", None),
            )
            return cv2.cvtColor(drawn, cv2.COLOR_BGR2RGB)
        except Exception:
            h, w = rgb.shape[:2]
            num, code, _tip = self._angle_guide(refs_n)
            margin_x, margin_y = int(w * 0.18), int(h * 0.12)
            x1, y1, x2, y2 = margin_x, margin_y, w - margin_x, h - margin_y
            color = (255, 40, 40)
            cv2.rectangle(rgb, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                rgb,
                f"{refs_n}/{MIN_SCAN_REFS}  NEXT: {code}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                color,
                2,
                cv2.LINE_AA,
            )
            return rgb

    def _refresh_catalog(self):
        if not self.catalog:
            return
        for i in self.cat_tree.get_children():
            self.cat_tree.delete(i)
        missing_skus: list[str] = []
        weak: list[str] = []
        for item in self.catalog.list_items():
            have = self.catalog.angles_present(item["sku"])
            angles_n = len(have)
            complete = self.catalog.sku_scan_complete(item["sku"])
            if complete:
                scan_s = f"✓ {angles_n}/{MIN_SCAN_REFS}"
            elif angles_n > 0:
                miss_ang = self.catalog.missing_angles(item["sku"])
                from src.vision.product_scan_coach import ANGLE_LABELS_RU

                miss_s = ",".join(ANGLE_LABELS_RU.get(a, a)[:1] for a in miss_ang)
                scan_s = f"! {angles_n}/{MIN_SCAN_REFS} (−{miss_s})"
                weak.append(item["sku"])
            else:
                scan_s = f"○ 0/{MIN_SCAN_REFS}"
                missing_skus.append(item["sku"])
            self.cat_tree.insert(
                "",
                "end",
                iid=item["sku"],
                values=(
                    item["sku"],
                    item["name"],
                    item["category"],
                    item["weight"],
                    ", ".join(item.get("aliases") or [])[:80],
                    scan_s,
                ),
            )
        if hasattr(self, "_unscanned_var"):
            try:
                cov = self.catalog.coverage_stats()
                ratio_pct = int(100 * cov["ratio"])
                incomplete = cov["incomplete_skus"]
                if cov["ratio"] < 0.9:
                    sample = ", ".join(incomplete[:8])
                    more = f" +{len(incomplete)-8}" if len(incomplete) > 8 else ""
                    self._unscanned_var.set(
                        f"⚠ Детектор слаб: полный скан (ЛИЦО+БОК+ДАЛЬШЕ) у {cov['complete']}/{cov['total']} "
                        f"SKU ({ratio_pct}%). Не хватает: {sample}{more}"
                    )
                else:
                    self._unscanned_var.set(
                        f"✓ Каталог готов: {cov['complete']}/{cov['total']} SKU с 3 ракурсами ({ratio_pct}%)"
                    )
            except Exception:
                if missing_skus or weak:
                    parts = []
                    if missing_skus:
                        parts.append(f"без эталонов ({len(missing_skus)}): {', '.join(missing_skus[:8])}")
                    if weak:
                        parts.append(f"мало ракурсов ({len(weak)}): {', '.join(weak[:8])}")
                    self._unscanned_var.set(
                        "⚠ Детектор «со своим» слаб, пока нет 3 фото на SKU — " + " · ".join(parts)
                    )
                else:
                    self._unscanned_var.set("✓ Все активные SKU имеют ≥3 эталона — галерея готова")
        self._update_scan_progress()

    def _on_cat_select(self, _evt=None):
        sel = self.cat_tree.selection()
        if not sel or not self.catalog:
            return
        item = self.catalog.get_item(sel[0])
        if not item:
            return
        self.f_sku.set(item["sku"])
        self.f_name.set(item["name"])
        self.f_cat.set(item["category"])
        self.f_weight.set(item["weight"])
        self.f_aliases.set(", ".join(item.get("aliases") or []))
        self._update_scan_progress()

    def _save_item(self):
        if not self.catalog:
            return
        sku = self.f_sku.get().strip()
        name = self.f_name.get().strip()
        if not sku or not name:
            messagebox.showwarning("Каталог", "Укажите SKU и название")
            return
        aliases = [a.strip() for a in self.f_aliases.get().split(",") if a.strip()]
        self.catalog.upsert_item(
            sku=sku,
            name=name,
            category=self.f_cat.get().strip() or "other",
            weight=self.f_weight.get().strip(),
            aliases=aliases,
        )
        if self.platform:
            self.platform.reload_catalog()
        self._refresh_catalog()
        self._status_var.set(f"Сохранено: {sku}")
        self._update_scan_progress()

    def _deactivate_item(self):
        sku = self.f_sku.get().strip()
        if not sku or not self.catalog:
            return
        if not messagebox.askyesno("Каталог", f"Выключить {sku} из распознавания?"):
            return
        self.catalog.deactivate(sku)
        if self.platform:
            self.platform.reload_catalog()
        self._refresh_catalog()

    def _sync_menu(self):
        if not self.catalog:
            return
        self.catalog.sync_club_menu()
        try:
            from src.vision.embedding_matcher import EmbeddingMatcher

            refs: dict[str, list[str]] = {}
            for ref in self.catalog.list_refs():
                refs.setdefault(ref["sku"], []).append(ref["path"])
            emb = EmbeddingMatcher(enabled=True)
            emb.invalidate()
            n = emb.build_gallery(refs, force=True)
            self._status_var.set(f"Галерея embedding: {n} векторов")
        except Exception as exc:
            self._status_var.set(f"Галерея embedding: {exc}")
        if self.platform:
            self.platform.reload_catalog()
        messagebox.showinfo("Каталог", "Меню распознавания и embedding-галерея обновлены")

    def _export_pack(self):
        if not self.catalog:
            return
        path = filedialog.asksaveasfilename(
            title="Экспорт каталога CyberX",
            defaultextension=".zip",
            initialfile=f"cyberx_catalog_pack_{time.strftime('%Y%m%d_%H%M')}.zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        try:
            out = self.catalog.export_pack(Path(path))
            messagebox.showinfo(
                "Экспорт",
                f"Готово!\n\n{out}\n\nОтправьте ZIP в Telegram / на флешку\nи на ПК клуба нажмите ИМПОРТ ZIP.",
            )
            self._status_var.set(f"Экспорт: {out.name}")
        except Exception as exc:
            messagebox.showerror("Экспорт", str(exc))

    def _import_pack(self):
        if not self.catalog:
            return
        path = filedialog.askopenfilename(
            title="Импорт каталога CyberX",
            filetypes=[("ZIP пакет", "*.zip"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            stats = self.catalog.import_pack(Path(path), merge=True)
            # всегда пересобрать меню + gallery (даже если мониторинг выкл)
            try:
                from src.vision.embedding_matcher import EmbeddingMatcher

                refs: dict[str, list[str]] = {}
                for ref in self.catalog.list_refs():
                    refs.setdefault(ref["sku"], []).append(ref["path"])
                emb = EmbeddingMatcher(enabled=True)
                emb.invalidate()
                n = emb.build_gallery(refs, force=True)
                self._status_var.set(f"Импорт OK · галерея {n} векторов")
            except Exception as exc:
                self._status_var.set(f"Импорт OK · галерея: {exc}")
            if self.platform:
                self.platform.reload_catalog()
            self.catalog.sync_club_menu()
            self._refresh_catalog()
            err = "\n".join(stats["errors"][:5])
            messagebox.showinfo(
                "Импорт",
                f"Товаров: {stats['products']}\nФото: {stats['images']}\n\n"
                f"Обязательно: Синхр. меню уже выполнена.\n"
                f"Жёлтая строка над списком исчезнет, когда у всех SKU будет ЛИЦО+БОК+ДАЛЬШЕ."
                + (f"\n\nЗамечания:\n{err}" if err else ""),
            )
        except Exception as exc:
            messagebox.showerror("Импорт", str(exc))

    def _set_pending_from_path(self, path: Path, *, announce: bool = True):
        self._pending_photo = path
        img = cv2.imread(str(path))
        if img is None:
            # иногда cv2.imread не читает только что записанный jpg — берём из памяти
            if self._pending_bgr is None:
                messagebox.showerror("Фото", "Не удалось открыть файл")
                self._pending_photo = None
                self._last_quality_ok = False
                self._last_quality_msg = ""
                self._last_ocr = ""
                self._update_scan_progress()
                return
            img = self._pending_bgr
        self._pending_bgr = img.copy()
        ok, msg, _issues = self._photo_quality(img)
        self._last_quality_ok = ok
        self._last_quality_msg = msg
        self._show_photo(img)
        self._run_ocr_preview(img)
        self._update_scan_progress()
        if announce:
            self._announce_scan_result()

    def _announce_scan_result(self):
        """После снимка: авто-доскан (если включён) + подсказка следующего ракурса."""
        missing, done, total, refs_n = self._scan_missing_steps()
        q = self._last_quality_msg or "—"
        ocr = self._last_ocr or "не прочитано"
        num, code, tip = self._angle_guide(refs_n)
        self._refresh_angle_hint()
        self._refresh_scan_local_banner()

        can_save = (
            self._pending_bgr is not None
            and self._last_quality_ok
            and bool(self.f_sku.get().strip())
            and bool(self.f_name.get().strip())
        )
        auto = bool(self._auto_enroll.get()) if hasattr(self, "_auto_enroll") else True

        if can_save and self.catalog and not self.catalog.get_item(self.f_sku.get().strip()):
            self._save_item()

        if done >= total and refs_n >= MIN_SCAN_REFS:
            messagebox.showinfo(
                "Скан продукции",
                f"✓ Скан полный ({refs_n}/{MIN_SCAN_REFS}).\nМожно ЭКСПОРТ ZIP.",
            )
            self._status_var.set(f"✓ Скан полный · {refs_n}/{MIN_SCAN_REFS}")
            return

        if can_save and refs_n < MIN_SCAN_REFS and auto and self._last_quality_ok:
            self._status_var.set(f"Авто-доскан · ракурс {refs_n + 1}/{MIN_SCAN_REFS} ({code})")
            self._enroll_photo(quiet_prompt=True, auto=True)
            return

        lines = [
            "Проверка кадра:",
            f"• Качество: {q}",
            f"• OCR: {ocr[:80]}",
            f"• Эталоны: {refs_n}/{MIN_SCAN_REFS}",
            f"• Следующий ракурс: {num}/{MIN_SCAN_REFS} — {code}",
            tip,
            "",
            "⚠ СКАН ЕЩЁ НЕ ДО КОНЦА",
        ]
        for m in missing[:5]:
            lines.append(f"• {m}")
        self._status_var.set(f"Скан неполный · {refs_n}/{MIN_SCAN_REFS} · дальше: {code}")
        body = "\n".join(lines)
        if can_save and refs_n < MIN_SCAN_REFS:
            body += "\n\nСохранить этот ракурс сейчас?"
            if messagebox.askyesno("Скан неполный", body):
                self._enroll_photo(quiet_prompt=True)
            return
        messagebox.showwarning("Скан неполный", body)


    def _run_ocr_preview(self, img: np.ndarray):
        try:
            from src.vision.menu_matcher import MenuMatcher

            text = MenuMatcher().read_text(img) or ""
            self._last_ocr = text.strip()
            self.ocr_preview.set(f"OCR: {self._last_ocr or '—'}")
            if self._last_ocr and not self.f_aliases.get().strip():
                self.f_aliases.set(", ".join(self._last_ocr.split()[:8]))
        except Exception as exc:
            self._last_ocr = ""
            self.ocr_preview.set(f"OCR ошибка: {exc}")
        self._update_scan_progress()

    def _phone_hint_text(self) -> str:
        sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
        num, code, tip = self._angle_guide()
        name = self.f_name.get().strip() if hasattr(self, "f_name") else ""
        head = f"SKU: {sku or '—'} {name}".strip()
        return f"{head}\nСейчас нужно: {code}\n{tip}"

    def _open_airdrop_inbox(self):
        from src.desktop.phone_scan import open_inbox_in_finder, DESKTOP_INBOX

        open_inbox_in_finder()
        self._status_var.set(f"AirDrop → {DESKTOP_INBOX}")

    def _open_camera_capture(self):
        """Открыть лучшую доступную камеру (Continuity / Mac)."""
        backends = []
        if hasattr(cv2, "CAP_AVFOUNDATION"):
            backends.append(cv2.CAP_AVFOUNDATION)
        backends.append(cv2.CAP_ANY)
        for backend in backends:
            for idx in range(0, 6):
                c = cv2.VideoCapture(idx, backend)
                if not c.isOpened():
                    try:
                        c.release()
                    except Exception:
                        pass
                    continue
                ok, frame = c.read()
                if ok and frame is not None and getattr(frame, "size", 0) > 0:
                    return c
                try:
                    c.release()
                except Exception:
                    pass
        return None

    def _start_continuity_camera(self):
        """Отдельное растягиваемое окно live-скана с подсказками."""
        sku = self.f_sku.get().strip()
        if not sku:
            messagebox.showwarning("Скан", "Сначала выберите / сохраните SKU слева.")
            return
        if self.catalog and not self.catalog.get_item(sku):
            self._save_item()

        # закрыть встроенный превью-поток — камера одна
        self._stop_webcam()
        if self._scan_studio is not None:
            try:
                self._scan_studio.close()
            except Exception:
                pass
            self._scan_studio = None

        from src.desktop.scan_studio import ScanStudio

        def get_angles(s: str):
            return self._sku_angle_state(s)

        def get_refs(s: str):
            if not self.catalog or not s:
                return []
            return self.catalog.list_refs(s)

        def _studio_closed():
            self._scan_studio = None
            self._status_var.set("Live-студия закрыта")

        self._scan_studio = ScanStudio(
            self.root,
            get_sku=lambda: self.f_sku.get().strip(),
            get_refs=get_refs,
            get_angles=get_angles,
            on_capture=self._studio_capture,
            open_camera=self._open_camera_capture,
            on_close=_studio_closed,
        )
        self._status_var.set("Live-студия скана открыта — раздвиньте окно")

    def _studio_capture(self, frame: np.ndarray, path: Path):
        """Кадр из Scan Studio → тот же pipeline, что снимок/iPhone."""
        self._pending_bgr = frame.copy()
        self._pending_photo = path
        self._show_photo(frame)
        ok, msg, _ = self._photo_quality(frame)
        self._last_quality_ok = ok
        self._last_quality_msg = msg
        sku = self.f_sku.get().strip()
        _have, nxt, _ = self._sku_angle_state(sku)
        try:
            coach = self._get_scan_coach()
            self._last_coach_verdict = coach.evaluate_capture(
                frame,
                target_angle=nxt or "front",
                existing_refs=self.catalog.list_refs(sku) if self.catalog and sku else [],
            )
        except Exception:
            self._last_coach_verdict = None
        self._run_ocr_preview(frame)
        self._update_scan_progress()
        self._refresh_angle_hint()
        self._status_var.set("Кадр из Live-студии")
        self._announce_scan_result()
        auto = bool(self._auto_enroll.get()) if hasattr(self, "_auto_enroll") else True
        enrolled = False
        if auto and self._last_quality_ok and (
            self._last_coach_verdict is None or self._last_coach_verdict.ok
        ):
            self._enroll_photo(quiet_prompt=True, auto=True)
            enrolled = True
        if self._scan_studio is not None:
            try:
                num, code, tip = self._angle_guide()
                if enrolled:
                    self._scan_studio.notify_enrolled(code, tip)
                else:
                    self._scan_studio.notify_pending(
                        self._last_coach_verdict.message
                        if self._last_coach_verdict
                        else self._last_quality_msg
                    )
            except Exception:
                pass

    def _toggle_phone_scan(self):
        if (self._phone_server and self._phone_server.is_running()) or self._inbox_watcher:
            self._stop_phone_scan()
            return
        sku = self.f_sku.get().strip()
        if not sku:
            messagebox.showwarning("iPhone", "Сначала укажите / выберите SKU слева.")
            return
        if self.catalog and not self.catalog.get_item(sku):
            self._save_item()
        try:
            from src.desktop.phone_scan import (
                PhoneScanServer,
                InboxWatcher,
                make_qr_image,
                open_inbox_in_finder,
                DESKTOP_INBOX,
            )

            self._inbox_watcher = InboxWatcher(self._on_phone_image)
            self._inbox_watcher.start()
            open_inbox_in_finder()

            url = ""
            urls_txt = "   (HTTP опционален — AirDrop главный)"
            try:
                if self._phone_server is None:
                    self._phone_server = PhoneScanServer(port=8765)
                url = self._phone_server.start(self._on_phone_image, hint=self._phone_hint_text())
                urls_txt = "\n".join(f"   {u}" for u in self._phone_server.urls()[:3])
            except Exception:
                pass

            self._phone_url_var.set(
                f"★ AirDrop (работает без сайта):\n"
                f"1. Фото на iPhone\n"
                f"2. Поделиться → AirDrop → Mac\n"
                f"3. В папку:\n   {DESKTOP_INBOX}\n"
                f"4. Desktop заберёт сам (лучше JPG)\n\n"
                f"Safari (часто не открывается из‑за роутера):\n{urls_txt}"
            )
            if url:
                qr = make_qr_image(url, size=160)
                self._phone_qr_imgtk = ImageTk.PhotoImage(qr)
                self._phone_qr_label.configure(image=self._phone_qr_imgtk)
            try:
                self._phone_box.pack_forget()
            except Exception:
                pass
            self._phone_box.pack(fill="x", padx=14, pady=(0, 8))
            self._status_var.set(f"Ждём AirDrop → {DESKTOP_INBOX.name}")
            messagebox.showinfo(
                "Скан с iPhone",
                "Не используйте Safari, если не открывается.\n\n"
                "AirDrop → папка CyberX_Inbox (уже открыта):\n"
                "фото с iPhone туда → Desktop подхватит.\n\n"
                "Или «iPhone как камера» для live-точек.\n\n"
                "iPhone: Настройки → Камера → Форматы →\n"
                "«Наиболее совместимый» (JPG).",
            )
        except Exception as exc:
            messagebox.showerror("iPhone", str(exc))

    def _stop_phone_scan(self):
        if self._phone_server:
            self._phone_server.stop()
        if self._inbox_watcher:
            self._inbox_watcher.stop()
            self._inbox_watcher = None
        try:
            self._phone_box.pack_forget()
        except Exception:
            pass
        self._phone_url_var.set("")
        self._status_var.set("iPhone скан выкл.")

    def _copy_phone_url(self):
        if not self._phone_server or not self._phone_server.is_running():
            return
        url = self._phone_server.url
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self._status_var.set(f"Скопировано: {url}")
        except Exception:
            pass

    def _on_phone_image(self, path: Path):
        """Вызов из потока HTTP — перекидываем в UI-thread."""
        self.root.after(0, lambda p=path: self._ingest_phone_photo(p))

    def _ingest_phone_photo(self, path: Path):
        path = Path(path)
        if not path.exists():
            return
        img = cv2.imread(str(path))
        if img is None and path.suffix.lower() in {".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp"}:
            try:
                try:
                    from pillow_heif import register_heif_opener

                    register_heif_opener()
                except Exception:
                    pass
                from PIL import Image
                import numpy as np

                pil = Image.open(path).convert("RGB")
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
            except Exception:
                img = None
        if img is None:
            messagebox.showwarning(
                "iPhone",
                f"Не удалось открыть фото:\n{path}\n\n"
                f"Поставьте на iPhone: Камера → Форматы → «Наиболее совместимый» (JPG).",
            )
            return
        self._pending_bgr = img
        self._pending_photo = path
        self._show_photo(img)
        ok, msg, _ = self._photo_quality(img)
        self._last_quality_ok = ok
        self._last_quality_msg = msg
        # coach evaluate for UI
        sku = self.f_sku.get().strip()
        have, nxt, _ = self._sku_angle_state(sku)
        try:
            coach = self._get_scan_coach()
            self._last_coach_verdict = coach.evaluate_capture(
                img,
                target_angle=nxt or "front",
                existing_refs=self.catalog.list_refs(sku) if self.catalog and sku else [],
            )
            if self._phone_server:
                if self._last_coach_verdict.ok:
                    self._phone_server.set_hint(
                        self._phone_hint_text() + f"\nПоследний кадр: OK ({self._last_coach_verdict.message})"
                    )
                else:
                    self._phone_server.set_hint(
                        self._phone_hint_text() + f"\n⚠ {self._last_coach_verdict.message}"
                    )
        except Exception:
            pass
        self._run_ocr_preview(img)
        self._update_scan_progress()
        self._status_var.set(f"Фото с iPhone: {path.name}")
        self._announce_scan_result()
        # авто-сохранение ракурса если включено
        auto = bool(self._auto_enroll.get()) if hasattr(self, "_auto_enroll") else True
        if auto and self._last_quality_ok and (
            self._last_coach_verdict is None or self._last_coach_verdict.ok
        ):
            self._enroll_photo(quiet_prompt=True, auto=True)
            if self._phone_server:
                self._phone_server.set_hint(self._phone_hint_text())

    def _toggle_webcam(self):
        if self._webcam is not None:
            self._stop_webcam()
            return
        # пробуем 0..3 — Continuity Camera часто не index 0
        cap = None
        for idx in range(0, 4):
            c = cv2.VideoCapture(idx)
            if c.isOpened():
                ok, frame = c.read()
                if ok and frame is not None:
                    cap = c
                    self._status_var.set(f"Камера Mac index={idx} (Continuity iPhone — если выбран в системе)")
                    break
                c.release()
        if cap is None:
            messagebox.showerror(
                "Камера",
                "Камера Mac не найдена.\n\n"
                "Лучше: кнопка «iPhone» (скан в Safari).\n"
                "Или Continuity Camera: iPhone рядом → "
                "Пункт управления Mac → Экранный монитор → iPhone.\n"
                "Или «Файл…» после AirDrop.",
            )
            return
        self._webcam = cap
        self._webcam_frame_i = 0
        self._refresh_scan_local_banner(
            live_note="Камера Mac: наведите товар → «СНИМОК Mac». Для телефона — кнопка iPhone."
        )
        self._tick_webcam()

    def _stop_webcam(self):
        if self._webcam_job:
            try:
                self.root.after_cancel(self._webcam_job)
            except Exception:
                pass
            self._webcam_job = None
        if self._webcam is not None:
            self._webcam.release()
            self._webcam = None
        self.webcam_label.configure(
            image="",
            text="Камера Mac выкл.\nДля скана нажмите «iPhone»\nили «Камера Mac» / «Файл…»",
            fg=CX_SUB,
        )
        self._webcam_imgtk = None
        self._update_scan_progress()

    def _preview_box_size(self, box: Optional[tk.Frame], fallback_w: int, fallback_h: int) -> tuple[int, int]:
        try:
            if box is not None:
                box.update_idletasks()
                w = max(int(box.winfo_width()), fallback_w)
                h = max(int(box.winfo_height()), fallback_h)
                return w - 8, h - 8
        except Exception:
            pass
        return fallback_w, fallback_h

    def _tick_webcam(self):
        if self._webcam is None:
            return
        ok, frame = self._webcam.read()
        if ok:
            self._last_webcam_bgr = frame
            self._webcam_frame_i = getattr(self, "_webcam_frame_i", 0) + 1
            sku = self.f_sku.get().strip() if hasattr(self, "f_sku") else ""
            have, nxt, refs_n = self._sku_angle_state(sku) if sku else ([], "front", 0)
            target = nxt or "front"
            # живая подсказка coach ~2 раз/сек
            if self._webcam_frame_i % 12 == 0:
                try:
                    coach = self._get_scan_coach()
                    live_v = coach.evaluate_capture(
                        frame,
                        target_angle=target,
                        existing_refs=self.catalog.list_refs(sku) if self.catalog and sku else [],
                    )
                    self._last_coach_verdict = live_v
                    from src.vision.product_scan_coach import ANGLE_LABELS_RU, ANGLE_TIPS

                    label = ANGLE_LABELS_RU.get(target, target)
                    if live_v.ok:
                        note = f"✓ Live OK — можно СНИМОК ({label}). {live_v.message}"
                    else:
                        tip = ANGLE_TIPS.get(target, "")
                        note = f"⚠ {live_v.message} · нужен {label}. {tip}"
                    self._refresh_scan_local_banner(live_note=note)
                    self._refresh_angle_hint()
                except Exception:
                    q_ok, q_msg, _ = self._photo_quality(frame)
                    note = (
                        f"Камера: {'OK' if q_ok else 'слабо'} ({q_msg}). "
                        f"Нажмите «СНИМОК», чтобы проверить."
                    )
                    self._refresh_scan_local_banner(live_note=note)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            max_w, max_h = self._preview_box_size(getattr(self, "_webcam_box", None), 520, 300)
            scale = min(max_w / w, max_h / h)
            rgb = cv2.resize(
                rgb,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
            )
            # ORB overlay — каждый 2–3 кадр для «прямого эфира»
            if self._webcam_frame_i % 3 == 0 or not hasattr(self, "_webcam_overlay_cache"):
                self._webcam_overlay_cache = self._draw_angle_overlay(rgb.copy(), refs_n)
            disp = getattr(self, "_webcam_overlay_cache", rgb)
            self._webcam_imgtk = ImageTk.PhotoImage(Image.fromarray(disp))
            self.webcam_label.configure(image=self._webcam_imgtk, text="")
        self._webcam_job = self.root.after(40, self._tick_webcam)

    def _capture_webcam(self):
        frame = getattr(self, "_last_webcam_bgr", None)
        if frame is None:
            messagebox.showwarning(
                "Снимок",
                "Сначала включите «Камера Mac» или пришлите фото с «iPhone» / «Файл…».",
            )
            return
        self._pending_bgr = frame.copy()
        tmp = Path(tempfile.gettempdir()) / f"cyberx_scan_{int(time.time())}.jpg"
        ok = cv2.imwrite(str(tmp), frame)
        if not ok:
            # всё равно проверяем из памяти
            tmp = Path(tempfile.gettempdir()) / f"cyberx_scan_{int(time.time())}.png"
            cv2.imwrite(str(tmp), frame)
        self._pending_photo = tmp
        self._show_photo(frame)
        ok_q, msg, _ = self._photo_quality(frame)
        self._last_quality_ok = ok_q
        self._last_quality_msg = msg
        self._run_ocr_preview(frame)
        self._update_scan_progress()
        # прокрутить к статусу/превью
        try:
            if getattr(self, "_cat_right_canvas", None):
                self._cat_right_canvas.yview_moveto(0.15)
        except Exception:
            pass
        self._announce_scan_result()

    def _pick_photo(self):
        path = filedialog.askopenfilename(
            title="Фото упаковки",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All", "*.*")],
        )
        if not path:
            return
        self._pending_bgr = None
        self._set_pending_from_path(Path(path), announce=True)

    def _enroll_photo(self, quiet_prompt: bool = False, auto: bool = False):
        if not self.catalog or not self._pending_photo or self._pending_bgr is None:
            messagebox.showwarning(
                "Каталог",
                "Сначала сделайте «СНИМОК + ПРОВЕРКА» (или Файл…) и укажите SKU.",
            )
            return
        sku = self.f_sku.get().strip()
        if not sku:
            messagebox.showwarning("Каталог", "Укажите SKU")
            return

        ok, msg, _issues = self._photo_quality(self._pending_bgr)
        self._last_quality_ok = ok
        self._last_quality_msg = msg

        have, nxt, _ = self._sku_angle_state(sku)
        target_angle = nxt or "front"
        coach_v = None
        try:
            coach = self._get_scan_coach()
            coach_v = coach.evaluate_capture(
                self._pending_bgr,
                target_angle=target_angle,
                existing_refs=self.catalog.list_refs(sku) if self.catalog.get_item(sku) else [],
            )
            self._last_coach_verdict = coach_v
            if not coach_v.ok:
                # авто/тихо: не сохраняем дубликаты и плохие кадры
                if auto or quiet_prompt:
                    self._status_var.set(f"Scan Coach: {coach_v.message}")
                    self._refresh_scan_local_banner(live_note=coach_v.message)
                    self._update_scan_progress()
                    return
                # ручной override только без duplicate/no_embed
                hard = set(coach_v.issues or []) & {"duplicate_angle", "slot_filled", "no_embed", "embed_fail"}
                if hard:
                    messagebox.showwarning("Scan Coach", coach_v.message)
                    self._update_scan_progress()
                    return
                cont = messagebox.askyesno(
                    "Scan Coach",
                    f"{coach_v.message}\n\nВсё равно сохранить как {target_angle}?",
                )
                if not cont:
                    self._update_scan_progress()
                    return
            else:
                target_angle = coach_v.angle or target_angle
                ok = True
                self._last_quality_ok = True
                self._last_quality_msg = coach_v.message
        except Exception as exc:
            logger.debug("coach evaluate: %s", exc)

        if not ok and not quiet_prompt and not auto:
            cont = messagebox.askyesno(
                "Скан неполный",
                f"Качество фото слабое:\n{msg}\n\nВсё равно сохранить этот ракурс?\n"
                f"(для полного скана нужно {MIN_SCAN_REFS} разных ракурса)",
            )
            if not cont:
                self._update_scan_progress()
                return
        if not ok and auto:
            num, code, tip = self._angle_guide()
            self._status_var.set(f"Авто-доскан: качество слабо — {code}")
            self._refresh_scan_local_banner(
                live_note=f"Качество слабое — ракурс НЕ сохранён. Нужен: {code}. {tip}"
            )
            self._update_scan_progress()
            return

        if not self.catalog.get_item(sku):
            self._save_item()
            if not self.catalog.get_item(sku):
                return

        path = Path(self._pending_photo)
        if not path.exists() and self._pending_bgr is not None:
            path = Path(tempfile.gettempdir()) / f"cyberx_enroll_{int(time.time())}.jpg"
            cv2.imwrite(str(path), self._pending_bgr)
            self._pending_photo = path

        ocr = self._last_ocr or self.ocr_preview.get().replace("OCR:", "").strip()
        if ocr == "—":
            ocr = ""
        dest = self.catalog.add_reference_image(
            sku, self._pending_photo, ocr_text=ocr, angle=target_angle
        )
        if ocr and not self.f_aliases.get().strip():
            self.f_aliases.set(", ".join(ocr.split()[:8]))
            self._save_item()

        angles_n = len(self.catalog.angles_present(sku))
        remaining = len(self.catalog.missing_angles(sku))
        num, code, tip = self._angle_guide()
        if self.platform:
            self.platform.reload_catalog()
        self._pending_photo = None
        self._pending_bgr = None
        self._last_quality_ok = False
        self._last_quality_msg = ""
        self._last_ocr = ""
        self._last_coach_verdict = None
        self.photo_preview.configure(
            image="",
            text=(
                f"Ракурс сохранён ({angles_n}/{MIN_SCAN_REFS}).\nДальше: {code}\n{tip}"
                if remaining
                else "✓ Скан готов — можно ЭКСПОРТ ZIP"
            ),
            fg=CX_SUB,
        )
        self.ocr_preview.set("OCR: —")
        self._refresh_catalog()
        self._update_scan_progress()
        self._refresh_angle_hint()

        if remaining > 0:
            self._status_var.set(f"Скан неполный · {sku}: {angles_n}/{MIN_SCAN_REFS} · дальше {code}")
            self._refresh_scan_local_banner(
                live_note=f"{'Автосохранено' if auto else 'Сохранено'}: {code} ещё нужен. {tip}"
            )
            if not auto:
                messagebox.showinfo(
                    "Скан ещё не полный",
                    f"Эталон сохранён:\n{dest}\n\n"
                    f"Прогресс ракурсов: {angles_n}/{MIN_SCAN_REFS}.\n"
                    f"Следующий: {code}\n{tip}\n\n"
                    f"Снова: наведите товар → СНИМОК + ПРОВЕРКА.",
                )
        else:
            self._status_var.set(f"✓ Скан завершён · {sku}")
            messagebox.showinfo(
                "Скан завершён",
                f"Товар {sku} полностью отсканирован ({angles_n}/{MIN_SCAN_REFS}: ЛИЦО+БОК+ДАЛЬШЕ).\n\n"
                f"Последний эталон: {dest}\n\n"
                f"Дальше: ЭКСПОРТ ZIP → Telegram → на ПК клуба ИМПОРТ ZIP → Синхр. меню.",
            )

    def _show_photo(self, bgr: np.ndarray):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        max_w, max_h = self._preview_box_size(getattr(self, "_photo_box", None), 520, 340)
        scale = min(max_w / w, max_h / h)
        rgb = cv2.resize(
            rgb,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        )
        self._photo_imgtk = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.photo_preview.configure(image=self._photo_imgtk, text="")

    def _refresh_alerts(self):
        if not self.store:
            return
        for i in self.alert_tree.get_children():
            self.alert_tree.delete(i)
        for a in self.store.recent_alerts(80):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a["ts"]))
            self.alert_tree.insert(
                "",
                "end",
                values=(ts, a["module"], a.get("camera") or "", a["severity"], a["title"]),
            )

    def start_monitor(self):
        if self._monitor_running:
            self._status_var.set("Мониторинг уже запущен")
            return
        try:
            from src.platform.orchestrator import Platform

            self.platform = Platform()
            self.platform.reload_catalog()
        except Exception as exc:
            messagebox.showerror("Старт", f"Не удалось запустить (проверьте .env и камеры):\n{exc}")
            return

        self._stop.clear()
        self._monitor_running = True
        self._cam_got_frame = False
        self._monitor_started_at = time.time()
        self.cam_label.configure(
            image="",
            fg=CX_SUB,
            text=(
                "МОНИТОРИНГ ЗАПУЩЕН\n\n"
                "Подключаемся к камерам…\n"
                "Первые кадры появятся через несколько секунд"
            ),
        )

        def bot_thread():
            try:
                from src.bot.admin_bot import AdminBot

                asyncio.set_event_loop(asyncio.new_event_loop())
                AdminBot().run_polling()
            except Exception:
                logger.exception("Telegram bot stopped")

        self._bot_thread = threading.Thread(target=bot_thread, name="admin-bot", daemon=True)
        self._bot_thread.start()

        def monitor():
            try:
                self.platform.run_loop(
                    stop_flag=self._stop,
                    show_opencv_dashboard=False,
                    on_cycle=self._on_cycle,
                    announce=True,
                )
            except Exception:
                logger.exception("monitor failed")
                self.root.after(
                    0,
                    lambda: self.cam_label.configure(
                        image="",
                        text=(
                            "Ошибка мониторинга\n"
                            "Смотрите лог / .env / сеть камер\n"
                            "Нажмите СТАРТ ещё раз"
                        ),
                    ),
                )
            finally:
                self._monitor_running = False

        self._monitor_thread = threading.Thread(target=monitor, name="vision", daemon=True)
        self._monitor_thread.start()
        self._status_var.set("МОНИТОРИНГ АКТИВЕН · Telegram + камеры")
        self._show_tab("cam")

    def stop_monitor(self):
        self._stop.set()
        if self.platform:
            self.platform.set_paused(False)
        self._status_var.set("Остановка…")
        self._cam_got_frame = False
        self.root.after(800, self._reset_cam_waiting)

    def _reset_cam_waiting(self):
        if self._monitor_running:
            return
        self.cam_label.configure(
            image="",
            text=(
                "Мониторинг остановлен\n"
                "Нажмите «СТАРТ МОНИТОРИНГА», чтобы снова увидеть камеры"
            ),
        )
        self._dash_imgtk = None

    def toggle_pause(self):
        if not self.platform:
            return
        self.platform.set_paused(not self.platform._paused)
        self._status_var.set("ПАУЗА" if self.platform._paused else "МОНИТОРИНГ АКТИВЕН")

    def _on_cycle(self, active_id: str, dash):
        self._last_dash = dash
        self._last_active = active_id

    def _poll_ui(self):
        dash = getattr(self, "_last_dash", None)
        if dash is not None:
            self._show_dash(dash)
            self._last_dash = None
            self._cam_got_frame = True
        elif self._monitor_running and not self._cam_got_frame:
            elapsed = int(time.time() - (self._monitor_started_at or time.time()))
            dots = "." * (1 + (elapsed % 3))
            online = ""
            if self.platform and self.platform.tiles:
                ok_n = sum(1 for t in self.platform.tiles.values() if t.ok)
                online = f"\nКамер в системе: {ok_n}/{len(self.platform.tiles)}"
            self.cam_label.configure(
                image="",
                text=(
                    f"МОНИТОРИНГ АКТИВЕН\n\nПодключение к камерам{dots}\n"
                    f"Прошло {elapsed} с{online}\n"
                    f"Если долго пусто — проверьте сеть NVR и .env"
                ),
            )
        self._refresh_own_status()
        if int(time.time()) % 5 == 0:
            self._refresh_alerts()
        self.root.after(400, self._poll_ui)

    def _show_dash(self, bgr: np.ndarray):
        if bgr is None:
            return
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        # растягиваем мозаику на всю область вкладки «Камеры»
        try:
            self.cam_label.update_idletasks()
            max_w = max(int(self.cam_label.winfo_width()), 960)
            max_h = max(int(self.cam_label.winfo_height()), 560)
        except Exception:
            max_w, max_h = 1600, 900
        # небольшой запас от краёв
        max_w = max(640, max_w - 8)
        max_h = max(400, max_h - 8)
        scale = min(max_w / w, max_h / h)
        if abs(scale - 1.0) > 0.02:
            rgb = cv2.resize(
                rgb,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
            )
        self._dash_imgtk = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.cam_label.configure(image=self._dash_imgtk, text="")

    def _refresh_own_status(self):
        if not self.platform:
            return
        for i in self.own_tree.get_children():
            self.own_tree.delete(i)
        for _cid, tile in self.platform.tiles.items():
            queue = ""
            if tile.is_reception:
                queue = f"{tile.reception_waited:.0f}/{tile.reception_need:.0f}s ({tile.reception_count})"
            own = sum(1 for f in tile.findings if f.kind == "own_products")
            self.own_tree.insert(
                "",
                "end",
                values=(tile.name, tile.status, queue, own if own else "—"),
            )

    def on_close(self):
        self._stop_webcam()
        if self._scan_studio is not None:
            try:
                self._scan_studio.close()
            except Exception:
                pass
            self._scan_studio = None
        try:
            self._stop_phone_scan()
        except Exception:
            pass
        self._stop.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    DesktopApp().run()


if __name__ == "__main__":
    main()
