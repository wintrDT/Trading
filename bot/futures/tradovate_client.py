# bot/futures/tradovate_client.py
import json
import asyncio
import logging
import httpx
import websockets
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _parse_quote_frame(frame: str) -> list:
    """Parse a Tradovate WebSocket market data frame into quote dicts."""
    if not frame.startswith('a['):
        return []
    try:
        messages = json.loads(frame[1:])
        quotes = []
        for msg in messages:
            if msg.get('e') == 'md' and 'quotes' in msg.get('d', {}):
                quotes.extend(msg['d']['quotes'])
        return quotes
    except (json.JSONDecodeError, KeyError):
        return []


class TradovateClient:
    def __init__(self, username, password, cid, sec, demo=True, device_id='sharp-bot-001'):
        self._username   = username
        self._password   = password
        self._cid        = cid
        self._sec        = sec
        self._demo       = demo
        self._device_id  = device_id
        self.access_token    = None
        self.md_access_token = None
        self._token_expires  = None
        self._account_id     = None
        self._account_spec   = None
        self._base = 'https://demo.tradovateapi.com/v1' if demo else 'https://live.tradovateapi.com/v1'

    # ── Auth ──────────────────────────────────────────────────────────

    def connect(self):
        payload = {
            'name':       self._username,
            'password':   self._password,
            'deviceId':   self._device_id,
            'appId':      'SharpBot',
            'appVersion': '1.0',
            'cid':        int(self._cid) if str(self._cid).isdigit() else 0,
            'sec':        self._sec,
        }
        resp = httpx.post(f'{self._base}/auth/accesstokenrequest', json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if 'errorText' in data:
            raise RuntimeError(f"Tradovate auth failed: {data['errorText']}")
        self.access_token    = data['accessToken']
        self.md_access_token = data.get('mdAccessToken', self.access_token)
        self._token_expires  = data.get('expirationTime')
        self._fetch_account()
        log.info('Tradovate connected [%s]', 'DEMO' if self._demo else 'LIVE')

    def _refresh_if_needed(self):
        if self._token_expires is None:
            return
        exp = datetime.fromisoformat(self._token_expires.replace('Z', '+00:00'))
        if (exp - datetime.now(timezone.utc)).total_seconds() < 300:
            resp = httpx.post(
                f'{self._base}/auth/renewaccesstoken',
                headers=self._auth_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token    = data['accessToken']
            self.md_access_token = data.get('mdAccessToken', self.access_token)
            self._token_expires  = data.get('expirationTime')
            log.info('Tradovate token refreshed')

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def _fetch_account(self):
        data = self._get('/account/list')
        if not data:
            raise RuntimeError('No Tradovate accounts found')
        acct = data[0]
        self._account_id   = acct['id']
        self._account_spec = acct['name']

    # ── REST helpers ──────────────────────────────────────────────────

    def _get(self, path, params=None):
        self._refresh_if_needed()
        resp = httpx.get(f'{self._base}{path}', params=params, headers=self._auth_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, json=None):
        self._refresh_if_needed()
        resp = httpx.post(f'{self._base}{path}', json=json, headers=self._auth_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Account ───────────────────────────────────────────────────────

    def get_account_balance(self):
        return self._get('/cashBalance/getcashbalancesnapshot', params={'accountId': self._account_id})

    def get_positions(self):
        return self._get('/position/list')

    # ── Orders ────────────────────────────────────────────────────────

    def place_order(self, symbol, action, qty, order_type='Market', price=None, stop_price=None):
        payload = {
            'accountSpec': self._account_spec,
            'accountId':   self._account_id,
            'action':      action,
            'symbol':      symbol,
            'orderQty':    qty,
            'orderType':   order_type,
            'isAutomated': True,
        }
        if price is not None:
            payload['price'] = price
        if stop_price is not None:
            payload['stopPrice'] = stop_price
        return self._post('/order/placeorder', json=payload)

    def cancel_order(self, order_id):
        return self._post('/order/cancelorder', json={'orderId': order_id})

    def get_order(self, order_id):
        return self._get('/order/item', params={'id': order_id})

    # ── WebSocket market data ─────────────────────────────────────────

    async def _stream_quotes_async(self, symbols: list, on_quote, timeout=30):
        uri = 'wss://md.tradovateapi.com/v1/websocket'
        async with websockets.connect(uri, ping_interval=20) as ws:
            opening = await asyncio.wait_for(ws.recv(), timeout=10)
            if opening != 'o':
                raise RuntimeError(f'Unexpected WS open frame: {opening}')
            await ws.send(f'authorize\n0\n\n{self.md_access_token}')
            auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
            auth_data = json.loads(auth_resp[1:])
            if auth_data[0].get('s') != 200:
                raise RuntimeError(f'WS auth failed: {auth_data}')
            for i, sym in enumerate(symbols, start=1):
                await ws.send(f'md/subscribequote\n{i}\n\n{json.dumps({"symbol": sym})}')
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                for quote in _parse_quote_frame(msg):
                    on_quote(quote)

    def get_current_prices(self, symbols: list, timeout=30) -> dict:
        """Returns {symbol: mid_price} for the given continuous symbols (e.g. 'ES', 'NQ')."""
        prices = {}

        def on_quote(q):
            sym  = q.get('symbol', '')
            base = ''.join(c for c in sym if c.isalpha())
            bid  = q.get('bid')
            ask  = q.get('ask')
            if bid and ask:
                prices[base] = round((float(bid) + float(ask)) / 2, 4)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._stream_quotes_async(symbols, on_quote, timeout=timeout))
        finally:
            loop.close()
        return prices
