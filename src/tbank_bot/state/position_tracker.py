from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    figi: str
    uid: str
    ticker: str
    direction: str
    lots: int
    entry_price: float
    margin: float
    notional: float
    stop_loss: float
    take_profit: float
    structure_summary: str
    opened_at: str
    mode: str  # live | paper
    last_unrealized_pnl: float = 0.0
    order_id: str = ""


@dataclass
class CloseEvent:
    figi: str
    ticker: str
    direction: str
    lots: int
    entry_price: float
    exit_price: float
    margin: float
    notional: float
    pnl: float
    pnl_pct: float
    opened_at: str
    closed_at: str
    mode: str
    structure_summary: str
    hold_minutes: int


class PositionTracker:
    """Отслеживание открытых ботом позиций и детекция закрытий."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.positions: dict[str, TrackedPosition] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("positions", []):
                pos = TrackedPosition(**item)
                self.positions[pos.figi] = pos
        except Exception as exc:
            logger.warning("Не удалось загрузить трекер позиций: %s", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"positions": [asdict(p) for p in self.positions.values()]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def register_open(
        self,
        *,
        figi: str,
        uid: str,
        ticker: str,
        direction: str,
        lots: int,
        entry_price: float,
        margin: float,
        notional: float,
        stop_loss: float,
        take_profit: float,
        structure_summary: str,
        mode: str,
        order_id: str = "",
    ) -> TrackedPosition:
        pos = TrackedPosition(
            figi=figi,
            uid=uid,
            ticker=ticker,
            direction=direction,
            lots=lots,
            entry_price=entry_price,
            margin=margin,
            notional=notional,
            stop_loss=stop_loss,
            take_profit=take_profit,
            structure_summary=structure_summary,
            opened_at=datetime.utcnow().isoformat(),
            mode=mode,
            order_id=order_id,
        )
        self.positions[figi] = pos
        self._save()
        return pos

    def update_unrealized(self, figi: str, pnl: float) -> None:
        if figi in self.positions:
            self.positions[figi].last_unrealized_pnl = pnl
            self._save()

    def sync_with_portfolio(
        self,
        open_figis: set[str],
        exit_prices: dict[str, float],
    ) -> List[CloseEvent]:
        """Возвращает события закрытия для позиций, исчезнувших из портфеля."""
        closed: List[CloseEvent] = []
        now = datetime.utcnow()
        for figi, pos in list(self.positions.items()):
            if figi in open_figis:
                continue

            exit_price = exit_prices.get(figi, pos.entry_price)
            if pos.direction == "long":
                pnl = (exit_price - pos.entry_price) * pos.lots
            else:
                pnl = (pos.entry_price - exit_price) * pos.lots

            if abs(pos.last_unrealized_pnl) > 0:
                pnl = pos.last_unrealized_pnl

            pnl_pct = (pnl / pos.margin * 100) if pos.margin > 0 else 0.0
            opened = datetime.fromisoformat(pos.opened_at)
            hold_min = int((now - opened).total_seconds() / 60)

            closed.append(
                CloseEvent(
                    figi=figi,
                    ticker=pos.ticker,
                    direction=pos.direction,
                    lots=pos.lots,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    margin=pos.margin,
                    notional=pos.notional,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    opened_at=pos.opened_at,
                    closed_at=now.isoformat(),
                    mode=pos.mode,
                    structure_summary=pos.structure_summary,
                    hold_minutes=hold_min,
                )
            )
            del self.positions[figi]

        if closed:
            self._save()
        return closed

    def get(self, figi: str) -> Optional[TrackedPosition]:
        return self.positions.get(figi)
