"""
Все настройки берутся ТОЛЬКО из переменных окружения (Render → Environment).
Никакого .env-файла в проекте нет и быть не должно —
секреты никогда не должны лежать в репозитории.
"""
import os
import sys


def _fail(name: str) -> "str":
    print(
        f"❌ Переменная окружения {name} не задана. "
        f"Задайте её в Render → Environment и передеплойте.",
        flush=True,
    )
    sys.exit(1)


BOT_TOKEN = os.environ.get("BOT_TOKEN") or _fail("BOT_TOKEN")

_admin_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.isdigit()}
if not ADMIN_IDS:
    _fail("ADMIN_IDS")

# Render сам задаёт RENDER_EXTERNAL_URL (https://<сервис>.onrender.com),
# так что PUBLIC_URL можно не указывать вручную.
PUBLIC_URL = (
    os.environ.get("PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or _fail("PUBLIC_URL")
).rstrip("/")

PORT = int(os.environ.get("PORT", "8080"))

# Приз можно получить сразу (замка в "корзинке" больше нет)
VAULT_LOCK_HOURS = 0

# Через сколько часов после выигрыша приз сгорает, если его не показали администратору
PRIZE_TTL_HOURS = 48

# Как часто гость может крутить колесо
SPIN_COOLDOWN_HOURS = 24

# По умолчанию ограничение 24 часа действует на ВСЕХ, включая админов.
# Для проверки можно временно задать в Render переменную ADMIN_NO_COOLDOWN=1.
ADMIN_NO_COOLDOWN = os.environ.get("ADMIN_NO_COOLDOWN", "0") == "1"

# строка подключения к PostgreSQL (Neon / Supabase)
DATABASE_URL = os.environ.get("DATABASE_URL") or _fail("DATABASE_URL")
