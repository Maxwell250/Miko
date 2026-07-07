from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd
from tinkoff.invest import (
    CandleInterval,
    Client,
    Future,
    OrderDirection,
    OrderType,
    Quotation,
    StopOrderDirection,
    StopOrderExpirationType,
    StopOrderType,
)
from tinkoff.invest.constants import INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX
from tinkoff.invest.utils import decimal_to_quotation, money_to_decimal, quotation_to_decimal

from tbank_bot.config import Settings


@dataclass
class OrderFill:
    order_id: str
    executed_price: float
    total_amount: float
    commission: float
    lots_executed: int


@dataclass
class FutureInstrument:
    figi: str
    uid: str
    ticker: str
    name: str
    lot: int
    min_price_increment: float
    expiration_date: datetime
    basic_asset: str


@dataclass
class PositionInfo:
    figi: str
    ticker: str
    direction: str  # long | short
    lots: int
    avg_price: float
    expected_yield: float


@dataclass
class AccountSnapshot:
    account_id: str
    total_amount: float
    available: float
    positions: List[PositionInfo]


def _q(value: float) -> Quotation:
    return decimal_to_quotation(value)


def _from_q(q: Quotation) -> float:
    return float(quotation_to_decimal(q))


class TBankBroker:
    """Обёртка над T-Bank Invest gRPC API для фьючерсов."""

    MOEX_PRIORITY = ("CNYRUBF", "SI", "RI", "MX", "CN", "BR", "GD", "SR")
    BLOCKED_TICKER_PARTS = ("PERP", "HOOD", "CRWD", "BTC", "ETH", "AAPL", "TSLA", "INTC", "GOOGL")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._target = INVEST_GRPC_API_SANDBOX if settings.tbank_sandbox else INVEST_GRPC_API
        self._margin_cache: dict[str, tuple[float, float]] = {}

    def _client(self) -> Client:
        return Client(
            self.settings.tbank_token,
            target=self._target,
            app_name=self.settings.tbank_app_name,
        )

    def get_account_id(self) -> str:
        if self.settings.tbank_account_id:
            return self.settings.tbank_account_id
        with self._client() as client:
            accounts = client.users.get_accounts()
            if not accounts.accounts:
                raise RuntimeError("Нет доступных счетов в T-Bank Invest")
            return accounts.accounts[0].id

    def list_futures(self, tickers: Optional[List[str]] = None) -> List[FutureInstrument]:
        with self._client() as client:
            resp = client.instruments.futures(instrument_status=2)
            items: List[FutureInstrument] = []
            now = datetime.utcnow()
            for f in resp.instruments:
                if f.expiration_date and f.expiration_date.replace(tzinfo=None) < now:
                    continue
                inst = _map_future(f)
                if tickers and inst.ticker.upper() not in [t.upper() for t in tickers]:
                    continue
                items.append(inst)
            return sorted(items, key=lambda x: x.expiration_date)

    def resolve_default_futures(self, settings: Settings | None = None) -> list[FutureInstrument]:
        """Ближайшие ликвидные фьючерсы Si (USD/RUB) и RTS (индекс)."""
        s = settings or self.settings
        all_f = self.list_futures()
        tickers = s.futures_ticker_list
        if tickers:
            return self.list_futures(tickers)

        selected: List[FutureInstrument] = []
        for prefix in ("SI", "RI"):
            candidates = [f for f in all_f if f.ticker.upper().startswith(prefix)]
            if candidates:
                selected.append(candidates[0])
        return selected or all_f[:2]

    @classmethod
    def _is_moex_futures_ticker(cls, ticker: str) -> bool:
        t = ticker.upper()
        if any(part in t for part in cls.BLOCKED_TICKER_PARTS):
            return False
        if t == "CNYRUBF":
            return True
        return t[:2] in ("SI", "RI", "MX", "CN", "BR", "GD", "SR", "NG", "SV", "ED")

    def _priority_key(self, ticker: str) -> int:
        t = ticker.upper()
        for i, prefix in enumerate(self.MOEX_PRIORITY):
            if t == prefix or t.startswith(prefix):
                return i
        return 99

    def resolve_tradeable_futures(
        self,
        settings: Settings | None = None,
        available: float | None = None,
    ) -> list[FutureInstrument]:
        """Ликвидные MOEX-фьючерсы, доступные по марже (без сотен API-запросов)."""
        s = settings or self.settings
        if s.futures_ticker_list:
            return self.list_futures(s.futures_ticker_list)[:3]

        candidates = [
            f for f in self.list_futures()
            if self._is_moex_futures_ticker(f.ticker)
        ]
        candidates.sort(key=lambda f: (self._priority_key(f.ticker), f.expiration_date))

        affordable: list[FutureInstrument] = []
        for inst in candidates[:20]:
            try:
                margin, _ = self.get_futures_margin(inst.uid)
            except Exception:
                continue
            if available is not None and margin > available * 0.95:
                continue
            affordable.append(inst)
            if len(affordable) >= 2:
                break

        if affordable:
            return affordable
        return self.resolve_default_futures(s)[:2]

    def get_candles(
        self,
        instrument_uid: str,
        interval: CandleInterval,
        days: int = 30,
    ) -> pd.DataFrame:
        from datetime import timedelta

        from tinkoff.invest.utils import now

        with self._client() as client:
            candles = client.get_all_candles(
                instrument_id=instrument_uid,
                from_=now() - timedelta(days=days),
                to=now(),
                interval=interval,
            )
            rows = []
            for c in candles:
                if not c.is_complete:
                    continue
                rows.append(
                    {
                        "time": c.time.replace(tzinfo=None),
                        "open": _from_q(c.open),
                        "high": _from_q(c.high),
                        "low": _from_q(c.low),
                        "close": _from_q(c.close),
                        "volume": c.volume,
                    }
                )
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            return df.sort_values("time").reset_index(drop=True)

    def get_last_price(self, instrument_uid: str) -> float:
        with self._client() as client:
            resp = client.market_data.get_last_prices(instrument_id=[instrument_uid])
            if not resp.last_prices:
                raise RuntimeError(f"Нет цены для {instrument_uid}")
            return _from_q(resp.last_prices[0].price)

    def get_trading_status_ok(self, instrument_uid: str) -> bool:
        with self._client() as client:
            st = client.market_data.get_trading_status(instrument_id=instrument_uid)
            return bool(st.market_order_available_flag and st.api_trade_available_flag)

    def get_account_snapshot(self, account_id: str) -> AccountSnapshot:
        with self._client() as client:
            portfolio = client.operations.get_portfolio(account_id=account_id)
            positions: List[PositionInfo] = []
            for p in portfolio.positions:
                if p.instrument_type != "futures":
                    continue
                qty = float(p.quantity.units + p.quantity.nano / 1e9)
                if qty == 0:
                    continue
                positions.append(
                    PositionInfo(
                        figi=p.figi,
                        ticker=p.ticker or p.figi,
                        direction="long" if qty > 0 else "short",
                        lots=int(abs(qty)),
                        avg_price=_from_q(p.average_position_price) if p.average_position_price else 0,
                        expected_yield=float(money_to_decimal(p.expected_yield)),
                    )
                )
            total = float(money_to_decimal(portfolio.total_amount_portfolio))
            avail = float(money_to_decimal(portfolio.total_amount_currencies))
            return AccountSnapshot(
                account_id=account_id,
                total_amount=total,
                available=avail,
                positions=positions,
            )

    def get_futures_margin(self, instrument_uid: str) -> tuple[float, float]:
        """initial margin on buy/sell per lot (RUB)."""
        if instrument_uid in self._margin_cache:
            return self._margin_cache[instrument_uid]

        import time

        from tinkoff.invest.exceptions import RequestError

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with self._client() as client:
                    m = client.instruments.get_futures_margin(instrument_id=instrument_uid)
                    initial_buy = float(money_to_decimal(m.initial_margin_on_buy))
                    initial_sell = float(money_to_decimal(m.initial_margin_on_sell))
                    initial = max(initial_buy, initial_sell)
                    step_amount = float(quotation_to_decimal(m.min_price_increment_amount))
                    result = (initial, step_amount)
                    self._margin_cache[instrument_uid] = result
                    return result
            except RequestError as exc:
                last_err = exc
                if "RESOURCE_EXHAUSTED" in str(exc) and attempt < 2:
                    time.sleep(35)
                    continue
                raise
        if last_err:
            raise last_err
        return 0.0, 1.0

    def post_market_order(
        self,
        account_id: str,
        instrument_uid: str,
        lots: int,
        direction: OrderDirection,
        order_id: str,
    ) -> OrderFill:
        with self._client() as client:
            resp = client.orders.post_order(
                instrument_id=instrument_uid,
                quantity=lots,
                direction=direction,
                account_id=account_id,
                order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=order_id,
            )
            executed = float(money_to_decimal(resp.executed_order_price)) if resp.executed_order_price else 0.0
            total = float(money_to_decimal(resp.total_order_amount)) if resp.total_order_amount else 0.0
            commission = float(money_to_decimal(resp.executed_commission)) if resp.executed_commission else 0.0
            return OrderFill(
                order_id=resp.order_id,
                executed_price=executed,
                total_amount=total,
                commission=commission,
                lots_executed=int(resp.lots_executed or lots),
            )

    def post_stop_loss(
        self,
        account_id: str,
        instrument_uid: str,
        lots: int,
        stop_price: float,
        direction: StopOrderDirection,
        order_id: str,
    ) -> str:
        with self._client() as client:
            resp = client.stop_orders.post_stop_order(
                instrument_id=instrument_uid,
                quantity=lots,
                stop_price=_q(stop_price),
                direction=direction,
                account_id=account_id,
                expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                stop_order_type=StopOrderType.STOP_ORDER_TYPE_STOP_LOSS,
                order_id=order_id,
            )
            return resp.stop_order_id

    def post_take_profit(
        self,
        account_id: str,
        instrument_uid: str,
        lots: int,
        take_price: float,
        direction: StopOrderDirection,
        order_id: str,
    ) -> str:
        with self._client() as client:
            resp = client.stop_orders.post_stop_order(
                instrument_id=instrument_uid,
                quantity=lots,
                price=_q(take_price),
                stop_price=_q(take_price),
                direction=direction,
                account_id=account_id,
                expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                stop_order_type=StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT,
                order_id=order_id,
            )
            return resp.stop_order_id

    def close_position_market(
        self,
        account_id: str,
        instrument_uid: str,
        lots: int,
        is_long: bool,
        order_id: str,
    ) -> str:
        direction = OrderDirection.ORDER_DIRECTION_SELL if is_long else OrderDirection.ORDER_DIRECTION_BUY
        fill = self.post_market_order(account_id, instrument_uid, lots, direction, order_id)
        return fill.order_id


def _map_future(f: Future) -> FutureInstrument:
    return FutureInstrument(
        figi=f.figi,
        uid=f.uid,
        ticker=f.ticker,
        name=f.name,
        lot=f.lot,
        min_price_increment=_from_q(f.min_price_increment),
        expiration_date=f.expiration_date.replace(tzinfo=None) if f.expiration_date else datetime.max,
        basic_asset=f.basic_asset or "",
    )
