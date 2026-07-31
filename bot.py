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

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
API_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 443))
WEBAPP_URL = "https://cyberxgift.ru/static/"
WEBHOOK_URL = "https://cyberxgift.ru/webhook"

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ========== MIDDLEWARE ДЛЯ CORS ==========

@middleware
async def cors_middleware(request, handler):
    """Добавление CORS заголовков для работы мини-приложения"""
    response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# ========== БАЗА ДАННЫХ ==========

async def init_db():
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS users 
                                (user_id INTEGER PRIMARY KEY, 
                                 last_spin_time INTEGER DEFAULT 0, 
                                 total_balance INTEGER DEFAULT 0,
                                 spins_count INTEGER DEFAULT 0,
                                 username TEXT DEFAULT '')''')
            await db.commit()
            logger.info("✅ База данных готова")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

async def get_user_balance(user_id: int) -> int:
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT total_balance FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else 0
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
        return 0

async def get_last_spin_time(user_id: int) -> int:
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT last_spin_time FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else 0
    except Exception as e:
        logger.error(f"Ошибка получения времени: {e}")
        return 0

# ========== ХЭНДЛЕРЫ КОМАНД ==========

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    first_name = message.from_user.first_name or "Пользователь"
    
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute(
                'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
                (user_id, username)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")
    
    logger.info(f"👤 Пользователь {user_id} (@{username}) запустил бота")
    
    web_app_button = InlineKeyboardButton(
        text="🎡 Крутить колесо",
        web_app=WebAppInfo(url=WEBAPP_URL + 'index.html')
    )
    
    balance_button = InlineKeyboardButton(
        text="💰 Баланс",
        callback_data="check_balance"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[web_app_button], [balance_button]]
    )
    
    await message.answer(
        f"🎰 Привет, {first_name}!\n\n"
        "Нажми на кнопку ниже, чтобы открыть колесо бонусов.\n"
        "⚡️ Крутить можно раз в 24 часа!\n\n"
        "🎁 Удачи!",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "check_balance")
async def check_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    await callback.answer(f"💰 Твой баланс: {balance} монет", show_alert=True)

# ========== ОБРАБОТЧИК MINI APP ==========

@dp.message(lambda message: message.web_app_data is not None)
async def handle_web_app_data(message: types.Message):
    user_id = message.from_user.id
    data = message.web_app_data.data
    
    logger.info(f"📩 ПОЛУЧЕН ЗАПРОС от {user_id}: {data[:100]}...")
    
    if data == 'get_status':
        await get_status_handler(message)
    elif data.startswith('spin_result:'):
        await spin_result_handler(message)
    else:
        await message.answer(json.dumps({'error': 'Неизвестный запрос'}))

async def get_status_handler(message: types.Message):
    user_id = message.from_user.id
    last_spin = await get_last_spin_time(user_id)
    now = int(time.time())
    time_diff = now - last_spin
    balance = await get_user_balance(user_id)
    
    if time_diff >= 86400:
        response = {'status': 'can_spin', 'wait_time': 0, 'balance': balance}
        logger.info(f"✅ Пользователь {user_id} МОЖЕТ крутить")
    else:
        wait_seconds = 86400 - time_diff
        response = {'status': 'wait', 'wait_time': wait_seconds, 'balance': balance}
        logger.info(f"⏳ Пользователь {user_id} должен ждать {wait_seconds} сек")
    
    await message.answer(json.dumps(response))
    logger.info(f"📤 ОТВЕТ ОТПРАВЛЕН для {user_id}")

async def spin_result_handler(message: types.Message):
    user_id = message.from_user.id
    data_str = message.web_app_data.data.replace('spin_result:', '')
    
    try:
        result_data = json.loads(data_str)
        prize_name = result_data.get('prize_name', '0')
        prize_value = result_data.get('value', 0)
        logger.info(f"🎯 Пользователь {user_id} выиграл: {prize_name} ({prize_value} монет)")
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга: {e}")
        await message.answer(json.dumps({'error': 'Ошибка данных'}))
        return
    
    last_spin = await get_last_spin_time(user_id)
    now = int(time.time())
    
    if now - last_spin < 86400:
        await message.answer(json.dumps({'error': 'Подождите 24 часа!'}))
        return
    
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute(
                'UPDATE users SET last_spin_time = ?, spins_count = spins_count + 1 WHERE user_id = ?',
                (now, user_id)
            )
            if prize_value > 0:
                await db.execute(
                    'UPDATE users SET total_balance = total_balance + ? WHERE user_id = ?',
                    (prize_value, user_id)
                )
            await db.commit()
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        await message.answer(json.dumps({'error': 'Ошибка БД'}))
        return
    
    new_balance = await get_user_balance(user_id)
    
    response = {
        'status': 'success',
        'prize': prize_name,
        'value': prize_value,
        'new_balance': new_balance
    }
    await message.answer(json.dumps(response))
    logger.info(f"🎉 Успешное вращение для {user_id}: +{prize_value} монет")
    
    if prize_value > 0:
        await message.answer(
            f"🎉 Поздравляю, {message.from_user.first_name}!\n\n"
            f"Ты выиграл **{prize_name}** монет!\n"
            f"💰 Твой баланс: {new_balance} монет\n\n"
            f"🔄 Следующее вращение будет доступно через 24 часа."
        )
    else:
        await message.answer(
            f"😅 Не повезло, {message.from_user.first_name}!\n"
            f"Ты выиграл **{prize_name}**\n\n"
            f"🔄 Попробуй снова через 24 часа!"
        )

# ========== WEBHOOK ==========

async def handle_webhook(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"❌ Ошибка webhook: {e}")
        return web.Response(status=500)

async def healthcheck(request):
    return web.Response(text="OK", status=200)

# ========== ЗАПУСК ==========

async def main():
    await init_db()
    
    # Создаем приложение с CORS
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get('/health', healthcheck)
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_static('/static/', path='static/', name='static', show_index=True)
    
    # Запускаем сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    
    logger.info(f"🌐 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика: {WEBAPP_URL}")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")
    
    # Устанавливаем webhook
    try:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки webhook: {e}")
        logger.info("🔄 Переключаемся на polling...")
        await bot.delete_webhook()
        await dp.start_polling(bot)
        return
    
    # Держим сервер активным
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
