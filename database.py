import datetime
import random
import aiosqlite

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


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(tg_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        is_admin = 1 if tg_id in ADMIN_IDS else 0
        await db.execute(
            """INSERT INTO users (tg_id, username, first_name, is_admin, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tg_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 is_admin=excluded.is_admin""",
            (tg_id, username, first_name, is_admin, now()),
        )
        await db.commit()


async def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


# ---------- Призы ----------

async def list_active_prizes():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM prizes WHERE active=1 AND (stock=-1 OR stock>0) ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_all_prizes():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM prizes ORDER BY id DESC")
        return [dict(r) for r in await cur.fetchall()]


async def create_prize(name, description, image_url, weight, stock):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO prizes (name, description, image_url, weight, stock, active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (name, description, image_url, weight, stock, now()),
        )
        await db.commit()
        return cur.lastrowid


async def update_prize(prize_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [prize_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE prizes SET {cols} WHERE id=?", values)
        await db.commit()


async def delete_prize(prize_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM prizes WHERE id=?", (prize_id,))
        await db.commit()


# ---------- Корзинка (vault) ----------

async def draw_prize_for_user(user_id: int):
    """Выбирает приз взвешенным случайным образом и кладёт в корзинку гостя с блокировкой на 24ч."""
    prizes = await list_active_prizes()
    if not prizes:
        return None

    weights = [max(p["weight"], 0) or 1 for p in prizes]
    prize = random.choices(prizes, weights=weights, k=1)[0]

    won_at = datetime.datetime.utcnow()
    unlock_at = won_at + datetime.timedelta(hours=VAULT_LOCK_HOURS)

    async with aiosqlite.connect(DB_PATH) as db:
        if prize["stock"] > 0:
            await db.execute(
                "UPDATE prizes SET stock = stock - 1 WHERE id=?", (prize["id"],)
            )
        cur = await db.execute(
            """INSERT INTO vault (user_id, prize_id, prize_name, prize_image, won_at, unlock_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'locked')""",
            (user_id, prize["id"], prize["name"], prize["image_url"],
             won_at.isoformat(), unlock_at.isoformat()),
        )
        await db.commit()
        vault_id = cur.lastrowid

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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM vault WHERE user_id=? ORDER BY won_at DESC", (user_id,)
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return [_compute_status(r) for r in rows]


async def get_all_vault(status_filter: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT vault.*, users.username, users.first_name
               FROM vault LEFT JOIN users ON users.tg_id = vault.user_id
               ORDER BY vault.won_at DESC"""
        )
        rows = [dict(r) for r in await cur.fetchall()]
    rows = [_compute_status(r) for r in rows]
    if status_filter:
        rows = [r for r in rows if r["display_status"] == status_filter]
    return rows


async def issue_prize(vault_id: int, admin_id: int):
    """Выдать приз гостю (только когда замок снят)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM vault WHERE id=?", (vault_id,))
        row = await cur.fetchone()
        if not row:
            return False, "Приз не найден"
        row = dict(row)
        row = _compute_status(row)
        if row["display_status"] == "issued":
            return False, "Приз уже выдан"
        if row["display_status"] == "locked":
            return False, "Приз ещё под замком"

        await db.execute(
            "UPDATE vault SET status='issued', issued_at=?, issued_by=? WHERE id=?",
            (now(), admin_id, vault_id),
        )
        await db.commit()
        return True, "Выдано"


async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        users = (await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone())["c"]
        prizes = (await (await db.execute("SELECT COUNT(*) c FROM prizes WHERE active=1")).fetchone())["c"]
        locked = (await (await db.execute(
            "SELECT COUNT(*) c FROM vault WHERE status='locked'")).fetchone())["c"]
        issued = (await (await db.execute(
            "SELECT COUNT(*) c FROM vault WHERE status='issued'")).fetchone())["c"]
        total_wins = (await (await db.execute("SELECT COUNT(*) c FROM vault")).fetchone())["c"]
    return {
        "users": users,
        "active_prizes": prizes,
        "locked_in_vault": locked,
        "issued": issued,
        "total_wins": total_wins,
    }
