import asyncio
import json
import os
import time
import logging
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

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@middleware
async def cors_middleware(request, handler):
    response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

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

# ========== КОМАНДЫ ==========

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    
    async with aiosqlite.connect('users.db') as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        await db.commit()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🎡 Крутить колесо", web_app=WebAppInfo(url=WEBAPP_URL + 'index.html'))],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")]
    ])
    await message.answer("🎰 Привет! Крути колесо раз в 24 часа!", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "balance")
async def balance(callback: types.CallbackQuery):
    balance = await get_balance(callback.from_user.id)
    await callback.answer(f"💰 Баланс: {balance} монет", show_alert=True)

# ========== ОБРАБОТЧИК МИНИ-ПРИЛОЖЕНИЯ ==========

@dp.message(lambda message: message.web_app_data is not None)
async def web_app_data(message: types.Message):
    user_id = message.from_user.id
    data = message.web_app_data.data
    logger.info(f"📩 ПОЛУЧЕН ЗАПРОС от {user_id}: {data[:50]}...")
    
    if data == 'get_status':
        last = await get_last_spin(user_id)
        now = int(time.time())
        balance = await get_balance(user_id)
        if now - last >= 86400:
            await message.answer(json.dumps({'status': 'can_spin', 'balance': balance}))
        else:
            await message.answer(json.dumps({'status': 'wait', 'wait_time': 86400 - (now - last), 'balance': balance}))
    
    elif data.startswith('spin_result:'):
        try:
            result = json.loads(data.replace('spin_result:', ''))
            prize = result.get('value', 0)
            async with aiosqlite.connect('users.db') as db:
                await db.execute('UPDATE users SET last_spin_time = ?, total_balance = total_balance + ? WHERE user_id = ?', 
                                (int(time.time()), prize, user_id))
                await db.commit()
            new_balance = await get_balance(user_id)
            await message.answer(json.dumps({'status': 'success', 'prize': result.get('label'), 'value': prize, 'new_balance': new_balance}))
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await message.answer(json.dumps({'status': 'error', 'message': 'Ошибка обработки'}))

# ========== WEBHOOK ==========

async def webhook_check(request):
    """Проверка для Telegram"""
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
    app.router.add_get('/webhook', webhook_check)  # для проверки Telegram
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_static('/static/', path='static/')
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    
    logger.info(f"🌐 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика: {WEBAPP_URL}")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")
    
    # Устанавливаем вебхук
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
