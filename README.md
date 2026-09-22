# BotNotification — камеры, детекция, Telegram

Компьютерное зрение для клуба: беру RTSP с Hikvision (или кадр с экрана iVMS), сканирую зону YOLO и шлю в Telegram уже готовый сигнал — человек у кассы, своя еда, заполненность витрины, инциденты.

Не «просто стрим», а пайплайн **камера → анализ → действие**.

```text
Hikvision RTSP / iVMS
        │
        ▼
   YOLOv8 + ROI
        │
        ▼
  присутствие N секунд / каталог / OCR
        │
        ▼
  Telegram + desktop для сотрудника
```

## Возможности

- Люди в зоне админ-кассы → алерт в Telegram со скрином
- Мозаика камер, чистота, холодильник/витрина
- Каталог бара: эталоны, OCR, экспорт/импорт ZIP без сканера
- Desktop для сотрудника (камеры, контроль, алерты)

Подробнее модули платформы — в [PLATFORM.md](PLATFORM.md).

## Быстрый старт (зона кассы)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Заполни `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CAMERA_*`.

```bash
python scripts/setup_roi.py
python main.py
```

Веса `.pt` в git не входят — YOLO подтянет `yolov8n.pt` при первом запуске или положи файл рядом.

## Стек

Python · OpenCV · Ultralytics YOLO · Telegram Bot API · Tk desktop · YAML-конфиг
