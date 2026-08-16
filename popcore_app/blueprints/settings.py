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
    # Store opening hours, JSON keyed by store code with weekday/weekend
    # blocks. Stores not listed fall back to 12-22 / 11-22.
    'schedule_open_hours':
        '{"DT": {"weekday": {"open": "12:00", "close": "22:00"},'
        '        "weekend": {"open": "11:00", "close": "22:00"}},'
        ' "MK": {"weekday": {"open": "12:00", "close": "21:00"},'
        '        "weekend": {"open": "11:00", "close": "21:00"}}}',
    # Extra fixed shift slots selectable in the shift dialog besides
    # full day / half days, JSON array of {label, start, end}.
    'schedule_shift_presets': '[]',
    # In-store positions per store, JSON keyed by store code
    # (e.g. Downtown has Front / Cashier / End).
    'schedule_positions': '{"DT": ["Front", "Cashier", "End"]}',
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


def _validate_day_block(block, path: str):
    if not isinstance(block, dict):
        return f'{path} must be an object'
    open_t  = block.get('open')
    close_t = block.get('close')
    for label, v in (('open', open_t), ('close', close_t)):
        if not isinstance(v, str) or not _TIME_RE.match(v):
            return f'{path}.{label} must be HH:MM'
    if open_t >= close_t:
        return f'{path}: open must be before close'
    return None


def _validate_open_hours(value: str):
    """Return an error string if the opening-hours JSON is malformed, else None.
    Accepts the per-store format ({"DT": {"weekday": {...}, "weekend": {...}}})
    and the legacy global format ({"weekday": {...}, "weekend": {...}})."""
    try:
        parsed = json.loads(value)
    except Exception:
        return 'schedule_open_hours must be valid JSON'
    if not isinstance(parsed, dict) or not parsed:
        return 'schedule_open_hours must be a non-empty JSON object'
    if 'weekday' in parsed or 'weekend' in parsed:
        for day_type in ('weekday', 'weekend'):
            err = _validate_day_block(parsed.get(day_type), f'schedule_open_hours.{day_type}')
            if err:
                return err
        return None
    for code, cfg in parsed.items():
        if not isinstance(cfg, dict):
            return f'schedule_open_hours["{code}"] must be an object'
        for day_type in ('weekday', 'weekend'):
            err = _validate_day_block(cfg.get(day_type), f'schedule_open_hours["{code}"].{day_type}')
            if err:
                return err
    return None


def _validate_shift_presets(value: str):
    """Return an error string if the custom shift presets JSON is malformed."""
    try:
        parsed = json.loads(value)
    except Exception:
        return 'schedule_shift_presets must be valid JSON'
    if not isinstance(parsed, list):
        return 'schedule_shift_presets must be a JSON array'
    if len(parsed) > 20:
        return 'schedule_shift_presets: at most 20 presets'
    for i, p in enumerate(parsed):
        if not isinstance(p, dict):
            return f'schedule_shift_presets[{i}] must be an object'
        label = p.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 40:
            return f'schedule_shift_presets[{i}].label must be a non-empty string (max 40 chars)'
        start, end = p.get('start'), p.get('end')
        for name, v in (('start', start), ('end', end)):
            if not isinstance(v, str) or not _TIME_RE.match(v):
                return f'schedule_shift_presets[{i}].{name} must be HH:MM'
        if start >= end:
            return f'schedule_shift_presets[{i}]: start must be before end'
    return None


def _validate_positions(value: str):
    """Return an error string if the per-store positions JSON is malformed."""
    try:
        parsed = json.loads(value)
    except Exception:
        return 'schedule_positions must be valid JSON'
    if not isinstance(parsed, dict):
        return 'schedule_positions must be a JSON object keyed by store code'
    for code, positions in parsed.items():
        if not isinstance(positions, list):
            return f'schedule_positions["{code}"] must be an array of position names'
        if len(positions) > 20:
            return f'schedule_positions["{code}"]: at most 20 positions'
        for p in positions:
            if not isinstance(p, str) or not p.strip() or len(p) > 40:
                return f'schedule_positions["{code}"] entries must be non-empty strings (max 40 chars)'
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
    if 'schedule_shift_presets' in data:
        err = _validate_shift_presets(str(data['schedule_shift_presets']))
        if err:
            return jsonify({'error': err}), 400
    if 'schedule_positions' in data:
        err = _validate_positions(str(data['schedule_positions']))
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
