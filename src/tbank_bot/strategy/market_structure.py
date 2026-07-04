from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pandas as pd


@dataclass
class SwingPoints:
    highs: List[Tuple[int, float]]
    lows: List[Tuple[int, float]]


@dataclass
class StructureAnalysis:
    pattern: str  # bullish | bearish | range | reversal_up | reversal_down
    higher_highs: bool
    higher_lows: bool
    lower_highs: bool
    lower_lows: bool
    swing_high: float
    swing_low: float
    support: float
    resistance: float
    price_vs_support: str  # above | at | below
    price_vs_resistance: str
    bb_position: str  # upper | mid | lower
    volume_trend: str  # rising | falling | flat
    ema_stack: str  # bullish | bearish | mixed
    summary: str


def find_swings(df: pd.DataFrame, window: int = 3) -> SwingPoints:
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    for i in range(window, len(df) - window):
        h = float(df.iloc[i]["high"])
        l = float(df.iloc[i]["low"])
        if h >= df.iloc[i - window : i + window + 1]["high"].max():
            highs.append((i, h))
        if l <= df.iloc[i - window : i + window + 1]["low"].min():
            lows.append((i, l))
    return SwingPoints(highs=highs[-6:], lows=lows[-6:])


def _bb_position(close: float, upper: float, lower: float) -> str:
    mid = (upper + lower) / 2
    if close >= upper * 0.998:
        return "upper"
    if close <= lower * 1.002:
        return "lower"
    if close > mid:
        return "upper_mid"
    return "lower_mid"


def analyze_structure(df: pd.DataFrame, ema20: float, ema50: float, ema200: float | None = None) -> StructureAnalysis:
    swings = find_swings(df)
    price = float(df.iloc[-1]["close"])

    sh = max((h for _, h in swings.highs), default=price)
    sl = min((l for _, l in swings.lows), default=price)

    hh = hl = lh = ll = False
    if len(swings.highs) >= 2:
        hh = swings.highs[-1][1] > swings.highs[-2][1]
        lh = swings.highs[-1][1] < swings.highs[-2][1]
    if len(swings.lows) >= 2:
        hl = swings.lows[-1][1] > swings.lows[-2][1]
        ll = swings.lows[-1][1] < swings.lows[-2][1]

    if hh and hl:
        pattern = "bullish"
    elif lh and ll:
        pattern = "bearish"
    elif hl and not hh:
        pattern = "reversal_up"
    elif lh and not ll:
        pattern = "reversal_down"
    else:
        pattern = "range"

    lookback = df.tail(40)
    support = float(lookback["low"].min())
    resistance = float(lookback["high"].max())
    dist_sup = (price - support) / price * 100 if price else 0
    dist_res = (resistance - price) / price * 100 if price else 0

    if dist_sup < 0.3:
        pvs = "at"
    elif price > support:
        pvs = "above"
    else:
        pvs = "below"

    if dist_res < 0.3:
        pvr = "at"
    elif price < resistance:
        pvr = "below"
    else:
        pvr = "above"

    # Bollinger
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    std20 = df["close"].rolling(20).std().iloc[-1]
    if np.isnan(sma20) or np.isnan(std20):
        bb_pos = "mid"
    else:
        bb_pos = _bb_position(price, float(sma20 + 2 * std20), float(sma20 - 2 * std20))

    vol = df["volume"].tail(10)
    if len(vol) >= 5:
        v_recent = float(vol.tail(3).mean())
        v_prev = float(vol.iloc[-6:-3].mean()) if len(vol) >= 6 else v_recent
        if v_recent > v_prev * 1.15:
            vol_trend = "rising"
        elif v_recent < v_prev * 0.85:
            vol_trend = "falling"
        else:
            vol_trend = "flat"
    else:
        vol_trend = "flat"

    if ema200 is not None and not np.isnan(ema200):
        if ema20 > ema50 > ema200:
            ema_stack = "bullish"
        elif ema20 < ema50 < ema200:
            ema_stack = "bearish"
        else:
            ema_stack = "mixed"
    elif ema20 > ema50 * 1.001:
        ema_stack = "bullish"
    elif ema20 < ema50 * 0.999:
        ema_stack = "bearish"
    else:
        ema_stack = "mixed"

    pattern_ru = {
        "bullish": "бычья (HH+HL)",
        "bearish": "медвежья (LH+LL)",
        "range": "боковик",
        "reversal_up": "разворот вверх",
        "reversal_down": "разворот вниз",
    }.get(pattern, pattern)

    summary_parts = [
        f"Структура: {pattern_ru}",
        f"EMA: {ema_stack}",
        f"Поддержка {support:.2f} / Сопротивление {resistance:.2f}",
        f"Объём: {vol_trend}",
    ]
    if bb_pos in ("upper", "upper_mid"):
        summary_parts.append("Цена у верхней BB")
    elif bb_pos in ("lower", "lower_mid"):
        summary_parts.append("Цена у нижней BB")

    return StructureAnalysis(
        pattern=pattern,
        higher_highs=hh,
        higher_lows=hl,
        lower_highs=lh,
        lower_lows=ll,
        swing_high=sh,
        swing_low=sl,
        support=support,
        resistance=resistance,
        price_vs_support=pvs,
        price_vs_resistance=pvr,
        bb_position=bb_pos,
        volume_trend=vol_trend,
        ema_stack=ema_stack,
        summary=" · ".join(summary_parts),
    )
