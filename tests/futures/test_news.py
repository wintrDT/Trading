# tests/futures/test_news.py
import pytest
from unittest.mock import patch, MagicMock
from bot.futures.news import parse_econ_events, parse_av_headlines, HIGH_IMPACT_KEYWORDS

def test_parse_econ_events_filters_high_impact():
    raw = [
        {'description': 'FOMC Meeting', 'date': '2026-05-14', 'time': '14:00', 'impact': 'High'},
        {'description': 'Retail Sales', 'date': '2026-05-14', 'time': '08:30', 'impact': 'Low'},
        {'description': 'CPI Report',   'date': '2026-05-14', 'time': '08:30', 'impact': 'High'},
    ]
    events = parse_econ_events(raw, date_str='2026-05-14')
    assert len(events) == 2
    assert all(e['impact'] == 'High' for e in events)

def test_parse_av_headlines_returns_list():
    raw_av = {
        'feed': [
            {'title': 'Fed raises rates', 'url': 'http://x.com', 'time_published': '20260514T143000',
             'summary': 'The Fed raised rates by 25bps.', 'overall_sentiment_label': 'Bearish'},
            {'title': 'Strong jobs report', 'url': 'http://y.com', 'time_published': '20260514T083000',
             'summary': 'NFP beat expectations.', 'overall_sentiment_label': 'Bullish'},
        ]
    }
    headlines = parse_av_headlines(raw_av, limit=5)
    assert len(headlines) == 2
    assert headlines[0]['title'] == 'Fed raises rates'
    assert headlines[0]['sentiment'] == 'Bearish'

def test_high_impact_keywords_coverage():
    assert 'FOMC' in HIGH_IMPACT_KEYWORDS
    assert 'CPI'  in HIGH_IMPACT_KEYWORDS
    assert 'NFP'  in HIGH_IMPACT_KEYWORDS
