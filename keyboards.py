from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from config import PUBLIC_URL


def guest_app_inline_button() -> InlineKeyboardMarkup:
    """Инлайн-кнопка Mini App для гостя — можно слать в личку или закреплять в группе."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Открыть CyberX",
                    web_app=WebAppInfo(url=f"{PUBLIC_URL}/app/"),
                )
            ]
        ]
    )


def guest_app_reply_button() -> ReplyKeyboardMarkup:
    """Кнопка Mini App снизу, в поле ввода (только для личных чатов с ботом)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎁 CyberX Gifts", web_app=WebAppInfo(url=f"{PUBLIC_URL}/app/"))]
        ],
        resize_keyboard=True,
    )


def admin_panel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Админ-панель",
                    web_app=WebAppInfo(url=f"{PUBLIC_URL}/admin/"),
                )
            ]
        ]
    )
