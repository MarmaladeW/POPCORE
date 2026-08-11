"""
blueprints/settings.py — App-wide settings (admin/manager only).
"""
import json
import re

from flask import Blueprint, request, jsonify
from db import get_db
from auth import role_required

bp = Blueprint('settings', __name__)

SETTINGS_DEFAULTS: dict[str, str] = {
    'insight_generate_time':         '02:00',
    'insight_high_price_threshold':  '100',
    'insight_dead_stock_days':       '14',
    'insight_stockout_days':         '7',
    'insight_velocity_ratio':        '2.0',
    'report_weekly_day':             'Monday',
    'report_weekly_time':            '08:00',
    'report_monthly_day':            '1',
    'report_monthly_time':           '08:00',
    'report_quarterly_time':         '08:00',
    'store_dt_name':                 'DT',
    'store_mk_name':                 'MK',
    # Day of month the wage period starts on (e.g. 4 → Aug 4 … Sep 3 is "August")
    'schedule_month_start_day':      '4',
    # Minimum staff per store during opening hours, JSON keyed by store code.
    # Stores not listed default to 1/1.
    'schedule_required_staff':
        '{"DT": {"weekday": 3, "weekend": 3}, "MK": {"weekday": 1, "weekend": 2}}',
    # Store opening hours (all stores share these), JSON with weekday/weekend.
    'schedule_open_hours':
        '{"weekday": {"open": "12:00", "close": "22:00"},'
        ' "weekend": {"open": "11:00", "close": "22:00"}}',
}

SETTINGS_WHITELIST = frozenset(SETTINGS_DEFAULTS.keys())


def _load_settings(db) -> dict:
    result = dict(SETTINGS_DEFAULTS)
    for row in db.execute('SELECT key, value FROM app_settings').fetchall():
        if row['key'] in SETTINGS_WHITELIST:
            result[row['key']] = row['value']
    return result


@bp.route('/api/settings')
@role_required('manager')
def get_settings():
    return jsonify(_load_settings(get_db()))


def _validate_required_staff(value: str):
    """Return an error string if the staffing JSON is malformed, else None."""
    try:
        parsed = json.loads(value)
    except Exception:
        return 'schedule_required_staff must be valid JSON'
    if not isinstance(parsed, dict):
        return 'schedule_required_staff must be a JSON object keyed by store code'
    for code, req in parsed.items():
        if not isinstance(req, dict):
            return f'schedule_required_staff["{code}"] must be an object'
        for field in ('weekday', 'weekend'):
            v = req.get(field)
            if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 20):
                return f'schedule_required_staff["{code}"].{field} must be an integer 0–20'
    return None


_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def _validate_open_hours(value: str):
    """Return an error string if the opening-hours JSON is malformed, else None."""
    try:
        parsed = json.loads(value)
    except Exception:
        return 'schedule_open_hours must be valid JSON'
    if not isinstance(parsed, dict):
        return 'schedule_open_hours must be a JSON object'
    for day_type in ('weekday', 'weekend'):
        block = parsed.get(day_type)
        if not isinstance(block, dict):
            return f'schedule_open_hours.{day_type} must be an object'
        open_t  = block.get('open')
        close_t = block.get('close')
        for label, v in (('open', open_t), ('close', close_t)):
            if not isinstance(v, str) or not _TIME_RE.match(v):
                return f'schedule_open_hours.{day_type}.{label} must be HH:MM'
        if open_t >= close_t:
            return f'schedule_open_hours.{day_type}: open must be before close'
    return None


@bp.route('/api/settings', methods=['PUT'])
@role_required('admin')
def put_settings():
    data = request.get_json() or {}
    unknown = [k for k in data if k not in SETTINGS_WHITELIST]
    if unknown:
        return jsonify({'error': f'Unknown settings keys: {", ".join(sorted(unknown))}'}), 400
    if 'schedule_required_staff' in data:
        err = _validate_required_staff(str(data['schedule_required_staff']))
        if err:
            return jsonify({'error': err}), 400
    if 'schedule_open_hours' in data:
        err = _validate_open_hours(str(data['schedule_open_hours']))
        if err:
            return jsonify({'error': err}), 400
    db = get_db()
    updated = []
    for key, value in data.items():
        db.execute(
            'INSERT INTO app_settings (key, value) VALUES (?, ?)'
            ' ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, str(value)),
        )
        updated.append(key)
    db.commit()
    return jsonify({'ok': True, 'updated': updated})
