from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

# MOEX основная сессия фьючерсов (МСК)
MSK = timezone(timedelta(hours=3))

# Лучшая ликвидность: 10:05–18:40 МСК
MAIN_OPEN = time(10, 5)
MAIN_CLOSE = time(18, 40)
# Вечерняя сессия: 19:05–23:40 (ниже качество)
EVENING_OPEN = time(19, 5)
EVENING_CLOSE = time(23, 40)


@dataclass
class MoexSessionInfo:
    is_trading_day: bool
    in_main_session: bool
    in_evening_session: bool
    quality: float  # 0-100
    label: str
    skip_reason: str = ""


def _now_msk() -> datetime:
    return datetime.now(MSK)


def analyze_moex_session(now: datetime | None = None) -> MoexSessionInfo:
    """Оценка торгового окна MOEX для РФ-фьючерсов."""
    now = now or _now_msk()
    wd = now.weekday()  # 0=Mon
    t = now.time()

    if wd >= 5:
        return MoexSessionInfo(
            is_trading_day=False,
            in_main_session=False,
            in_evening_session=False,
            quality=0,
            label="выходной",
            skip_reason="MOEX закрыта (суббота/воскресенье)",
        )

    # Понедельник: не торгуем первые 20 мин (гэпы)
    if wd == 0 and t < time(10, 20):
        return MoexSessionInfo(
            is_trading_day=True,
            in_main_session=False,
            in_evening_session=False,
            quality=10,
            label="открытие недели",
            skip_reason="Пауза после открытия понедельника (гэп-риск)",
        )

    in_main = MAIN_OPEN <= t <= MAIN_CLOSE
    in_evening = EVENING_OPEN <= t <= EVENING_CLOSE

    if in_main:
        # Пик ликвидности 11:00–17:00
        peak = time(11, 0) <= t <= time(17, 0)
        quality = 95 if peak else 80
        return MoexSessionInfo(
            is_trading_day=True,
            in_main_session=True,
            in_evening_session=False,
            quality=quality,
            label="основная сессия",
        )

    if in_evening:
        return MoexSessionInfo(
            is_trading_day=True,
            in_main_session=False,
            in_evening_session=True,
            quality=55,
            label="вечерняя сессия",
        )

    if t < MAIN_OPEN:
        return MoexSessionInfo(
            is_trading_day=True,
            in_main_session=False,
            in_evening_session=False,
            quality=0,
            label="до открытия",
            skip_reason="До начала основной сессии MOEX",
        )

    return MoexSessionInfo(
        is_trading_day=True,
        in_main_session=False,
        in_evening_session=False,
        quality=0,
        label="перерыв",
        skip_reason="Между сессиями MOEX",
    )
