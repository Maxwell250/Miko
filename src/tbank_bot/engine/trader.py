from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from tinkoff.invest import CandleInterval, OrderDirection, StopOrderDirection

from tbank_bot.broker.tbank import FutureInstrument, TBankBroker
from tbank_bot.config import Settings, TradingMode
from tbank_bot.risk.engine import RiskEngine
from tbank_bot.state.controller import BotController
from tbank_bot.strategy.futures_strategy import SignalDirection, generate_signal
from tbank_bot.telegram_notify import format_cycle_report, send_telegram_message

logger = logging.getLogger(__name__)


class FuturesTradingEngine:
    """Движок: анализ → риск → исполнение long/short на фьючерсах MOEX."""

    def __init__(
        self,
        base_settings: Settings,
        broker: TBankBroker,
        controller: BotController,
    ) -> None:
        self.base_settings = base_settings
        self.broker = broker
        self.controller = controller

    @property
    def settings(self) -> Settings:
        return self.controller.get_effective_settings(self.base_settings)

    def _risk(self) -> RiskEngine:
        return RiskEngine(self.settings)

    async def run_cycle(self, *, scan_only: bool = False) -> list[dict]:
        settings = self.settings
        if not settings.tbank_token:
            raise RuntimeError("TBANK_TOKEN не задан. Получите токен: tbank.ru/invest/open-api")

        account_id = self.broker.get_account_id()
        snapshot = self.broker.get_account_snapshot(account_id)
        instruments = self.broker.resolve_default_futures(settings)

        results: list[dict] = []
        risk = self._risk()
        for inst in instruments:
            result = await self._process_instrument(account_id, snapshot, inst, risk, scan_only=scan_only)
            results.append(result)
        return results

    async def _process_instrument(
        self,
        account_id: str,
        snapshot,
        inst: FutureInstrument,
        risk: RiskEngine,
        *,
        scan_only: bool = False,
    ) -> dict:
        settings = self.settings
        report: dict = {
            "ticker": inst.ticker,
            "time": datetime.utcnow().isoformat(),
            "action": "skip",
        }

        try:
            market_open = self.broker.get_trading_status_ok(inst.uid)
            if not market_open:
                if scan_only:
                    report["market_open"] = False
                else:
                    report["reason"] = "Торговля недоступна (биржа закрыта или инструмент недоступен)"
                    return report
            else:
                report["market_open"] = True

            df = self.broker.get_candles(inst.uid, CandleInterval.CANDLE_INTERVAL_HOUR, days=45)
            if df.empty or len(df) < 60:
                report["reason"] = "Недостаточно свечей"
                return report

            signal = generate_signal(
                df,
                stop_atr_mult=settings.default_stop_atr_mult,
                tp_atr_mult=settings.default_tp_atr_mult,
            )
            if not signal:
                report["reason"] = "Сигнал не сгенерирован"
                return report

            price = self.broker.get_last_price(inst.uid)
            signal.entry_price = price

            initial_margin, _ = self.broker.get_futures_margin(inst.uid)
            decision = risk.evaluate(signal, snapshot, initial_margin, price)

            report.update(
                {
                    "direction": signal.direction.value,
                    "confidence": round(signal.confidence, 1),
                    "entry": price,
                    "sl": round(signal.stop_loss, 4),
                    "tp": round(signal.take_profit, 4),
                    "context_score": round(signal.context.score, 1),
                    "trend": signal.context.trend,
                    "adx": round(signal.context.adx, 1),
                    "rsi": round(signal.context.rsi, 1),
                    "volatility": signal.context.volatility_regime,
                    "risk_ok": decision.allowed,
                    "risk_reason": decision.reason,
                    "lots": decision.lots,
                }
            )

            if not decision.allowed:
                report["reason"] = decision.reason
                return report

            if signal.direction == SignalDirection.FLAT:
                report["reason"] = signal.reason
                return report

            for pos in snapshot.positions:
                if pos.figi == inst.figi:
                    report["reason"] = f"Уже есть позиция {pos.direction} {pos.lots} лот."
                    return report

            if settings.trading_mode == TradingMode.PAPER or scan_only:
                report["action"] = "paper_signal"
                prefix = "СКАН" if scan_only else "PAPER"
                report["reason"] = f"{prefix}: {signal.direction.value.upper()} {decision.lots} лот."
                if scan_only and not market_open:
                    report["reason"] += " Биржа сейчас закрыта — ордер не выставляется."
                return report

            order_id = str(uuid4())
            if signal.direction == SignalDirection.LONG:
                order_dir = OrderDirection.ORDER_DIRECTION_BUY
                stop_dir = StopOrderDirection.STOP_ORDER_DIRECTION_SELL
            else:
                order_dir = OrderDirection.ORDER_DIRECTION_SELL
                stop_dir = StopOrderDirection.STOP_ORDER_DIRECTION_BUY

            oid = self.broker.post_market_order(
                account_id, inst.uid, decision.lots, order_dir, order_id
            )
            report["order_id"] = oid
            report["action"] = "opened"

            try:
                sl_id = str(uuid4())
                self.broker.post_stop_loss(
                    account_id,
                    inst.uid,
                    decision.lots,
                    signal.stop_loss,
                    stop_dir,
                    sl_id,
                )
                report["stop_order_id"] = sl_id
            except Exception as exc:
                logger.warning("Stop-loss не выставлен: %s", exc)
                report["stop_error"] = str(exc)

            report["reason"] = f"LIVE {signal.direction.value.upper()} {decision.lots} лот @ {price}"
            return report

        except Exception as exc:
            logger.exception("Ошибка %s", inst.ticker)
            report["reason"] = str(exc)
            report["action"] = "error"
            return report

    async def _notify(self, report: dict) -> None:
        settings = self.settings
        if not settings.telegram_enabled or not self.controller.overrides.notify_signals:
            return
        if report.get("action") not in ("paper_signal", "opened", "error"):
            return
        text = format_cycle_report(report)
        await send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            text,
        )

    async def run_forever(self) -> None:
        logger.info("Trading worker ready (ожидает ▶️ Старт в Telegram)")
        while True:
            await self.controller.wait_running()
            if not self.controller.is_running():
                await asyncio.sleep(1)
                continue

            settings = self.settings
            error: str | None = None
            try:
                results = await self.run_cycle()
                self.controller.record_cycle(results)
                for r in results:
                    logger.info(
                        "[%s] %s: %s",
                        r.get("ticker"),
                        r.get("action"),
                        r.get("reason", ""),
                    )
                    await self._notify(r)
            except Exception as exc:
                error = str(exc)
                logger.exception("Цикл ошибка: %s", exc)
                self.controller.record_cycle([], error=error)
                await self._notify({"action": "error", "reason": error, "ticker": "—"})

            await self.controller.sleep_interruptible(settings.loop_interval_sec)
