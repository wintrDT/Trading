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

TOPSTEPX_BASE = 'https://api.topstepx.com'

# Symbol -> contract searchText used by /api/Contract/search. The endpoint
# returns active contracts matching the search; we pick the front-month
# (activeContract=True) at connect() time.
_TOPSTEPX_SEARCH = {
    'ES': 'ES',   # E-mini S&P 500
    'NQ': 'NQ',   # E-mini Nasdaq-100
    'GC': 'GC',   # Gold
}

# TopstepX order type / side enums (from swagger schema)
_ORDER_TYPE_MARKET = 2     # 1=Limit, 2=Market, 3=Stop, 4=TrailingStop
_ORDER_SIDE_BUY  = 0       # Bid (buyer)
_ORDER_SIDE_SELL = 1       # Ask (seller)


class TopstepXClient:
    """Broker client targeting TopStep funded/eval accounts via the TopstepX API."""

    def __init__(self, username: str, api_key: str, account_id: str):
        self.username    = username
        self.api_key     = api_key
        self.account_id  = int(account_id) if str(account_id).isdigit() else account_id
        self._token      = None
        self._token_expires_at = 0.0   # epoch seconds
        self._contracts  = {}           # symbol -> resolved contract id (string)
        self._lock       = threading.Lock()
        self._http       = httpx.Client(timeout=10.0, base_url=TOPSTEPX_BASE)
        self._connected  = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        """Authenticate and resolve front-month contracts.

        Returns True on success, False on any failure (logged) — caller falls
        through to next broker option.
        """
        if not (self.username and self.api_key and self.account_id):
            log.info('TopstepX: missing credentials — skipping')
            return False

        try:
            with self._lock:
                resp = self._http.post('/api/Auth/loginKey', json={
                    'userName': self.username,
                    'apiKey':   self.api_key,
                })
                resp.raise_for_status()
                data = resp.json()
                if not data.get('success') or not data.get('token'):
                    log.error('TopstepX auth failed: %s', data)
                    return False
                self._token = data['token']
                # JWTs from TopstepX typically last ~24h. Refresh proactively at 23h.
                self._token_expires_at = time.time() + 23 * 3600
                self._http.headers['Authorization'] = f'Bearer {self._token}'

            # Resolve front-month contract id for each symbol in our universe
            for symbol, search_text in _TOPSTEPX_SEARCH.items():
                contract_id = self._resolve_contract(search_text)
                if contract_id:
                    self._contracts[symbol] = contract_id
                    log.info('TopstepX: resolved %s -> %s', symbol, contract_id)

            if not self._contracts:
                log.error('TopstepX: no contracts resolved — disconnecting')
                return False

            self._connected = True
            log.info('TopstepX connected (account=%s, contracts=%d)',
                     self.account_id, len(self._contracts))
            return True
        except httpx.HTTPError as e:
            log.exception('TopstepX connect HTTP error: %s', e)
            return False
        except Exception:
            log.exception('TopstepX connect unexpected error')
            return False

    def disconnect(self):
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
        """Return the front-month contractId for search_text, or None."""
        try:
            with self._lock:
                resp = self._http.post('/api/Contract/search', json={
                    'searchText': search_text,
                    'live': True,
                })
                resp.raise_for_status()
                contracts = resp.json().get('contracts', [])
                # Prefer activeContract=True (front-month)
                active = [c for c in contracts if c.get('activeContract')]
                pick   = active[0] if active else (contracts[0] if contracts else None)
                return pick['id'] if pick else None
        except Exception:
            log.exception('TopstepX contract resolution failed for %s', search_text)
            return None

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    def get_account_balance(self) -> dict:
        """Returns shape compatible with main.job_snapshot:
            {'netLiquidatingValue', 'cashBalance', 'openTradeEquity', 'realizedPnL'}
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        with self._lock:
            resp = self._http.post('/api/Account/search', json={'onlyActiveAccounts': True})
            resp.raise_for_status()
            accounts = resp.json().get('accounts', [])
            acct = next((a for a in accounts if a.get('id') == self.account_id), None)
            if acct is None:
                raise RuntimeError(f'TopstepX account {self.account_id} not found')

            # TradingAccountModel has 'balance' — TopstepX doesn't return separate
            # cash vs equity for sim/funded accounts. Use balance for all three fields
            # so existing snapshot/dashboard code keeps working.
            balance = float(acct.get('balance', 0))
            return {
                'netLiquidatingValue': balance,
                'cashBalance':         balance,
                'openTradeEquity':     0.0,    # not exposed by TopstepX REST
                'realizedPnL':         0.0,    # tracked in /api/Trade/search separately
                'canTrade':            bool(acct.get('canTrade', False)),
                'simulated':           bool(acct.get('simulated', True)),
            }

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    def get_futures_prices(self, symbols: list) -> dict:
        """Polls the most recent 1-minute bar's close for each symbol.

        TopstepX's REST historical-bars endpoint is the simplest live-price source
        without standing up a SignalR WebSocket. Bar resolution is 1 minute, so
        prices can lag by up to ~60s. Sufficient for the bot's 30s scan loop;
        upgrade to SignalR real-time hub if tighter latency is needed.
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        self._ensure_token()
        out = {}
        end_time   = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=5)  # last 5 minutes = a few bars
        for symbol in symbols:
            contract_id = self._contracts.get(symbol)
            if not contract_id:
                continue
            try:
                with self._lock:
                    resp = self._http.post('/api/History/retrieveBars', json={
                        'contractId':        contract_id,
                        'live':              True,
                        'startTime':         start_time.isoformat().replace('+00:00', 'Z'),
                        'endTime':           end_time.isoformat().replace('+00:00', 'Z'),
                        'unit':              2,     # 2 = Minute
                        'unitNumber':        1,
                        'limit':             5,
                        'includePartialBar': True,
                    })
                    resp.raise_for_status()
                    bars = resp.json().get('bars', [])
                    if bars:
                        out[symbol] = float(bars[-1]['close'])
            except Exception:
                log.exception('TopstepX price fetch failed for %s', symbol)
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
            resp.raise_for_status()
            data = resp.json()
            if not data.get('success'):
                raise RuntimeError(f'TopstepX order rejected: {data}')
            return {'orderId': data.get('orderId') or data.get('id') or 'UNKNOWN'}
