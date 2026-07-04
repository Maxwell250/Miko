from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from tinkoff.invest import CandleInterval, OrderDirection, StopOrderDirection

from tbank_bot.broker.tbank import FutureInstrument, TBankBroker
from tbank_bot.config import Settings, TradingMode
from tbank_bot.risk.engine import RiskEngine
from tbank_bot.strategy.futures_strategy import SignalDirection, TradeSignal, generate_signal

logger = logging.getLogger(__name__)


class FuturesTradingEngine:
    """Движок: анализ → риск → исполнение long/short на фьючерсах MOEX."""

    def __init__(self, settings: Settings, broker: TBankBroker) -> None:
        self.settings = settings
        self.broker = broker
        self.risk = RiskEngine(settings)

    async def run_cycle(self) -> list[dict]:
        if not self.settings.tbank_token:
            raise RuntimeError("TBANK_TOKEN не задан. Получите токен: tbank.ru/invest/open-api")

        account_id = self.broker.get_account_id()
        snapshot = self.broker.get_account_snapshot(account_id)
        instruments = self.broker.resolve_default_futures()

        results: list[dict] = []
        for inst in instruments:
            result = await self._process_instrument(account_id, snapshot, inst)
            results.append(result)
        return results

    async def _process_instrument(
        self,
        account_id: str,
        snapshot,
        inst: FutureInstrument,
    ) -> dict:
        report: dict = {
            "ticker": inst.ticker,
            "time": datetime.utcnow().isoformat(),
            "action": "skip",
        }

        try:
            if not self.broker.get_trading_status_ok(inst.uid):
                report["reason"] = "Торговля недоступна (биржа закрыта или инструмент недоступен)"
                return report

            df = self.broker.get_candles(inst.uid, CandleInterval.CANDLE_INTERVAL_HOUR, days=45)
            if df.empty or len(df) < 60:
                report["reason"] = "Недостаточно свечей"
                return report

            signal = generate_signal(
                df,
                stop_atr_mult=self.settings.default_stop_atr_mult,
                tp_atr_mult=self.settings.default_tp_atr_mult,
            )
            if not signal:
                report["reason"] = "Сигнал не сгенерирован"
                return report

            price = self.broker.get_last_price(inst.uid)
            signal.entry_price = price

            initial_margin, _ = self.broker.get_futures_margin(inst.uid)
            decision = self.risk.evaluate(signal, snapshot, initial_margin, price)

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

            # Уже есть позиция по этому figi?
            for pos in snapshot.positions:
                if pos.figi == inst.figi:
                    report["reason"] = f"Уже есть позиция {pos.direction} {pos.lots} лот."
                    return report

            if self.settings.trading_mode == TradingMode.PAPER:
                report["action"] = "paper_signal"
                report["reason"] = f"PAPER: {signal.direction.value.upper()} {decision.lots} лот."
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

    async def run_forever(self, notify_callback=None) -> None:
        logger.info(
            "Старт бота mode=%s sandbox=%s interval=%ss",
            self.settings.trading_mode.value,
            self.settings.tbank_sandbox,
            self.settings.loop_interval_sec,
        )
        while True:
            try:
                results = await self.run_cycle()
                for r in results:
                    logger.info("[%s] %s: %s", r.get("ticker"), r.get("action"), r.get("reason", ""))
                    if notify_callback:
                        await notify_callback(r)
            except Exception as exc:
                logger.exception("Цикл ошибка: %s", exc)
                if notify_callback:
                    await notify_callback({"action": "error", "reason": str(exc)})
            await asyncio.sleep(self.settings.loop_interval_sec)
