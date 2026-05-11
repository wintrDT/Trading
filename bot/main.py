import logging
from datetime import datetime
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import config
from bot.db import init_db, insert_scan, insert_account_snapshot
from bot.tt_client import TastytradeClient
from bot.scanner import scan_underlying
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
    return _Time(9, 45) <= now.time() <= _Time(15, 45)


def job_scan(client):
    if not _is_market_hours():
        return
    log.info('Scanner starting')
    try:
        balance = client.get_account_balance()
        net_liq = float(balance.net_liquidating_value)
        for symbol in config.UNDERLYINGS:
            setups = scan_underlying(client, symbol)
            for setup in setups:
                scan_id = insert_scan(config.DB_PATH, {
                    **setup,
                    'ts': datetime.now(ET).isoformat(),
                    'traded': 0,
                })
                log.info('Setup: %s %s exp %s credit $%.2f/share',
                         setup['strategy'], symbol, setup['expiration'], setup['credit'])
                trade_id = place_spread(client, config.DB_PATH, setup, scan_id, net_liq)
                if trade_id is not None:
                    log.info('Trade placed: id=%s %s %s', trade_id, setup['strategy'], symbol)
                else:
                    log.info('Trade not opened: %s %s', setup['strategy'], symbol)
    except Exception:
        log.exception('Scanner error')


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
    )
    try:
        client.connect()
    except Exception:
        log.exception('Failed to connect to Tastytrade — exiting')
        return
    log.info('Connected to Tastytrade — account %s', config.TASTYTRADE_ACCOUNT_NUMBER)

    def _job_scan():
        job_scan(client)

    def _job_manage():
        job_manage(client)

    def _job_snapshot():
        job_snapshot(client)

    scheduler = BlockingScheduler(timezone=ET)

    # Scan every 15 min during market hours (9:00–15:30 ET, last scan at 15:30)
    scheduler.add_job(
        _job_scan,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='0,15,30', second=0, timezone=ET),
        id='scanner',
    )
    # Manage positions every 5 min, capped at :45 to stay within market hours
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
