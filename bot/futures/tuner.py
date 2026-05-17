# bot/futures/tuner.py
import logging
from bot.futures.db import _conn, set_setting
from bot.futures.config import SYMBOLS, TUNE_BOUNDS

log = logging.getLogger(__name__)


def _get_recent_trades(db_path, symbol, direction, limit=40):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT entry_rsi, entry_dev_pct, pnl FROM futures_trades "
            "WHERE status='closed' AND symbol=? AND direction=? "
            "AND entry_rsi IS NOT NULL ORDER BY close_ts DESC LIMIT ?",
            (symbol, direction, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def _score(subset):
    """Return (avg_pnl, win_rate) for a subset of trades."""
    n = len(subset)
    avg_pnl  = sum(t['pnl'] or 0 for t in subset) / n
    win_rate = sum(1 for t in subset if (t['pnl'] or 0) > 0) / n
    return avg_pnl, win_rate


def _pick_best(candidates, score_fn):
    """Pick the candidate with highest avg_pnl. Within $0.50, prefer higher win rate."""
    best = None  # (avg_pnl, win_rate, thresh)
    for thresh in candidates:
        result = score_fn(thresh)
        if result is None:
            continue
        avg_pnl, win_rate = result
        if best is None:
            best = (avg_pnl, win_rate, thresh)
            continue
        # tiebreaker: within $0.50 EV, prefer higher win rate (smoother equity)
        if avg_pnl > best[0] + 0.5:
            best = (avg_pnl, win_rate, thresh)
        elif abs(avg_pnl - best[0]) <= 0.5 and win_rate > best[1]:
            best = (avg_pnl, win_rate, thresh)
    return best


def _best_rsi_threshold(trades, direction):
    """Find RSI cutoff that maximizes expected value while keeping at least 40% of trades."""
    if direction == 'long':
        candidates = range(30, 70, 5)
        def passes(rsi, thresh): return rsi is None or rsi > thresh
    else:
        candidates = range(45, 75, 5)
        def passes(rsi, thresh): return rsi is None or rsi < thresh

    min_trades = max(5, int(len(trades) * 0.4))

    def score_fn(thresh):
        subset = [t for t in trades if passes(t['entry_rsi'], thresh)]
        if len(subset) < min_trades:
            return None
        return _score(subset)

    best = _pick_best(candidates, score_fn)
    if best is None:
        return None, 0.0, 0.0
    avg_pnl, win_rate, thresh = best
    return thresh, avg_pnl, win_rate


def _best_dev_threshold(trades):
    """Find VWAP deviation threshold that maximizes expected value while keeping at least 40% of trades."""
    candidates = [round(x * 0.01, 2) for x in range(8, 26, 2)]  # 0.08 to 0.24
    min_trades = max(5, int(len(trades) * 0.4))

    def score_fn(thresh):
        subset = [t for t in trades if t['entry_dev_pct'] is not None and abs(t['entry_dev_pct']) >= thresh]
        if len(subset) < min_trades:
            return None
        return _score(subset)

    best = _pick_best(candidates, score_fn)
    if best is None:
        return None, 0.0, 0.0
    avg_pnl, win_rate, thresh = best
    return thresh, avg_pnl, win_rate


def run_tuner(db_path):
    for symbol in SYMBOLS:
        for direction in ('long', 'short'):
            trades = _get_recent_trades(db_path, symbol, direction)
            if len(trades) < 10:
                log.info('Tuner: not enough %s %s trades (%d) — skipping', symbol, direction, len(trades))
                continue

            rsi_thresh, rsi_ev, rsi_wr = _best_rsi_threshold(trades, direction)
            dev_thresh, dev_ev, dev_wr = _best_dev_threshold(trades)

            if rsi_thresh is not None:
                key = f'tune_{symbol.lower()}_{direction}_rsi'
                b = TUNE_BOUNDS[direction]['rsi']
                clamped = max(b['min'], min(b['max'], rsi_thresh))
                set_setting(db_path, key, str(clamped))
                log.info('Tuner: %s %s RSI → %s (avg P&L $%.2f, win rate %.0f%%)',
                         symbol, direction, clamped, rsi_ev, rsi_wr * 100)

            if dev_thresh is not None:
                key = f'tune_{symbol.lower()}_{direction}_dev'
                b = TUNE_BOUNDS['dev']
                clamped = max(b['min'], min(b['max'], dev_thresh))
                set_setting(db_path, key, str(clamped))
                log.info('Tuner: %s %s dev → %.2f%% (avg P&L $%.2f, win rate %.0f%%)',
                         symbol, direction, clamped, dev_ev, dev_wr * 100)
