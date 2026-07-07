from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from tbank_bot.strategy.futures_strategy import (
    MarketContext,
    SignalDirection,
    TradeSignal,
    analyze_market_context,
    rsi,
)
from tbank_bot.strategy.moex_session import analyze_moex_session


@dataclass
class RangeCorridor:
    support: float
    resistance: float
    mid: float
    width_pct: float
    position_pct: float  # 0=низ, 100=верх коридора
    is_narrow: bool
    adx: float
    summary: str


def detect_corridor(
    df: pd.DataFrame,
    lookback: int = 48,
    min_width_pct: float = 0.12,
    max_width_pct: float = 1.8,
    max_adx: float = 24.0,
) -> Optional[RangeCorridor]:
    """Узкий боковой коридор по последним свечам H1."""
    if len(df) < lookback + 10:
        return None

    window = df.tail(lookback)
    price = float(df.iloc[-1]["close"])
    support = float(window["low"].min())
    resistance = float(window["high"].max())
    if resistance <= support or price <= 0:
        return None

    mid = (support + resistance) / 2
    width = resistance - support
    width_pct = width / mid * 100
    position_pct = (price - support) / width * 100

    ctx = analyze_market_context(df)
    adx = ctx.adx
    is_narrow = min_width_pct <= width_pct <= max_width_pct and adx <= max_adx

    summary = (
        f"Коридор {support:.2f}–{resistance:.2f} ({width_pct:.2f}%), "
        f"цена на {position_pct:.0f}%, ADX={adx:.1f}"
    )

    return RangeCorridor(
        support=support,
        resistance=resistance,
        mid=mid,
        width_pct=width_pct,
        position_pct=position_pct,
        is_narrow=is_narrow,
        adx=adx,
        summary=summary,
    )


def generate_range_corridor_signal(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame | None = None,
    *,
    lookback: int = 48,
    entry_zone_pct: float = 22.0,
    min_width_pct: float = 0.12,
    max_width_pct: float = 1.8,
    max_adx: float = 24.0,
    stop_outside_pct: float = 0.15,
) -> Optional[TradeSignal]:
    """
    Боковик: покупка у нижней границы коридора, продажа у верхней.
    TP — середина или противоположная граница.
    """
    if len(df_h1) < 60:
        return None

    session = analyze_moex_session()
    ctx = analyze_market_context(df_h1)
    price = float(df_h1.iloc[-1]["close"])
    corridor = detect_corridor(
        df_h1,
        lookback=lookback,
        min_width_pct=min_width_pct,
        max_width_pct=max_width_pct,
        max_adx=max_adx,
    )

    if session.skip_reason:
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason=session.skip_reason,
        )

    if not corridor:
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason="Не удалось определить коридор",
        )

    if not corridor.is_narrow:
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason=(
                f"Нет узкого боковика: ширина {corridor.width_pct:.2f}%, "
                f"ADX={corridor.adx:.1f} (нужно ADX≤{max_adx})"
            ),
        )

    rsi_val = ctx.rsi
    m15_confirm = True
    m15_note = ""
    if df_m15 is not None and len(df_m15) >= 20:
        m15_rsi = float(rsi(df_m15["close"], 7).iloc[-1])
        m15_note = f"M15 RSI={m15_rsi:.0f}"
        if corridor.position_pct <= entry_zone_pct and m15_rsi > 55:
            m15_confirm = False
        if corridor.position_pct >= (100 - entry_zone_pct) and m15_rsi < 45:
            m15_confirm = False

    direction = SignalDirection.FLAT
    confidence = 58.0
    reason_parts = [corridor.summary, m15_note]

    buffer = corridor.width_pct / 100 * price * (stop_outside_pct / 100) * 10
    buffer = max(buffer, ctx.atr * 0.3)

    # Низ коридора → LONG
    if corridor.position_pct <= entry_zone_pct and rsi_val <= 48:
        direction = SignalDirection.LONG
        confidence += 18
        if corridor.position_pct <= entry_zone_pct / 2:
            confidence += 10
        reason_parts.append(f"LONG у поддержки ({corridor.position_pct:.0f}%)")
        sl = corridor.support - buffer
        tp = corridor.mid
        if corridor.width_pct < 0.8:
            tp = corridor.resistance - buffer * 0.5

    # Верх коридора → SHORT
    elif corridor.position_pct >= (100 - entry_zone_pct) and rsi_val >= 52:
        direction = SignalDirection.SHORT
        confidence += 18
        if corridor.position_pct >= 100 - entry_zone_pct / 2:
            confidence += 10
        reason_parts.append(f"SHORT у сопротивления ({corridor.position_pct:.0f}%)")
        sl = corridor.resistance + buffer
        tp = corridor.mid
        if corridor.width_pct < 0.8:
            tp = corridor.support + buffer * 0.5
    else:
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=30,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason=(
                f"Цена в середине коридора ({corridor.position_pct:.0f}%), "
                f"ждём низ≤{entry_zone_pct:.0f}% или верх≥{100-entry_zone_pct:.0f}%"
            ),
        )

    if not m15_confirm:
        confidence -= 12
        reason_parts.append("M15 не подтвердил")

    if session.in_main_session:
        confidence += 8
    elif session.in_evening_session:
        confidence -= 5

    confidence = max(0.0, min(100.0, confidence))

    if ctx.structure and ctx.structure.summary:
        reason_parts.append(ctx.structure.summary[:80])

    return TradeSignal(
        direction=direction,
        confidence=confidence,
        entry_price=price,
        stop_loss=sl,
        take_profit=tp,
        atr=ctx.atr,
        context=ctx,
        reason="; ".join(reason_parts),
    )
