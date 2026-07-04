from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="▶️ Старт"),
                KeyboardButton(text="⏹ Стоп"),
            ],
            [
                KeyboardButton(text="📊 Статус"),
                KeyboardButton(text="🔍 Скан"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
                KeyboardButton(text="📈 Позиции"),
            ],
            [
                KeyboardButton(text="❓ Помощь"),
            ],
        ],
        resize_keyboard=True,
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Paper", callback_data="set:mode:paper"),
                InlineKeyboardButton(text="🔴 Live", callback_data="set:mode:live"),
            ],
            [
                InlineKeyboardButton(text="Риск −", callback_data="set:risk:-0.5"),
                InlineKeyboardButton(text="Риск +", callback_data="set:risk:+0.5"),
            ],
            [
                InlineKeyboardButton(text="Conf −5", callback_data="set:conf:-5"),
                InlineKeyboardButton(text="Conf +5", callback_data="set:conf:+5"),
            ],
            [
                InlineKeyboardButton(text="Интервал −60с", callback_data="set:interval:-60"),
                InlineKeyboardButton(text="Интервал +60с", callback_data="set:interval:+60"),
            ],
            [
                InlineKeyboardButton(text="SL ATR −0.25", callback_data="set:sl:-0.25"),
                InlineKeyboardButton(text="SL ATR +0.25", callback_data="set:sl:+0.25"),
            ],
            [
                InlineKeyboardButton(text="TP ATR −0.5", callback_data="set:tp:-0.5"),
                InlineKeyboardButton(text="TP ATR +0.5", callback_data="set:tp:+0.5"),
            ],
            [
                InlineKeyboardButton(text="🔔 Сигналы вкл/выкл", callback_data="set:notify:toggle"),
            ],
            [
                InlineKeyboardButton(text="♻️ Сброс настроек", callback_data="set:reset"),
            ],
        ]
    )
