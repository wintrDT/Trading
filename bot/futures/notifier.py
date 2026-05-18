# bot/futures/notifier.py
"""Telegram notification module.

Send trade alerts and system events to your Telegram via the bot API.

Setup:
1. Open Telegram, search @BotFather
2. /newbot → name your bot → get a token like 123456:ABC...
3. Open https://api.telegram.org/bot<TOKEN>/getUpdates after sending /start to your bot
4. Find your chat_id in the JSON response
5. Save token + chat_id in Settings page or futures_settings DB

The notifier is a no-op if credentials aren't configured — bot keeps running
fine without Telegram. Errors in the notify path never crash the trading loop.
"""
import logging
from typing import Optional

import httpx


log = logging.getLogger(__name__)

# Module-level singleton, initialized once at bot startup
_notifier: Optional['TelegramNotifier'] = None


class TelegramNotifier:
    """Lightweight Telegram bot API client for trade notifications."""

    def __init__(self, token: str, chat_id: str,
                 notify_entries: bool = True,
                 notify_exits: bool = True,
                 notify_big: bool = True,
                 notify_system: bool = True):
        self.token   = token
        self.chat_id = chat_id
        self.notify_entries = notify_entries
        self.notify_exits   = notify_exits
        self.notify_big     = notify_big
        self.notify_system  = notify_system
        self._enabled = bool(token and chat_id)
        self._http = httpx.Client(timeout=5.0)
        if self._enabled:
            log.info('Telegram notifier enabled for chat_id=%s', chat_id)
        else:
            log.info('Telegram notifier disabled — no token/chat_id')

    def _send(self, msg: str):
        if not self._enabled:
            return
        try:
            url = f'https://api.telegram.org/bot{self.token}/sendMessage'
            r = self._http.post(url, json={
                'chat_id':    self.chat_id,
                'text':       msg,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            })
            r.raise_for_status()
        except Exception as e:
            log.warning('Telegram send failed: %s', e)


def init(token: str, chat_id: str, **opts):
    """Initialize the module singleton. Call once at bot startup."""
    global _notifier
    _notifier = TelegramNotifier(token, chat_id, **opts)


def _n() -> Optional[TelegramNotifier]:
    return _notifier


def notify_entry(symbol: str, direction: str, price: float,
                 stop: float, target: float, contracts: int, strategy: str):
    n = _n()
    if not (n and n.notify_entries):
        return
    arrow = '📈' if direction == 'long' else '📉'
    msg = (
        f'{arrow} <b>ENTRY: {direction.upper()} {symbol}</b>\n'
        f'  @ {price:.2f}  ({contracts}x {strategy})\n'
        f'  stop {stop:.2f}  target {target:.2f}'
    )
    n._send(msg)


def notify_exit(symbol: str, direction: str, pnl: float, reason: str,
                entry: float, exit_price: float):
    n = _n()
    if not n:
        return

    sign  = '+' if pnl >= 0 else ''
    emoji = '✅' if pnl > 0 else '❌' if pnl < 0 else '⏸'

    # Big-move call-out: use loud emojis when >= $300 in either direction
    if n.notify_big and abs(pnl) >= 300:
        emoji = '🚀' if pnl > 0 else '🔻'

    if not (n.notify_exits or (n.notify_big and abs(pnl) >= 300)):
        return

    msg = (
        f'{emoji} <b>EXIT: {symbol} {direction.upper()}</b>\n'
        f'  {reason}  ({entry:.2f} → {exit_price:.2f})\n'
        f'  P&L: <b>{sign}${pnl:.2f}</b>'
    )
    n._send(msg)


def notify_armed(symbol: str, direction: str, peak_pct: float):
    """Optional — armed (pending bounce confirmation) state. Off by default."""
    n = _n()
    if not n:
        return
    msg = f'🎯 ARMED {direction.upper()} {symbol} — peak {peak_pct:+.3f}%'
    n._send(msg)


def notify_system(msg: str, level: str = 'info'):
    """System events: daily loss limit hit, EOD flat, big errors."""
    n = _n()
    if not (n and n.notify_system):
        return
    emoji = {'info': 'ℹ️', 'warning': '⚠️', 'error': '🚨', 'critical': '🛑'}.get(level, 'ℹ️')
    n._send(f'{emoji} <b>SYSTEM:</b> {msg}')


def test():
    """Send a test message to verify Telegram setup. Returns True on success."""
    n = _n()
    if not n:
        return False
    if not n._enabled:
        return False
    n._send('🧪 <b>TEST:</b> Sharp Bot Telegram notifier is connected.')
    return True
