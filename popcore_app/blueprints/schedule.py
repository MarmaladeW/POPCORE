"""
blueprints/schedule.py — employee profiles, availability, shifts, monthly hours report.
"""
import datetime as _dt
import secrets

from flask import Blueprint, request, jsonify, Response

from db import get_db
from auth import (
    login_required, role_required,
    ROLE_HIERARCHY, ROLE_CLAIM,
    AUTH0_MGMT_CLIENT_ID, _mgmt_get,
)
from blueprints.stores import _resolve_store
import urllib.parse

bp = Blueprint('schedule', __name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_or_create_employee(con, auth0_id: str, name: str = '', email: str = '') -> dict:
    """Return the employees row for auth0_id, creating it if absent.
    Also backfills name/email into existing rows that have empty values."""
    cur = con.cursor()
    cur.execute('SELECT * FROM employees WHERE auth0_id = ?', (auth0_id,))
    row = cur.fetchone()
    if row:
        updates = {}
        if name and not row['name']:
            updates['name'] = name
        if email and not row['email']:
            updates['email'] = email
        if updates:
            set_clause = ', '.join(f'{k} = ?' for k in updates)
            cur.execute(
                f'UPDATE employees SET {set_clause} WHERE auth0_id = ?',
                (*updates.values(), auth0_id)
            )
            con.commit()
            cur.execute('SELECT * FROM employees WHERE auth0_id = ?', (auth0_id,))
            return dict(cur.fetchone())
        return dict(row)
    cur.execute(
        'INSERT INTO employees (auth0_id, name, email) VALUES (?, ?, ?)',
        (auth0_id, name, email)
    )
    con.commit()
    cur.execute('SELECT * FROM employees WHERE auth0_id = ?', (auth0_id,))
    return dict(cur.fetchone())


def _hours_between(start_time: str, end_time: str) -> float:
    """Return decimal hours between HH:MM strings. Returns 0 if end <= start."""
    try:
        sh, sm = int(start_time[:2]), int(start_time[3:5])
        eh, em = int(end_time[:2]), int(end_time[3:5])
        diff = (eh * 60 + em) - (sh * 60 + sm)
        return max(0.0, diff / 60.0)
    except Exception:
        return 0.0


def _month_start_day(con) -> int:
    """Day of month the wage period starts on (app setting, default 4)."""
    try:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key = 'schedule_month_start_day'"
        ).fetchone()
        day = int(row['value']) if row else 4
    except Exception:
        day = 4
    return min(max(day, 1), 28)


def _wage_period(year: int, month: int, start_day: int):
    """Return (first_day, last_day) of the wage period anchored at year-month.
    With start_day=4, the 'August' period is Aug 4 … Sep 3."""
    start = _dt.date(year, month, start_day)
    if month == 12:
        nxt = _dt.date(year + 1, 1, start_day)
    else:
        nxt = _dt.date(year, month + 1, start_day)
    return start, nxt - _dt.timedelta(days=1)


def _current_wage_anchor(start_day: int):
    """Return (year, month) of the wage period containing today."""
    today = _dt.date.today()
    year, month = today.year, today.month
    if today.day < start_day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return year, month


def _require_store_param(con):
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    if store_code == 'ALL':
        return None, 'ALL', None
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        return None, None, (jsonify({'error': 'Invalid store code'}), 400)
    store_id, store_code = resolved
    return store_id, store_code, None


def _require_store_body(con, data):
    store_code = (data.get('store_code') or '').strip().upper()
    if not store_code:
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    if store_code == 'ALL':
        return None, None, (jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400)
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        return None, None, (jsonify({'error': 'Invalid store code'}), 400)
    store_id, store_code = resolved
    return store_id, store_code, None


# ─── Schedule config (readable by any logged-in user) ─────────────────────────

@bp.route('/api/schedule/config', methods=['GET'])
@login_required
def schedule_config():
    """Schedule-related settings for calendar rendering. /api/settings is
    manager-only, but every employee's calendar needs opening hours etc."""
    from blueprints.settings import SETTINGS_DEFAULTS
    keys = ('schedule_month_start_day', 'schedule_required_staff', 'schedule_open_hours')
    con = get_db()
    result = {}
    for key in keys:
        row = con.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
        result[key] = row['value'] if row else SETTINGS_DEFAULTS.get(key, '')
    con.close()
    return jsonify(result)


# ─── Employee profile ──────────────────────────────────────────────────────────

@bp.route('/api/schedule/me', methods=['GET'])
@login_required
def schedule_me_get():
    auth0_id = request.jwt_payload.get('sub', '')
    email    = request.jwt_payload.get('email', '')
    name     = request.jwt_payload.get('name') or request.jwt_payload.get('nickname', '')
    con = get_db()
    emp = _get_or_create_employee(con, auth0_id, name=name, email=email)
    con.close()
    return jsonify(emp)


@bp.route('/api/schedule/me', methods=['PATCH'])
@login_required
def schedule_me_patch():
    auth0_id = request.jwt_payload.get('sub', '')
    data     = request.get_json(silent=True) or {}
    con      = get_db()
    emp      = _get_or_create_employee(con, auth0_id)
    updates  = {}
    if 'name' in data:
        updates['name'] = str(data['name'])[:120]
    if 'email' in data:
        updates['email'] = str(data['email'])[:200]
    if updates:
        set_clause = ', '.join(f'{k} = ?' for k in updates)
        con.execute(
            f'UPDATE employees SET {set_clause} WHERE id = ?',
            list(updates.values()) + [emp['id']]
        )
        con.commit()
    row = con.execute('SELECT * FROM employees WHERE id = ?', (emp['id'],)).fetchone()
    con.close()
    return jsonify(dict(row))


@bp.route('/api/schedule/employees', methods=['GET'])
@role_required('manager')
def schedule_employees():
    con  = get_db()
    rows = con.execute(
        'SELECT * FROM employees WHERE is_active = 1 ORDER BY name'
    ).fetchall()
    employees = [dict(r) for r in rows]

    # Backfill names from Auth0 for any employee still missing one
    missing = [e for e in employees if not e.get('name')]
    if missing and AUTH0_MGMT_CLIENT_ID:
        try:
            cur = con.cursor()
            for emp in missing:
                uid_enc = urllib.parse.quote(emp['auth0_id'], safe='')
                resp = _mgmt_get(f'users/{uid_enc}',
                                 params={'fields': 'name,nickname,username,email',
                                         'include_fields': 'true'})
                if resp.status_code == 200:
                    data = resp.json()
                    fetched_name  = (data.get('name') or data.get('nickname') or
                                     data.get('username') or '').strip()
                    fetched_email = data.get('email', '').strip()
                    if fetched_name and fetched_name == emp['auth0_id']:
                        fetched_name = ''
                    if fetched_name or fetched_email:
                        cur.execute(
                            'UPDATE employees SET name = ?, email = ? WHERE id = ?',
                            (fetched_name, fetched_email, emp['id'])
                        )
                        emp['name']  = fetched_name
                        emp['email'] = fetched_email
            con.commit()
        except Exception:
            pass  # non-fatal: return whatever names we have

    con.close()
    employees.sort(key=lambda e: e.get('name') or e.get('email') or '')
    return jsonify(employees)


# ─── Availability ──────────────────────────────────────────────────────────────

@bp.route('/api/schedule/availability/me', methods=['GET'])
@login_required
def schedule_avail_me():
    auth0_id = request.jwt_payload.get('sub', '')
    start    = request.args.get('start', '')
    end      = request.args.get('end', '')
    con      = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    emp   = _get_or_create_employee(con, auth0_id)
    query = 'SELECT * FROM availability WHERE employee_id = ?'
    params: list = [emp['id']]
    if store_code != 'ALL':
        query  += ' AND store_id = ?'; params.append(store_id)
    if start:
        query  += ' AND date >= ?'; params.append(start)
    if end:
        query  += ' AND date <= ?'; params.append(end)
    rows = con.execute(query, params).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/availability', methods=['GET'])
@role_required('manager')
def schedule_avail_all():
    start = request.args.get('start', '')
    end   = request.args.get('end', '')
    con   = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    query = '''
        SELECT a.*, e.name AS employee_name, e.auth0_id,
               COALESCE(st.code, '') AS store_code
        FROM availability a
        JOIN employees e ON e.id = a.employee_id
        LEFT JOIN stores st ON st.id = a.store_id
        WHERE e.is_active = 1
    '''
    params: list = []
    if store_code != 'ALL':
        query  += ' AND a.store_id = ?'; params.append(store_id)
    if start:
        query  += ' AND a.date >= ?'; params.append(start)
    if end:
        query  += ' AND a.date <= ?'; params.append(end)
    query += ' ORDER BY a.date, e.name'
    rows = con.execute(query, params).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/availability', methods=['POST'])
@login_required
def schedule_avail_upsert():
    auth0_id   = request.jwt_payload.get('sub', '')
    data       = request.get_json(silent=True) or {}
    avail_date = data.get('date', '')
    start_time = data.get('start_time', '')
    end_time   = data.get('end_time', '')
    notes      = data.get('notes', '')
    if not avail_date or not start_time or not end_time:
        return jsonify({'error': 'date, start_time, end_time required'}), 400
    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    emp = _get_or_create_employee(con, auth0_id)
    con.execute('''
        INSERT INTO availability
            (employee_id, date, start_time, end_time, notes, store_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(employee_id, date) DO UPDATE SET
            start_time = excluded.start_time,
            end_time   = excluded.end_time,
            notes      = excluded.notes,
            store_id   = excluded.store_id,
            updated_at = datetime('now')
    ''', (emp['id'], avail_date, start_time, end_time, notes, store_id))
    con.commit()
    row = con.execute(
        'SELECT * FROM availability WHERE employee_id = ? AND date = ?',
        (emp['id'], avail_date)
    ).fetchone()
    con.close()
    return jsonify(dict(row)), 201


@bp.route('/api/schedule/availability/<int:avail_id>', methods=['DELETE'])
@login_required
def schedule_avail_delete(avail_id):
    auth0_id = request.jwt_payload.get('sub', '')
    con      = get_db()
    row      = con.execute('SELECT * FROM availability WHERE id = ?', (avail_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    emp = _get_or_create_employee(con, auth0_id)
    if row['employee_id'] != emp['id']:
        con.close()
        return jsonify({'error': 'Forbidden'}), 403
    con.execute('DELETE FROM availability WHERE id = ?', (avail_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Shifts ────────────────────────────────────────────────────────────────────

@bp.route('/api/schedule/shifts', methods=['GET'])
@login_required
def schedule_shifts_get():
    auth0_id    = request.jwt_payload.get('sub', '')
    role        = request.jwt_payload.get(ROLE_CLAIM, 'viewer')
    start       = request.args.get('start', '')
    end         = request.args.get('end', '')
    employee_id = request.args.get('employee_id', '')
    con         = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err

    if store_code == 'ALL':
        if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
            query  = '''
                SELECT s.*, e.name AS employee_name, e.auth0_id,
                       COALESCE(st.code, '') AS store_code
                FROM shifts s
                JOIN employees e ON e.id = s.employee_id
                LEFT JOIN stores st ON st.id = s.store_id
            '''
            params: list = []
            if employee_id:
                query += ' WHERE s.employee_id = ?'; params.append(int(employee_id))
        else:
            emp = _get_or_create_employee(con, auth0_id)
            query  = '''
                SELECT s.*, e.name AS employee_name, e.auth0_id,
                       COALESCE(st.code, '') AS store_code
                FROM shifts s
                JOIN employees e ON e.id = s.employee_id
                LEFT JOIN stores st ON st.id = s.store_id
                WHERE s.employee_id = ?
            '''
            params = [emp['id']]
        if start:
            query += (' AND' if params else ' WHERE') + ' s.date >= ?'; params.append(start)
        if end:
            query += ' AND s.date <= ?'; params.append(end)
    elif ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
        query  = '''
            SELECT s.*, e.name AS employee_name, e.auth0_id,
                   COALESCE(st.code, '') AS store_code
            FROM shifts s
            JOIN employees e ON e.id = s.employee_id
            LEFT JOIN stores st ON st.id = s.store_id
            WHERE s.store_id = ?
        '''
        params = [store_id]
        if employee_id:
            query += ' AND s.employee_id = ?'; params.append(int(employee_id))
        if start:
            query += ' AND s.date >= ?'; params.append(start)
        if end:
            query += ' AND s.date <= ?'; params.append(end)
    else:
        emp = _get_or_create_employee(con, auth0_id)
        query  = '''
            SELECT s.*, e.name AS employee_name, e.auth0_id,
                   COALESCE(st.code, '') AS store_code
            FROM shifts s
            JOIN employees e ON e.id = s.employee_id
            LEFT JOIN stores st ON st.id = s.store_id
            WHERE s.employee_id = ? AND s.store_id = ?
        '''
        params = [emp['id'], store_id]
        if start:
            query += ' AND s.date >= ?'; params.append(start)
        if end:
            query += ' AND s.date <= ?'; params.append(end)

    query += ' ORDER BY s.date, e.name'
    rows = con.execute(query, params).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/shifts/me', methods=['GET'])
@login_required
def schedule_shifts_me():
    auth0_id = request.jwt_payload.get('sub', '')
    start    = request.args.get('start', '')
    end      = request.args.get('end', '')
    con      = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    emp    = _get_or_create_employee(con, auth0_id)
    query  = '''
        SELECT s.*, e.name AS employee_name, e.auth0_id,
               COALESCE(st.code, '') AS store_code
        FROM shifts s
        JOIN employees e ON e.id = s.employee_id
        LEFT JOIN stores st ON st.id = s.store_id
        WHERE s.employee_id = ?
    '''
    params: list = [emp['id']]
    if store_code != 'ALL':
        query += ' AND s.store_id = ?'; params.append(store_id)
    if start:
        query += ' AND s.date >= ?'; params.append(start)
    if end:
        query += ' AND s.date <= ?'; params.append(end)
    query += ' ORDER BY s.date'
    rows = con.execute(query, params).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/shifts', methods=['POST'])
@role_required('manager')
def schedule_shifts_create():
    data        = request.get_json(silent=True) or {}
    assigned_by = request.jwt_payload.get('sub', '')
    employee_id = data.get('employee_id')
    shift_date  = data.get('date', '')
    start_time  = data.get('start_time', '')
    end_time    = data.get('end_time', '')
    notes       = data.get('notes', '')
    if not employee_id or not shift_date or not start_time or not end_time:
        return jsonify({'error': 'employee_id, date, start_time, end_time required'}), 400
    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    emp = con.execute('SELECT id FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    con.execute('''
        INSERT INTO shifts
            (employee_id, date, start_time, end_time, assigned_by, notes, store_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(employee_id, date) DO UPDATE SET
            start_time  = excluded.start_time,
            end_time    = excluded.end_time,
            assigned_by = excluded.assigned_by,
            notes       = excluded.notes,
            store_id    = excluded.store_id,
            updated_at  = datetime('now')
    ''', (employee_id, shift_date, start_time, end_time, assigned_by, notes, store_id))
    con.commit()
    row = con.execute(
        'SELECT * FROM shifts WHERE employee_id = ? AND date = ?',
        (employee_id, shift_date)
    ).fetchone()
    con.close()
    return jsonify(dict(row)), 201


@bp.route('/api/schedule/shifts/<int:shift_id>', methods=['PATCH'])
@role_required('manager')
def schedule_shifts_update(shift_id):
    data = request.get_json(silent=True) or {}
    con  = get_db()
    row  = con.execute('SELECT * FROM shifts WHERE id = ?', (shift_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {}
    for field in ('start_time', 'end_time', 'notes'):
        if field in data:
            updates[field] = data[field]
    if 'store_code' in data:
        store_id, store_code, err = _require_store_body(con, data)
        if err:
            con.close()
            return err
        updates['store_id'] = store_id
    if updates:
        set_parts = []
        vals: list = []
        for k, v in updates.items():
            set_parts.append(f'{k} = ?')
            vals.append(v)
        set_parts.append("updated_at = datetime('now')")
        con.execute(
            f'UPDATE shifts SET {", ".join(set_parts)} WHERE id = ?',
            vals + [shift_id]
        )
        con.commit()
    updated = con.execute('SELECT * FROM shifts WHERE id = ?', (shift_id,)).fetchone()
    con.close()
    return jsonify(dict(updated))


@bp.route('/api/schedule/shifts/<int:shift_id>', methods=['DELETE'])
@role_required('manager')
def schedule_shifts_delete(shift_id):
    con = get_db()
    row = con.execute('SELECT id FROM shifts WHERE id = ?', (shift_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    con.execute('DELETE FROM shifts WHERE id = ?', (shift_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Conflict check ────────────────────────────────────────────────────────────

@bp.route('/api/schedule/conflicts', methods=['GET'])
@login_required
def schedule_conflicts():
    try:
        employee_id = int(request.args.get('employee_id', ''))
    except (TypeError, ValueError):
        return jsonify({'error': 'employee_id must be an integer'}), 400
    shift_date = (request.args.get('date') or '').strip()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not shift_date or not store_code:
        return jsonify({'error': 'employee_id, date, and store_code are required'}), 400

    con = get_db()
    store_row = con.execute(
        'SELECT id FROM stores WHERE code = ? AND is_active = 1', (store_code,)
    ).fetchone()
    if not store_row:
        con.close()
        return jsonify({'error': 'Invalid store_code'}), 400

    store_id = store_row['id']
    rows = con.execute('''
        SELECT s.id AS shift_id, st.code AS store_code, st.name AS store_name,
               s.date, s.start_time, s.end_time
        FROM shifts s
        JOIN stores st ON st.id = s.store_id
        WHERE s.employee_id = ?
          AND s.date = ?
          AND s.store_id != ?
    ''', (employee_id, shift_date, store_id)).fetchall()
    con.close()

    conflicts = [
        {
            'shift_id':   r['shift_id'],
            'store_code': r['store_code'],
            'store_name': r['store_name'],
            'start_time': f"{r['date']}T{r['start_time']}",
            'end_time':   f"{r['date']}T{r['end_time']}",
        }
        for r in rows
    ]
    return jsonify({'has_conflict': len(conflicts) > 0, 'conflicts': conflicts})


# ─── Monthly hours report ──────────────────────────────────────────────────────

@bp.route('/api/schedule/reports/monthly', methods=['GET'])
@role_required('manager')
def schedule_report_monthly():
    import datetime as dt
    from collections import defaultdict

    con = get_db()
    start_day = _month_start_day(con)
    def_year, def_month = _current_wage_anchor(start_day)
    try:
        year  = int(request.args.get('year',  def_year))
        month = int(request.args.get('month', def_month))
        first_day, last_day = _wage_period(year, month, start_day)
    except ValueError:
        con.close()
        return jsonify({'error': 'year and month must be integers'}), 400

    month_str = f'{year}-{month:02d}'

    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err

    emps = con.execute('SELECT * FROM employees WHERE is_active = 1 ORDER BY name').fetchall()
    emp_ids = [e['id'] for e in emps]

    if not emp_ids:
        con.close()
        return jsonify({
            'month': month_str, 'employees': [],
            'period_start': str(first_day), 'period_end': str(last_day),
            'month_start_day': start_day,
        })

    placeholders = ','.join('?' * len(emp_ids))
    query = f'''SELECT * FROM shifts
                WHERE employee_id IN ({placeholders})
                  AND date >= ? AND date <= ?'''
    params = emp_ids + [str(first_day), str(last_day)]
    if store_code != 'ALL':
        query += ' AND store_id = ?'
        params.append(store_id)
    query += ' ORDER BY date'
    shifts = con.execute(query, params).fetchall()
    con.close()

    shifts_by_emp: dict = defaultdict(list)
    for s in shifts:
        shifts_by_emp[s['employee_id']].append(dict(s))

    result = []
    for emp in emps:
        emp_shifts = shifts_by_emp.get(emp['id'], [])
        total_hours = 0.0
        weeks: dict = {}
        for s in emp_shifts:
            h  = _hours_between(s['start_time'], s['end_time'])
            total_hours += h
            d  = dt.date.fromisoformat(s['date'])
            wk = f'{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}'
            if wk not in weeks:
                weeks[wk] = {'total': 0.0, 'days': {}}
            weeks[wk]['total'] = round(weeks[wk]['total'] + h, 2)
            weeks[wk]['days'][s['date']] = round(
                weeks[wk]['days'].get(s['date'], 0.0) + h, 2
            )
        result.append({
            'id':          emp['id'],
            'name':        emp['name'],
            'email':       emp['email'],
            'total_hours': round(total_hours, 2),
            'weeks':       weeks,
        })

    return jsonify({
        'month': month_str,
        'employees': result,
        'period_start': str(first_day),
        'period_end': str(last_day),
        'month_start_day': start_day,
    })


# ─── Per-employee hours (wage period) ─────────────────────────────────────────

@bp.route('/api/schedule/employees/<int:emp_id>/hours', methods=['GET'])
@role_required('manager')
def schedule_employee_hours(emp_id):
    """Hours worked by one employee in a wage period, across all stores.
    Defaults to the period containing today when year/month are omitted."""
    con = get_db()
    start_day = _month_start_day(con)
    def_year, def_month = _current_wage_anchor(start_day)
    try:
        year  = int(request.args.get('year',  def_year))
        month = int(request.args.get('month', def_month))
        first_day, last_day = _wage_period(year, month, start_day)
    except ValueError:
        con.close()
        return jsonify({'error': 'year and month must be integers'}), 400

    emp = con.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404

    rows = con.execute('''
        SELECT s.*, COALESCE(st.code, '') AS store_code
        FROM shifts s
        LEFT JOIN stores st ON st.id = s.store_id
        WHERE s.employee_id = ? AND s.date >= ? AND s.date <= ?
        ORDER BY s.date
    ''', (emp_id, str(first_day), str(last_day))).fetchall()
    con.close()

    total = 0.0
    by_store: dict = {}
    for r in rows:
        h = _hours_between(r['start_time'], r['end_time'])
        total += h
        code = r['store_code'] or '?'
        by_store[code] = round(by_store.get(code, 0.0) + h, 2)

    return jsonify({
        'employee_id':     emp_id,
        'name':            emp['name'],
        'email':           emp['email'],
        'period_start':    str(first_day),
        'period_end':      str(last_day),
        'month_start_day': start_day,
        'total_hours':     round(total, 2),
        'shift_count':     len(rows),
        'by_store':        by_store,
    })


# ─── Calendar sync (iCalendar subscription feed) ──────────────────────────────

def _ics_escape(text: str) -> str:
    return (str(text or '')
            .replace('\\', '\\\\')
            .replace(';', '\\;')
            .replace(',', '\\,')
            .replace('\r\n', '\\n')
            .replace('\n', '\\n'))


def _ics_fold(line: str) -> str:
    """Fold content lines to ~74 chars per RFC 5545."""
    out = []
    while len(line) > 74:
        out.append(line[:74])
        line = ' ' + line[74:]
    out.append(line)
    return '\r\n'.join(out)


@bp.route('/api/schedule/calendar-feed', methods=['GET'])
@login_required
def schedule_calendar_feed_url():
    """Return (creating if needed) the current user's private iCal feed token."""
    auth0_id = request.jwt_payload.get('sub', '')
    con = get_db()
    emp = _get_or_create_employee(con, auth0_id)
    token = emp.get('ical_token')
    if not token:
        token = secrets.token_urlsafe(24)
        con.execute('UPDATE employees SET ical_token = ? WHERE id = ?', (token, emp['id']))
        con.commit()
    con.close()
    return jsonify({'token': token, 'path': f'/api/schedule/ical/{token}.ics'})


@bp.route('/api/schedule/calendar-feed/reset', methods=['POST'])
@login_required
def schedule_calendar_feed_reset():
    """Rotate the feed token (invalidates any previously shared URL)."""
    auth0_id = request.jwt_payload.get('sub', '')
    con = get_db()
    emp = _get_or_create_employee(con, auth0_id)
    token = secrets.token_urlsafe(24)
    con.execute('UPDATE employees SET ical_token = ? WHERE id = ?', (token, emp['id']))
    con.commit()
    con.close()
    return jsonify({'token': token, 'path': f'/api/schedule/ical/{token}.ics'})


@bp.route('/api/schedule/ical/<token>.ics', methods=['GET'])
def schedule_ical_feed(token):
    """Public (token-authenticated) iCalendar feed of one employee's shifts.
    Calendar apps subscribed to this URL re-fetch it periodically, so schedule
    changes propagate without the employee doing anything."""
    if not token or len(token) < 16:
        return jsonify({'error': 'Not found'}), 404
    con = get_db()
    emp = con.execute(
        'SELECT * FROM employees WHERE ical_token = ?', (token,)
    ).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Not found'}), 404

    window_start = _dt.date.today() - _dt.timedelta(days=90)
    rows = con.execute('''
        SELECT s.*, COALESCE(st.code, '') AS store_code,
               COALESCE(st.name, '') AS store_name,
               COALESCE(st.address, '') AS store_address
        FROM shifts s
        LEFT JOIN stores st ON st.id = s.store_id
        WHERE s.employee_id = ? AND s.date >= ?
        ORDER BY s.date
    ''', (emp['id'], str(window_start))).fetchall()
    con.close()

    now_utc  = _dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    cal_name = f"POPCORE Shifts — {emp['name'] or emp['email'] or 'Employee'}"

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//POPCORE//Shift Schedule//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        _ics_fold(f'X-WR-CALNAME:{_ics_escape(cal_name)}'),
        'X-WR-TIMEZONE:America/Toronto',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
        'X-PUBLISHED-TTL:PT1H',
        'BEGIN:VTIMEZONE',
        'TZID:America/Toronto',
        'BEGIN:STANDARD',
        'DTSTART:19701101T020000',
        'RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU',
        'TZOFFSETFROM:-0400',
        'TZOFFSETTO:-0500',
        'TZNAME:EST',
        'END:STANDARD',
        'BEGIN:DAYLIGHT',
        'DTSTART:19700308T020000',
        'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU',
        'TZOFFSETFROM:-0500',
        'TZOFFSETTO:-0400',
        'TZNAME:EDT',
        'END:DAYLIGHT',
        'END:VTIMEZONE',
    ]

    for s in rows:
        date_c  = s['date'].replace('-', '')
        start_c = (s['start_time'] or '00:00').replace(':', '') + '00'
        end_c   = (s['end_time'] or '00:00').replace(':', '') + '00'
        store_label = s['store_name'] or s['store_code'] or 'POPCORE'
        summary  = f'POPCORE shift — {store_label}'
        location = ', '.join(p for p in (s['store_name'] or s['store_code'], s['store_address']) if p)
        lines += [
            'BEGIN:VEVENT',
            f"UID:popcore-shift-{s['id']}@popcore",
            f'DTSTAMP:{now_utc}',
            f'DTSTART;TZID=America/Toronto:{date_c}T{start_c}',
            f'DTEND;TZID=America/Toronto:{date_c}T{end_c}',
            _ics_fold(f'SUMMARY:{_ics_escape(summary)}'),
        ]
        if location:
            lines.append(_ics_fold(f'LOCATION:{_ics_escape(location)}'))
        if s['notes']:
            lines.append(_ics_fold(f'DESCRIPTION:{_ics_escape(s["notes"])}'))
        lines.append('END:VEVENT')

    lines.append('END:VCALENDAR')
    body = '\r\n'.join(lines) + '\r\n'
    return Response(
        body,
        mimetype='text/calendar',
        headers={
            'Content-Disposition': 'inline; filename="popcore-shifts.ics"',
            'Cache-Control': 'no-cache',
        },
    )
