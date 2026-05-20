# bot/futures/topstep_client.py
"""TopstepX API client.

Built against the TopstepX OpenAPI spec at api.topstepx.com/swagger/v1/swagger.json.
Method signatures match what main.py / trader.py / manager.py already expect
from a broker client — same interface as Tastytrade and Tradovate clients.

If credentials are missing OR connect() fails, the bot falls through to the
Tastytrade / sim path (see main.py broker priority block).

Auth: POST /api/Auth/loginKey  body: {userName, apiKey} -> {token, success}
Token is then sent as Bearer auth header on all other endpoints.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx


log = logging.getLogger(__name__)

TOPSTEPX_BASE      = 'https://api.topstepx.com'
TOPSTEPX_MARKET_HUB = 'https://rtc.topstepx.com/hubs/market'


# ============================================================================
# Real-time market data stream (SignalR WebSocket)
# ============================================================================
class TopstepXMarketStream:
    """SignalR WebSocket subscription for live TopstepX quote ticks.

    Reads quotes for subscribed contracts into a shared in-memory dict so
    get_futures_prices() returns sub-second-fresh data instead of polling REST
    1-min bars. Falls back gracefully if the stream fails — caller can check
    is_connected() and use REST polling as backup.

    Method/event names are based on the ProjectX gateway convention (TopstepX
    is ProjectX-powered). If names differ, the stream will log errors and the
    bot stays on REST polling — no crashes.
    """

    def __init__(self, get_token):
        self._get_token = get_token   # callable returning current bearer token
        self._lock       = threading.Lock()
        self._quotes     = {}          # contractId -> last price float
        self._connection = None
        self._connected  = False
        self._subscribed = set()       # contract ids we want subscribed
        self._last_tick_at = 0.0       # epoch seconds — for staleness checks

    def connect(self) -> bool:
        try:
            from signalrcore.hub_connection_builder import HubConnectionBuilder
        except ImportError:
            log.error('signalrcore not installed — run `pip install signalrcore` to enable real-time prices')
            return False

        try:
            token = self._get_token()
            if not token:
                log.warning('No token — cannot start TopstepX SignalR stream')
                return False

            url = f'{TOPSTEPX_MARKET_HUB}?access_token={token}'
            self._connection = (HubConnectionBuilder()
                .with_url(url, options={'skip_negotiation': True, 'verify_ssl': True})
                .with_automatic_reconnect({
                    'type': 'raw',
                    'keep_alive_interval': 10,
                    'reconnect_interval':  5,
                    'max_attempts':        100,
                })
                .build())

            # Try multiple event names — different ProjectX versions use different ones
            for event in ('GatewayQuote', 'Quote', 'OnQuote', 'contractQuote'):
                self._connection.on(event, self._on_quote)

            self._connection.on_open(self._on_open)
            self._connection.on_close(self._on_close)
            self._connection.on_error(self._on_error)

            self._connection.start()
            return True
        except Exception:
            log.exception('TopstepX SignalR stream connect failed')
            return False

    def _on_open(self):
        with self._lock:
            self._connected = True
        log.info('TopstepX SignalR market stream OPEN')
        # Re-subscribe to all contracts (covers reconnects)
        for contract_id in list(self._subscribed):
            self._subscribe_internal(contract_id)

    def _on_close(self):
        with self._lock:
            self._connected = False
        log.warning('TopstepX SignalR market stream CLOSED')

    def _on_error(self, data):
        log.error('TopstepX SignalR error: %s', data)

    def _on_quote(self, args):
        """Quote handler — args structure varies by ProjectX version.

        Typical shapes:
          [contractId, {lastPrice, bid, ask, ...}]
          {'contractId': '...', 'lastPrice': ...}
        """
        try:
            contract_id = None
            payload     = None

            if isinstance(args, list) and len(args) >= 2:
                contract_id = str(args[0])
                payload     = args[1]
            elif isinstance(args, list) and len(args) == 1:
                payload     = args[0]
                if isinstance(payload, dict):
                    contract_id = str(payload.get('contractId') or payload.get('symbolId') or '')
            elif isinstance(args, dict):
                payload     = args
                contract_id = str(args.get('contractId') or args.get('symbolId') or '')

            if not (contract_id and isinstance(payload, dict)):
                return

            price = (payload.get('lastPrice') or payload.get('last')
                     or payload.get('price')  or payload.get('lastTradePrice'))
            if price is None:
                # Some feeds publish bid/ask only — average them as fallback
                bid = payload.get('bid'); ask = payload.get('ask')
                if bid and ask:
                    price = (float(bid) + float(ask)) / 2.0

            if price is not None:
                with self._lock:
                    self._quotes[contract_id] = float(price)
                    self._last_tick_at = time.time()
        except Exception:
            log.exception('TopstepX quote handler error: %s', args)

    def subscribe(self, contract_id: str):
        with self._lock:
            self._subscribed.add(contract_id)
        if self._connected:
            self._subscribe_internal(contract_id)

    def _subscribe_internal(self, contract_id: str):
        # Try several method names — ProjectX has used different ones across versions
        for method in ('SubscribeContractQuotes', 'SubscribeContractMarketData', 'Subscribe'):
            try:
                self._connection.send(method, [contract_id])
                log.info('TopstepX subscribed %s via %s', contract_id, method)
                return
            except Exception:
                continue
        log.error('TopstepX: no working subscribe method found for %s', contract_id)

    def get_price(self, contract_id: str):
        with self._lock:
            return self._quotes.get(str(contract_id))

    def is_connected(self) -> bool:
        # Treat as disconnected if no tick in 60s — feed went silent
        if not self._connected:
            return False
        if self._last_tick_at and (time.time() - self._last_tick_at) > 60:
            return False
        return True

    def stop(self):
        try:
            if self._connection:
                self._connection.stop()
        except Exception:
            pass
        self._connected = False

# Symbol -> contract searchText used by /api/Contract/search. The endpoint
# returns active contracts matching the search; we pick the front-month
# (activeContract=True) at connect() time.
# KEYS must match SYMBOLS in bot/futures/config.py ('ES', 'NQ') — the bot
# refers to instruments by those throughout. VALUES are the TopstepX search
# text. Update the contract codes each quarter as the front-month rolls.
_TOPSTEPX_SEARCH = {
    'ES': 'ESM6',   # E-mini S&P 500 — June 2026 front-month (TopStep uses 1-digit year)
    'NQ': 'NQM6',   # E-mini Nasdaq-100 — June 2026 front-month
}
# Quarterly rollover reminder:
#   March  -> H (e.g. ESH6, NQH6)
#   June   -> M (current: ESM6, NQM6)
#   Sept   -> U (e.g. ESU6, NQU6)
#   Dec    -> Z (e.g. ESZ6, NQZ6)
# Update this dict the day after rollover (typically ~8 days before contract expiry).

# TopstepX order type / side enums (from swagger schema)
_ORDER_TYPE_MARKET = 2     # 1=Limit, 2=Market, 3=Stop, 4=TrailingStop
_ORDER_TYPE_STOP   = 3     # resting stop-market order (server-side protective stop)
_ORDER_SIDE_BUY  = 0       # Bid (buyer)
_ORDER_SIDE_SELL = 1       # Ask (seller)


class TopstepXClient:
    """Broker client targeting TopStep funded/eval accounts via the TopstepX API."""

    def __init__(self, username: str, api_key: str, account_id: str):
        self.username    = username
        self.api_key     = api_key
        # Keep BOTH forms — TopstepX may return id as int or string; compare as both
        self.account_id_raw = str(account_id)
        self.account_id     = int(account_id) if str(account_id).isdigit() else account_id
        self._token      = None
        self._token_expires_at = 0.0   # epoch seconds
        self._contracts  = {}           # symbol -> resolved contract id (string)
        self._lock       = threading.Lock()
        self._http       = httpx.Client(timeout=15.0, base_url=TOPSTEPX_BASE)
        self._connected  = False
        self._last_error = None         # most recent error string for diagnostics
        self._stream     = None         # TopstepXMarketStream (SignalR) — set by connect()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        """Authenticate and resolve front-month contracts.

        Returns True on success, False on any failure (logged) — caller falls
        through to next broker option. Sets self._last_error with a human
        readable string on failure for the diagnostic /settings test endpoint.
        """
        self._last_error = None
        if not (self.username and self.api_key and self.account_id_raw):
            self._last_error = 'Missing credentials (username/api_key/account_id)'
            log.info('TopstepX: %s', self._last_error)
            return False

        try:
            with self._lock:
                resp = self._http.post('/api/Auth/loginKey', json={
                    'userName': self.username,
                    'apiKey':   self.api_key,
                })
                if resp.status_code != 200:
                    self._last_error = f'Auth HTTP {resp.status_code}: {resp.text[:200]}'
                    log.error('TopstepX: %s', self._last_error)
                    return False
                data = resp.json()
                token = data.get('token') or data.get('accessToken')
                if not token:
                    self._last_error = f'Auth response missing token: {data}'
                    log.error('TopstepX: %s', self._last_error)
                    return False
                self._token = token
                # JWTs from TopstepX typically last ~24h. Refresh proactively at 23h.
                self._token_expires_at = time.time() + 23 * 3600
                self._http.headers['Authorization'] = f'Bearer {self._token}'

            # Verify the account exists and is tradeable before resolving contracts
            try:
                bal = self._fetch_account_balance()
                log.info('TopstepX account found: balance=$%.2f canTrade=%s simulated=%s',
                         bal['netLiquidatingValue'], bal['canTrade'], bal['simulated'])
            except Exception as e:
                self._last_error = f'Account lookup failed: {e}'
                log.error('TopstepX: %s', self._last_error)
                return False

            # Resolve front-month contract id for each symbol in our universe
            for symbol, search_text in _TOPSTEPX_SEARCH.items():
                contract_id = self._resolve_contract(search_text)
                if contract_id:
                    self._contracts[symbol] = contract_id
                    log.info('TopstepX: resolved %s -> %s', symbol, contract_id)
                else:
                    log.warning('TopstepX: could not resolve %s contract', symbol)

            if not self._contracts:
                self._last_error = 'No tradeable contracts could be resolved'
                log.error('TopstepX: %s', self._last_error)
                return False

            self._connected = True

            # Start the SignalR market data stream for sub-second quotes.
            # If it fails, get_futures_prices falls back to REST polling automatically.
            try:
                self._stream = TopstepXMarketStream(get_token=lambda: self._token)
                if self._stream.connect():
                    for contract_id in self._contracts.values():
                        self._stream.subscribe(contract_id)
                    log.info('TopstepX SignalR stream subscribed for %d contracts', len(self._contracts))
                else:
                    log.warning('TopstepX SignalR not started — using REST polling for prices')
                    self._stream = None
            except Exception:
                log.exception('TopstepX SignalR setup error — falling back to REST polling')
                self._stream = None

            log.info('TopstepX connected (account=%s, contracts=%d, stream=%s)',
                     self.account_id, len(self._contracts),
                     'live' if self._stream else 'REST')
            return True
        except httpx.HTTPError as e:
            self._last_error = f'HTTP error: {e}'
            log.exception('TopstepX connect HTTP error')
            return False
        except Exception as e:
            self._last_error = f'Unexpected error: {e}'
            log.exception('TopstepX connect unexpected error')
            return False

    def disconnect(self):
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        try:
            self._http.close()
        except Exception:
            pass
        self._connected = False

    def _ensure_token(self):
        """Refresh the session token if it's near expiry."""
        if time.time() < self._token_expires_at - 300:  # 5-min safety margin
            return
        try:
            with self._lock:
                resp = self._http.post('/api/Auth/validate')
                resp.raise_for_status()
                data = resp.json()
                new_token = data.get('newToken')
                if new_token:
                    self._token = new_token
                    self._http.headers['Authorization'] = f'Bearer {new_token}'
                    self._token_expires_at = time.time() + 23 * 3600
        except Exception:
            log.exception('TopstepX token refresh failed — reconnecting')
            self.connect()

    def _resolve_contract(self, search_text: str) -> str | None:
        """Return the front-month contractId for search_text, or None.

        Tries live=True first (funded/live accounts). If that returns empty,
        retries with live=False (Combine/sim accounts use delayed feed).
        """
        for live_flag in (True, False):
            try:
                with self._lock:
                    resp = self._http.post('/api/Contract/search', json={
                        'searchText': search_text,
                        'live': live_flag,
                    })
                    if resp.status_code != 200:
                        log.warning('Contract/search %s live=%s -> HTTP %s: %s',
                                    search_text, live_flag, resp.status_code, resp.text[:300])
                        continue
                    payload = resp.json()
                    contracts = payload.get('contracts', []) if isinstance(payload, dict) else payload
                    if not contracts:
                        log.info('Contract/search %s live=%s returned 0 — trying next', search_text, live_flag)
                        continue
                    names = [f"{c.get('id')}={c.get('name', '?')} sym={c.get('symbolId','?')} active={c.get('activeContract')}" for c in contracts[:5]]
                    log.info('Contract/search %s live=%s -> %d results: %s',
                             search_text, live_flag, len(contracts), names)
                    # Prefer activeContract=True (front-month)
                    active = [c for c in contracts if c.get('activeContract')]
                    candidates = active if active else contracts
                    # Within candidates, prefer the FULL-size E-mini over the Micro.
                    # TopstepX symbolIds: F.US.EP (full ES) vs F.US.MES (Micro). The Micro
                    # base symbols always start with 'M' (MES, MNQ, MGC, MYM, M2K, etc.).
                    def _is_micro(c):
                        sym = (c.get('symbolId') or '').upper()
                        parts = sym.split('.')
                        if len(parts) >= 3:
                            return parts[2].startswith('M') and parts[2] != 'M6'  # M6 = Yen
                        return False
                    non_micro = [c for c in candidates if not _is_micro(c)]
                    pick = non_micro[0] if non_micro else candidates[0]
                    if pick.get('id') is not None:
                        log.info('  picked %s (sym=%s)', pick.get('id'), pick.get('symbolId'))
                        return str(pick['id'])
            except Exception:
                log.exception('TopstepX contract resolution failed for %s (live=%s)', search_text, live_flag)
                continue
        return None

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    def _fetch_account_balance(self) -> dict:
        """Internal — fetches balance without the _connected guard, used during connect()."""
        with self._lock:
            resp = self._http.post('/api/Account/search', json={'onlyActiveAccounts': True})
            if resp.status_code != 200:
                raise RuntimeError(f'/api/Account/search HTTP {resp.status_code}: {resp.text[:200]}')
            payload = resp.json()
            # Response may be {'accounts': [...]} or a raw array — handle both
            accounts = payload.get('accounts', []) if isinstance(payload, dict) else payload
            if not accounts:
                raise RuntimeError('No active accounts returned by TopstepX')
            # Compare account IDs as strings — TopstepX returns numeric ints,
            # but credentials are strings from the DB
            acct = next((a for a in accounts if str(a.get('id')) == self.account_id_raw), None)
            if acct is None:
                ids = [str(a.get('id')) for a in accounts]
                raise RuntimeError(f'Account {self.account_id_raw} not found — accounts on file: {ids}')

            balance = float(acct.get('balance', 0))
            return {
                'netLiquidatingValue': balance,
                'cashBalance':         balance,
                'openTradeEquity':     0.0,    # not exposed by TopstepX REST
                'realizedPnL':         0.0,    # tracked in /api/Trade/search separately
                'canTrade':            bool(acct.get('canTrade', False)),
                'simulated':           bool(acct.get('simulated', True)),
            }

    def get_account_balance(self) -> dict:
        """Returns shape compatible with main.job_snapshot:
            {'netLiquidatingValue', 'cashBalance', 'openTradeEquity', 'realizedPnL'}
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        return self._fetch_account_balance()

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    def get_futures_prices(self, symbols: list) -> dict:
        """Returns {symbol: last_price} for each requested futures symbol.

        Two paths:
          1. SignalR stream (preferred) — sub-second tick prices read from memory
          2. REST History fallback — 1-min bar polling, up to ~60s lag
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')

        # Path 1: SignalR stream — instant lookup
        if self._stream and self._stream.is_connected():
            out = {}
            for symbol in symbols:
                contract_id = self._contracts.get(symbol)
                if contract_id:
                    price = self._stream.get_price(contract_id)
                    if price is not None:
                        out[symbol] = price
            if out:
                return out
            log.warning('SignalR connected but no quotes yet — falling back to REST this scan')

        # Path 2: REST History fallback
        return self._fetch_prices_rest(symbols)

    def _fetch_prices_rest(self, symbols: list) -> dict:
        """REST History bar polling. Used as fallback when SignalR isn't streaming.

        Combine accounts use live=False (delayed/sim feed). Funded accounts use
        live=True. We try live=False first since the bot is currently on a Combine.
        """
        self._ensure_token()
        out = {}
        end_time   = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=5)
        for symbol in symbols:
            contract_id = self._contracts.get(symbol)
            if not contract_id:
                continue
            for live_flag in (False, True):
                try:
                    with self._lock:
                        resp = self._http.post('/api/History/retrieveBars', json={
                            'contractId':        contract_id,
                            'live':              live_flag,
                            'startTime':         start_time.isoformat().replace('+00:00', 'Z'),
                            'endTime':           end_time.isoformat().replace('+00:00', 'Z'),
                            'unit':              2,
                            'unitNumber':        1,
                            'limit':             5,
                            'includePartialBar': True,
                        })
                        if resp.status_code != 200:
                            continue
                        payload = resp.json()
                        bars = payload.get('bars', []) if isinstance(payload, dict) else payload
                        if bars:
                            last = bars[-1]
                            # TopStep bars use single-letter keys (t/o/h/l/c/v).
                            # Accept 'close' too in case the schema changes.
                            close = last.get('c', last.get('close'))
                            if close is not None:
                                out[symbol] = float(close)
                                break  # got data, stop trying other live_flag
                except Exception:
                    log.exception('TopstepX REST price fetch failed for %s (live=%s)', symbol, live_flag)
                    continue
        return out

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def place_order(self, symbol: str, action: str, contracts: int) -> dict:
        """Submit a market order. action is 'Buy' or 'Sell'.

        Returns {'orderId': ...} for compatibility with the existing trader.py
        which calls str(resp.get('orderId', 'UNKNOWN')).
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        contract_id = self._contracts.get(symbol)
        if not contract_id:
            raise RuntimeError(f'TopstepX: no resolved contract for {symbol}')

        side = _ORDER_SIDE_BUY if action == 'Buy' else _ORDER_SIDE_SELL
        with self._lock:
            resp = self._http.post('/api/Order/place', json={
                'accountId':  self.account_id,
                'contractId': contract_id,
                'type':       _ORDER_TYPE_MARKET,
                'side':       side,
                'size':       int(contracts),
            })
            if resp.status_code != 200:
                raise RuntimeError(f'/api/Order/place HTTP {resp.status_code}: {resp.text[:300]}')
            data = resp.json()
            # success flag is optional — some endpoints just return orderId; check both
            if data.get('success') is False:
                err = data.get('errorMessage') or data.get('error') or 'unknown error'
                raise RuntimeError(f'TopstepX order rejected: {err}')
            order_id = data.get('orderId') or data.get('id') or data.get('order', {}).get('id')
            if not order_id:
                raise RuntimeError(f'TopstepX order placed but no orderId in response: {data}')
            return {'orderId': str(order_id)}

    def place_stop_order(self, symbol: str, action: str, contracts: int, stop_price: float) -> dict:
        """Place a resting server-side STOP order (the protective backstop).

        action is the CLOSING side: 'Sell' to protect a long, 'Buy' to protect a short.
        Fills at the broker the instant price trips stop_price — caps fast-move losses
        that the 5s synthetic-stop poll can't catch.
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        contract_id = self._contracts.get(symbol)
        if not contract_id:
            raise RuntimeError(f'TopstepX: no resolved contract for {symbol}')
        side = _ORDER_SIDE_BUY if action == 'Buy' else _ORDER_SIDE_SELL
        with self._lock:
            resp = self._http.post('/api/Order/place', json={
                'accountId':  self.account_id,
                'contractId': contract_id,
                'type':       _ORDER_TYPE_STOP,
                'side':       side,
                'size':       int(contracts),
                'stopPrice':  round(float(stop_price), 2),
            })
            if resp.status_code != 200:
                raise RuntimeError(f'/api/Order/place (stop) HTTP {resp.status_code}: {resp.text[:300]}')
            data = resp.json()
            if data.get('success') is False:
                raise RuntimeError(f'TopstepX stop order rejected: {data.get("errorMessage") or data.get("error")}')
            order_id = data.get('orderId') or data.get('id') or data.get('order', {}).get('id')
            if not order_id:
                raise RuntimeError(f'TopstepX stop order placed but no orderId in response: {data}')
            return {'orderId': str(order_id)}

    def cancel_order(self, order_id) -> bool:
        """Cancel a working order (e.g. the resting protective stop) by id.

        Returns True on success or if the order is already gone (filled/cancelled).
        """
        if not self._connected or not order_id:
            return False
        self._ensure_token()
        try:
            with self._lock:
                resp = self._http.post('/api/Order/cancel',
                                       json={'accountId': self.account_id, 'orderId': int(order_id)})
            if resp.status_code != 200:
                # Already filled/cancelled is benign — the order is gone either way
                log.info('TopstepX cancel_order %s -> HTTP %s (likely already gone)', order_id, resp.status_code)
                return False
            data = resp.json()
            if data.get('success') is False:
                log.info('TopstepX cancel_order %s -> %s (likely already gone)', order_id, data.get('errorMessage'))
                return False
            return True
        except Exception:
            log.exception('TopstepX cancel_order %s failed', order_id)
            return False

    # ------------------------------------------------------------------ #
    # Position management — flatten + reconciliation
    # ------------------------------------------------------------------ #
    def get_open_position(self, symbol: str) -> dict | None:
        """Return the live open position for `symbol` from TopStep, or None.

        Shape: {'size': int, 'side': 'long'|'short', 'avgPrice': float, 'contractId': str}
        Used to reconcile bot DB against broker reality before opening/closing.
        """
        if not self._connected:
            return None
        self._ensure_token()
        contract_id = self._contracts.get(symbol)
        try:
            with self._lock:
                resp = self._http.post('/api/Position/searchOpen', json={'accountId': self.account_id})
                if resp.status_code != 200:
                    return None
                payload = resp.json()
                positions = payload.get('positions', []) if isinstance(payload, dict) else payload
            for p in positions:
                # match by resolved contract id, else by display name prefix
                pid = str(p.get('contractId', ''))
                disp = (p.get('contractDisplayName') or '').upper()
                if (contract_id and pid == contract_id) or disp.startswith(symbol.upper()):
                    size = int(p.get('size', 0))
                    if size == 0:
                        return None
                    # TopStep position type: 1 = Long, 2 = Short
                    side = 'long' if p.get('type') == 1 else 'short'
                    return {'size': size, 'side': side,
                            'avgPrice': float(p.get('averagePrice', 0)),
                            'contractId': pid}
            return None
        except Exception:
            log.exception('TopstepX get_open_position failed for %s', symbol)
            return None

    def close_position(self, symbol: str) -> bool:
        """Flatten the entire open position for `symbol` at TopStep.

        Uses Position/closeContract (guaranteed flat) rather than a netting
        market order — avoids residual/flip when DB size != broker size.
        Returns True on success (or if already flat).
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        contract_id = self._contracts.get(symbol)
        if not contract_id:
            # try to discover it from the open position
            pos = self.get_open_position(symbol)
            contract_id = pos['contractId'] if pos else None
        if not contract_id:
            log.info('TopstepX close_position: no contract for %s — already flat', symbol)
            return True
        with self._lock:
            resp = self._http.post('/api/Position/closeContract',
                                   json={'accountId': self.account_id, 'contractId': contract_id})
            if resp.status_code != 200:
                raise RuntimeError(f'closeContract HTTP {resp.status_code}: {resp.text[:200]}')
            data = resp.json()
            if data.get('success') is False:
                # errorCode for "no position" is benign — treat as already flat
                err = (data.get('errorMessage') or '').lower()
                if 'no position' in err or 'not found' in err:
                    return True
                raise RuntimeError(f'closeContract rejected: {data.get("errorMessage")}')
            return True

    def get_close_fill(self, symbol: str, since_iso: str, retries: int = 5, delay: float = 0.4) -> dict | None:
        """Read the ACTUAL closing fill(s) for `symbol` from TopStep after a close.

        Lets us record the broker's real exit price + realized P&L instead of the
        live quote that tripped the exit (which slips on the market fill). Closing
        fills carry a non-null profitAndLoss; opening fills report null.

        Returns {'price', 'pnl', 'fees', 'size'} or None if nothing found.
        """
        if not self._connected:
            return None
        contract_id = self._contracts.get(symbol)
        if not contract_id:
            return None
        self._ensure_token()
        import time as _time
        for _ in range(retries):
            _time.sleep(delay)
            end = datetime.now(timezone.utc) + timedelta(seconds=5)
            try:
                with self._lock:
                    resp = self._http.post('/api/Trade/search', json={
                        'accountId':      self.account_id,
                        'startTimestamp': since_iso,
                        'endTimestamp':   end.isoformat().replace('+00:00', 'Z'),
                    })
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                trades = payload.get('trades', []) if isinstance(payload, dict) else payload
            except Exception:
                log.exception('TopstepX Trade/search failed for %s', symbol)
                continue
            fills = [t for t in trades
                     if str(t.get('contractId')) == contract_id
                     and t.get('profitAndLoss') is not None
                     and not t.get('voided')]
            total = sum(int(f.get('size', 0)) for f in fills)
            if total <= 0:
                continue
            price = sum(float(f['price']) * int(f.get('size', 0)) for f in fills) / total
            pnl   = sum(float(f.get('profitAndLoss') or 0) for f in fills)
            fees  = sum(float(f.get('fees') or 0) + float(f.get('commissions') or 0) for f in fills)
            return {'price': round(price, 4), 'pnl': pnl, 'fees': fees, 'size': total}
        return None

    # ------------------------------------------------------------------ #
    # Diagnostics — for "Test Connection" button in settings
    # ------------------------------------------------------------------ #
    def test_connection(self) -> dict:
        """Try to authenticate + look up account + resolve contracts.
        Returns a detailed dict for UI display. Never raises.
        """
        ok = self.connect()
        if not ok:
            return {
                'success': False,
                'stage':   'connect',
                'error':   self._last_error or 'Unknown error',
            }
        # If we got here, we have token + account + at least one contract
        return {
            'success':   True,
            'account':   self.account_id_raw,
            'contracts': dict(self._contracts),  # copy for response
        }
