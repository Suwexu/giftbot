import asyncio
import json
import os
import time
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiohttp import web
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv('BOT_TOKEN')
WEBAPP_URL = os.getenv('WEBAPP_URL', '')
PORT = int(os.getenv('PORT', 8080))

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========
async def init_db():
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS users 
                                (user_id INTEGER PRIMARY KEY, 
                                 last_spin_time INTEGER DEFAULT 0, 
                                 total_balance INTEGER DEFAULT 0,
                                 spins_count INTEGER DEFAULT 0)''')
            await db.commit()
            logger.info("✅ База данных готова")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")

# ========== ХЭНДЛЕРЫ ==========

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute(
                'INSERT OR IGNORE INTO users (user_id) VALUES (?)',
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
    
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
        f"🎰 Привет, {message.from_user.first_name}!\n\n"
        "Нажми на кнопку ниже, чтобы открыть колесо бонусов.\n"
        "⚡️ Крутить можно раз в 24 часа!\n\n"
        "🎁 Удачи!",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "check_balance")
async def check_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT total_balance FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            balance = result[0] if result else 0
    except:
        balance = 0
    
    await callback.answer(f"💰 Твой баланс: {balance} монет", show_alert=True)

# ========== ОБРАБОТКА WEB APP ==========

@dp.message(lambda message: message.web_app_data is not None)
async def handle_web_app_data(message: types.Message):
    user_id = message.from_user.id
    data = message.web_app_data.data
    
    if data == 'get_status':
        await get_status_handler(message)
    elif data.startswith('spin_result:'):
        await spin_result_handler(message)
    else:
        await message.answer(json.dumps({'error': 'Неизвестный запрос'}))

async def get_status_handler(message: types.Message):
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT last_spin_time FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            last_spin = result[0] if result else 0
    except:
        last_spin = 0
    
    now = int(time.time())
    time_diff = now - last_spin
    balance = await get_user_balance(user_id)
    
    if time_diff >= 86400:
        response = {'status': 'can_spin', 'wait_time': 0, 'balance': balance}
    else:
        response = {'status': 'wait', 'wait_time': 86400 - time_diff, 'balance': balance}
    
    await message.answer(json.dumps(response))

async def spin_result_handler(message: types.Message):
    user_id = message.from_user.id
    data_str = message.web_app_data.data.replace('spin_result:', '')
    
    try:
        result_data = json.loads(data_str)
        prize_name = result_data.get('prize_name')
        prize_value = result_data.get('value', 0)
    except:
        await message.answer(json.dumps({'error': 'Ошибка данных'}))
        return
    
    # Проверка времени
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT last_spin_time FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            last_spin = result[0] if result else 0
    except:
        last_spin = 0
    
    now = int(time.time())
    
    if now - last_spin < 86400:
        await message.answer(json.dumps({
            'error': 'Подождите 24 часа!'
        }))
        return
    
    # Обновление данных
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
        logger.error(f"Ошибка обновления: {e}")
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
    
    # Уведомление в чат
    if prize_value > 0:
        await message.answer(
            f"🎉 Поздравляю! Ты выиграл **{prize_name}** монет!\n"
            f"💰 Баланс: {new_balance} монет"
        )
    else:
        await message.answer(f"😅 Выпало: **{prize_name}**\nПопробуй через 24 часа!")

async def get_user_balance(user_id: int) -> int:
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT total_balance FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else 0
    except:
        return 0

# ========== WEBHOOK ==========

async def handle_webhook(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)

# ========== HEALTHCHECK ==========

async def healthcheck(request):
    """Healthcheck для Railway"""
    return web.Response(text="OK", status=200)

# ========== ЗАПУСК ==========

async def main():
    # Инициализация БД
    await init_db()
    
    # Создаем веб-приложение
    app = web.Application()
    
    # Добавляем маршруты
    app.router.add_get('/health', healthcheck)
    app.router.add_post('/webhook', handle_webhook)
    
    # ПРАВИЛЬНАЯ НАСТРОЙКА СТАТИКИ
    # Указываем путь к папке static и разрешаем доступ ко всем файлам
    app.router.add_static('/static/', path='static/', name='static', show_index=True)
    
    # Запускаем сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    
    logger.info(f"🚀 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика: {WEBAPP_URL}")
    logger.info(f"❤️ Healthcheck: {WEBAPP_URL.rstrip('/static/')}/health")
    
    # Устанавливаем webhook
    webhook_url = f"{WEBAPP_URL.rstrip('/static/')}/webhook"
    try:
        await bot.set_webhook(webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
    except Exception as e:
        logger.warning(f"⚠️ Webhook не установлен: {e}")
        logger.info("🔄 Переключаемся на polling режим...")
        asyncio.create_task(dp.start_polling(bot))
    
    # Держим сервер активным
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")

if __name__ == '__main__':
    asyncio.run(main())