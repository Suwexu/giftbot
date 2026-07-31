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
PORT = int(os.getenv('PORT', 8080))

if not API_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========

async def init_db():
    """Инициализация базы данных"""
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
    """Получение баланса пользователя"""
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
    """Получение времени последнего вращения"""
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
    """Обработка команды /start"""
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
    
    logger.info(f"👤 Пользователь {user_id} (@{username}) запустил бота")
    
    # Кнопка с мини-приложением (НОВЫЙ URL!)
    web_app_button = InlineKeyboardButton(
        text="🎡 Крутить колесо",
        web_app=WebAppInfo(url="https://giftbot-production-6040.up.railway.app/static/index.html")
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
    """Обработка кнопки 'Баланс'"""
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    
    await callback.answer(f"💰 Твой баланс: {balance} монет", show_alert=True)

# ========== ГЛАВНЫЙ ОБРАБОТЧИК MINI APP ==========

@dp.message(lambda message: message.web_app_data is not None)
async def handle_web_app_data(message: types.Message):
    """Обработчик всех запросов из мини-приложения"""
    user_id = message.from_user.id
    data = message.web_app_data.data
    
    logger.info(f"📩 ПОЛУЧЕН ЗАПРОС от {user_id}: {data[:100]}...")
    
    if data == 'get_status':
        await get_status_handler(message)
    elif data.startswith('spin_result:'):
        await spin_result_handler(message)
    else:
        logger.warning(f"⚠️ Неизвестный запрос: {data}")
        await message.answer(json.dumps({
            'error': 'Неизвестный запрос'
        }))

async def get_status_handler(message: types.Message):
    """Возвращает статус: можно крутить или нет"""
    user_id = message.from_user.id
    
    last_spin = await get_last_spin_time(user_id)
    now = int(time.time())
    time_diff = now - last_spin
    balance = await get_user_balance(user_id)
    
    if time_diff >= 86400:
        response = {
            'status': 'can_spin',
            'wait_time': 0,
            'balance': balance
        }
        logger.info(f"✅ Пользователь {user_id} МОЖЕТ крутить")
    else:
        wait_seconds = 86400 - time_diff
        response = {
            'status': 'wait',
            'wait_time': wait_seconds,
            'balance': balance
        }
        logger.info(f"⏳ Пользователь {user_id} должен ждать {wait_seconds} сек")
    
    await message.answer(json.dumps(response))
    logger.info(f"📤 ОТВЕТ ОТПРАВЛЕН для {user_id}")

async def spin_result_handler(message: types.Message):
    """Обработка результата вращения колеса"""
    user_id = message.from_user.id
    data_str = message.web_app_data.data.replace('spin_result:', '')
    
    try:
        result_data = json.loads(data_str)
        prize_name = result_data.get('prize_name', '0')
        prize_value = result_data.get('value', 0)
        logger.info(f"🎯 Пользователь {user_id} выиграл: {prize_name} ({prize_value} монет)")
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга данных: {e}")
        await message.answer(json.dumps({
            'error': 'Ошибка формата данных'
        }))
        return
    
    last_spin = await get_last_spin_time(user_id)
    now = int(time.time())
    
    if now - last_spin < 86400:
        wait_time = 86400 - (now - last_spin)
        logger.warning(f"⚠️ Попытка читерства от {user_id} (осталось {wait_time} сек)")
        await message.answer(json.dumps({
            'error': f'Подождите еще {wait_time // 3600} часов!'
        }))
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
            logger.info(f"💾 Данные обновлены для {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления БД: {e}")
        await message.answer(json.dumps({
            'error': 'Ошибка базы данных'
        }))
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

# ========== HTTP СЕРВЕР ДЛЯ HEALTHCHECK ==========

async def healthcheck(request):
    """Healthcheck для Railway"""
    return web.Response(text="OK", status=200)

async def start_http_server():
    """Запуск HTTP сервера для healthcheck"""
    app = web.Application()
    app.router.add_get('/health', healthcheck)
    app.router.add_static('/static/', path='static/', name='static', show_index=True)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    logger.info(f"🌐 HTTP сервер запущен на порту {PORT}")
    logger.info(f"❤️ Healthcheck: /health")
    logger.info(f"📁 Статика доступна по адресу: https://giftbot-production-6040.up.railway.app/static/")

# ========== ЗАПУСК ==========

async def main():
    """Основная функция запуска"""
    # Инициализация БД
    await init_db()
    
    # Запускаем HTTP сервер в фоне
    asyncio.create_task(start_http_server())
    
    # Небольшая задержка, чтобы сервер точно запустился
    await asyncio.sleep(1)
    
    logger.info("🚀 Бот запускается в polling-режиме...")
    
    # Принудительно останавливаем старые polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка удаления webhook: {e}")
    
    await asyncio.sleep(1)
    
    logger.info("🔄 Запуск polling...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        await asyncio.sleep(5)
        logger.info("🔄 Повторная попытка запуска polling...")
        await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")