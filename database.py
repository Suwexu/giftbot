"""
Работа с БД через PostgreSQL (asyncpg).
Сигнатуры всех функций те же, что были в версии на sqlite3,
поэтому bot.py и handlers/* менять не нужно.
"""
import datetime
import random
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from config import DATABASE_URL, VAULT_LOCK_HOURS, ADMIN_IDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS prizes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    weight INTEGER DEFAULT 1,
    stock INTEGER DEFAULT -1,
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS vault (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    prize_id INTEGER,
    prize_name TEXT NOT NULL,
    prize_image TEXT,
    won_at TEXT NOT NULL,
    unlock_at TEXT NOT NULL,
    status TEXT DEFAULT 'locked',
    issued_at TEXT,
    issued_by BIGINT
);
"""

_pool: asyncpg.Pool | None = None


def _clean_dsn(dsn: str) -> str:
    """Neon добавляет в строку channel_binding=require — asyncpg его не понимает."""
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(
        _clean_dsn(DATABASE_URL),
        min_size=1,
        max_size=5,
        statement_cache_size=0,               # совместимо с pgbouncer/pooler
        max_inactive_connection_lifetime=240,  # Neon усыпляет простаивающие соединения
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def upsert_user(tg_id: int, username: str, first_name: str):
    is_admin = 1 if tg_id in ADMIN_IDS else 0
    await _pool.execute(
        """INSERT INTO users (tg_id, username, first_name, is_admin, created_at)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (tg_id) DO UPDATE SET
             username=EXCLUDED.username,
             first_name=EXCLUDED.first_name,
             is_admin=EXCLUDED.is_admin""",
        tg_id, username, first_name, is_admin, now(),
    )


async def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


# ---------- Призы ----------

async def list_active_prizes():
    rows = await _pool.fetch(
        "SELECT * FROM prizes WHERE active=1 AND (stock=-1 OR stock>0) ORDER BY id"
    )
    return [dict(r) for r in rows]


async def list_all_prizes():
    rows = await _pool.fetch("SELECT * FROM prizes ORDER BY id DESC")
    return [dict(r) for r in rows]


async def create_prize(name, description, image_url, weight, stock):
    return await _pool.fetchval(
        """INSERT INTO prizes (name, description, image_url, weight, stock, active, created_at)
           VALUES ($1, $2, $3, $4, $5, 1, $6)
           RETURNING id""",
        name, description, image_url, weight, stock, now(),
    )


_PRIZE_FIELDS = {"name", "description", "image_url", "weight", "stock", "active"}
_PRIZE_INT_FIELDS = {"weight", "stock"}


async def update_prize(prize_id, **fields):
    fields = {k: v for k, v in fields.items() if k in _PRIZE_FIELDS}
    if not fields:
        return
    values = []
    for k, v in fields.items():
        if k in _PRIZE_INT_FIELDS:
            v = int(v)
        elif k == "active":
            v = 1 if v in (True, 1, "1", "true", "True") else 0
        values.append(v)
    cols = ", ".join(f"{k}=${i}" for i, k in enumerate(fields, start=1))
    values.append(prize_id)
    await _pool.execute(
        f"UPDATE prizes SET {cols} WHERE id=${len(values)}", *values
    )


async def delete_prize(prize_id):
    await _pool.execute("DELETE FROM prizes WHERE id=$1", prize_id)


# ---------- Корзинка (vault) ----------

async def draw_prize_for_user(user_id: int):
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # FOR UPDATE — чтобы два одновременных спина не списали один и тот же остаток
            rows = await conn.fetch(
                """SELECT * FROM prizes
                   WHERE active=1 AND (stock=-1 OR stock>0)
                   ORDER BY id FOR UPDATE"""
            )
            prizes = [dict(r) for r in rows]
            if not prizes:
                return None

            weights = [max(p["weight"] or 0, 0) or 1 for p in prizes]
            prize = random.choices(prizes, weights=weights, k=1)[0]

            won_at = datetime.datetime.utcnow()
            unlock_at = won_at + datetime.timedelta(hours=VAULT_LOCK_HOURS)

            if prize["stock"] > 0:
                await conn.execute(
                    "UPDATE prizes SET stock = stock - 1 WHERE id=$1", prize["id"]
                )

            vault_id = await conn.fetchval(
                """INSERT INTO vault (user_id, prize_id, prize_name, prize_image, won_at, unlock_at, status)
                   VALUES ($1, $2, $3, $4, $5, $6, 'locked')
                   RETURNING id""",
                user_id, prize["id"], prize["name"], prize["image_url"],
                won_at.isoformat(), unlock_at.isoformat(),
            )

    return {
        "id": vault_id,
        "prize_name": prize["name"],
        "prize_image": prize["image_url"],
        "won_at": won_at.isoformat(),
        "unlock_at": unlock_at.isoformat(),
    }


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


async def get_user_vault(user_id: int):
    rows = await _pool.fetch(
        "SELECT * FROM vault WHERE user_id=$1 ORDER BY won_at DESC", user_id
    )
    return [_compute_status(dict(r)) for r in rows]


async def get_all_vault(status_filter: str = None):
    rows = await _pool.fetch(
        """SELECT vault.*, users.username, users.first_name
           FROM vault LEFT JOIN users ON users.tg_id = vault.user_id
           ORDER BY vault.won_at DESC"""
    )
    items = [_compute_status(dict(r)) for r in rows]
    if status_filter:
        items = [r for r in items if r["display_status"] == status_filter]
    return items


async def issue_prize(vault_id: int, admin_id: int):
    async with _pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM vault WHERE id=$1 FOR UPDATE", vault_id
            )
            if not row:
                return False, "Приз не найден"
            row = _compute_status(dict(row))
            if row["display_status"] == "issued":
                return False, "Приз уже выдан"
            if row["display_status"] == "locked":
                return False, "Приз ещё под замком"

            await conn.execute(
                "UPDATE vault SET status='issued', issued_at=$1, issued_by=$2 WHERE id=$3",
                now(), admin_id, vault_id,
            )
            return True, "Выдано"


async def stats():
    users = await _pool.fetchval("SELECT COUNT(*) FROM users")
    prizes = await _pool.fetchval("SELECT COUNT(*) FROM prizes WHERE active=1")
    locked = await _pool.fetchval("SELECT COUNT(*) FROM vault WHERE status='locked'")
    issued = await _pool.fetchval("SELECT COUNT(*) FROM vault WHERE status='issued'")
    total_wins = await _pool.fetchval("SELECT COUNT(*) FROM vault")
    return {
        "users": users,
        "active_prizes": prizes,
        "locked_in_vault": locked,
        "issued": issued,
        "total_wins": total_wins,
    }
