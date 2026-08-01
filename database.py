"""
Работа с БД через стандартный sqlite3 (без внешних зависимостей).
Каждый вызов выполняется в отдельном потоке через asyncio.to_thread,
чтобы не блокировать event loop бота — снаружи функции остаются async,
вызывающий код (bot.py, handlers/*) не меняется.
"""
import asyncio
import datetime
import random
import sqlite3

from config import DB_PATH, VAULT_LOCK_HOURS, ADMIN_IDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS prizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    weight INTEGER DEFAULT 1,
    stock INTEGER DEFAULT -1,
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    prize_id INTEGER,
    prize_name TEXT NOT NULL,
    prize_image TEXT,
    won_at TEXT NOT NULL,
    unlock_at TEXT NOT NULL,
    status TEXT DEFAULT 'locked',
    issued_at TEXT,
    issued_by INTEGER
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _init_db_sync():
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


async def init_db():
    await asyncio.to_thread(_init_db_sync)


def _upsert_user_sync(tg_id, username, first_name):
    conn = _connect()
    try:
        is_admin = 1 if tg_id in ADMIN_IDS else 0
        conn.execute(
            """INSERT INTO users (tg_id, username, first_name, is_admin, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tg_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 is_admin=excluded.is_admin""",
            (tg_id, username, first_name, is_admin, now()),
        )
        conn.commit()
    finally:
        conn.close()


async def upsert_user(tg_id: int, username: str, first_name: str):
    await asyncio.to_thread(_upsert_user_sync, tg_id, username, first_name)


async def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


# ---------- Призы ----------

def _list_active_prizes_sync():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM prizes WHERE active=1 AND (stock=-1 OR stock>0) ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def list_active_prizes():
    return await asyncio.to_thread(_list_active_prizes_sync)


def _list_all_prizes_sync():
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM prizes ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def list_all_prizes():
    return await asyncio.to_thread(_list_all_prizes_sync)


def _create_prize_sync(name, description, image_url, weight, stock):
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO prizes (name, description, image_url, weight, stock, active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (name, description, image_url, weight, stock, now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


async def create_prize(name, description, image_url, weight, stock):
    return await asyncio.to_thread(_create_prize_sync, name, description, image_url, weight, stock)


def _update_prize_sync(prize_id, fields: dict):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [prize_id]
    conn = _connect()
    try:
        conn.execute(f"UPDATE prizes SET {cols} WHERE id=?", values)
        conn.commit()
    finally:
        conn.close()


async def update_prize(prize_id, **fields):
    await asyncio.to_thread(_update_prize_sync, prize_id, fields)


def _delete_prize_sync(prize_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM prizes WHERE id=?", (prize_id,))
        conn.commit()
    finally:
        conn.close()


async def delete_prize(prize_id):
    await asyncio.to_thread(_delete_prize_sync, prize_id)


# ---------- Корзинка (vault) ----------

def _draw_prize_for_user_sync(user_id: int):
    conn = _connect()
    try:
        prizes = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM prizes WHERE active=1 AND (stock=-1 OR stock>0) ORDER BY id"
            ).fetchall()
        ]
        if not prizes:
            return None

        weights = [max(p["weight"], 0) or 1 for p in prizes]
        prize = random.choices(prizes, weights=weights, k=1)[0]

        won_at = datetime.datetime.utcnow()
        unlock_at = won_at + datetime.timedelta(hours=VAULT_LOCK_HOURS)

        if prize["stock"] > 0:
            conn.execute("UPDATE prizes SET stock = stock - 1 WHERE id=?", (prize["id"],))

        cur = conn.execute(
            """INSERT INTO vault (user_id, prize_id, prize_name, prize_image, won_at, unlock_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'locked')""",
            (user_id, prize["id"], prize["name"], prize["image_url"],
             won_at.isoformat(), unlock_at.isoformat()),
        )
        conn.commit()
        vault_id = cur.lastrowid

        return {
            "id": vault_id,
            "prize_name": prize["name"],
            "prize_image": prize["image_url"],
            "won_at": won_at.isoformat(),
            "unlock_at": unlock_at.isoformat(),
        }
    finally:
        conn.close()


async def draw_prize_for_user(user_id: int):
    return await asyncio.to_thread(_draw_prize_for_user_sync, user_id)


def _compute_status(row: dict) -> dict:
    unlock_at = datetime.datetime.fromisoformat(row["unlock_at"])
    if row["status"] == "issued":
        row["display_status"] = "issued"
    elif datetime.datetime.utcnow() >= unlock_at:
        row["display_status"] = "unlocked"
    else:
        row["display_status"] = "locked"
        row["seconds_left"] = max(
            0, int((unlock_at - datetime.datetime.utcnow()).total_seconds())
        )
    return row


def _get_user_vault_sync(user_id: int):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM vault WHERE user_id=? ORDER BY won_at DESC", (user_id,)
        ).fetchall()
        return [_compute_status(dict(r)) for r in rows]
    finally:
        conn.close()


async def get_user_vault(user_id: int):
    return await asyncio.to_thread(_get_user_vault_sync, user_id)


def _get_all_vault_sync(status_filter):
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT vault.*, users.username, users.first_name
               FROM vault LEFT JOIN users ON users.tg_id = vault.user_id
               ORDER BY vault.won_at DESC"""
        ).fetchall()
        rows = [_compute_status(dict(r)) for r in rows]
        if status_filter:
            rows = [r for r in rows if r["display_status"] == status_filter]
        return rows
    finally:
        conn.close()


async def get_all_vault(status_filter: str = None):
    return await asyncio.to_thread(_get_all_vault_sync, status_filter)


def _issue_prize_sync(vault_id: int, admin_id: int):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM vault WHERE id=?", (vault_id,)).fetchone()
        if not row:
            return False, "Приз не найден"
        row = _compute_status(dict(row))
        if row["display_status"] == "issued":
            return False, "Приз уже выдан"
        if row["display_status"] == "locked":
            return False, "Приз ещё под замком"

        conn.execute(
            "UPDATE vault SET status='issued', issued_at=?, issued_by=? WHERE id=?",
            (now(), admin_id, vault_id),
        )
        conn.commit()
        return True, "Выдано"
    finally:
        conn.close()


async def issue_prize(vault_id: int, admin_id: int):
    return await asyncio.to_thread(_issue_prize_sync, vault_id, admin_id)


def _stats_sync():
    conn = _connect()
    try:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        prizes = conn.execute("SELECT COUNT(*) c FROM prizes WHERE active=1").fetchone()["c"]
        locked = conn.execute("SELECT COUNT(*) c FROM vault WHERE status='locked'").fetchone()["c"]
        issued = conn.execute("SELECT COUNT(*) c FROM vault WHERE status='issued'").fetchone()["c"]
        total_wins = conn.execute("SELECT COUNT(*) c FROM vault").fetchone()["c"]
        return {
            "users": users,
            "active_prizes": prizes,
            "locked_in_vault": locked,
            "issued": issued,
            "total_wins": total_wins,
        }
    finally:
        conn.close()


async def stats():
    return await asyncio.to_thread(_stats_sync)
