# bot/futures/main.py
import logging
from datetime import datetime
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from bot.futures.config import (
    FUTURES_DB_PATH, SYMBOLS, TICK_INFO, STRATEGY_PARAMS, RISK_RULES,
    TIMEZONE, MARKET_OPEN, MARKET_CLOSE, ORB_END,
)
from bot.futures.db import (
    init_db, insert_signal, get_daily_pnl, get_setting,
    insert_snapshot,
)
from bot.futures.tradovate_client import TradovateClient
from bot.futures.strategy import VWAPState, ORBState, calc_vwap, check_vwap_signal, check_orb_signal
from bot.futures.risk import is_daily_loss_limit_hit
from bot.futures.trader import place_entry
from bot.futures.manager import manage_futures_positions

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ET = pytz.timezone(TIMEZONE)

_vwap_states: dict = {}
_orb_states:  dict = {}


def _is_market_hours():
    now = datetime.now(ET).time()
    oh, om = map(int, MARKET_OPEN.split(':'))
    ch, cm = map(int, MARKET_CLOSE.split(':'))
    return _Time(oh, om) <= now <= _Time(ch, cm)


def _is_orb_period():
    now = datetime.now(ET).time()
    oh, om = map(int, MARKET_OPEN.split(':'))
    eh, em = map(int, ORB_END.split(':'))
    return _Time(oh, om) <= now < _Time(eh, em)


def _orb_end_minute():
    h, m = map(int, ORB_END.split(':'))
    return h * 60 + m


def _now_minute():
    now = datetime.now(ET)
    return now.hour * 60 + now.minute


def _reset_daily_state():
    global _vwap_states, _orb_states
    _vwap_states = {s: VWAPState() for s in SYMBOLS}
    _orb_states  = {s: ORBState()  for s in SYMBOLS}
    log.info('Daily state reset — VWAP and ORB cleared')


def job_scan(client):
    if not _is_market_hours():
        return

    today     = datetime.now(ET).strftime('%Y-%m-%d')
    daily_pnl = get_daily_pnl(FUTURES_DB_PATH, today)
    if is_daily_loss_limit_hit(daily_pnl, RISK_RULES['daily_loss_limit']):
        log.warning('Daily loss limit hit ($%.2f) — skipping scan', daily_pnl)
        return

    sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'

    try:
        prices = client.get_current_prices(SYMBOLS, timeout=20)
    except Exception:
        log.exception('Failed to fetch prices')
        return

    orb_period  = _is_orb_period()
    orb_end_min = _orb_end_minute()
    now_min     = _now_minute()
    now_iso     = datetime.now(ET).isoformat()

    for symbol in SYMBOLS:
        price = prices.get(symbol)
        if price is None:
            continue

        tick = TICK_INFO[symbol]['tick']
        vwap_state = _vwap_states.setdefault(symbol, VWAPState())
        orb_state  = _orb_states.setdefault(symbol, ORBState())

        vwap_state.add_bar(price=price, volume=1)

        if not orb_state._ready and now_min >= orb_end_min:
            orb_state.set_ready()

        if orb_period:
            orb_state.update(price=price, ts_minute=now_min)
            continue

        vwap    = calc_vwap(vwap_state)
        signal  = None
        strategy = None

        if vwap is not None:
            direction = check_vwap_signal(price, vwap, STRATEGY_PARAMS['vwap_deviation_pct'])
            if direction:
                signal, strategy = direction, 'vwap'

        orb_dir = check_orb_signal(price, orb_state, orb_end_min,
                                    STRATEGY_PARAMS['orb_min_range_ticks'], tick)
        if orb_dir:
            signal, strategy = orb_dir, 'orb'

        if signal is None:
            continue

        signal_id = insert_signal(FUTURES_DB_PATH, {
            'ts': now_iso, 'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'vwap': vwap,
            'orb_high': orb_state.high if orb_state._ready else None,
            'orb_low':  orb_state.low  if orb_state._ready else None,
            'traded': 0,
        })
        log.info('Signal: %s %s %s @ %.2f', strategy, signal, symbol, price)
        place_entry(client, FUTURES_DB_PATH, {
            'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'signal_id': signal_id,
        }, contracts=1, sim=sim)


def job_manage(client):
    if not _is_market_hours():
        return
    try:
        prices = client.get_current_prices(SYMBOLS, timeout=15)
        sim    = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
        manage_futures_positions(client, FUTURES_DB_PATH, current_prices=prices, sim=sim)
    except Exception:
        log.exception('Manager error')


def job_snapshot(client):
    try:
        bal = client.get_account_balance()
        insert_snapshot(FUTURES_DB_PATH, {
            'ts':                 datetime.now(ET).isoformat(),
            'net_liq':            float(bal.get('netLiquidatingValue', 0)),
            'cash':               float(bal.get('cashBalance', 0)),
            'open_pnl':           float(bal.get('openTradeEquity', 0)),
            'realized_pnl_today': float(bal.get('realizedPnL', 0)),
        })
    except Exception:
        log.exception('Snapshot error')


def main():
    init_db(FUTURES_DB_PATH)
    _reset_daily_state()

    tv_username  = get_setting(FUTURES_DB_PATH, 'tv_username',  '')
    tv_password  = get_setting(FUTURES_DB_PATH, 'tv_password',  '')
    tv_cid       = get_setting(FUTURES_DB_PATH, 'tv_cid',       '')
    tv_sec       = get_setting(FUTURES_DB_PATH, 'tv_sec',       '')
    tv_device_id = get_setting(FUTURES_DB_PATH, 'tv_device_id', 'sharp-bot-futures-001')
    tv_demo      = get_setting(FUTURES_DB_PATH, 'tv_demo',      'true').lower() == 'true'

    if not tv_username or not tv_password:
        log.error('Tradovate credentials not configured. Set them in Settings → Tradovate.')
        return

    client = TradovateClient(tv_username, tv_password, tv_cid, tv_sec,
                             demo=tv_demo, device_id=tv_device_id)
    try:
        client.connect()
    except Exception:
        log.exception('Failed to connect to Tradovate — exiting')
        return

    def _scan():     job_scan(client)
    def _manage():   job_manage(client)
    def _snapshot(): job_snapshot(client)
    def _reset():    _reset_daily_state()

    scheduler = BlockingScheduler(timezone=ET)
    scheduler.add_job(_scan,     IntervalTrigger(seconds=45, timezone=ET), id='scan')
    scheduler.add_job(_manage,   IntervalTrigger(seconds=15, timezone=ET), id='manage')
    scheduler.add_job(_snapshot, CronTrigger(day_of_week='mon-fri', hour='10-15', minute=0, timezone=ET), id='snapshot')
    scheduler.add_job(_reset,    CronTrigger(day_of_week='mon-fri', hour=9, minute=29, timezone=ET), id='reset')

    log.info('Futures bot running [%s]. Ctrl+C to stop.', 'DEMO' if tv_demo else 'LIVE')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Shutting down.')
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
