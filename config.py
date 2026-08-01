"""
Все настройки берутся ТОЛЬКО из переменных окружения Railway (Variables в
настройках сервиса). Никакого .env-файла в проекте нет и быть не должно —
секреты никогда не должны лежать в репозитории.
"""
import os
import sys


def _fail(name: str) -> "str":
    print(
        f"❌ Переменная окружения {name} не задана. "
        f"Задайте её в Railway → Settings → Variables и передеплойте.",
        flush=True,
    )
    sys.exit(1)


BOT_TOKEN = os.environ.get("BOT_TOKEN") or _fail("BOT_TOKEN")

_admin_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.isdigit()}
if not ADMIN_IDS:
    _fail("ADMIN_IDS")

PUBLIC_URL = (os.environ.get("PUBLIC_URL") or _fail("PUBLIC_URL")).rstrip("/")

PORT = int(os.environ.get("PORT", "8080"))

# сколько часов приз лежит заблокированным в "корзинке" гостя
VAULT_LOCK_HOURS = 24

DB_PATH = os.environ.get("DB_PATH", "giftbot.db")
