"""
Проверка подлинности данных, которые Telegram передаёт Mini App (initData).
Это защищает админку и API от подделки запросов извне Telegram.
Алгоритм официальный: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from config import BOT_TOKEN, ADMIN_IDS


def validate_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None

    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_raw = parsed.get("user")
    user = json.loads(user_raw) if user_raw else None
    if not user:
        return None

    return {
        "id": user["id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "is_admin": user["id"] in ADMIN_IDS,
    }
