# Доп. модели для детектора «со своим» + Product Scan Coach

Готовой нейросети под весь бар CyberX нет. Стек:
**YOLO → OpenCLIP gallery + OCR** (как Pic2Product) + **Scan Coach** (ORB-точки + слоты ЛИЦО/БОК/ДАЛЬШЕ).

## Скачать / прогреть

```bash
.venv_mac/bin/python scripts/download_embedding_model.py
.venv_mac/bin/python scripts/sanitize_aliases.py --from-yaml
.venv_mac/bin/python scripts/build_embedding_gallery.py
.venv_mac/bin/python scripts/smoke_own_detector.py
```

Опционально быстрее matching: `pip install faiss-cpu`

| Модуль | Зачем |
|--------|--------|
| `yolov8s.pt` | Люди + bottle/cup/food |
| `retail_product.pt` | Упаковки `product` |
| OpenCLIP ViT-B-32 | Few-shot gallery |
| `product_scan_coach.py` | Точки + проверка ракурсов при скане |

Полный ops: [`docs/OPS_OWN_DETECTOR.md`](../docs/OPS_OWN_DETECTOR.md)
