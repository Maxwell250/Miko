#!/usr/bin/env python3
"""T-Bank Invest futures bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# src/ на PYTHONPATH для tinkoff SDK и tbank_bot
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from tbank_bot.broker.tbank import TBankBroker
from tbank_bot.config import get_settings
from tbank_bot.engine.trader import FuturesTradingEngine
from tbank_bot.telegram_notify import format_cycle_report, send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tbank_bot")


async def main() -> None:
    settings = get_settings()
    broker = TBankBroker(settings)
    engine = FuturesTradingEngine(settings, broker)

    async def notify(report: dict) -> None:
        if not settings.telegram_enabled:
            return
        if report.get("action") in ("paper_signal", "opened", "error"):
            text = format_cycle_report(report)
            await send_telegram_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                text,
            )

    if "--once" in sys.argv:
        results = await engine.run_cycle()
        for r in results:
            print(r)
            await notify(r)
        return

    await engine.run_forever(notify_callback=notify)


if __name__ == "__main__":
    asyncio.run(main())
