from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)


async def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=30) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram error %s: %s", resp.status, body)


def format_cycle_report(report: dict) -> str:
    ticker = report.get("ticker", "?")
    action = report.get("action", "?")
    direction = report.get("direction", "-")
    lines = [
        f"<b>{ticker}</b> · {action}",
        f"Направление: {direction} · confidence: {report.get('confidence', '-')}",
        f"Entry: {report.get('entry', '-')} · SL: {report.get('sl', '-')} · TP: {report.get('tp', '-')}",
        f"Trend: {report.get('trend', '-')} · ADX: {report.get('adx', '-')} · RSI: {report.get('rsi', '-')}",
        f"Risk: {report.get('risk_reason', report.get('reason', ''))}",
    ]
    if report.get("lots"):
        lines.append(f"Лоты: {report['lots']}")
    return "\n".join(lines)
