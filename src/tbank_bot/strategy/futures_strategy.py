from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from tbank_bot.strategy.market_structure import StructureAnalysis, analyze_structure


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class MarketContext:
    trend: str  # up | down | range
    adx: float
    rsi: float
    atr: float
    atr_percentile: float
    volatility_regime: str  # low | normal | high
    momentum_score: float
    session_ok: bool
    funding_proxy: float
    score: float  # 0-100
    structure: StructureAnalysis | None = None


@dataclass
class TradeSignal:
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    context: MarketContext
    reason: str


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low = df["high"], df["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / tr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.rolling(period).mean()


def analyze_market_context(df: pd.DataFrame) -> MarketContext:
    """Анализ фона рынка: тренд, структура, волатильность, momentum."""
    from datetime import datetime, timezone

    work = df.copy()
    work["ema20"] = ema(work["close"], 20)
    work["ema50"] = ema(work["close"], 50)
    work["ema200"] = ema(work["close"], 200)
    work["rsi14"] = rsi(work["close"], 14)
    work["atr14"] = atr(work, 14)
    work["adx14"] = adx(work, 14)

    last = work.iloc[-1]
    adx_val = float(last["adx14"]) if not np.isnan(last["adx14"]) else 0.0
    rsi_val = float(last["rsi14"]) if not np.isnan(last["rsi14"]) else 50.0
    atr_val = float(last["atr14"]) if not np.isnan(last["atr14"]) else 0.0
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"]) if not np.isnan(last["ema200"]) else None

    atr_series = work["atr14"].dropna()
    atr_pct = float((atr_series <= atr_val).mean() * 100) if len(atr_series) > 20 else 50.0

    if atr_pct > 80:
        vol_regime = "high"
    elif atr_pct < 25:
        vol_regime = "low"
    else:
        vol_regime = "normal"

    if ema20 > ema50 * 1.001:
        trend = "up"
    elif ema20 < ema50 * 0.999:
        trend = "down"
    else:
        trend = "range"

    hour = datetime.now(timezone.utc).hour
    session_ok = 7 <= hour <= 20

    structure = analyze_structure(work, ema20, ema50, ema200)

    momentum = 0.0
    if trend == "up":
        momentum += 20
    elif trend == "down":
        momentum -= 20

    if structure.pattern == "bullish":
        momentum += 15
    elif structure.pattern == "bearish":
        momentum -= 15
    elif structure.pattern == "reversal_up":
        momentum += 10
    elif structure.pattern == "reversal_down":
        momentum -= 10

    if adx_val > 25:
        momentum += 12 if trend == "up" else (-12 if trend == "down" else 0)
    if 45 <= rsi_val <= 65:
        momentum += 8
    elif rsi_val > 72:
        momentum -= 12
    elif rsi_val < 28:
        momentum += 12

    if structure.volume_trend == "rising" and trend == "up":
        momentum += 5
    elif structure.volume_trend == "rising" and trend == "down":
        momentum -= 5

    score = 50.0
    score += min(adx_val, 40) * 0.5
    score += momentum
    if vol_regime == "high":
        score -= 10
    if not session_ok:
        score -= 15
    score = max(0.0, min(100.0, score))

    return MarketContext(
        trend=trend,
        adx=adx_val,
        rsi=rsi_val,
        atr=atr_val,
        atr_percentile=atr_pct,
        volatility_regime=vol_regime,
        momentum_score=momentum,
        session_ok=session_ok,
        funding_proxy=0.0,
        score=score,
        structure=structure,
    )


def generate_signal(
    df: pd.DataFrame,
    stop_atr_mult: float = 1.5,
    tp_atr_mult: float = 3.0,
) -> Optional[TradeSignal]:
    """Генерация long/short сигнала с учётом структуры рынка."""
    if len(df) < 60:
        return None

    ctx = analyze_market_context(df)
    price = float(df.iloc[-1]["close"])
    struct = ctx.structure

    direction = SignalDirection.FLAT
    confidence = ctx.score
    reason_parts = [
        f"ADX={ctx.adx:.1f}",
        f"RSI={ctx.rsi:.1f}",
        f"trend={ctx.trend}",
    ]
    if struct:
        reason_parts.append(struct.summary)

    if ctx.atr <= 0:
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason="Недостаточно данных ATR",
        )

    if ctx.volatility_regime == "high":
        confidence -= 12
        reason_parts.append("повышенная волатильность")

    long_ok = (
        ctx.trend in ("up", "range")
        and ctx.adx >= 18
        and 32 <= ctx.rsi <= 70
        and struct
        and struct.pattern in ("bullish", "reversal_up", "range")
        and struct.ema_stack in ("bullish", "mixed")
    )
    if long_ok and struct and struct.price_vs_support in ("at", "above"):
        confidence += 8

    short_ok = (
        ctx.trend in ("down", "range")
        and ctx.adx >= 18
        and 30 <= ctx.rsi <= 68
        and struct
        and struct.pattern in ("bearish", "reversal_down", "range")
        and struct.ema_stack in ("bearish", "mixed")
    )
    if short_ok and struct and struct.price_vs_resistance in ("at", "below"):
        confidence += 8

    if long_ok and (not short_ok or ctx.momentum_score > 0):
        direction = SignalDirection.LONG
        reason_parts.append("long: бычья структура + тренд")
    elif short_ok and (not long_ok or ctx.momentum_score < 0):
        direction = SignalDirection.SHORT
        reason_parts.append("short: медвежья структура + тренд")

    if direction == SignalDirection.LONG:
        sl_atr = price - ctx.atr * stop_atr_mult
        sl_struct = struct.support - ctx.atr * 0.2 if struct else sl_atr
        sl = min(sl_atr, sl_struct) if struct else sl_atr
        tp = price + ctx.atr * tp_atr_mult
        if struct and struct.resistance > price:
            tp = min(tp, struct.resistance)
    elif direction == SignalDirection.SHORT:
        sl_atr = price + ctx.atr * stop_atr_mult
        sl_struct = struct.resistance + ctx.atr * 0.2 if struct else sl_atr
        sl = max(sl_atr, sl_struct) if struct else sl_atr
        tp = price - ctx.atr * tp_atr_mult
        if struct and struct.support < price:
            tp = max(tp, struct.support)
    else:
        sl, tp = price, price

    confidence = max(0.0, min(100.0, confidence))

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
