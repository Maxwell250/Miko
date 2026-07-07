from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from tbank_bot.strategy.futures_strategy import (
    MarketContext,
    SignalDirection,
    TradeSignal,
    analyze_market_context,
    atr,
    ema,
    generate_signal,
    rsi,
)
from tbank_bot.strategy.moex_session import MoexSessionInfo, analyze_moex_session


@dataclass
class RankedOpportunity:
    ticker: str
    signal: TradeSignal
    score: float
    margin: float
    affordable: bool
    regime: str
    mtf_aligned: bool
    session: MoexSessionInfo


def _detect_regime(ctx: MarketContext) -> str:
    if ctx.adx >= 25:
        return "trend"
    if ctx.adx < 18:
        return "range"
    return "transition"


def _mtf_alignment(df_h1: pd.DataFrame, df_m15: pd.DataFrame) -> tuple[bool, str]:
    """H1 — направление, M15 — точка входа."""
    if len(df_m15) < 30:
        return False, "мало M15 данных"

    h1 = analyze_market_context(df_h1)
    m15 = df_m15.copy()
    m15["ema9"] = ema(m15["close"], 9)
    m15["ema21"] = ema(m15["close"], 21)
    m15["rsi7"] = rsi(m15["close"], 7)
    last = m15.iloc[-1]
    m15_trend = "up" if last["ema9"] > last["ema21"] else "down"

    if h1.trend == "up" and m15_trend == "up" and float(last["rsi7"]) < 65:
        return True, "H1↑ M15↑ pullback"
    if h1.trend == "down" and m15_trend == "down" and float(last["rsi7"]) > 35:
        return True, "H1↓ M15↓ pullback"
    if h1.trend == "range":
        return True, "H1 range — вход по M15"
    return False, f"H1={h1.trend} M15={m15_trend} не совпали"


def _range_mean_reversion(df: pd.DataFrame, ctx: MarketContext, price: float) -> Optional[TradeSignal]:
    """Боковик MOEX: отскок от Bollinger в range-режиме."""
    if ctx.adx >= 22:
        return None

    sma = df["close"].rolling(20).mean().iloc[-1]
    std = df["close"].rolling(20).std().iloc[-1]
    if pd.isna(sma) or pd.isna(std) or std <= 0:
        return None

    upper = float(sma + 2 * std)
    lower = float(sma - 2 * std)
    mid = float(sma)
    band_width = (upper - lower) / mid * 100 if mid else 0

    if band_width < 0.3:
        return None

    atr_val = ctx.atr
    direction = SignalDirection.FLAT
    confidence = 55.0
    reason = f"range MR, BB width={band_width:.2f}%"

    if price <= lower * 1.002 and ctx.rsi < 42:
        direction = SignalDirection.LONG
        confidence += 15
        reason += "; отскок от нижней BB"
    elif price >= upper * 0.998 and ctx.rsi > 58:
        direction = SignalDirection.SHORT
        confidence += 15
        reason += "; отскок от верхней BB"
    else:
        return None

    if direction == SignalDirection.LONG:
        sl = price - atr_val * 1.2
        tp = mid
    else:
        sl = price + atr_val * 1.2
        tp = mid

    return TradeSignal(
        direction=direction,
        confidence=min(100, confidence),
        entry_price=price,
        stop_loss=sl,
        take_profit=tp,
        atr=atr_val,
        context=ctx,
        reason=reason,
    )


def generate_rf_signal(
    df_h1: pd.DataFrame,
    df_m15: pd.DataFrame | None = None,
    stop_atr_mult: float = 1.5,
    tp_atr_mult: float = 2.5,
    strict_mtf: bool = True,
) -> Optional[TradeSignal]:
    """
    Оптимизированная стратегия для MOEX:
    - тренд: следование + MTF
    - боковик: mean reversion
    - фильтр сессии РФ
    """
    if len(df_h1) < 60:
        return None

    session = analyze_moex_session()
    ctx = analyze_market_context(df_h1)
    price = float(df_h1.iloc[-1]["close"])
    regime = _detect_regime(ctx)

    base = generate_signal(df_h1, stop_atr_mult=stop_atr_mult, tp_atr_mult=tp_atr_mult)
    if not base:
        return None

    signal = base
    reason_parts = [signal.reason, f"режим={regime}", f"сессия={session.label}"]

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

    signal.confidence += session.quality * 0.08
    if session.in_evening_session:
        signal.confidence -= 8
        reason_parts.append("вечерка −8")

    mtf_ok, mtf_note = (True, "без M15")
    if df_m15 is not None and len(df_m15) >= 30:
        mtf_ok, mtf_note = _mtf_alignment(df_h1, df_m15)
        if mtf_ok:
            signal.confidence += 12
        else:
            signal.confidence -= 15

    reason_parts.append(mtf_note)

    if regime == "range" and signal.direction == SignalDirection.FLAT:
        mr = _range_mean_reversion(df_h1, ctx, price)
        if mr:
            signal = mr
            reason_parts.append("range mean-reversion")

    if regime == "trend" and signal.direction != SignalDirection.FLAT:
        if not mtf_ok:
            if strict_mtf:
                signal = TradeSignal(
                    direction=SignalDirection.FLAT,
                    confidence=signal.confidence - 20,
                    entry_price=price,
                    stop_loss=price,
                    take_profit=price,
                    atr=ctx.atr,
                    context=ctx,
                    reason="Тренд без MTF-подтверждения",
                )
            else:
                signal.confidence -= 10
                reason_parts.append("MTF не совпал (мягкий режим)")
        else:
            signal.confidence += 10
            reason_parts.append("тренд + MTF")

    # Волатильность: в мягком режиме не блокируем, только штрафуем
    if ctx.volatility_regime == "high" and regime != "trend":
        signal.confidence -= 12 if strict_mtf else 6

    signal.confidence = max(0.0, min(100.0, signal.confidence))
    signal.reason = "; ".join(reason_parts)

    return signal


def rank_opportunity(
    ticker: str,
    signal: TradeSignal,
    margin: float,
    available: float,
    session: MoexSessionInfo | None = None,
    mtf_aligned: bool = False,
) -> RankedOpportunity:
    session = session or analyze_moex_session()
    regime = _detect_regime(signal.context)
    affordable = margin > 0 and margin <= available * 0.92

    score = signal.confidence
    if affordable:
        score += 10
    else:
        score -= 40
    if mtf_aligned:
        score += 8
    if session.in_main_session:
        score += 5
    if signal.direction != SignalDirection.FLAT:
        rr = abs(signal.take_profit - signal.entry_price) / max(
            abs(signal.entry_price - signal.stop_loss), 1e-9
        )
        score += min(rr * 5, 15)

    # Приоритет ликвидным РФ инструментам
    prefix = ticker.upper()[:2]
    if prefix in ("SI", "RI", "MX", "CN"):
        score += 3

    return RankedOpportunity(
        ticker=ticker,
        signal=signal,
        score=score,
        margin=margin,
        affordable=affordable,
        regime=regime,
        mtf_aligned=mtf_aligned,
        session=session,
    )
