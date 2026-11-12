"""
Работа с БД через PostgreSQL (asyncpg).
Сигнатуры всех функций те же, что были в версии на sqlite3,
поэтому bot.py и handlers/* менять не нужно.
"""
import datetime
import random
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from config import (
    DATABASE_URL, VAULT_LOCK_HOURS, ADMIN_IDS, SPIN_COOLDOWN_HOURS, ADMIN_NO_COOLDOWN, PRIZE_TTL_HOURS,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_spin_at TEXT;

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

def _cooldown_applies(user_id: int) -> bool:
    return not (ADMIN_NO_COOLDOWN and user_id in ADMIN_IDS)


def _wait_seconds(last_spin_at) -> int:
    if not last_spin_at:
        return 0
    next_at = datetime.datetime.fromisoformat(last_spin_at) + datetime.timedelta(
        hours=SPIN_COOLDOWN_HOURS
    )
    return max(0, int((next_at - datetime.datetime.utcnow()).total_seconds()))


async def get_spin_wait(user_id: int) -> int:
    """Сколько секунд осталось до следующего вращения (0 — можно крутить)."""
    if not _cooldown_applies(user_id):
        return 0
    last = await _pool.fetchval("SELECT last_spin_at FROM users WHERE tg_id=$1", user_id)
    return _wait_seconds(last)


async def draw_prize_for_user(user_id: int):
    """
    Возвращает:
      None                      — нет доступных призов
      {"cooldown": секунды}     — колесо уже крутили, ждать ещё столько секунд
      {...приз...}              — выигрыш (сразу попадает в "корзинку")
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (tg_id, created_at) VALUES ($1, $2) ON CONFLICT (tg_id) DO NOTHING",
                user_id, now(),
            )
            # блокируем строку гостя, чтобы два одновременных спина не прошли
            last = await conn.fetchval(
                "SELECT last_spin_at FROM users WHERE tg_id=$1 FOR UPDATE", user_id
            )
            if _cooldown_applies(user_id):
                wait = _wait_seconds(last)
                if wait > 0:
                    return {"cooldown": wait}

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

            await conn.execute(
                "UPDATE users SET last_spin_at=$1 WHERE tg_id=$2",
                won_at.isoformat(), user_id,
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
        "prize_id": prize["id"],
        "prize_name": prize["name"],
        "prize_image": prize["image_url"],
        "won_at": won_at.isoformat(),
        "unlock_at": unlock_at.isoformat(),
        "expires_at": (won_at + datetime.timedelta(hours=PRIZE_TTL_HOURS)).isoformat(),
    }


def _compute_status(row: dict) -> dict:
    now_dt = datetime.datetime.utcnow()
    unlock_at = datetime.datetime.fromisoformat(row["unlock_at"])
    expires_at = datetime.datetime.fromisoformat(row["won_at"]) + datetime.timedelta(hours=PRIZE_TTL_HOURS)
    row["expires_at"] = expires_at.isoformat()
    if row["status"] == "issued":
        row["display_status"] = "issued"
    elif now_dt >= expires_at:
        row["display_status"] = "expired"   # срок вышел, приз сгорел
    elif now_dt >= unlock_at:
        row["display_status"] = "unlocked"
    else:
        row["display_status"] = "locked"
        row["seconds_left"] = max(0, int((unlock_at - now_dt).total_seconds()))
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
            if row["display_status"] == "expired":
                return False, "Срок действия приза истёк"
            if row["display_status"] == "locked":
                return False, "Приз ещё под замком"

            await conn.execute(
                "UPDATE vault SET status='issued', issued_at=$1, issued_by=$2 WHERE id=$3",
                now(), admin_id, vault_id,
            )
            return True, "Выдано"


async def claim_prize(vault_id: int, user_id: int):
    """Гость закрыл экран выдачи: приз списывается и пропадает из его списка.
    Запись остаётся в базе со статусом 'issued' — админ видит её в панели."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM vault WHERE id=$1 FOR UPDATE", vault_id
            )
            if not row or row["user_id"] != user_id:
                return False, "Приз не найден"
            if row["status"] == "issued":
                return False, "Приз уже выдан"
            if _compute_status(dict(row))["display_status"] == "expired":
                return False, "Срок действия приза истёк"
            await conn.execute(
                "UPDATE vault SET status='issued', issued_at=$1, issued_by=$2 WHERE id=$3",
                now(), user_id, vault_id,
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
