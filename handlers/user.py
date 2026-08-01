from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

import database as db
from keyboards import guest_app_inline_button, guest_app_reply_button, admin_panel_button
from config import ADMIN_IDS

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await db.upsert_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )

    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Привет, админ 👋\n\n"
            "Открой Mini App ниже, чтобы выдавать призы гостям, "
            "а через кнопку «⚙️ Админ-панель» — управлять призами и корзинками.",
            reply_markup=guest_app_reply_button(),
        )
        await message.answer("Панель управления:", reply_markup=admin_panel_button())
        return

    await message.answer(
        "Добро пожаловать в <b>CyberX Community</b> 🎮⚡\n\n"
        "Жми на кнопку ниже, чтобы забрать свой приз.",
        reply_markup=guest_app_reply_button(),
    )


@router.message(F.text == "🎁 CyberX Gifts")
async def fallback_button(message: Message):
    await message.answer("Открой Mini App кнопкой в поле ввода 👇", reply_markup=guest_app_inline_button())
