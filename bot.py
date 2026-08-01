import asyncio
import json
import logging
import pathlib

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
from security import validate_init_data
from config import BOT_TOKEN, PORT
from handlers import user as user_handlers
from handlers import admin as admin_handlers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("giftbot")

BASE_DIR = pathlib.Path(__file__).parent
STATIC_DIR = BASE_DIR / "webapp" / "static"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(user_handlers.router)
dp.include_router(admin_handlers.router)


# ---------------- helpers ----------------

def auth_from_request(data: dict) -> dict | None:
    init_data = data.get("initData", "")
    return validate_init_data(init_data)


def json_response(payload, status=200):
    return web.json_response(payload, status=status)


# ---------------- system ----------------

async def healthcheck(request):
    return web.Response(text="OK", status=200)


# ---------------- guest API ----------------

async def api_verify(request):
    body = await request.json()
    user = auth_from_request(body)
    if not user:
        return json_response({"ok": False, "error": "invalid initData"}, 401)
    await db.upsert_user(user["id"], user.get("username") or "", user.get("first_name") or "")
    return json_response({"ok": True, "user": user})


async def api_prizes(request):
    prizes = await db.list_active_prizes()
    return json_response({"ok": True, "prizes": prizes})


async def api_spin(request):
    body = await request.json()
    user = auth_from_request(body)
    if not user:
        return json_response({"ok": False, "error": "invalid initData"}, 401)

    prize = await db.draw_prize_for_user(user["id"])
    if not prize:
        return json_response({"ok": False, "error": "Нет доступных призов"}, 400)
    return json_response({"ok": True, "prize": prize})


async def api_vault(request):
    body = await request.json()
    user = auth_from_request(body)
    if not user:
        return json_response({"ok": False, "error": "invalid initData"}, 401)
    items = await db.get_user_vault(user["id"])
    return json_response({"ok": True, "items": items})


# ---------------- admin API ----------------

def require_admin(user):
    return user and user.get("is_admin")


async def api_admin_stats(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    return json_response({"ok": True, "stats": await db.stats()})


async def api_admin_prizes_list(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    return json_response({"ok": True, "prizes": await db.list_all_prizes()})


async def api_admin_prize_create(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    p = body.get("prize", {})
    pid = await db.create_prize(
        p.get("name", "Без названия"),
        p.get("description", ""),
        p.get("image_url", ""),
        int(p.get("weight", 1)),
        int(p.get("stock", -1)),
    )
    return json_response({"ok": True, "id": pid})


async def api_admin_prize_update(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    prize_id = int(request.match_info["prize_id"])
    fields = body.get("fields", {})
    allowed = {"name", "description", "image_url", "weight", "stock", "active"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    await db.update_prize(prize_id, **fields)
    return json_response({"ok": True})


async def api_admin_prize_delete(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    prize_id = int(request.match_info["prize_id"])
    await db.delete_prize(prize_id)
    return json_response({"ok": True})


async def api_admin_vault_list(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    status_filter = body.get("status")
    items = await db.get_all_vault(status_filter)
    return json_response({"ok": True, "items": items})


async def api_admin_vault_issue(request):
    body = await request.json()
    user = auth_from_request(body)
    if not require_admin(user):
        return json_response({"ok": False, "error": "forbidden"}, 403)
    vault_id = int(request.match_info["vault_id"])
    ok, message = await db.issue_prize(vault_id, user["id"])
    return json_response({"ok": ok, "message": message}, 200 if ok else 400)


# ---------------- app wiring ----------------

def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", healthcheck)

    app.router.add_post("/api/verify", api_verify)
    app.router.add_get("/api/prizes", api_prizes)
    app.router.add_post("/api/spin", api_spin)
    app.router.add_post("/api/vault", api_vault)

    app.router.add_post("/api/admin/stats", api_admin_stats)
    app.router.add_post("/api/admin/prizes", api_admin_prizes_list)
    app.router.add_post("/api/admin/prizes/create", api_admin_prize_create)
    app.router.add_post("/api/admin/prizes/{prize_id}/update", api_admin_prize_update)
    app.router.add_post("/api/admin/prizes/{prize_id}/delete", api_admin_prize_delete)
    app.router.add_post("/api/admin/vault", api_admin_vault_list)
    app.router.add_post("/api/admin/vault/{vault_id}/issue", api_admin_vault_issue)

    app.router.add_static("/app/", STATIC_DIR / "guest", show_index=False)
    app.router.add_static("/admin/", STATIC_DIR / "admin", show_index=False)
    return app


async def run_web_server():
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    log.info("✅ Веб-сервер запущен на порту %s", PORT)


async def main():
    await db.init_db()
    await run_web_server()
    log.info("🤖 Бот запущен, начинаем polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
