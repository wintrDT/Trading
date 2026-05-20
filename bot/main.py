import logging
from datetime import datetime
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot import config
from bot.db import init_db, insert_scan, insert_account_snapshot, get_bot_setting
from bot.tt_client import TastytradeClient
from bot.scanner import scan_underlying, scan_underlying_0dte
from bot.trader import place_spread
from bot.manager import manage_positions

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger(__name__)

ET = pytz.timezone(config.TIMEZONE)

# Run as: python -m bot.main  (from the Kalshi-Bot project root)
# Required env vars: TT_SECRET, TT_REFRESH, TT_ACCOUNT (see bot/config.py)


def _is_market_hours():
    now = datetime.now(ET)
    return _Time(9, 45) <= now.time() <= _Time(15, 30)


def _is_0dte_window():
    """0DTE entries only in morning (9:45-11:00) and midday (13:30-14:30) windows."""
    now = datetime.now(ET).time()
    return _Time(9, 45) <= now <= _Time(11, 0) or _Time(13, 30) <= now <= _Time(14, 30)


def job_scan(client):
    if not _is_market_hours():
        return
    log.info('Scanner starting')
    try:
        vix = client.get_vix()
        if vix is None:
            log.warning('VIX unavailable — proceeding without VIX filter')
        else:
            log.info('VIX=%.2f (allowed %.1f–%.1f)', vix, config.VIX_MIN, config.VIX_MAX)
            if not (config.VIX_MIN <= vix <= config.VIX_MAX):
                log.info('VIX outside range — skipping scan')
                return

        balance = client.get_account_balance()
        net_liq = float(balance.net_liquidating_value)

        # Collect all setups across underlyings, then place only the best one
        # (SPY/QQQ/IWM are ~95% correlated — multiple trades = concentrated risk)
        all_setups = []
        for symbol in config.UNDERLYINGS:
            setups = scan_underlying(client, symbol)
            for setup in setups:
                scan_id = insert_scan(config.DB_PATH, {
                    **setup,
                    'ts': datetime.now(ET).isoformat(),
                    'traded': 0,
                })
                log.info('Setup: %s %s exp %s credit $%.2f iv_rank=%.0f',
                         setup['strategy'], symbol, setup['expiration'],
                         setup['credit'], setup['iv_rank'])
                all_setups.append((setup, scan_id))

        if not all_setups:
            return

        # Pick the setup with the highest IV rank
        all_setups.sort(key=lambda x: x[0].get('iv_rank', 0), reverse=True)
        best_setup, best_scan_id = all_setups[0]
        log.info('Correlation cap: selecting %s %s (iv_rank=%.0f) from %d setup(s)',
                 best_setup['strategy'], best_setup['underlying'],
                 best_setup['iv_rank'], len(all_setups))

        sim = get_bot_setting(config.DB_PATH, 'trading_mode', 'live') == 'sim'
        if sim:
            log.info('SIMULATION MODE — trade will not be sent to Tastytrade')
        trade_id = place_spread(client, config.DB_PATH, best_setup, best_scan_id, net_liq, sim=sim)
        if trade_id is not None:
            log.info('Trade placed: id=%s %s %s%s', trade_id, best_setup['strategy'], best_setup['underlying'], ' [SIM]' if sim else '')
        else:
            log.info('Trade not opened: %s %s', best_setup['strategy'], best_setup['underlying'])
    except Exception:
        log.exception('Scanner error')


def job_scan_0dte(client):
    if not _is_0dte_window():
        return
    log.info('0DTE scanner starting')
    try:
        balance = client.get_account_balance()
        net_liq = float(balance.net_liquidating_value)

        all_setups = []
        for symbol in config.UNDERLYINGS:
            setups = scan_underlying_0dte(client, symbol)
            for setup in setups:
                scan_id = insert_scan(config.DB_PATH, {
                    **setup,
                    'ts': datetime.now(ET).isoformat(),
                    'traded': 0,
                    'trade_type': '0dte',
                })
                log.info('0DTE setup: %s exp %s credit $%.2f', symbol, setup['expiration'], setup['credit'])
                all_setups.append((setup, scan_id))

        if not all_setups:
            log.info('0DTE: no qualifying setups')
            return

        # Pick highest credit (IV rank not available for 0DTE)
        all_setups.sort(key=lambda x: x[0].get('credit', 0), reverse=True)
        best_setup, best_scan_id = all_setups[0]

        sim = get_bot_setting(config.DB_PATH, 'trading_mode', 'live') == 'sim'
        trade_id = place_spread(client, config.DB_PATH, best_setup, best_scan_id, net_liq, sim=sim)
        if trade_id is not None:
            log.info('0DTE trade placed: id=%s %s%s', trade_id, best_setup['underlying'], ' [SIM]' if sim else '')
        else:
            log.info('0DTE trade not opened: %s', best_setup['underlying'])
    except Exception:
        log.exception('0DTE scanner error')


def job_manage(client):
    if not _is_market_hours():
        return
    log.info('Manager starting')
    try:
        manage_positions(client, config.DB_PATH)
    except Exception:
        log.exception('Manager error')


def job_snapshot(client):
    log.info('Account snapshot')
    try:
        balance = client.get_account_balance()
        insert_account_snapshot(config.DB_PATH, {
            'ts': datetime.now(ET).isoformat(),
            'net_liq': float(balance.net_liquidating_value),
            'cash': float(balance.cash_balance),
            'open_pnl': float(getattr(balance, 'unrealized_day_profit_loss', 0)),
            'realized_pnl_today': float(getattr(balance, 'realized_day_profit_loss', 0)),
        })
    except Exception:
        log.exception('Snapshot error')


def main():
    init_db(config.DB_PATH)
    client = TastytradeClient(
        config.TASTYTRADE_PROVIDER_SECRET,
        config.TASTYTRADE_REFRESH_TOKEN,
        config.TASTYTRADE_ACCOUNT_NUMBER,
        paper_account=config.TASTYTRADE_PAPER_ACCOUNT,
    )
    try:
        client.connect()
    except Exception:
        log.exception('Failed to connect to Tastytrade — exiting')
        return
    mode = 'PAPER' if client.is_paper else 'LIVE'
    log.info('Connected to Tastytrade [%s] — account %s', mode, client.account.account_number)

    def _job_scan():
        job_scan(client)

    def _job_scan_0dte():
        job_scan_0dte(client)

    def _job_manage():
        job_manage(client)

    def _job_snapshot():
        job_snapshot(client)

    scheduler = BlockingScheduler(timezone=ET)

    # Premium scanner — every 15 min during market hours
    scheduler.add_job(
        _job_scan,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='0,15,30,45', second=0, timezone=ET),
        id='scanner',
    )
    # 0DTE scanner — every 45 seconds (time window guard is inside the job)
    scheduler.add_job(
        _job_scan_0dte,
        IntervalTrigger(seconds=45, timezone=ET),
        id='scanner_0dte',
    )
    # Manage positions every 5 min
    scheduler.add_job(
        _job_manage,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='0-45/5', second=30, timezone=ET),
        id='manager',
    )
    # Hourly account snapshot
    scheduler.add_job(
        _job_snapshot,
        CronTrigger(day_of_week='mon-fri', hour='10-15', minute=0, second=0, timezone=ET),
        id='snapshot',
    )

    log.info('Scheduler running. Ctrl+C to stop.')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Shutting down.')
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
