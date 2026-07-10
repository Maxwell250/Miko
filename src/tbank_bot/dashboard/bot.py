from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from tbank_bot.broker.tbank import TBankBroker
from tbank_bot.config import Settings, TradingMode, get_settings
from tbank_bot.dashboard.formatters import format_help, format_positions, format_scan_results, format_status
from tbank_bot.dashboard.keyboards import main_menu_keyboard, settings_keyboard
from tbank_bot.engine.trader import FuturesTradingEngine
from tbank_bot.state.controller import BotController, get_controller

logger = logging.getLogger(__name__)

router = Router()


def _allowed(user_id: int, settings: Settings, username: str | None = None) -> bool:
    allowed_ids: list[str] = []
    if settings.telegram_chat_id:
        allowed_ids.append(str(settings.telegram_chat_id))
    allowed_ids.extend(settings.admin_id_list)

    has_restrictions = bool(allowed_ids or settings.allowed_username_list)
    if str(user_id) in allowed_ids:
        return True
    if username and settings.allowed_username_list:
        uname = username.lstrip("@").lower()
        if uname in settings.allowed_username_list:
            return True
    return not has_restrictions


def _deny_msg() -> str:
    return "⛔ Нет доступа. Этот бот доступен только владельцу."


def _check_access(message_or_query, settings: Settings) -> bool:
    user = message_or_query.from_user
    return _allowed(user.id, settings, user.username)


@router.message(Command("start"))
async def cmd_start(message: Message, settings: Settings) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    uid = message.from_user.id
    controller = get_controller()
    controller.set_owner_chat_id(uid)
    extra = ""
    if not settings.telegram_chat_id:
        extra = f"\n\n🆔 Ваш chat_id: <code>{uid}</code>"
    await message.answer(
        "👋 <b>T-Bank Futures Bot Dashboard</b>\n\n"
        "Используйте кнопки ниже или /help для команд.\n"
        "По умолчанию бот <b>остановлен</b> — нажмите ▶️ Старт."
        f"{extra}",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message, settings: Settings) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    await message.answer(format_help(), parse_mode="HTML")


@router.message(Command("start_bot"))
@router.message(F.text == "▶️ Старт")
async def cmd_start_bot(message: Message, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    msg = controller.start()
    await message.answer(f"✅ {msg}", parse_mode="HTML")


@router.message(Command("stop_bot"))
@router.message(F.text == "⏹ Стоп")
async def cmd_stop_bot(message: Message, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    msg = controller.stop()
    await message.answer(f"🛑 {msg}", parse_mode="HTML")


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(
    message: Message,
    settings: Settings,
    broker: TBankBroker,
    controller: BotController,
) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    snapshot = None
    try:
        if settings.tbank_token:
            account_id, snapshot = await asyncio.to_thread(
                _fetch_account_snapshot, broker
            )
    except Exception as exc:
        logger.warning("Status portfolio error: %s", exc)
    await message.answer(
        format_status(controller, settings, snapshot),
        parse_mode="HTML",
    )


def _fetch_account_snapshot(broker: TBankBroker):
    account_id = broker.get_account_id()
    return account_id, broker.get_account_snapshot(account_id)


@router.message(Command("positions"))
@router.message(F.text == "📈 Позиции")
async def cmd_positions(message: Message, settings: Settings, broker: TBankBroker) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    if not settings.tbank_token:
        await message.answer("⚠️ TBANK_TOKEN не задан")
        return
    try:
        _, snapshot = await asyncio.to_thread(_fetch_account_snapshot, broker)
        await message.answer(format_positions(snapshot), parse_mode="HTML")
    except Exception as exc:
        await message.answer(f"❌ {exc}")


@router.message(Command("scan"))
@router.message(F.text == "🔍 Скан")
async def cmd_scan(
    message: Message,
    settings: Settings,
    broker: TBankBroker,
    controller: BotController,
) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    await message.answer("🔍 Запускаю скан...")
    try:
        engine = FuturesTradingEngine(settings, broker, controller)
        results = await engine.run_cycle(scan_only=True)
        controller.record_cycle(results)
        await message.answer(format_scan_results(results), parse_mode="HTML")
    except Exception as exc:
        logger.exception("Scan error")
        await message.answer(f"❌ {exc}")


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    eff = controller.get_effective_settings(settings)
    text = format_status(controller, settings) + "\n\n<b>Быстрая настройка:</b>"
    await message.answer(text, reply_markup=settings_keyboard(), parse_mode="HTML")


@router.message(Command("mode"))
async def cmd_mode(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    if not command.args or command.args not in ("paper", "live"):
        await message.answer("Использование: /mode paper или /mode live")
        return
    controller.set_override("trading_mode", TradingMode(command.args))
    await message.answer(f"✅ Режим: <code>{command.args}</code>", parse_mode="HTML")


@router.message(Command("risk"))
async def cmd_risk(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    try:
        val = float(command.args)
        controller.set_override("max_risk_per_trade_pct", val)
        await message.answer(f"✅ Риск на сделку: {val}%")
    except (TypeError, ValueError):
        await message.answer("Использование: /risk 1.0")


@router.message(Command("confidence"))
async def cmd_confidence(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    try:
        val = float(command.args)
        controller.set_override("min_signal_confidence", val)
        await message.answer(f"✅ Min confidence: {val}")
    except (TypeError, ValueError):
        await message.answer("Использование: /confidence 65")


@router.message(Command("interval"))
async def cmd_interval(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    try:
        val = int(command.args)
        controller.set_override("loop_interval_sec", max(60, val))
        await message.answer(f"✅ Интервал: {val} сек")
    except (TypeError, ValueError):
        await message.answer("Использование: /interval 300")


@router.message(Command("tickers"))
async def cmd_tickers(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    val = (command.args or "").strip().upper()
    controller.set_override("tbank_futures_tickers", val)
    await message.answer(f"✅ Тикеры: {val or 'авто Si+RTS'}")


@router.message(Command("sl"))
async def cmd_sl(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    try:
        val = float(command.args)
        controller.set_override("default_stop_atr_mult", val)
        await message.answer(f"✅ SL ATR mult: {val}")
    except (TypeError, ValueError):
        await message.answer("Использование: /sl 1.5")


@router.message(Command("tp"))
async def cmd_tp(message: Message, command: CommandObject, settings: Settings, controller: BotController) -> None:
    if not _check_access(message, settings):
        await message.answer(_deny_msg())
        return
    try:
        val = float(command.args)
        controller.set_override("default_tp_atr_mult", val)
        await message.answer(f"✅ TP ATR mult: {val}")
    except (TypeError, ValueError):
        await message.answer("Использование: /tp 3.0")


@router.callback_query(F.data.startswith("set:"))
async def on_settings_callback(
    query: CallbackQuery,
    settings: Settings,
    controller: BotController,
) -> None:
    if not _check_access(query, settings):
        await query.answer("Нет доступа", show_alert=True)
        return

    eff = controller.get_effective_settings(settings)
    parts = query.data.split(":")
    action = parts[1]

    if action == "reset":
        controller.reset_overrides()
        await query.message.edit_text("♻️ Настройки сброшены к .env", reply_markup=settings_keyboard())
        await query.answer()
        return

    if action == "mode":
        mode = TradingMode(parts[2])
        controller.set_override("trading_mode", mode)
        await query.answer(f"Режим: {mode.value}")

    elif action == "risk":
        delta = float(parts[2])
        val = max(0.1, min(5.0, eff.max_risk_per_trade_pct + delta))
        controller.set_override("max_risk_per_trade_pct", val)
        await query.answer(f"Риск: {val}%")

    elif action == "conf":
        delta = float(parts[2])
        val = max(30.0, min(95.0, eff.min_signal_confidence + delta))
        controller.set_override("min_signal_confidence", val)
        await query.answer(f"Confidence: {val}")

    elif action == "interval":
        delta = int(parts[2])
        val = max(60, eff.loop_interval_sec + delta)
        controller.set_override("loop_interval_sec", val)
        await query.answer(f"Интервал: {val}с")

    elif action == "sl":
        delta = float(parts[2])
        val = max(0.5, eff.default_stop_atr_mult + delta)
        controller.set_override("default_stop_atr_mult", val)
        await query.answer(f"SL ATR: {val}")

    elif action == "tp":
        delta = float(parts[2])
        val = max(1.0, eff.default_tp_atr_mult + delta)
        controller.set_override("default_tp_atr_mult", val)
        await query.answer(f"TP ATR: {val}")

    elif action == "notify":
        current = controller.overrides.notify_signals
        controller.overrides.notify_signals = not current
        await query.answer(f"Сигналы: {'вкл' if not current else 'выкл'}")

    try:
        await query.message.edit_reply_markup(reply_markup=settings_keyboard())
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


class DashboardBot:
    def __init__(
        self,
        settings: Settings,
        broker: TBankBroker,
        controller: BotController,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.controller = controller
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dp = Dispatcher()
        self.dp["settings"] = settings
        self.dp["broker"] = broker
        self.dp["controller"] = controller
        self.dp.include_router(router)

    async def run(self) -> None:
        logger.info("Telegram dashboard started")
        await self.dp.start_polling(self.bot)


async def run_dashboard(settings: Settings, broker: TBankBroker, controller: BotController) -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан для дашборда")
    dash = DashboardBot(settings, broker, controller)
    await dash.run()
