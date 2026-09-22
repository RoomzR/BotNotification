# Операционный чеклист: детектор «со своим» + Product Scan Coach

Цель: **≥90% recall** на `data/eval_own` при полном каталоге (ЛИЦО+БОК+ДАЛЬШЕ на каждый SKU).
Без полного скана: soft-алерты; Telegram для soft **выключен** (`soft_telegram: false`).

Перед сменой — [`docs/RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## 1. Скан на ноуте (Scan Coach)

1. Desktop → КАТАЛОГ → SKU
2. Веб-камера: **точки ORB** + прогресс `ЛИЦО | БОК | ДАЛЬШЕ`
3. Coach блокирует повтор того же ракурса («Это снова ЛИЦО…»)
4. Нужны все 3 слота → в таблице `✓ 3/3` (не просто 3 фото)
5. Все SKU → **ЭКСПОРТ ZIP**

## 2. ПК клуба

1. **ИМПОРТ ZIP** (галерея пересобирается автоматически)
2. Жёлтая строка над списком должна исчезнуть (coverage ≥ 90%)

## 3. Скрипты

```bash
.venv_mac/bin/python scripts/sanitize_aliases.py --from-yaml
.venv_mac/bin/python scripts/download_embedding_model.py
.venv_mac/bin/python scripts/build_embedding_gallery.py
.venv_mac/bin/python scripts/smoke_own_detector.py
.venv_mac/bin/python scripts/eval_own_detector.py
```

## 4. Камеры / алерты

- Main stream для own_products: `prefer_mainstream_for_own: true`
- Confirm требует **непрерывного** присутствия (пропадание сбрасывает таймер)
- Soft → только UI, пока не включите `soft_telegram: true`

## Стек

Pic2Product: YOLO → CLIP gallery + OCR.  
Scan Coach: ORB mesh + angle slots.
