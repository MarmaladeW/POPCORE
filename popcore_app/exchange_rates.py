"""Today's CAD→CNY reference rate, for WeChat Pay and Alipay when no rate is set in Settings.

Reference rates are published once a working day. Settings always take precedence. The cashier
charges the CNY amount the checkout screen shows, and each payment keeps that amount and its rate.
"""
import re
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

import requests

SOURCES = (
    ('European Central Bank', 'https://api.frankfurter.app/latest', {'from': 'CAD', 'to': 'CNY'},
     lambda data: (data['rates']['CNY'], data['date'])),
    ('ExchangeRate-API', 'https://open.er-api.com/v6/latest/CAD', None,
     lambda data: (data['rates']['CNY'], str(data.get('time_last_update_utc', ''))[5:16])),
)
FRESH_SECONDS = 3600      # look the rate up again after an hour
RETRY_SECONDS = 120       # after a failed lookup, wait before asking again
OLDEST_SECONDS = 86400    # never offer a rate older than a day
_RATE = re.compile(r'[0-9]{1,3}(?:\.[0-9]{1,4})?\Z')
_lock = threading.Lock()
_cache = {'value': None, 'at': 0.0, 'failed_at': 0.0}


def cad_to_fen(cad_cents, rate):
    """CNY fen for CAD cents at a rate such as '4.7528', rounded half up as the checkout screen rounds.

    None unless the rate is above 0 and at most 100 with up to 4 decimal places, as Settings allows."""
    if type(cad_cents) is not int or cad_cents < 0 or not isinstance(rate, str) or not _RATE.match(rate):
        return None
    whole, _, fraction = rate.partition('.')
    scaled = int(whole) * 10_000 + int(fraction.ljust(4, '0'))
    if not 0 < scaled <= 1_000_000:
        return None
    return (cad_cents * scaled + 5_000) // 10_000


def cny_per_cad():
    """{'rate': '4.7528', 'date': '2026-10-20', 'source': 'European Central Bank'}, or None when unavailable."""
    now = time.time()
    with _lock:
        if _cache['value'] and now - _cache['at'] < FRESH_SECONDS:
            return _cache['value']
        if now - _cache['failed_at'] < RETRY_SECONDS:
            return _cache['value'] if _cache['value'] and now - _cache['at'] < OLDEST_SECONDS else None
    for name, url, params, read in SOURCES:
        try:
            response = requests.get(url, params=params, timeout=(2, 3))
            response.raise_for_status()
            rate, date = read(response.json())
            value = Decimal(str(rate)).quantize(Decimal('0.0001'), ROUND_HALF_UP)
            if not Decimal('1') <= value <= Decimal('20'):
                continue
            result = {'rate': f'{value}', 'date': str(date)[:20], 'source': name}
            with _lock:
                _cache.update(value=result, at=now)
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, ArithmeticError):
            continue
    with _lock:
        _cache['failed_at'] = now
        return _cache['value'] if _cache['value'] and now - _cache['at'] < OLDEST_SECONDS else None
