from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tinkoff.invest import CandleInterval, OrderDirection, StopOrderDirection

from tbank_bot.broker.tbank import FutureInstrument, TBankBroker
from tbank_bot.config import Settings, TradingMode
from tbank_bot.risk.engine import RiskEngine
from tbank_bot.state.controller import BotController
from tbank_bot.state.position_tracker import CloseEvent, PositionTracker
from tbank_bot.strategy.futures_strategy import SignalDirection, generate_signal
from tbank_bot.telegram_notify import (
    format_close_notification,
    format_cycle_report,
    notify_user,
)

logger = logging.getLogger(__name__)

DEFAULT_TRACKER_PATH = Path("data/positions.json")


class FuturesTradingEngine:
    """Движок: анализ → риск → исполнение long/short на фьючерсах MOEX."""

    def __init__(
        self,
        base_settings: Settings,
        broker: TBankBroker,
        controller: BotController,
        tracker_path: Path | None = None,
    ) -> None:
        self.base_settings = base_settings
        self.broker = broker
        self.controller = controller
        self.tracker = PositionTracker(tracker_path or DEFAULT_TRACKER_PATH)

    @property
    def settings(self) -> Settings:
        return self.controller.get_effective_settings(self.base_settings)

    def _risk(self) -> RiskEngine:
        return RiskEngine(self.settings)

    def _structure_summary(self, signal) -> str:
        if signal.context.structure:
            return signal.context.structure.summary
        return ""

    async def _check_paper_exits(self, account_id: str) -> list[CloseEvent]:
        """Виртуальное закрытие paper-позиций по SL/TP."""
        closed: list[CloseEvent] = []
        for figi, pos in list(self.tracker.positions.items()):
            if pos.mode != "paper":
                continue
            try:
                price = self.broker.get_last_price(pos.uid)
            except Exception:
                continue

            hit_sl = (pos.direction == "long" and price <= pos.stop_loss) or (
                pos.direction == "short" and price >= pos.stop_loss
            )
            hit_tp = (pos.direction == "long" and price >= pos.take_profit) or (
                pos.direction == "short" and price <= pos.take_profit
            )
            if not hit_sl and not hit_tp:
                self.tracker.update_unrealized(
                    figi,
                    (price - pos.entry_price) * pos.lots
                    if pos.direction == "long"
                    else (pos.entry_price - price) * pos.lots,
                )
                continue

            exit_price = pos.stop_loss if hit_sl else pos.take_profit
            pnl = (
                (exit_price - pos.entry_price) * pos.lots
                if pos.direction == "long"
                else (pos.entry_price - exit_price) * pos.lots
            )
            now = datetime.utcnow()
            opened = datetime.fromisoformat(pos.opened_at)
            event = CloseEvent(
                figi=figi,
                ticker=pos.ticker,
                direction=pos.direction,
                lots=pos.lots,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                margin=pos.margin,
                notional=pos.notional,
                pnl=pnl,
                pnl_pct=(pnl / pos.margin * 100) if pos.margin else 0,
                opened_at=pos.opened_at,
                closed_at=now.isoformat(),
                mode="paper",
                structure_summary=pos.structure_summary,
                hold_minutes=int((now - opened).total_seconds() / 60),
            )
            closed.append(event)
            del self.tracker.positions[figi]
            self.tracker._save()

        return closed

    async def _sync_live_closes(self, snapshot, account_id: str) -> list[CloseEvent]:
        open_figis = {p.figi for p in snapshot.positions}
        exit_prices: dict[str, float] = {}

        for figi, pos in self.tracker.positions.items():
            if figi in open_figis:
                for p in snapshot.positions:
                    if p.figi == figi:
                        self.tracker.update_unrealized(figi, p.expected_yield)
                continue
            try:
                exit_prices[figi] = self.broker.get_last_price(pos.uid)
            except Exception:
                exit_prices[figi] = pos.entry_price

        return self.tracker.sync_with_portfolio(open_figis, exit_prices)

    async def _notify_closes(self, events: list[CloseEvent]) -> None:
        settings = self.settings
        if not settings.has_notification_target(self.controller.owner_chat_id):
            return
        if not self.controller.overrides.notify_signals:
            return
        for ev in events:
            await notify_user(settings, self.controller, format_close_notification(ev))

    async def run_cycle(self, *, scan_only: bool = False) -> list[dict]:
        settings = self.settings
        if not settings.tbank_token:
            raise RuntimeError("TBANK_TOKEN не задан. Получите токен: tbank.ru/invest/open-api")

        account_id = self.broker.get_account_id()
        snapshot = self.broker.get_account_snapshot(account_id)

        if not scan_only:
            paper_closed = await self._check_paper_exits(account_id)
            live_closed = await self._sync_live_closes(snapshot, account_id)
            await self._notify_closes(paper_closed + live_closed)

        instruments = self.broker.resolve_default_futures(settings)
        results: list[dict] = []
        risk = self._risk()
        for inst in instruments:
            result = await self._process_instrument(
                account_id, snapshot, inst, risk, scan_only=scan_only
            )
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
        struct_summary = ""
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
            struct_summary = self._structure_summary(signal)

            initial_margin, step_amount = self.broker.get_futures_margin(inst.uid)
            decision = risk.evaluate(signal, snapshot, initial_margin, price)

            margin_total = initial_margin * decision.lots
            notional = price * decision.lots * max(step_amount, 1)

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
                    "structure_summary": struct_summary,
                    "structure_pattern": signal.context.structure.pattern if signal.context.structure else "",
                    "risk_ok": decision.allowed,
                    "risk_reason": decision.reason,
                    "lots": decision.lots,
                    "margin": round(margin_total, 2),
                    "notional": round(notional, 2),
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

            if self.tracker.get(inst.figi):
                report["reason"] = "Позиция уже отслеживается ботом"
                return report

            if settings.trading_mode == TradingMode.PAPER or scan_only:
                report["action"] = "paper_signal"
                report["mode"] = "paper"
                prefix = "СКАН" if scan_only else "PAPER"
                report["reason"] = f"{prefix}: {signal.direction.value.upper()} {decision.lots} лот."
                if scan_only and not market_open:
                    report["reason"] += " Биржа закрыта — ордер не выставляется."
                elif not scan_only:
                    self.tracker.register_open(
                        figi=inst.figi,
                        uid=inst.uid,
                        ticker=inst.ticker,
                        direction=signal.direction.value,
                        lots=decision.lots,
                        entry_price=price,
                        margin=margin_total,
                        notional=notional,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        structure_summary=struct_summary,
                        mode="paper",
                    )
                return report

            # LIVE
            order_id = str(uuid4())
            if signal.direction == SignalDirection.LONG:
                order_dir = OrderDirection.ORDER_DIRECTION_BUY
                stop_dir = StopOrderDirection.STOP_ORDER_DIRECTION_SELL
            else:
                order_dir = OrderDirection.ORDER_DIRECTION_SELL
                stop_dir = StopOrderDirection.STOP_ORDER_DIRECTION_BUY

            fill = self.broker.post_market_order(
                account_id, inst.uid, decision.lots, order_dir, order_id
            )
            entry_price = fill.executed_price or price
            report["order_id"] = fill.order_id
            report["entry"] = entry_price
            report["commission"] = fill.commission
            report["notional"] = fill.total_amount or notional
            report["action"] = "opened"
            report["mode"] = "live"

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

            try:
                tp_id = str(uuid4())
                self.broker.post_take_profit(
                    account_id,
                    inst.uid,
                    decision.lots,
                    signal.take_profit,
                    stop_dir,
                    tp_id,
                )
                report["tp_order_id"] = tp_id
            except Exception as exc:
                logger.warning("Take-profit не выставлен: %s", exc)
                report["tp_error"] = str(exc)

            self.tracker.register_open(
                figi=inst.figi,
                uid=inst.uid,
                ticker=inst.ticker,
                direction=signal.direction.value,
                lots=decision.lots,
                entry_price=entry_price,
                margin=margin_total,
                notional=report["notional"],
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                structure_summary=struct_summary,
                mode="live",
                order_id=fill.order_id,
            )

            report["reason"] = (
                f"LIVE {signal.direction.value.upper()} {decision.lots} лот @ {entry_price:,.2f}"
            )
            return report

        except Exception as exc:
            logger.exception("Ошибка %s", inst.ticker)
            report["reason"] = str(exc)
            report["action"] = "error"
            return report

    async def _notify(self, report: dict) -> None:
        settings = self.settings
        if not settings.has_notification_target(self.controller.owner_chat_id):
            return
        if not self.controller.overrides.notify_signals:
            return
        if report.get("action") not in ("paper_signal", "opened", "error"):
            return
        text = format_cycle_report(report)
        await notify_user(settings, self.controller, text)

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
