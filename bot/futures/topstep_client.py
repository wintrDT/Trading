# bot/futures/topstep_client.py
"""TopstepX API client.

Scaffolded stub — the method shape matches what main.py / trader.py / manager.py
already expect from a broker client (same interface as Tastytrade and Tradovate).

When the user fills in real HTTP calls, no other file should need to change.
Until then, connect() returns False and the bot falls through to the current
Tradovate / Tastytrade path.

API base: https://api.topstepx.com  (TopstepX REST)
Auth: username + API key -> session token (Bearer)
"""
import logging
import threading

import httpx


log = logging.getLogger(__name__)

TOPSTEPX_BASE = 'https://api.topstepx.com'


# Symbol -> TopstepX contract code. TopstepX uses front-month contract codes
# (e.g. CON.F.US.ES.M25). Concrete codes depend on quarter rollover; resolve
# at connect() time by querying the contract endpoint.
_TOPSTEPX_CONTRACT_HINT = {
    'ES': 'F.US.EP',   # E-mini S&P 500
    'NQ': 'F.US.ENQ',  # E-mini Nasdaq-100
    'GC': 'F.US.GC',   # Gold
}


class TopstepXClient:
    """Broker client targeting TopStep funded accounts via the TopstepX API."""

    def __init__(self, username: str, api_key: str, account_id: str):
        self.username     = username
        self.api_key      = api_key
        self.account_id   = account_id
        self._token       = None
        self._contracts   = {}        # symbol -> resolved TopstepX contract id
        self._lock        = threading.Lock()
        self._http        = httpx.Client(timeout=10.0, base_url=TOPSTEPX_BASE)
        self._connected   = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        """Authenticate and resolve front-month contracts.

        Returns True on success, False if credentials are missing/invalid.
        TODO: fill in TopstepX auth flow once we have working credentials.
        """
        if not (self.username and self.api_key and self.account_id):
            log.info('TopstepX: missing credentials — skipping')
            return False

        # Placeholder — TopstepX API integration not yet implemented.
        # Real flow will be:
        #   POST /api/Auth/loginKey  body: {userName, apiKey}  -> {token}
        #   GET  /api/Contract/search?searchText=ES  -> resolve front-month id
        log.warning('TopstepX: client scaffolded but HTTP calls not implemented yet')
        return False

    def disconnect(self):
        try:
            self._http.close()
        except Exception:
            pass
        self._connected = False

    # ------------------------------------------------------------------ #
    # Account / positions
    # ------------------------------------------------------------------ #
    def get_account_balance(self) -> dict:
        """Returns shape compatible with main.job_snapshot:
            {'netLiquidatingValue', 'cashBalance', 'openTradeEquity', 'realizedPnL'}
        """
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        # TODO: POST /api/Account/search -> pick self.account_id -> map fields
        raise NotImplementedError('TopstepX get_account_balance — fill in')

    # ------------------------------------------------------------------ #
    # Market data — must match Tastytrade/Tradovate get_futures_prices shape
    # ------------------------------------------------------------------ #
    def get_futures_prices(self, symbols: list) -> dict:
        """Return {symbol: last_price} for each requested futures symbol."""
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        # TODO: POST /api/History/retrieveBars or use real-time WS feed
        raise NotImplementedError('TopstepX get_futures_prices — fill in')

    # ------------------------------------------------------------------ #
    # Orders — signature matches what trader.place_entry / manager close call
    # ------------------------------------------------------------------ #
    def place_order(self, symbol: str, action: str, contracts: int) -> dict:
        """Submit a market order. action is 'Buy' or 'Sell'. Returns {'orderId': ...}."""
        if not self._connected:
            raise RuntimeError('TopstepX not connected')
        # TODO: POST /api/Order/place  body: {accountId, contractId, type=2 (market), side, size}
        raise NotImplementedError('TopstepX place_order — fill in')
