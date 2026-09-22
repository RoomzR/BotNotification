"""Telegram-команды администратора CyberX Control."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.langame.client import LangameClient
from src.services.ops import DeviceService, TaskService, TemperatureService
from src.storage.db import Store

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("cyberx.bot")


HELP = """
CyberX Control — команды

🎥 Камеры / правила
/rules — правила клуба
/alerts — последние алерты

🧊 Склад / накладные
/invoice sku|name|qty|price ; ... — принять накладную
  пример: /invoice energy|Adrenaline|24|3.5 ; chips|Lays|12|2
/stock — остатки
/adjust sku|name|qty|note — списание(+qty)/начисление
  пример: /adjust energy|Adrenaline|-2|продажа

💰 Бонусы
/bonus account|amount|note — заявка на начисление
/approve <id> — подтвердить бонус (собственник)

🖥 ПК
/pc <имя> tech|block|unlock|reboot

👥 Гости
/guest op|guest|detail — лог операции
/guests — последние операции

🌡 Температура
/temp <зона> <C>
/temps

🎮 Устройства
/phone <host> — ping рабочего телефона
/pad <место> ok|bad [note]

📋 Задачи
/task title|detail|assignee|hours
/tasks
/done <id>

Старый монитор только кассы: run.bat
Платформа всего клуба: run_platform.bat
"""


class AdminBot:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        with open(ROOT / "platform.yaml", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.store = Store()
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
        self.allowed = {os.getenv("TELEGRAM_CHAT_ID", "").strip()}

    def _auth(self, update: Update) -> bool:
        chat = update.effective_chat
        if not chat:
            return False
        return str(chat.id) in self.allowed

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        await update.message.reply_text(HELP)

    async def rules_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        lines = ["Правила CyberX Gomel (Интернациональная 13):", ""]
        for r in self.cfg.get("rules", []):
            lines.append(f"• {r.get('title')} [{r.get('severity')}]")
            if r.get("text"):
                lines.append(f"  {r['text']}")
        await update.message.reply_text("\n".join(lines))

    async def alerts_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        rows = self.store.recent_alerts(15)
        if not rows:
            await update.message.reply_text("Алертов пока нет.")
            return
        text = "\n\n".join(
            f"#{r['id']} [{r['severity']}] {r['title']}\n{r['camera'] or '-'}: {r['detail'][:160]}"
            for r in rows
        )
        await update.message.reply_text(text[:3900])

    async def invoice_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        raw = " ".join(context.args)
        if not raw:
            await update.message.reply_text("Формат: /invoice sku|name|qty|price ; ...")
            return
        lines = []
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            bits = [x.strip() for x in part.split("|")]
            if len(bits) < 3:
                continue
            lines.append(
                {
                    "sku": bits[0],
                    "name": bits[1],
                    "qty": int(float(bits[2])),
                    "price": float(bits[3]) if len(bits) > 3 else 0,
                }
            )
        if not lines:
            await update.message.reply_text("Не разобрал строки накладной.")
            return
        res = self.langame.import_invoice(lines, note="telegram", actor=str(update.effective_user.id))
        await update.message.reply_text(f"Накладная принята ({res}). Позиций: {len(lines)}")

    async def stock_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        rows = self.store.list_products()
        if not rows:
            await update.message.reply_text("Склад пуст. Добавь /invoice")
            return
        text = "\n".join(f"{r['sku']}: {r['name']} — {r['qty']} шт." for r in rows)
        await update.message.reply_text(text[:3900])

    async def adjust_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        raw = " ".join(context.args)
        bits = [x.strip() for x in raw.split("|")]
        if len(bits) < 3:
            await update.message.reply_text("Формат: /adjust sku|name|qty|note")
            return
        qty = int(float(bits[2]))
        note = bits[3] if len(bits) > 3 else ""
        self.langame.stock_adjust(bits[0], bits[1], qty, note, actor=str(update.effective_user.id))
        await update.message.reply_text(f"OK: {bits[1]} {qty:+d}")

    async def bonus_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        raw = " ".join(context.args)
        bits = [x.strip() for x in raw.split("|")]
        if len(bits) < 2:
            await update.message.reply_text("Формат: /bonus account|amount|note")
            return
        amount = float(bits[1])
        note = bits[2] if len(bits) > 2 else ""
        require = bool(self.cfg.get("bonuses", {}).get("require_owner_confirm", True))
        bid = self.langame.accrue_bonus(bits[0], amount, note, auto_approve=not require)
        if require:
            await update.message.reply_text(f"Заявка на бонус #{bid} создана. Подтверди: /approve {bid}")
        else:
            await update.message.reply_text(f"Бонус #{bid} начислен сразу.")

    async def approve_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if not context.args:
            await update.message.reply_text("Формат: /approve <id>")
            return
        bid = int(context.args[0])
        ok = self.store.approve_bonus(bid, by=str(update.effective_user.id))
        await update.message.reply_text("Подтверждено ✅" if ok else "Не найдено / уже подтверждено")

    async def pc_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Формат: /pc <имя> tech|block|unlock|reboot")
            return
        pc, action = context.args[0], context.args[1]
        mapping = {"tech": "tech_mode", "block": "block", "unlock": "unlock", "reboot": "reboot"}
        action = mapping.get(action, action)
        res = self.langame.pc_action(pc, action)
        await update.message.reply_text(f"PC {res['pc']} {res['action']}: {res['status']}\n{res['detail']}")

    async def guest_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        raw = " ".join(context.args)
        bits = [x.strip() for x in raw.split("|")]
        if len(bits) < 2:
            await update.message.reply_text("Формат: /guest op|guest|detail")
            return
        detail = bits[2] if len(bits) > 2 else ""
        self.langame.register_guest_op(bits[0], bits[1], detail)
        await update.message.reply_text("Гостевая операция записана.")

    async def guests_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        rows = self.store.recent_guest_ops(20)
        if not rows:
            await update.message.reply_text("Лог гостей пуст.")
            return
        text = "\n".join(f"#{r['id']} {r['op']} {r['guest']}: {r['detail']}" for r in rows)
        await update.message.reply_text(text[:3900])

    async def temp_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Формат: /temp <зона> <C>")
            return
        zone, celsius = context.args[0], float(context.args[1])
        warn = self.temps.record(zone, celsius, source="telegram")
        msg = f"Записал {zone}: {celsius:.1f}°C"
        if warn:
            msg += "\n" + warn
        await update.message.reply_text(msg)

    async def temps_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        await update.message.reply_text(self.temps.summary())

    async def phone_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if not context.args:
            await update.message.reply_text("Формат: /phone <host_or_ip>")
            return
        host = context.args[0]
        ok = self.devices.ping_host(host)
        await update.message.reply_text("Телефон/хост OK ✅" if ok else "Недоступен ❌")

    async def pad_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Формат: /pad <место> ok|bad [note]")
            return
        seat, state = context.args[0], context.args[1].lower()
        note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        ok = state in ("ok", "1", "good")
        self.devices.mark_gamepad(seat, ok, note)
        await update.message.reply_text(f"Геймпад {seat}: {'OK' if ok else 'ПРОБЛЕМА'}")

    async def task_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        raw = " ".join(context.args)
        bits = [x.strip() for x in raw.split("|")]
        if len(bits) < 1 or not bits[0]:
            await update.message.reply_text("Формат: /task title|detail|assignee|hours")
            return
        title = bits[0]
        detail = bits[1] if len(bits) > 1 else ""
        assignee = bits[2] if len(bits) > 2 else "admin"
        hours = float(bits[3]) if len(bits) > 3 else 24
        tid = self.tasks.create(title, detail, assignee, hours)
        await update.message.reply_text(f"Задача #{tid} создана для {assignee}")

    async def tasks_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        rows = self.store.open_tasks()
        if not rows:
            await update.message.reply_text("Открытых задач нет.")
            return
        text = "\n".join(f"#{r['id']} {r['title']} → {r['assignee']}" for r in rows)
        await update.message.reply_text(text)

    async def done_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._auth(update):
            return
        if not context.args:
            await update.message.reply_text("Формат: /done <id>")
            return
        ok = self.tasks.done(int(context.args[0]))
        await update.message.reply_text("Закрыто ✅" if ok else "Не найдено")

    def build_app(self) -> Application:
        token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler(["help", "start"], self.help_cmd))
        app.add_handler(CommandHandler("rules", self.rules_cmd))
        app.add_handler(CommandHandler("alerts", self.alerts_cmd))
        app.add_handler(CommandHandler("invoice", self.invoice_cmd))
        app.add_handler(CommandHandler("stock", self.stock_cmd))
        app.add_handler(CommandHandler("adjust", self.adjust_cmd))
        app.add_handler(CommandHandler("bonus", self.bonus_cmd))
        app.add_handler(CommandHandler("approve", self.approve_cmd))
        app.add_handler(CommandHandler("pc", self.pc_cmd))
        app.add_handler(CommandHandler("guest", self.guest_cmd))
        app.add_handler(CommandHandler("guests", self.guests_cmd))
        app.add_handler(CommandHandler("temp", self.temp_cmd))
        app.add_handler(CommandHandler("temps", self.temps_cmd))
        app.add_handler(CommandHandler("phone", self.phone_cmd))
        app.add_handler(CommandHandler("pad", self.pad_cmd))
        app.add_handler(CommandHandler("task", self.task_cmd))
        app.add_handler(CommandHandler("tasks", self.tasks_cmd))
        app.add_handler(CommandHandler("done", self.done_cmd))
        return app

    def run_polling(self) -> None:
        app = self.build_app()
        logger.info("Admin bot polling...")
        app.run_polling(drop_pending_updates=True)


def run_bot_in_thread() -> threading.Thread:
    bot = AdminBot()

    def _run():
        bot.run_polling()

    t = threading.Thread(target=_run, name="tg-admin-bot", daemon=True)
    t.start()
    return t
