from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from tbank_bot.config import Settings, TradingMode


@dataclass
class RuntimeOverrides:
    trading_mode: Optional[TradingMode] = None
    max_risk_per_trade_pct: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_open_positions: Optional[int] = None
    min_signal_confidence: Optional[float] = None
    default_stop_atr_mult: Optional[float] = None
    default_tp_atr_mult: Optional[float] = None
    loop_interval_sec: Optional[int] = None
    tbank_futures_tickers: Optional[str] = None
    notify_signals: bool = True

    def apply(self, base: Settings) -> Settings:
        updates = {
            k: v
            for k, v in self.__dict__.items()
            if v is not None and k != "notify_signals" and k in Settings.model_fields
        }
        return base.model_copy(update=updates)

    def to_display(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class BotController:
    """Управление циклом торговли и runtime-настройками из Telegram."""

    def __init__(self) -> None:
        self.running: bool = False
        self.overrides = RuntimeOverrides()
        self.last_results: List[dict] = []
        self.last_error: Optional[str] = None
        self.last_cycle_at: Optional[datetime] = None
        self.cycles_total: int = 0
        self.started_at: Optional[datetime] = None
        self._run_event = asyncio.Event()

    def get_effective_settings(self, base: Settings) -> Settings:
        return self.overrides.apply(base)

    def start(self) -> str:
        self.running = True
        self.started_at = datetime.utcnow()
        self._run_event.set()
        return "Торговый цикл запущен"

    def stop(self) -> str:
        self.running = False
        self._run_event.clear()
        return "Торговый цикл остановлен"

    def is_running(self) -> bool:
        return self.running

    async def wait_running(self) -> None:
        await self._run_event.wait()

    def set_override(self, key: str, value: Any) -> str:
        if not hasattr(self.overrides, key):
            return f"Неизвестный параметр: {key}"
        setattr(self.overrides, key, value)
        if key == "trading_mode" and isinstance(value, str):
            setattr(self.overrides, key, TradingMode(value))
        return f"{key} = {value}"

    def reset_overrides(self) -> None:
        self.overrides = RuntimeOverrides()

    def record_cycle(self, results: List[dict], error: Optional[str] = None) -> None:
        self.last_results = results
        self.last_error = error
        self.last_cycle_at = datetime.utcnow()
        self.cycles_total += 1

    async def sleep_interruptible(self, seconds: int) -> None:
        for _ in range(max(1, seconds)):
            if not self.running:
                return
            await asyncio.sleep(1)


_controller: Optional[BotController] = None


def get_controller() -> BotController:
    global _controller
    if _controller is None:
        _controller = BotController()
    return _controller
