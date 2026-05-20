"""
Reset bad tuner thresholds from the DB so the bot uses live config defaults again.
Run once from the trading directory: python reset_tuner.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'bot', 'data', 'futures.db')

TUNER_KEYS = [
    'tune_es_long_rsi',  'tune_es_short_rsi',
    'tune_nq_long_rsi',  'tune_nq_short_rsi',
    'tune_es_long_dev',  'tune_es_short_dev',
    'tune_nq_long_dev',  'tune_nq_short_dev',
]

def main():
    if not os.path.exists(DB_PATH):
        print(f'DB not found at {DB_PATH} — nothing to reset.')
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Show current values before wiping
        print('Current tuner values:')
        for key in TUNER_KEYS:
            cur.execute('SELECT value FROM futures_settings WHERE key = ?', (key,))
            row = cur.fetchone()
            print(f'  {key} = {row[0] if row else "(not set)"}')

        # Delete all tuner overrides — bot falls back to dynamic/config defaults
        cur.executemany('DELETE FROM futures_settings WHERE key = ?', [(k,) for k in TUNER_KEYS])

        # Ensure trading is not paused
        cur.execute('INSERT OR REPLACE INTO futures_settings (key, value) VALUES (?, ?)',
                    ('trading_paused', 'false'))

        conn.commit()
        print('\nDone — tuner overrides cleared, trading_paused reset to false.')
        print('Restart the bot for changes to take effect.')

if __name__ == '__main__':
    main()
