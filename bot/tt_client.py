import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

log = logging.getLogger(__name__)
from tastytrade import Session, Account, DXLinkStreamer
from tastytrade.instruments import NestedOptionChain
from tastytrade.dxfeed import Quote, Greeks
from tastytrade.metrics import get_market_metrics
from tastytrade.order import (
    NewOrder, OrderAction, OrderType, OrderTimeInForce,
    Leg, InstrumentType, PriceEffect,
)


@dataclass
class OptionSnapshot:
    option_type: str  # 'C' or 'P'
    strike_price: float
    delta: float
    bid: float
    ask: float


class TastytradeClient:
    def __init__(self, provider_secret, refresh_token, account_number, paper_account=None):
        self._provider_secret = provider_secret
        self._refresh_token = refresh_token
        self._account_number = paper_account or account_number
        self._is_paper = bool(paper_account)
        self.session = None
        self.account = None
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()

    @property
    def is_paper(self) -> bool:
        return self._is_paper

    def _run(self, coro):
        with self._lock:
            return self._loop.run_until_complete(coro)

    def connect(self):
        async def _connect():
            self.session = Session(
                provider_secret=self._provider_secret,
                refresh_token=self._refresh_token,
            )
            accounts = await Account.get(self.session)
            candidates = accounts if isinstance(accounts, list) else [accounts]
            self.account = next(
                (a for a in candidates if a.account_number == self._account_number),
                candidates[0],
            )
        self._run(_connect())

    def get_iv_rank(self, symbol):
        async def _get():
            metrics = await get_market_metrics(self.session, [symbol])
            if not metrics:
                return None
            rank = metrics[0].implied_volatility_index_rank
            return float(rank) * 100 if rank is not None else None
        return self._run(_get())

    def get_options_with_market_data(self, symbol: str, min_dte: int, max_dte: int) -> dict:
        """Returns {expiration_date: [OptionSnapshot, ...]} for expirations in DTE range."""
        async def _get():
            from anyio import fail_after, create_task_group
            from collections import defaultdict

            chains = await NestedOptionChain.get(self.session, symbol)
            if not chains:
                return {}

            today = date.today()
            sym_meta = {}
            for chain in chains:
                for exp in chain.expirations:
                    dte = (exp.expiration_date - today).days
                    if not (min_dte <= dte <= max_dte):
                        continue
                    for strike in exp.strikes:
                        sym_meta[strike.call_streamer_symbol] = (exp.expiration_date, float(strike.strike_price), 'C')
                        sym_meta[strike.put_streamer_symbol] = (exp.expiration_date, float(strike.strike_price), 'P')

            if not sym_meta:
                return {}

            streamer_symbols = list(sym_meta.keys())
            n = len(streamer_symbols)
            quotes: dict = {}
            greeks_data: dict = {}

            async def collect_quotes(streamer):
                try:
                    with fail_after(20):
                        async for event in streamer.listen(Quote):
                            quotes[event.event_symbol] = event
                            if len(quotes) >= n:
                                return
                except TimeoutError:
                    pass

            async def collect_greeks(streamer):
                try:
                    with fail_after(20):
                        async for event in streamer.listen(Greeks):
                            greeks_data[event.event_symbol] = event
                            if len(greeks_data) >= n:
                                return
                except TimeoutError:
                    pass

            async with DXLinkStreamer(self.session) as streamer:
                await streamer.subscribe(Quote, streamer_symbols)
                await streamer.subscribe(Greeks, streamer_symbols)
                async with create_task_group() as tg:
                    tg.start_soon(collect_quotes, streamer)
                    tg.start_soon(collect_greeks, streamer)

            result = defaultdict(list)
            for sym, (exp_date, strike_price, opt_type) in sym_meta.items():
                q = quotes.get(sym)
                g = greeks_data.get(sym)
                if q is None or g is None:
                    continue
                delta = float(g.delta) if g.delta is not None else 0.0
                result[exp_date].append(OptionSnapshot(
                    option_type=opt_type,
                    strike_price=strike_price,
                    delta=delta,
                    bid=float(q.bid_price),
                    ask=float(q.ask_price),
                ))

            return dict(result)

        return self._run(_get())

    def get_sim_marks(self, trades: list) -> dict:
        """Returns {trade_id: current_spread_mark} for sim trades using live DXFeed quotes."""
        async def _get():
            from anyio import fail_after
            from collections import defaultdict
            from bot.scanner import build_option_symbol
            from datetime import date as _date

            underlyings = {t['underlying'] for t in trades}
            occ_to_streamer: dict = {}
            for underlying in underlyings:
                try:
                    chains = await NestedOptionChain.get(self.session, underlying)
                    for chain in chains:
                        for exp in chain.expirations:
                            for strike in exp.strikes:
                                occ_to_streamer[strike.call] = strike.call_streamer_symbol
                                occ_to_streamer[strike.put] = strike.put_streamer_symbol
                except Exception:
                    pass

            trade_legs: dict = {}
            for trade in trades:
                underlying = trade['underlying']
                exp = _date.fromisoformat(trade['expiration'])
                legs = [
                    (build_option_symbol(underlying, exp, 'P', trade['short_put_strike']), +1),
                    (build_option_symbol(underlying, exp, 'P', trade['long_put_strike']), -1),
                ]
                if trade['strategy'] == 'iron_condor':
                    legs += [
                        (build_option_symbol(underlying, exp, 'C', trade['short_call_strike']), +1),
                        (build_option_symbol(underlying, exp, 'C', trade['long_call_strike']), -1),
                    ]
                streamer_legs = [(occ_to_streamer[occ], sign) for occ, sign in legs if occ in occ_to_streamer]
                trade_legs[trade['id']] = streamer_legs

            all_syms = list({ss for legs in trade_legs.values() for ss, _ in legs})
            if not all_syms:
                return {}

            quotes: dict = {}
            async with DXLinkStreamer(self.session) as streamer:
                await streamer.subscribe(Quote, all_syms)
                try:
                    with fail_after(15):
                        async for event in streamer.listen(Quote):
                            quotes[event.event_symbol] = event
                            if len(quotes) >= len(all_syms):
                                break
                except TimeoutError:
                    pass

            result = {}
            for trade_id, legs in trade_legs.items():
                mark = 0.0
                ok = True
                for ss, sign in legs:
                    q = quotes.get(ss)
                    if q is None:
                        ok = False
                        break
                    mark += sign * (float(q.bid_price) + float(q.ask_price)) / 2
                if ok:
                    result[trade_id] = round(mark, 4)
            return result

        return self._run(_get())

    def _stooq_closes(self, stooq_symbol: str, days: int) -> list[float]:
        import httpx
        try:
            resp = httpx.get(
                "https://stooq.com/q/d/l/",
                params={"s": stooq_symbol, "i": "d"},
                timeout=10,
            )
            resp.raise_for_status()
            closes = []
            for line in resp.text.strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) > 4 and parts[4].strip():
                    try:
                        closes.append(float(parts[4]))
                    except ValueError:
                        pass
            return closes[-days:]
        except Exception as exc:
            log.warning("Stooq fetch failed for %s: %s", stooq_symbol, exc)
            return []

    # Cache: {root_symbol: streamer_symbol} — refreshed once per session
    _futures_symbol_cache: dict = {}
    _futures_cache_date = None

    def _refresh_futures_symbols(self, symbols: list):
        """Look up front-month streamer symbols and cache them. Refreshes monthly."""
        from tastytrade.instruments import Future
        from datetime import date

        today = date.today()
        if self._futures_cache_date == today.replace(day=1) and all(s in self._futures_symbol_cache for s in symbols):
            return

        async def _lookup():
            dx_to_root = {}
            for root in symbols:
                try:
                    contracts = await Future.get(self.session, product_codes=[root])
                    active = [f for f in contracts if f.expiration_date >= today]
                    if not active:
                        continue
                    active.sort(key=lambda f: f.expiration_date)
                    dx_to_root[active[0].streamer_symbol] = root
                except Exception:
                    pass
            return dx_to_root

        result = self._run(_lookup())
        if result:
            self._futures_symbol_cache = result
            self._futures_cache_date = today.replace(day=1)

    def get_futures_prices(self, symbols: list) -> dict:
        """Real-time futures mid prices via DXFeed. symbols = ['ES','NQ','GC'] → {'ES': price, ...}"""
        self._refresh_futures_symbols(symbols)
        dx_to_root = {k: v for k, v in self._futures_symbol_cache.items() if v in symbols}
        if not dx_to_root:
            return {}

        async def _get():
            from anyio import fail_after
            quotes = {}
            async with DXLinkStreamer(self.session) as streamer:
                await streamer.subscribe(Quote, list(dx_to_root.keys()))
                try:
                    with fail_after(15):
                        async for event in streamer.listen(Quote):
                            bid = float(event.bid_price or 0)
                            ask = float(event.ask_price or 0)
                            if bid > 0 and ask > 0:
                                root = dx_to_root.get(event.event_symbol)
                                if root:
                                    quotes[root] = round((bid + ask) / 2, 4)
                            if len(quotes) >= len(dx_to_root):
                                break
                except TimeoutError:
                    log.warning('DXFeed quote timeout for %s', list(dx_to_root.values()))
            return quotes

        return self._run(_get())

    def get_vix(self) -> float | None:
        """Returns current VIX level via DXFeed Quote."""
        async def _get():
            from anyio import fail_after
            async with DXLinkStreamer(self.session) as streamer:
                await streamer.subscribe(Quote, ['$VIX.X'])
                try:
                    with fail_after(10):
                        event = await streamer.get_event(Quote)
                        mid = (float(event.bid_price) + float(event.ask_price)) / 2
                        return mid if mid > 0 else None
                except TimeoutError:
                    return None
        return self._run(_get())

    def get_sma_and_price(self, symbol: str, days: int = 20) -> tuple[float | None, float | None]:
        closes = self._stooq_closes(f"{symbol.lower()}.us", days)
        if len(closes) < days:
            return None, None
        return closes[-1], sum(closes) / len(closes)

    def get_account_balance(self):
        return self._run(self.account.get_balances(self.session))

    def get_positions(self):
        return self._run(self.account.get_positions(self.session))

    def get_order(self, order_id):
        return self._run(self.account.get_order(self.session, order_id))

    def cancel_order(self, order_id):
        return self._run(self.account.delete_order(self.session, order_id))

    def place_order(self, legs, price_credit):
        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[
                Leg(
                    instrument_type=InstrumentType.EQUITY_OPTION,
                    symbol=leg['symbol'],
                    quantity=leg['quantity'],
                    action=OrderAction[leg['action']],
                )
                for leg in legs
            ],
            price=Decimal(str(round(price_credit, 2))),
            price_effect=PriceEffect.CREDIT,
        )
        return self._run(self.account.place_order(self.session, order, dry_run=False))

    def place_debit_order(self, legs, price_debit):
        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[
                Leg(
                    instrument_type=InstrumentType.EQUITY_OPTION,
                    symbol=leg['symbol'],
                    quantity=leg['quantity'],
                    action=OrderAction[leg['action']],
                )
                for leg in legs
            ],
            price=Decimal(str(round(price_debit, 2))),
            price_effect=PriceEffect.DEBIT,
        )
        return self._run(self.account.place_order(self.session, order, dry_run=False))
