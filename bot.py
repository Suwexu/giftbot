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
WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://your-domain.railway.app/static/')
PORT = int(os.getenv('PORT', 8080))

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Инициализация базы данных
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
            logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

# ========== ХЭНДЛЕРЫ КОМАНД ==========

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    first_name = message.from_user.first_name or "Пользователь"
    
    # Регистрация пользователя
    try:
        async with aiosqlite.connect('users.db') as db:
            await db.execute(
                'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
                (user_id, username)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")
    
    logger.info(f"Пользователь {user_id} (@{username}) запустил бота")
    
    # Кнопка с мини-приложением
    web_app_button = InlineKeyboardButton(
        text="🎡 Крутить колесо",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )
    
    # Кнопка для проверки баланса
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
    
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT total_balance FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            balance = result[0] if result else 0
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
        balance = 0
    
    await callback.answer(f"💰 Твой баланс: {balance} монет", show_alert=True)

# ========== ОБРАБОТКА ЗАПРОСОВ ОТ WEB APP ==========

@dp.message(lambda message: message.web_app_data is not None)
async def handle_web_app_data(message: types.Message):
    user_id = message.from_user.id
    data = message.web_app_data.data
    
    logger.info(f"Получен запрос от пользователя {user_id}: {data[:50]}...")
    
    # Обработка запроса статуса
    if data == 'get_status':
        await get_status_handler(message)
    # Обработка результата вращения
    elif data.startswith('spin_result:'):
        await spin_result_handler(message)
    else:
        await message.answer(json.dumps({
            'error': 'Неизвестный запрос'
        }))

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
    except Exception as e:
        logger.error(f"Ошибка получения статуса: {e}")
        last_spin = 0
    
    now = int(time.time())
    time_diff = now - last_spin
    
    balance = await get_user_balance(user_id)
    
    if time_diff >= 86400:  # 24 часа
        response = {
            'status': 'can_spin',
            'wait_time': 0,
            'balance': balance
        }
    else:
        wait_seconds = 86400 - time_diff
        response = {
            'status': 'wait',
            'wait_time': wait_seconds,
            'balance': balance
        }
    
    await message.answer(json.dumps(response))

async def spin_result_handler(message: types.Message):
    user_id = message.from_user.id
    data_str = message.web_app_data.data.replace('spin_result:', '')
    
    try:
        result_data = json.loads(data_str)
        prize_id = result_data.get('prize_id')
        prize_name = result_data.get('prize_name')
        prize_value = result_data.get('value', 0)
    except json.JSONDecodeError:
        await message.answer(json.dumps({
            'error': 'Ошибка формата данных'
        }))
        return
    
    # Двойная проверка времени (защита от читов)
    try:
        async with aiosqlite.connect('users.db') as db:
            cursor = await db.execute(
                'SELECT last_spin_time FROM users WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
            last_spin = result[0] if result else 0
    except Exception as e:
        logger.error(f"Ошибка проверки времени: {e}")
        last_spin = 0
    
    now = int(time.time())
    
    if now - last_spin < 86400:
        wait_time = 86400 - (now - last_spin)
        await message.answer(json.dumps({
            'error': f'Подождите еще {wait_time // 3600} часов!'
        }))
        return
    
    # Обновление данных пользователя
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
        logger.error(f"Ошибка обновления данных: {e}")
        await message.answer(json.dumps({
            'error': 'Ошибка базы данных'
        }))
        return
    
    new_balance = await get_user_balance(user_id)
    
    # Отправляем ответ в Web App
    response = {
        'status': 'success',
        'prize': prize_name,
        'value': prize_value,
        'new_balance': new_balance
    }
    await message.answer(json.dumps(response))
    
    # Отправляем уведомление пользователю в чат
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
    
    logger.info(f"Пользователь {user_id} выиграл {prize_value} монет")

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

# ========== WEBHOOK HANDLER ==========

async def handle_webhook(request):
    """Обработка входящих запросов от Telegram"""
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}")
        return web.Response(status=500)

# ========== ЗАПУСК БОТА ==========

async def on_startup(app):
    """Действия при запуске сервера"""
    logger.info("🚀 Бот запускается...")
    
    # Инициализация БД
    await init_db()
    
    # Пытаемся установить webhook
    webhook_url = f"{WEBAPP_URL.rstrip('/static/')}/webhook"
    try:
        await bot.set_webhook(webhook_url)
        logger.info(f"✅ Webhook установлен на {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки webhook: {e}")
        logger.info("Бот будет работать в polling режиме")

async def on_shutdown(app):
    """Действия при остановке"""
    logger.info("🛑 Бот останавливается...")
    await bot.session.close()

async def main():
    """Основная функция запуска"""
    # Создаем приложение
    app = web.Application()
    
    # Добавляем маршруты
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_static('/static/', path='static/', name='static')
    
    # Добавляем обработчики старта/остановки
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Запуск сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    
    logger.info(f"🌐 Сервер запущен на порту {PORT}")
    logger.info(f"📁 Статика доступна по адресу: {WEBAPP_URL}")
    
    # Держим сервер работающим
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")