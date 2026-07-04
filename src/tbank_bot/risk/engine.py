from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from tbank_bot.broker.tbank import AccountSnapshot
from tbank_bot.config import Settings
from tbank_bot.strategy.futures_strategy import SignalDirection, TradeSignal


@dataclass
class RiskDecision:
    allowed: bool
    lots: int
    reason: str


@dataclass
class DailyRiskState:
    date: date
    starting_equity: float
    realized_pnl: float = 0.0

    @property
    def loss_pct(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return max(0.0, -self.realized_pnl / self.starting_equity * 100)


class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._daily: Optional[DailyRiskState] = None

    def update_daily(self, snapshot: AccountSnapshot) -> None:
        today = date.today()
        if not self._daily or self._daily.date != today:
            self._daily = DailyRiskState(date=today, starting_equity=snapshot.total_amount)

    def kill_switch_active(self) -> bool:
        if not self._daily:
            return False
        return self._daily.loss_pct >= self.settings.max_daily_loss_pct

    def evaluate(
        self,
        signal: TradeSignal,
        snapshot: AccountSnapshot,
        margin_per_lot: float,
        price: float,
        min_lot: int = 1,
    ) -> RiskDecision:
        self.update_daily(snapshot)

        if self.kill_switch_active():
            return RiskDecision(False, 0, f"Kill-switch: дневной убыток {self._daily.loss_pct:.2f}%")

        if signal.direction == SignalDirection.FLAT:
            return RiskDecision(False, 0, signal.reason or "Нет сигнала")

        if signal.confidence < self.settings.min_signal_confidence:
            return RiskDecision(
                False,
                0,
                f"Confidence {signal.confidence:.0f} < {self.settings.min_signal_confidence}",
            )

        if len(snapshot.positions) >= self.settings.max_open_positions:
            return RiskDecision(False, 0, "Достигнут лимит открытых позиций")

        for pos in snapshot.positions:
            if pos.figi and signal.context:
                pass  # позиция по инструменту проверяется снаружи

        stop_dist = abs(signal.entry_price - signal.stop_loss)
        if stop_dist <= 0:
            return RiskDecision(False, 0, "Нулевое расстояние до SL")

        risk_amount = snapshot.total_amount * (self.settings.max_risk_per_trade_pct / 100)
        # Упрощённый расчёт: risk ≈ stop_dist * point_value * lots
        # Для MOEX фьючерсов используем margin как прокси
        if margin_per_lot <= 0:
            margin_per_lot = snapshot.total_amount * 0.05

        max_lots_by_risk = max(min_lot, int(risk_amount / (stop_dist * min_lot * 0.01 + 1e-9)))
        max_lots_by_margin = max(min_lot, int(snapshot.available / margin_per_lot)) if margin_per_lot else min_lot

        lots = min(max_lots_by_risk, max_lots_by_margin, 10)
        lots = max(min_lot, lots)

        if lots < min_lot:
            return RiskDecision(False, 0, "Недостаточно средств для минимального лота")

        rr = abs(signal.take_profit - signal.entry_price) / stop_dist
        if rr < 1.5:
            return RiskDecision(False, 0, f"R:R {rr:.2f} < 1.5")

        return RiskDecision(True, lots, f"OK lots={lots}, R:R={rr:.2f}, risk={self.settings.max_risk_per_trade_pct}%")
