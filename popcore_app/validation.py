"""Small validators shared by stock-changing API routes."""

from datetime import date


SQLITE_INTEGER_MAX = 2**63 - 1


def read_int(value, field, minimum=0):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not minimum <= value <= SQLITE_INTEGER_MAX:
        raise ValueError(f'{field} must be an integer >= {minimum}')
    return value


def read_date(value, field='date'):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be an ISO date')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f'{field} must be an ISO date') from exc


def invalid_input(exc, *, line=None):
    payload = {
        'error': str(exc),
        'code': 'invalid_input',
        'field': str(exc).split(' ', 1)[0],
    }
    if line is not None:
        payload['line'] = line
    return payload
