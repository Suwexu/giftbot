import asyncio
import json
import os
import time
import logging
import hmac
import hashlib
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
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
SPIN_COOLDOWN = 86400  # 24 часа

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

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


# ========== ПРОВЕРКА TELEGRAM initData ==========
# Раньше user_id брался напрямую из сообщения бота (в web_app_data), это было
# нормально, пока связь шла через sendData(). Теперь связь идёт по HTTP,
# поэтому каждый запрос нужно подписывать initData и проверять подпись —
# иначе кто угодно сможет накрутить себе баланс, просто зная user_id.

def validate_init_data(init_data: str, bot_token: str, max_age: int = SPIN_COOLDOWN) -> dict | None:
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


# ========== БАЗА ДАННЫХ ==========

async def init_db():
    async with aiosqlite.connect('users.db') as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                            (user_id INTEGER PRIMARY KEY, 
                             last_spin_time INTEGER DEFAULT 0, 
                             total_balance INTEGER DEFAULT 0,
                             username TEXT DEFAULT '')''')
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


# ========== КОМАНДЫ БОТА ==========

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    await ensure_user(user_id, username)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🎡 Крутить колесо", web_app=WebAppInfo(url=WEBAPP_URL + 'index.html'))],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")]
    ])
    await message.answer("🎰 Привет! Крути колесо раз в 24 часа!", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == "balance")
async def balance(callback: types.CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    await callback.answer(f"💰 Баланс: {bal} монет", show_alert=True)


# Старый обработчик sendData оставлен для обратной совместимости
# (например, если где-то ещё вызывается tg.sendData), но основной
# рабочий путь теперь — HTTP API ниже.
@dp.message(lambda message: message.web_app_data is not None)
async def web_app_data(message: types.Message):
    logger.info(f"📩 (legacy) web_app_data от {message.from_user.id}: {message.web_app_data.data[:50]}")


# ========== HTTP API ДЛЯ MINI APP ==========
# Фронтенд теперь ходит сюда через fetch(), а не через tg.sendData(),
# потому что sendData закрывает Mini App сразу после отправки и получить
# ответ внутри приложения невозможно — из-за этого и была вечная
# "Нет ответа от сервера".

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
    await ensure_user(user_id, user.get('username', ''))

    last = await get_last_spin(user_id)
    now = int(time.time())

    if now - last < SPIN_COOLDOWN:
        bal = await get_balance(user_id)
        return web.json_response({'status': 'wait', 'wait_time': SPIN_COOLDOWN - (now - last), 'balance': bal})

    try:
        prize_value = int(body.get('value', 0))
    except (TypeError, ValueError):
        prize_value = 0
    prize_label = str(body.get('prize_name', ''))[:20]

    async with aiosqlite.connect('users.db') as db:
        await db.execute(
            'UPDATE users SET last_spin_time = ?, total_balance = total_balance + ? WHERE user_id = ?',
            (now, prize_value, user_id)
        )
        await db.commit()

    new_balance = await get_balance(user_id)
    logger.info(f"🎁 {user_id} выиграл {prize_value} ({prize_label}). Новый баланс: {new_balance}")
    return web.json_response({
        'status': 'success',
        'prize': prize_label,
        'value': prize_value,
        'new_balance': new_balance
    })


# ========== WEBHOOK ==========

async def webhook_check(request):
    return web.Response(text="OK", status=200)


async def handle_webhook(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)


async def healthcheck(request):
    return web.Response(text="OK", status=200)


# ========== ЗАПУСК ==========

async def main():
    await init_db()

    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get('/health', healthcheck)
    app.router.add_get('/webhook', webhook_check)
    app.router.add_post('/webhook', handle_webhook)

    app.router.add_get('/api/status', api_status)
    app.router.add_post('/api/spin', api_spin)
    app.router.add_route('OPTIONS', '/api/status', lambda r: web.Response(status=200))
    app.router.add_route('OPTIONS', '/api/spin', lambda r: web.Response(status=200))

    app.router.add_static('/static/', path='static/')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()

    logger.info(f"🌐 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика: {WEBAPP_URL}")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")
    logger.info(f"🔌 API: /api/status, /api/spin")

    try:
        await bot.delete_webhook()
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки webhook: {e}")
        logger.info("🔄 Переключаемся на polling...")
        await bot.delete_webhook()
        await dp.start_polling(bot)
        return

    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
