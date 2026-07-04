from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

import aiohttp

from tbank_bot.telegram_html import escape_html

if TYPE_CHECKING:
    from tbank_bot.config import Settings
    from tbank_bot.state.controller import BotController
    from tbank_bot.state.position_tracker import CloseEvent, TrackedPosition

logger = logging.getLogger(__name__)


async def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=30) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram error %s: %s", resp.status, body)


def notification_chat_ids(settings: "Settings", controller: "BotController | None" = None) -> List[str]:
    ids: List[str] = []
    if settings.telegram_chat_id:
        ids.append(str(settings.telegram_chat_id))
    ids.extend(settings.admin_id_list)
    if controller and controller.owner_chat_id:
        oid = str(controller.owner_chat_id)
        if oid not in ids:
            ids.append(oid)
    return ids


async def notify_user(settings: "Settings", controller: "BotController | None", text: str) -> None:
    if not settings.telegram_bot_token:
        return
    for chat_id in notification_chat_ids(settings, controller):
        await send_telegram_message(settings.telegram_bot_token, chat_id, text)


def _dir_emoji(direction: str) -> str:
    return "🟢 LONG" if direction == "long" else "🔴 SHORT"


def format_open_notification(report: dict) -> str:
    direction = report.get("direction", "?")
    mode = escape_html(report.get("mode", "live").upper())
    ticker = escape_html(report.get("ticker", "?"))
    lines = [
        f"<b>📥 ОТКРЫТИЕ · {mode}</b>",
        f"<b>{ticker}</b> · {_dir_emoji(direction)}",
        f"Лоты: <b>{escape_html(report.get('lots', 1))}</b>",
        f"Вход: <b>{report.get('entry', 0):,.2f}</b>",
        f"ГО (маржа): <b>{report.get('margin', 0):,.0f} ₽</b>",
        f"Объём: <b>{report.get('notional', 0):,.0f} ₽</b>",
        f"SL: {escape_html(report.get('sl', '-'))} · TP: {escape_html(report.get('tp', '-'))}",
        f"Confidence: {escape_html(report.get('confidence', '-'))}%",
    ]
    if report.get("commission"):
        lines.append(f"Комиссия: {report['commission']:,.2f} ₽")
    if report.get("structure_summary"):
        lines.append(f"\n📐 <i>{escape_html(report['structure_summary'][:300])}</i>")
    if report.get("reason"):
        lines.append(f"\n{escape_html(report['reason'][:200])}")
    return "\n".join(lines)


def format_close_notification(event: "CloseEvent") -> str:
    sign = "+" if event.pnl >= 0 else ""
    emoji = "✅" if event.pnl >= 0 else "❌"
    ticker = escape_html(event.ticker)
    lines = [
        f"<b>{emoji} ЗАКРЫТИЕ · {escape_html(event.mode.upper())}</b>",
        f"<b>{ticker}</b> · {_dir_emoji(event.direction)}",
        f"Лоты: <b>{event.lots}</b> · время в сделке: {event.hold_minutes} мин",
        f"Вход: {event.entry_price:,.2f} → Выход: {event.exit_price:,.2f}",
        f"ГО: {event.margin:,.0f} ₽ · Объём: {event.notional:,.0f} ₽",
        f"<b>Результат: {sign}{event.pnl:,.2f} ₽ ({sign}{event.pnl_pct:.1f}%)</b>",
    ]
    if event.structure_summary:
        lines.append(f"\n📐 <i>{escape_html(event.structure_summary[:200])}</i>")
    return "\n".join(lines)


def format_paper_open(report: dict) -> str:
    direction = report.get("direction", "?")
    ticker = escape_html(report.get("ticker", "?"))
    lines = [
        f"<b>📋 СИГНАЛ (PAPER)</b>",
        f"<b>{ticker}</b> · {_dir_emoji(direction)}",
        f"Лоты: {escape_html(report.get('lots', 1))} · Вход: {report.get('entry', 0):,.2f}",
        f"ГО ~{report.get('margin', 0):,.0f} ₽ · SL {escape_html(report.get('sl'))} · TP {escape_html(report.get('tp'))}",
        f"Confidence: {escape_html(report.get('confidence', '-'))}%",
    ]
    if report.get("structure_summary"):
        lines.append(f"\n📐 <i>{escape_html(report['structure_summary'][:300])}</i>")
    return "\n".join(lines)


def format_cycle_report(report: dict) -> str:
    action = report.get("action", "?")
    if action == "opened":
        return format_open_notification(report)
    if action == "paper_signal":
        return format_paper_open(report)
    ticker = escape_html(report.get("ticker", "?"))
    direction = escape_html(report.get("direction", "-"))
    lines = [
        f"<b>{ticker}</b> · {escape_html(action)}",
        f"Направление: {direction} · confidence: {escape_html(report.get('confidence', '-'))}",
        f"Entry: {escape_html(report.get('entry', '-'))} · SL: {escape_html(report.get('sl', '-'))} · TP: {escape_html(report.get('tp', '-'))}",
        f"Trend: {escape_html(report.get('trend', '-'))} · ADX: {escape_html(report.get('adx', '-'))} · RSI: {escape_html(report.get('rsi', '-'))}",
        f"{escape_html(report.get('risk_reason', report.get('reason', '')))}",
    ]
    if report.get("structure_summary"):
        lines.append(f"📐 {escape_html(report['structure_summary'][:200])}")
    return "\n".join(lines)
