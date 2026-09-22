# CyberX — чеклист перед официальным запуском

## Готово к работе, если все пункты ✓

### A. Окружение
- [ ] `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CAMERA_*`
- [ ] `.venv` / `.venv_mac` установлен, зависимости из `requirements.txt`
- [ ] `scripts/download_models.py` + `scripts/download_embedding_model.py` без ошибок
- [ ] `scripts/smoke_own_detector.py` → `ALL SMOKE OK`

### B. Каталог бара (критично)
- [ ] В Desktop **все** SKU: `✓ 3/3` (ЛИЦО + БОК + ДАЛЬШЕ), жёлтой строки «детектор слаб» нет
- [ ] Coverage ≥ 90% (`coverage_stats` / баннер каталога)
- [ ] `sanitize_aliases.py --from-yaml` прогнан
- [ ] `build_embedding_gallery.py` → vectors > 0
- [ ] ZIP экспорт с ноута импортирован на ПК клуба + Синхр. меню

### C. Камеры
- [ ] Мониторинг стартует, мозаика живая
- [ ] На own_products камерах видны метки `КЛУБ:` / `СВОЁ:` / `??:`
- [ ] `prefer_mainstream_for_own: true` в `platform.yaml`
- [ ] Проверка: чужой пакет у гостя → soft/hard алерт в UI (Telegram soft выкл по умолчанию)

### D. Пользовательская логика (сотрудник)
- [ ] Scan Coach не даёт сохранить тот же ракурс дважды
- [ ] Авто-доскан не спамит модалками (статус внизу)
- [ ] Подсказка показывает что делать: «Нужен БОК…»
- [ ] Импорт ZIP пересобирает галерею
- [ ] Справка («Как работать») совпадает с кнопками UI

### E. Eval (цель ≥90%)
- [ ] ≥20 фото в `data/eval_own/club` и `own`
- [ ] `eval_own_detector.py` recall ≥ 0.9, exit 0

### F. Политика алертов
- [ ] `soft_telegram: false` пока каталог неполный (уже default)
- [ ] После coverage ≥ 0.9 можно включить `soft_telegram: true` осознанно
- [ ] Confirm: алерт только при **непрерывном** присутствии ~12с (hard) / ~25с (soft)

## Быстрый smoke перед сменой

```bash
.venv_mac/bin/python scripts/smoke_own_detector.py
.venv_mac/bin/python scripts/build_embedding_gallery.py
```

Документы: `docs/OPS_OWN_DETECTOR.md`, `models/README.md`.
