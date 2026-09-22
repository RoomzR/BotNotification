"""Адаптер Langame Admin / локальный stub."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from src.storage.db import Store

logger = logging.getLogger(__name__)


class LangameClient:
    """
    mode=stub — всё в локальной БД (можно проверять логику уже сейчас).
    mode=http — вызовы внешнего API, когда дадите URL/токен Admin API.
    """

    def __init__(self, mode: str, base_url: str, api_token: str, store: Store, pc_agent_url: str = ""):
        self.mode = (mode or "stub").lower()
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.store = store
        self.pc_agent_url = pc_agent_url.rstrip("/")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    def import_invoice(self, lines: list[dict], note: str = "", actor: str = "admin") -> str:
        """lines: [{sku,name,qty,price}]"""
        for line in lines:
            self.store.upsert_product(
                sku=str(line.get("sku") or line["name"]),
                name=str(line["name"]),
                qty_delta=int(line["qty"]),
                price=float(line.get("price") or 0),
                note=f"Накладная: {note}",
                actor=actor,
            )
        if self.mode == "http" and self.base_url:
            try:
                httpx.post(
                    f"{self.base_url}/inventory/invoice",
                    headers=self._headers(),
                    json={"lines": lines, "note": note},
                    timeout=15,
                )
            except Exception as exc:
                logger.warning("Langame invoice sync failed: %s", exc)
                return f"stub_ok_http_fail:{exc}"
        return f"imported:{len(lines)}"

    def stock_adjust(self, sku: str, name: str, qty_delta: int, note: str, actor: str) -> str:
        self.store.upsert_product(sku, name, qty_delta, note=note, actor=actor)
        if self.mode == "http" and self.base_url:
            try:
                httpx.post(
                    f"{self.base_url}/inventory/adjust",
                    headers=self._headers(),
                    json={"sku": sku, "name": name, "qty": qty_delta, "note": note},
                    timeout=15,
                )
            except Exception as exc:
                logger.warning("Langame adjust failed: %s", exc)
        return "ok"

    def accrue_bonus(self, account: str, amount: float, note: str, auto_approve: bool = False) -> int:
        status = "approved" if auto_approve else "pending"
        bid = self.store.add_bonus(account, amount, "bonus", note, status=status)
        if status == "approved" and self.mode == "http" and self.base_url:
            try:
                httpx.post(
                    f"{self.base_url}/billing/bonus",
                    headers=self._headers(),
                    json={"account": account, "amount": amount, "note": note},
                    timeout=15,
                )
            except Exception as exc:
                logger.warning("Langame bonus failed: %s", exc)
        return bid

    def register_guest_op(self, op: str, guest: str, detail: str) -> None:
        self.store.add_guest_op(op, guest, detail)
        if self.mode == "http" and self.base_url:
            try:
                httpx.post(
                    f"{self.base_url}/guests/ops",
                    headers=self._headers(),
                    json={"op": op, "guest": guest, "detail": detail},
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("Langame guest op failed: %s", exc)

    def pc_action(self, pc: str, action: str) -> dict[str, Any]:
        """action: tech_mode | unlock | block | reboot"""
        status = "stub_logged"
        detail = "Сохранено локально. Подключите pc_agent_url для реальной блокировки."
        if self.pc_agent_url:
            try:
                r = httpx.post(
                    f"{self.pc_agent_url}/pc/{pc}/{action}",
                    headers=self._headers(),
                    timeout=10,
                )
                status = f"http_{r.status_code}"
                detail = r.text[:300]
            except Exception as exc:
                status = "http_error"
                detail = str(exc)
        elif self.mode == "http" and self.base_url:
            try:
                r = httpx.post(
                    f"{self.base_url}/pcs/{pc}/{action}",
                    headers=self._headers(),
                    timeout=10,
                )
                status = f"http_{r.status_code}"
                detail = r.text[:300]
            except Exception as exc:
                status = "http_error"
                detail = str(exc)
        self.store.add_pc_action(pc, action, status, detail)
        return {"pc": pc, "action": action, "status": status, "detail": detail}
