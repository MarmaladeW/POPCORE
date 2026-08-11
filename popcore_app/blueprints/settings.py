"""
blueprints/settings.py — App-wide settings (admin/manager only).
"""
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


@bp.route('/api/settings', methods=['PUT'])
@role_required('admin')
def put_settings():
    data = request.get_json() or {}
    unknown = [k for k in data if k not in SETTINGS_WHITELIST]
    if unknown:
        return jsonify({'error': f'Unknown settings keys: {", ".join(sorted(unknown))}'}), 400
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
