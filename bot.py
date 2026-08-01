import asyncio
import json
import os
import time
import logging
import random
import hmac
import hashlib
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, ErrorEvent
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiohttp.web import middleware
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 443))
WEBAPP_URL = "https://cyberxgift.ru/static/"
WEBHOOK_URL = "https://cyberxgift.ru/webhook"
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', 'cyberxgift_webhook_secret_2026')
SPIN_COOLDOWN = 86400  # 24 часа

# ID администратора в Telegram — задаётся переменной окружения на Railway.
# Только этот пользователь получит доступ к /admin и к API редактирования призов.
_admin_id_raw = os.getenv('ADMIN_ID', '')
ADMIN_ID = int(_admin_id_raw) if _admin_id_raw.strip().isdigit() else None

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")
if ADMIN_ID is None:
    logger.warning("⚠️ ADMIN_ID не задан — админ-панель будет недоступна никому!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()


@middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        response = web.Response(status=200)
    else:
        response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ========== ЛОГИРОВАНИЕ ОШИБОК АПДЕЙТОВ ==========
# Раньше исключения при обработке апдейтов (например, в /start) нигде не
# отображались — бот просто "молчал". Теперь всё падает в лог явно.

@dp.error()
async def error_handler(event: ErrorEvent):
    logger.exception(f"❌ Ошибка при обработке апдейта: {event.exception}")
    return True


# ========== ПРОВЕРКА TELEGRAM initData ==========

def validate_init_data(init_data: str, bot_token: str, max_age: int = SPIN_COOLDOWN):
    try:
        if not init_data:
            return None
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop('hash', None)
        if not received_hash:
            return None

        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("⚠️ Неверная подпись initData")
            return None

        auth_date = int(parsed.get('auth_date', 0))
        if time.time() - auth_date > max_age:
            logger.warning("⚠️ initData устарела")
            return None

        user_json = parsed.get('user')
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception as e:
        logger.error(f"Ошибка проверки initData: {e}")
        return None


def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


# ========== БАЗА ДАННЫХ ==========

DEFAULT_PRIZES = [
    # label, value, is_points, weight, color
    ("50 бонусов", 50, 1, 1, "#4b69ff"),
    ("100 бонусов", 100, 1, 1, "#8847ff"),
    ("1 час игры в зоне Стандарт", 0, 0, 1, "#d32ce6"),
    ("1 час игры в Панораме", 0, 0, 1, "#eb4b4b"),
    ("150 бонусов", 150, 1, 1, "#f5c842"),
]


async def init_db():
    async with aiosqlite.connect('users.db') as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                            (user_id INTEGER PRIMARY KEY, 
                             last_spin_time INTEGER DEFAULT 0, 
                             total_balance INTEGER DEFAULT 0,
                             username TEXT DEFAULT '')''')

        await db.execute('''CREATE TABLE IF NOT EXISTS prizes
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             label TEXT NOT NULL,
                             value INTEGER DEFAULT 0,
                             is_points INTEGER DEFAULT 1,
                             weight INTEGER DEFAULT 1,
                             color TEXT DEFAULT '#f5c842',
                             active INTEGER DEFAULT 1,
                             sort_order INTEGER DEFAULT 0)''')

        await db.execute('''CREATE TABLE IF NOT EXISTS wins
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             user_id INTEGER,
                             username TEXT,
                             prize_label TEXT,
                             prize_value INTEGER,
                             is_points INTEGER,
                             created_at INTEGER)''')

        cursor = await db.execute('SELECT COUNT(*) FROM prizes')
        count = (await cursor.fetchone())[0]
        if count == 0:
            for i, (label, value, is_points, weight, color) in enumerate(DEFAULT_PRIZES):
                await db.execute(
                    'INSERT INTO prizes (label, value, is_points, weight, color, active, sort_order) VALUES (?,?,?,?,?,1,?)',
                    (label, value, is_points, weight, color, i)
                )
            logger.info("✅ Призы по умолчанию добавлены")

        await db.commit()
    logger.info("✅ База данных готова")


async def ensure_user(user_id: int, username: str):
    async with aiosqlite.connect('users.db') as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        await db.commit()


async def get_balance(user_id):
    async with aiosqlite.connect('users.db') as db:
        cursor = await db.execute('SELECT total_balance FROM users WHERE user_id = ?', (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0


async def get_last_spin(user_id):
    async with aiosqlite.connect('users.db') as db:
        cursor = await db.execute('SELECT last_spin_time FROM users WHERE user_id = ?', (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0


async def get_active_prizes():
    async with aiosqlite.connect('users.db') as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT id, label, value, is_points, weight, color FROM prizes WHERE active = 1 ORDER BY sort_order, id'
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_all_prizes():
    async with aiosqlite.connect('users.db') as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM prizes ORDER BY sort_order, id')
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


def pick_weighted_prize(prizes):
    total_weight = sum(max(p['weight'], 0) for p in prizes)
    if total_weight <= 0:
        return random.choice(prizes)
    r = random.uniform(0, total_weight)
    upto = 0
    for p in prizes:
        upto += max(p['weight'], 0)
        if r <= upto:
            return p
    return prizes[-1]


# ========== КОМАНДЫ БОТА ==========

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    await ensure_user(user_id, username)
    logger.info(f"▶️ /start от {user_id} ({username})")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🎁 Открыть кейс", web_app=WebAppInfo(url=WEBAPP_URL + 'index.html'))],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")]
    ])
    await message.answer("🎁 Привет! Открывай кейс раз в 24 часа и получай бонусы!", reply_markup=keyboard)


@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return  # молчим для не-админов
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("⚙️ Админ-панель", web_app=WebAppInfo(url=WEBAPP_URL + 'admin.html'))]
    ])
    await message.answer("⚙️ Панель управления призами", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == "balance")
async def balance(callback: types.CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    await callback.answer(f"💰 Баланс: {bal} монет", show_alert=True)


# ========== ПУБЛИЧНОЕ HTTP API ДЛЯ MINI APP ==========

async def api_prizes(request):
    prizes = await get_active_prizes()
    return web.json_response({'prizes': prizes})


async def api_status(request):
    init_data = request.query.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not user:
        return web.json_response({'error': 'unauthorized'}, status=401)

    user_id = user['id']
    await ensure_user(user_id, user.get('username', ''))

    last = await get_last_spin(user_id)
    now = int(time.time())
    bal = await get_balance(user_id)

    if now - last >= SPIN_COOLDOWN:
        return web.json_response({'status': 'can_spin', 'balance': bal})
    return web.json_response({'status': 'wait', 'wait_time': SPIN_COOLDOWN - (now - last), 'balance': bal})


async def api_spin(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'invalid json'}, status=400)

    init_data = body.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not user:
        return web.json_response({'error': 'unauthorized'}, status=401)

    user_id = user['id']
    username = user.get('username', '')
    await ensure_user(user_id, username)

    last = await get_last_spin(user_id)
    now = int(time.time())

    if now - last < SPIN_COOLDOWN:
        bal = await get_balance(user_id)
        return web.json_response({'status': 'wait', 'wait_time': SPIN_COOLDOWN - (now - last), 'balance': bal})

    prizes = await get_active_prizes()
    if not prizes:
        return web.json_response({'error': 'no prizes configured'}, status=500)

    won = pick_weighted_prize(prizes)
    points_delta = won['value'] if won['is_points'] else 0

    async with aiosqlite.connect('users.db') as db:
        await db.execute(
            'UPDATE users SET last_spin_time = ?, total_balance = total_balance + ? WHERE user_id = ?',
            (now, points_delta, user_id)
        )
        await db.execute(
            'INSERT INTO wins (user_id, username, prize_label, prize_value, is_points, created_at) VALUES (?,?,?,?,?,?)',
            (user_id, username, won['label'], won['value'], won['is_points'], now)
        )
        await db.commit()

    new_balance = await get_balance(user_id)
    logger.info(f"🎁 {user_id} выиграл «{won['label']}». Новый баланс: {new_balance}")
    return web.json_response({
        'status': 'success',
        'prize': {
            'id': won['id'],
            'label': won['label'],
            'value': won['value'],
            'is_points': bool(won['is_points']),
            'color': won['color'],
        },
        'new_balance': new_balance
    })


# ========== АДМИНСКОЕ HTTP API ==========

def _check_admin_request(user):
    return user and is_admin(user.get('id'))


async def api_admin_prizes_get(request):
    init_data = request.query.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not _check_admin_request(user):
        return web.json_response({'error': 'forbidden'}, status=403)
    prizes = await get_all_prizes()
    return web.json_response({'prizes': prizes})


async def api_admin_prizes_save(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'invalid json'}, status=400)

    init_data = body.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not _check_admin_request(user):
        return web.json_response({'error': 'forbidden'}, status=403)

    prize_id = body.get('id')
    label = str(body.get('label', '')).strip()[:100]
    if not label:
        return web.json_response({'error': 'label required'}, status=400)
    try:
        value = int(body.get('value', 0))
    except (TypeError, ValueError):
        value = 0
    is_points = 1 if body.get('is_points', True) else 0
    try:
        weight = max(0, int(body.get('weight', 1)))
    except (TypeError, ValueError):
        weight = 1
    color = str(body.get('color', '#f5c842'))[:20]
    active = 1 if body.get('active', True) else 0
    try:
        sort_order = int(body.get('sort_order', 0))
    except (TypeError, ValueError):
        sort_order = 0

    async with aiosqlite.connect('users.db') as db:
        if prize_id:
            await db.execute(
                '''UPDATE prizes SET label=?, value=?, is_points=?, weight=?, color=?, active=?, sort_order=?
                   WHERE id=?''',
                (label, value, is_points, weight, color, active, sort_order, prize_id)
            )
        else:
            await db.execute(
                '''INSERT INTO prizes (label, value, is_points, weight, color, active, sort_order)
                   VALUES (?,?,?,?,?,?,?)''',
                (label, value, is_points, weight, color, active, sort_order)
            )
        await db.commit()

    prizes = await get_all_prizes()
    return web.json_response({'status': 'ok', 'prizes': prizes})


async def api_admin_prizes_delete(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'invalid json'}, status=400)

    init_data = body.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not _check_admin_request(user):
        return web.json_response({'error': 'forbidden'}, status=403)

    prize_id = body.get('id')
    if not prize_id:
        return web.json_response({'error': 'id required'}, status=400)

    async with aiosqlite.connect('users.db') as db:
        await db.execute('DELETE FROM prizes WHERE id = ?', (prize_id,))
        await db.commit()

    prizes = await get_all_prizes()
    return web.json_response({'status': 'ok', 'prizes': prizes})


async def api_admin_wins(request):
    init_data = request.query.get('init_data', '')
    user = validate_init_data(init_data, API_TOKEN)
    if not _check_admin_request(user):
        return web.json_response({'error': 'forbidden'}, status=403)

    async with aiosqlite.connect('users.db') as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM wins ORDER BY created_at DESC LIMIT 100'
        )
        rows = await cursor.fetchall()
        return web.json_response({'wins': [dict(r) for r in rows]})


# ========== СЛУЖЕБНОЕ ==========

async def healthcheck(request):
    return web.Response(text="OK", status=200)


async def options_ok(request):
    return web.Response(status=200)


# ========== ЗАПУСК ==========

async def on_startup(bot: Bot):
    info = await bot.get_webhook_info()
    if info.url != WEBHOOK_URL:
        await bot.set_webhook(
            WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        logger.info(f"✅ Webhook уже установлен: {WEBHOOK_URL}")


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    app.router.add_get('/health', healthcheck)

    app.router.add_get('/api/prizes', api_prizes)
    app.router.add_get('/api/status', api_status)
    app.router.add_post('/api/spin', api_spin)
    app.router.add_route('OPTIONS', '/api/status', options_ok)
    app.router.add_route('OPTIONS', '/api/spin', options_ok)
    app.router.add_route('OPTIONS', '/api/prizes', options_ok)

    app.router.add_get('/api/admin/prizes', api_admin_prizes_get)
    app.router.add_post('/api/admin/prizes', api_admin_prizes_save)
    app.router.add_post('/api/admin/prizes/delete', api_admin_prizes_delete)
    app.router.add_get('/api/admin/wins', api_admin_wins)
    app.router.add_route('OPTIONS', '/api/admin/prizes', options_ok)
    app.router.add_route('OPTIONS', '/api/admin/prizes/delete', options_ok)
    app.router.add_route('OPTIONS', '/api/admin/wins', options_ok)

    app.router.add_static('/static/', path='static/')

    # Официальный обработчик вебхука aiogram — вместо ручного парсинга апдейтов,
    # который мог тихо ломаться на /start.
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )
    webhook_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)
    dp.startup.register(on_startup)

    return app


async def main():
    await init_db()
    app = build_app()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()

    logger.info(f"🌐 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика: {WEBAPP_URL}")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")
    logger.info(f"🔌 API: /api/prizes, /api/status, /api/spin, /api/admin/*")

    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
