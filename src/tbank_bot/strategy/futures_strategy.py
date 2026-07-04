from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


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
    funding_proxy: float  # placeholder for macro bias
    score: float  # 0-100


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
    """Анализ фона рынка: тренд, волатильность, momentum."""
    from datetime import datetime, timezone

    work = df.copy()
    work["ema20"] = ema(work["close"], 20)
    work["ema50"] = ema(work["close"], 50)
    work["rsi14"] = rsi(work["close"], 14)
    work["atr14"] = atr(work, 14)
    work["adx14"] = adx(work, 14)

    last = work.iloc[-1]
    adx_val = float(last["adx14"]) if not np.isnan(last["adx14"]) else 0.0
    rsi_val = float(last["rsi14"]) if not np.isnan(last["rsi14"]) else 50.0
    atr_val = float(last["atr14"]) if not np.isnan(last["atr14"]) else 0.0

    atr_series = work["atr14"].dropna()
    if len(atr_series) > 20:
        atr_pct = float((atr_series <= atr_val).mean() * 100)
    else:
        atr_pct = 50.0

    if atr_pct > 80:
        vol_regime = "high"
    elif atr_pct < 25:
        vol_regime = "low"
    else:
        vol_regime = "normal"

    if last["ema20"] > last["ema50"] * 1.001:
        trend = "up"
    elif last["ema20"] < last["ema50"] * 0.999:
        trend = "down"
    else:
        trend = "range"

    # MOEX основная сессия ~ 07:00-20:50 UTC (10:00-23:50 MSK approx, simplified)
    hour = datetime.now(timezone.utc).hour
    session_ok = 7 <= hour <= 20

    momentum = 0.0
    if trend == "up":
        momentum += 25
    elif trend == "down":
        momentum -= 25
    if adx_val > 25:
        momentum += 15 if trend == "up" else (-15 if trend == "down" else 0)
    if 45 <= rsi_val <= 65:
        momentum += 10
    elif rsi_val > 70:
        momentum -= 10
    elif rsi_val < 30:
        momentum += 10

    score = 50.0
    score += min(adx_val, 40) * 0.5
    score += momentum
    if vol_regime == "high":
        score -= 15
    if not session_ok:
        score -= 20
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
    )


def generate_signal(
    df: pd.DataFrame,
    stop_atr_mult: float = 1.5,
    tp_atr_mult: float = 3.0,
) -> Optional[TradeSignal]:
    """Генерация long/short сигнала на фьючерс."""
    if len(df) < 60:
        return None

    ctx = analyze_market_context(df)
    price = float(df.iloc[-1]["close"])

    if ctx.atr <= 0 or ctx.volatility_regime == "high":
        return TradeSignal(
            direction=SignalDirection.FLAT,
            confidence=0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            atr=ctx.atr,
            context=ctx,
            reason="Высокая волатильность или недостаточно данных",
        )

    direction = SignalDirection.FLAT
    confidence = ctx.score
    reason_parts = [f"ADX={ctx.adx:.1f}", f"RSI={ctx.rsi:.1f}", f"trend={ctx.trend}"]

    # Long: восходящий тренд + momentum
    if ctx.trend == "up" and ctx.adx >= 20 and 35 <= ctx.rsi <= 68:
        direction = SignalDirection.LONG
        confidence += 10
        reason_parts.append("EMA bullish crossover zone")

    # Short: нисходящий тренд
    elif ctx.trend == "down" and ctx.adx >= 20 and 32 <= ctx.rsi <= 65:
        direction = SignalDirection.SHORT
        confidence += 10
        reason_parts.append("EMA bearish structure")

    confidence = max(0.0, min(100.0, confidence))

    if direction == SignalDirection.LONG:
        sl = price - ctx.atr * stop_atr_mult
        tp = price + ctx.atr * tp_atr_mult
    elif direction == SignalDirection.SHORT:
        sl = price + ctx.atr * stop_atr_mult
        tp = price - ctx.atr * tp_atr_mult
    else:
        sl, tp = price, price

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
