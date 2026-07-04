#!/usr/bin/env python3
"""T-Bank Invest futures bot — entry point with Telegram dashboard."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from tbank_bot.broker.tbank import TBankBroker
from tbank_bot.config import get_settings
from tbank_bot.dashboard.bot import run_dashboard
from tbank_bot.engine.trader import FuturesTradingEngine
from tbank_bot.state.controller import get_controller
from tbank_bot.telegram_notify import format_cycle_report, notify_user

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tbank_bot")


async def run_once(settings, broker, controller) -> None:
    tracker_path = ROOT / "data" / "positions.json"
    engine = FuturesTradingEngine(settings, broker, controller, tracker_path=tracker_path)
    controller.start()
    results = await engine.run_cycle()
    controller.record_cycle(results)
    for r in results:
        print(r)
        if settings.has_notification_target(controller.owner_chat_id):
            await notify_user(settings, controller, format_cycle_report(r))


async def run_with_dashboard(settings, broker, controller) -> None:
    tracker_path = ROOT / "data" / "positions.json"
    engine = FuturesTradingEngine(settings, broker, controller, tracker_path=tracker_path)
    await asyncio.gather(
        engine.run_forever(),
        run_dashboard(settings, broker, controller),
    )


async def run_headless(settings, broker, controller) -> None:
    """Без Telegram — сразу стартует цикл."""
    controller.start()
    tracker_path = ROOT / "data" / "positions.json"
    engine = FuturesTradingEngine(settings, broker, controller, tracker_path=tracker_path)
    await engine.run_forever()


async def main() -> None:
    settings = get_settings()
    broker = TBankBroker(settings)
    controller = get_controller()

    if "--once" in sys.argv:
        await run_once(settings, broker, controller)
        return

    if settings.telegram_bot_token:
        logger.info("Режим: Telegram dashboard + trading worker")
        await run_with_dashboard(settings, broker, controller)
    else:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — headless режим, автостарт")
        await run_headless(settings, broker, controller)


if __name__ == "__main__":
    asyncio.run(main())
