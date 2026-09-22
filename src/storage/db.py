"""SQLite-хранилище платформы CyberX Control."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "cyberx.db"


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  module TEXT NOT NULL,
                  camera TEXT,
                  severity TEXT,
                  title TEXT,
                  detail TEXT,
                  payload TEXT
                );
                CREATE TABLE IF NOT EXISTS products (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sku TEXT UNIQUE,
                  name TEXT NOT NULL,
                  qty INTEGER NOT NULL DEFAULT 0,
                  price REAL DEFAULT 0,
                  updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS product_ops (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  op TEXT,
                  sku TEXT,
                  name TEXT,
                  qty INTEGER,
                  note TEXT,
                  actor TEXT
                );
                CREATE TABLE IF NOT EXISTS bonuses (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  account TEXT,
                  amount REAL,
                  kind TEXT,
                  note TEXT,
                  status TEXT,
                  approved_by TEXT
                );
                CREATE TABLE IF NOT EXISTS guest_ops (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  op TEXT,
                  guest TEXT,
                  detail TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at REAL,
                  due_at REAL,
                  title TEXT,
                  detail TEXT,
                  assignee TEXT,
                  status TEXT,
                  result TEXT
                );
                CREATE TABLE IF NOT EXISTS temperatures (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  zone TEXT,
                  celsius REAL,
                  source TEXT
                );
                CREATE TABLE IF NOT EXISTS device_checks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  device TEXT,
                  ok INTEGER,
                  detail TEXT
                );
                CREATE TABLE IF NOT EXISTS pc_actions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  pc TEXT,
                  action TEXT,
                  status TEXT,
                  detail TEXT
                );
                CREATE TABLE IF NOT EXISTS staff_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  camera TEXT,
                  present INTEGER,
                  detail TEXT
                );
                """
            )

    def add_alert(
        self,
        module: str,
        title: str,
        detail: str = "",
        camera: str = "",
        severity: str = "info",
        payload: Optional[dict] = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO alerts(ts,module,camera,severity,title,detail,payload) VALUES(?,?,?,?,?,?,?)",
                (
                    time.time(),
                    module,
                    camera,
                    severity,
                    title,
                    detail,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def recent_alerts(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_product(self, sku: str, name: str, qty_delta: int, price: float = 0, note: str = "", actor: str = "system") -> None:
        now = time.time()
        with self._conn() as c:
            row = c.execute("SELECT qty FROM products WHERE sku=?", (sku,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO products(sku,name,qty,price,updated_at) VALUES(?,?,?,?,?)",
                    (sku, name, max(0, qty_delta), price, now),
                )
            else:
                new_qty = max(0, int(row["qty"]) + qty_delta)
                c.execute(
                    "UPDATE products SET name=?, qty=?, price=CASE WHEN ? > 0 THEN ? ELSE price END, updated_at=? WHERE sku=?",
                    (name, new_qty, price, price, now, sku),
                )
            op = "in" if qty_delta >= 0 else "out"
            c.execute(
                "INSERT INTO product_ops(ts,op,sku,name,qty,note,actor) VALUES(?,?,?,?,?,?,?)",
                (now, op, sku, name, qty_delta, note, actor),
            )

    def list_products(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM products ORDER BY name").fetchall()]

    def add_bonus(self, account: str, amount: float, kind: str, note: str, status: str = "pending") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO bonuses(ts,account,amount,kind,note,status,approved_by) VALUES(?,?,?,?,?,?,?)",
                (time.time(), account, amount, kind, note, status, ""),
            )
            return int(cur.lastrowid)

    def approve_bonus(self, bonus_id: int, by: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE bonuses SET status='approved', approved_by=? WHERE id=? AND status='pending'",
                (by, bonus_id),
            )
            return cur.rowcount > 0

    def add_guest_op(self, op: str, guest: str, detail: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO guest_ops(ts,op,guest,detail) VALUES(?,?,?,?)",
                (time.time(), op, guest, detail),
            )

    def recent_guest_ops(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM guest_ops ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def add_task(self, title: str, detail: str, assignee: str, due_hours: float = 24) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks(created_at,due_at,title,detail,assignee,status,result) VALUES(?,?,?,?,?,?,?)",
                (now, now + due_hours * 3600, title, detail, assignee, "open", ""),
            )
            return int(cur.lastrowid)

    def complete_task(self, task_id: int, result: str = "done") -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='done', result=? WHERE id=? AND status='open'",
                (result, task_id),
            )
            return cur.rowcount > 0

    def open_tasks(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status='open' ORDER BY due_at").fetchall()]

    def overdue_tasks(self) -> list[dict]:
        now = time.time()
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM tasks WHERE status='open' AND due_at < ? ORDER BY due_at",
                    (now,),
                ).fetchall()
            ]

    def add_temperature(self, zone: str, celsius: float, source: str = "manual") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO temperatures(ts,zone,celsius,source) VALUES(?,?,?,?)",
                (time.time(), zone, celsius, source),
            )

    def latest_temps(self) -> dict[str, float]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT zone, celsius FROM temperatures t1
                WHERE ts = (SELECT MAX(ts) FROM temperatures t2 WHERE t2.zone = t1.zone)
                """
            ).fetchall()
        return {r["zone"]: float(r["celsius"]) for r in rows}

    def add_device_check(self, device: str, ok: bool, detail: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO device_checks(ts,device,ok,detail) VALUES(?,?,?,?)",
                (time.time(), device, 1 if ok else 0, detail),
            )

    def add_pc_action(self, pc: str, action: str, status: str, detail: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO pc_actions(ts,pc,action,status,detail) VALUES(?,?,?,?,?)",
                (time.time(), pc, action, status, detail),
            )

    def add_staff_event(self, camera: str, present: bool, detail: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO staff_events(ts,camera,present,detail) VALUES(?,?,?,?)",
                (time.time(), camera, 1 if present else 0, detail),
            )
