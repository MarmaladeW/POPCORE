"""
blueprints/schedule.py — employee profiles, availability, shifts, monthly hours report.
"""
import datetime as _dt
import secrets
import re as _re
from zoneinfo import ZoneInfo

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
    if not isinstance(data.get('store_code'), str):
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    store_code = data['store_code'].strip().upper()
    if not store_code:
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    if store_code == 'ALL':
        return None, None, (jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400)
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        return None, None, (jsonify({'error': 'Invalid store code'}), 400)
    store_id, store_code = resolved
    return store_id, store_code, None


DEFAULT_SCHEDULE_REMINDER = '提前十分钟到！'

# ─── Schedule config (readable by any logged-in user) ─────────────────────────

@bp.route('/api/schedule/config', methods=['GET'])
@login_required
def schedule_config():
    """Schedule-related settings for calendar rendering. /api/settings is
    manager-only, but every employee's calendar needs opening hours etc."""
    from blueprints.settings import SETTINGS_DEFAULTS
    keys = ('schedule_month_start_day', 'schedule_required_staff', 'schedule_open_hours',
            'schedule_shift_presets', 'schedule_positions', 'schedule_reminder')
    con = get_db()
    result = {}
    for key in keys:
        row = con.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
        result[key] = row['value'] if row else SETTINGS_DEFAULTS.get(key, DEFAULT_SCHEDULE_REMINDER if key == 'schedule_reminder' else '')
    con.close()
    return jsonify(result)


@bp.route('/api/schedule/reminder', methods=['PUT'])
@role_required('manager')
def update_schedule_reminder():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'content'}:
        return jsonify({'error': 'Provide only reminder content'}), 400
    content = data['content']
    if not isinstance(content, str) or not content.strip() or len(content) > 1000:
        return jsonify({'error': 'Reminder must be 1–1000 characters'}), 400
    con = get_db()
    try:
        con.execute(
            "INSERT INTO app_settings (key,value) VALUES ('schedule_reminder',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (content.strip(),),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({'content': content.strip()})


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
    # (trainees have no Auth0 account — skip them)
    missing = [e for e in employees if not e.get('name') and not e.get('is_trainee')]
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
    employees.sort(key=lambda e: (e.get('is_trainee') or 0, e.get('name') or e.get('email') or ''))
    return jsonify(employees)


@bp.route('/api/schedule/employees/<int:emp_id>', methods=['PATCH'])
@role_required('manager')
def schedule_employee_patch(emp_id):
    """Rename an employee/trainee (display name shown across the schedule)."""
    data = request.get_json(silent=True) or {}
    con  = get_db()
    row  = con.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    updates = {}
    if 'name' in data:
        name = str(data['name']).strip()[:120]
        if not name:
            con.close()
            return jsonify({'error': 'name cannot be empty'}), 400
        updates['name'] = name
    if 'email' in data:
        updates['email'] = str(data['email']).strip()[:200]
    if updates:
        set_clause = ', '.join(f'{k} = ?' for k in updates)
        con.execute(
            f'UPDATE employees SET {set_clause} WHERE id = ?',
            list(updates.values()) + [emp_id]
        )
        con.commit()
    updated = con.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
    con.close()
    return jsonify(dict(updated))


# ─── Trainees ──────────────────────────────────────────────────────────────────
# Trainees don't get their own sign-in: they live in the employees table with a
# synthetic auth0_id and is_trainee = 1, so they can be scheduled like anyone
# else while never matching a real Auth0 login.

@bp.route('/api/schedule/trainees', methods=['GET'])
@role_required('manager')
def schedule_trainees_list():
    con  = get_db()
    rows = con.execute(
        'SELECT * FROM employees WHERE is_active = 1 AND is_trainee = 1 ORDER BY name'
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/trainees', methods=['POST'])
@role_required('manager')
def schedule_trainees_create():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:120]
    if not name:
        return jsonify({'error': 'name is required'}), 400
    con = get_db()
    con.execute(
        'INSERT INTO employees (auth0_id, name, email, is_trainee) VALUES (?, ?, ?, 1)',
        (f'trainee|{secrets.token_hex(12)}', name, '')
    )
    con.commit()
    row = con.execute(
        'SELECT * FROM employees WHERE id = last_insert_rowid()'
    ).fetchone()
    con.close()
    return jsonify(dict(row)), 201


@bp.route('/api/schedule/trainees/<int:trainee_id>', methods=['DELETE'])
@role_required('manager')
def schedule_trainees_delete(trainee_id):
    """Deactivate a trainee (their past shifts stay on record)."""
    con = get_db()
    row = con.execute(
        'SELECT id FROM employees WHERE id = ? AND is_trainee = 1', (trainee_id,)
    ).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Trainee not found'}), 404
    con.execute('UPDATE employees SET is_active = 0 WHERE id = ?', (trainee_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Availability ──────────────────────────────────────────────────────────────

_AVAILABILITY_ANCHOR = _dt.date(2026, 9, 14)


def _schedule_date(value):
    if not isinstance(value, str) or not _re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('Use dates in YYYY-MM-DD format')
    return _dt.date.fromisoformat(value)


def _schedule_times(start, end):
    if any(not isinstance(t, str) or not _re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', t)
           for t in (start, end)) or end <= start:
        raise ValueError('Use HH:MM times with end after start')


def _availability_day(data):
    if not isinstance(data, dict):
        raise ValueError('Each day must be an object')
    _schedule_date(data.get('date'))
    status = data.get('status', 'available')
    if status not in ('available', 'unavailable'):
        raise ValueError('Choose available or unavailable for each day')
    if not isinstance(data.get('notes', ''), str):
        raise ValueError('Notes must be text')
    start, end = data.get('start_time', ''), data.get('end_time', '')
    if status == 'available':
        _schedule_times(start, end)
    elif start != '' or end != '':
        raise ValueError('Unavailable days must have empty times')
    return (data['date'], start, end, data.get('notes', ''), status)


def _availability_period(date):
    return str(date - _dt.timedelta(days=(date - _AVAILABILITY_ANCHOR).days % 14))


def _availability_rows(con, rows):
    submissions = {}
    result = []
    for row in rows:
        item = dict(row)
        try:
            key = (item['employee_id'], item['store_id'], _availability_period(_schedule_date(item['date'])))
        except ValueError:
            # Historical malformed dates remain readable, but never count as submitted.
            item.update(submitted_at=None, submission_version=0)
        else:
            if key not in submissions:
                submissions[key] = con.execute('''SELECT version,submitted_at FROM availability_submissions
                    WHERE employee_id=? AND store_id=? AND period_start=?''', key).fetchone()
            submission = submissions[key]
            item.update(submitted_at=submission['submitted_at'] if submission else None,
                        submission_version=submission['version'] if submission else 0)
        result.append(item)
    return result


def _invalidate_availability(con, employee_id, store_id, date):
    try:
        period = _availability_period(_schedule_date(date))
    except ValueError:
        return
    con.execute('''INSERT INTO availability_submissions(employee_id,store_id,period_start,version,submitted_at)
        VALUES (?,?,?,1,NULL) ON CONFLICT(employee_id,store_id,period_start)
        DO UPDATE SET version=version+1,submitted_at=NULL''', (employee_id,store_id,period))


def _save_availability(con, employee_id, store_id, day):
    date, start, end, notes, status = day
    con.execute('''INSERT INTO availability(employee_id,store_id,date,start_time,end_time,notes,status)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(employee_id,date,store_id) DO UPDATE SET
        start_time=excluded.start_time,end_time=excluded.end_time,notes=excluded.notes,
        status=excluded.status,updated_at=datetime('now')''',
        (employee_id,store_id,date,start,end,notes,status))


def _availability_allows_shift(con, employee_id, store_id, date, start, end):
    submission = con.execute('''SELECT submitted_at FROM availability_submissions
        WHERE employee_id=? AND store_id=? AND period_start=?''',
        (employee_id,store_id,_availability_period(_schedule_date(date)))).fetchone()
    if not submission or not submission['submitted_at']:
        return True
    row = con.execute('''SELECT a.start_time,a.end_time FROM availability a
        JOIN availability_submissions s ON s.employee_id=a.employee_id AND s.store_id=a.store_id
        WHERE a.employee_id=? AND a.store_id=? AND a.date=? AND a.status='available'
        AND s.period_start=? AND s.submitted_at IS NOT NULL''',
        (employee_id,store_id,date,_availability_period(_schedule_date(date)))).fetchone()
    return row is not None and row['start_time'] <= start and row['end_time'] >= end


@bp.route('/api/schedule/availability/period', methods=['GET', 'PUT'])
@login_required
def schedule_availability_period():
    data = request.args if request.method == 'GET' else request.get_json(silent=True)
    if not data or not hasattr(data, 'get'):
        return jsonify({'error': 'A period and store are required'}), 400
    subject = request.jwt_payload.get('sub')
    if not isinstance(subject, str) or not subject.strip():
        return jsonify({'error': 'Authenticated identity required'}), 401
    try:
        first = _schedule_date(data.get('period_start'))
        if _availability_period(first) != str(first):
            raise ValueError('period_start must start a two-week cycle anchored to 2026-09-14')
        last = first + _dt.timedelta(days=13)
        if 'employee_id' in data:
            raise ValueError('Only your own availability can be submitted or read here')
        days = []
        if request.method == 'PUT':
            if type(data.get('version')) is not int or data['version'] < 0:
                raise ValueError('A nonnegative integer version is required')
            if not isinstance(data.get('days'), list) or len(data['days']) != 14:
                raise ValueError('Submit all 14 days')
            if any(not isinstance(day, dict) or 'status' not in day for day in data['days']):
                raise ValueError('An explicit status is required for all 14 days')
            days = [_availability_day(day) for day in data['days']]
            if {day[0] for day in days} != {str(first + _dt.timedelta(days=i)) for i in range(14)}:
                raise ValueError('Submit each date in this cycle exactly once')
    except (ValueError, OverflowError) as exc:
        return jsonify({'error': str(exc)}), 400
    con = get_db()
    try:
        store_id, store_code, err = _require_store_body(con, data)
        if err:
            return err
        emp = _get_or_create_employee(con, subject)
        if not emp['is_active'] or emp['is_trainee']:
            return jsonify({'error': 'Active employee required'}), 403
        con.execute('BEGIN IMMEDIATE' if request.method == 'PUT' else 'BEGIN')
        submission = con.execute('''SELECT version,submitted_at FROM availability_submissions
            WHERE employee_id=? AND store_id=? AND period_start=?''', (emp['id'],store_id,str(first))).fetchone()
        version = submission['version'] if submission else 0
        if request.method == 'PUT':
            if version != data['version']:
                return jsonify({'error': 'Availability changed. Reload this period before resubmitting.', 'version': version}), 409
            for day in days:
                _save_availability(con, emp['id'], store_id, day)
            con.execute('''INSERT INTO availability_submissions(employee_id,store_id,period_start,version,submitted_at)
                VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(employee_id,store_id,period_start) DO UPDATE SET
                version=excluded.version,submitted_at=excluded.submitted_at''', (emp['id'],store_id,str(first),version+1))
        submission = con.execute('''SELECT version,submitted_at FROM availability_submissions
            WHERE employee_id=? AND store_id=? AND period_start=?''', (emp['id'],store_id,str(first))).fetchone()
        rows = con.execute('''SELECT * FROM availability WHERE employee_id=? AND store_id=?
            AND date>=? AND date<=? ORDER BY date''', (emp['id'],store_id,str(first),str(last))).fetchall()
        result = dict(period_start=str(first),period_end=str(last),store_code=store_code,
                      version=submission['version'] if submission else 0,
                      submitted_at=submission['submitted_at'] if submission else None,
                      days=_availability_rows(con,rows))
        con.commit()
        return jsonify(result)
    finally:
        con.close()


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
    con.execute('BEGIN')
    rows = con.execute(query, params).fetchall()
    result = _availability_rows(con, rows)
    con.close()
    return jsonify(result)


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
        SELECT a.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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
    con.execute('BEGIN')
    rows = con.execute(query, params).fetchall()
    result = _availability_rows(con, rows)
    con.close()
    return jsonify(result)


@bp.route('/api/schedule/availability', methods=['POST'])
@login_required
def schedule_avail_upsert():
    auth0_id   = request.jwt_payload.get('sub', '')
    data       = request.get_json(silent=True) or {}
    try:
        day = _availability_day(data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    emp = _get_or_create_employee(con, auth0_id)
    con.execute('BEGIN IMMEDIATE')
    _save_availability(con, emp['id'], store_id, day)
    _invalidate_availability(con, emp['id'], store_id, day[0])
    con.commit()
    row = con.execute(
        'SELECT * FROM availability WHERE employee_id = ? AND date = ? AND store_id = ?',
        (emp['id'], day[0], store_id)
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
    con.execute('BEGIN IMMEDIATE')
    con.execute('DELETE FROM availability WHERE id = ?', (avail_id,))
    _invalidate_availability(con, emp['id'], row['store_id'], row['date'])
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
                SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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
                SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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
            SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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
            SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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
        SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee,
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


@bp.route('/api/schedule/attendance/today', methods=['GET', 'POST'])
@role_required('staff')
def schedule_attendance_today():
    data = request.get_json(silent=True) if request.method == 'POST' else None
    if request.method == 'POST' and (
        not isinstance(data, dict) or set(data) != {'shift_id'}
        or type(data['shift_id']) is not int or data['shift_id'] <= 0
    ):
        return jsonify({'error': 'A positive shift_id is required; time and employee are recorded automatically.'}), 400

    con = get_db()
    try:
        # Serialize with shift edits and other punch requests before checking eligibility.
        con.execute('BEGIN IMMEDIATE' if request.method == 'POST' else 'BEGIN')
        now = _dt.datetime.now(_dt.timezone.utc)
        today = now.astimezone(ZoneInfo('America/Toronto')).date().isoformat()
        employee = con.execute(
            'SELECT id FROM employees WHERE auth0_id=? AND is_active=1',
            (request.jwt_payload['sub'],),
        ).fetchone()
        shift = attendance = None
        if employee:
            shift = con.execute('''SELECT s.id,s.date,s.start_time,s.end_time,s.store_id,
                    st.code AS store_code,st.name AS store_name
                FROM shifts s JOIN stores st ON st.id=s.store_id
                WHERE s.employee_id=? AND s.date=? AND st.is_active=1''',
                (employee['id'], today)).fetchone()
            attendance = con.execute('''SELECT * FROM schedule_attendance
                WHERE employee_id=? AND business_date=?''', (employee['id'], today)).fetchone()
        if request.method == 'POST':
            if not employee or not shift or data['shift_id'] != shift['id']:
                return jsonify({'error': 'An active assigned shift for today is required. Refresh your schedule.'}), 403
            if attendance is None:
                con.execute('''INSERT INTO schedule_attendance
                    (employee_id,business_date,store_id,shift_id,punched_in_at)
                    VALUES (?,?,?,?,?)''',
                    (employee['id'], today, shift['store_id'], shift['id'], now.isoformat()))
                attendance = con.execute('''SELECT * FROM schedule_attendance
                    WHERE employee_id=? AND business_date=?''', (employee['id'], today)).fetchone()
        con.commit()
        return jsonify({'business_date': today, 'shift': dict(shift) if shift else None,
                        'attendance': dict(attendance) if attendance else None})
    finally:
        con.close()


@bp.route('/api/schedule/shifts', methods=['POST'])
@role_required('manager')
def schedule_shifts_create():
    data        = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400
    assigned_by = request.jwt_payload.get('sub', '')
    employee_id = data.get('employee_id')
    shift_date  = data.get('date', '')
    start_time  = data.get('start_time', '')
    end_time    = data.get('end_time', '')
    notes       = data.get('notes', '')
    position    = str(data.get('position') or '').strip()[:40]
    if not employee_id or not shift_date or not start_time or not end_time:
        return jsonify({'error': 'employee_id, date, start_time, end_time required'}), 400
    try:
        _schedule_date(shift_date)
        _schedule_times(start_time, end_time)
        if type(employee_id) is not int or employee_id <= 0:
            raise ValueError('employee_id must be a positive integer')
        if not isinstance(data.get('notes', ''), str):
            raise ValueError('Notes must be text')
        if 'require_availability' in data and type(data['require_availability']) is not bool:
            raise ValueError('require_availability must be a boolean')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    emp = con.execute(
        'SELECT id, is_active, is_schedulable FROM employees WHERE id = ?',
        (employee_id,),
    ).fetchone()
    if not emp or not emp['is_active']:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    if not emp['is_schedulable']:
        con.close()
        return jsonify({'error': 'Employee is disabled for shift assignment'}), 409
    if con.execute('SELECT id FROM shifts WHERE employee_id=? AND date=?', (employee_id,shift_date)).fetchone():
        con.close()
        return jsonify({'error': 'Employee already has a shift on this date. Edit the existing shift.'}), 409
    if data.get('require_availability') and not _availability_allows_shift(con, employee_id,store_id,shift_date,start_time,end_time):
        con.close()
        return jsonify({'error': 'Submitted availability no longer covers these hours. Reload availability.'}), 409
    con.execute("""INSERT INTO shifts
        (employee_id,date,start_time,end_time,assigned_by,notes,store_id,position)
        VALUES (?,?,?,?,?,?,?,?)""",
        (employee_id,shift_date,start_time,end_time,assigned_by,notes,store_id,position))
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
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400
    con  = get_db()
    con.execute('BEGIN IMMEDIATE')
    row  = con.execute('SELECT * FROM shifts WHERE id = ?', (shift_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {}
    for field in ('start_time', 'end_time', 'notes'):
        if field in data:
            updates[field] = data[field]
    if 'position' in data:
        updates['position'] = str(data['position'] or '').strip()[:40]
    if 'store_code' in data:
        store_id, store_code, err = _require_store_body(con, data)
        if err:
            con.close()
            return err
        updates['store_id'] = store_id
    try:
        _schedule_times(updates.get('start_time', row['start_time']), updates.get('end_time', row['end_time']))
        if not isinstance(data.get('notes', ''), str):
            raise ValueError('Notes must be text')
        if 'require_availability' in data and type(data['require_availability']) is not bool:
            raise ValueError('require_availability must be a boolean')
    except ValueError as exc:
        con.close()
        return jsonify({'error': str(exc)}), 400
    if data.get('require_availability') and not _availability_allows_shift(
            con,row['employee_id'],updates.get('store_id',row['store_id']),row['date'],
            updates.get('start_time',row['start_time']),updates.get('end_time',row['end_time'])):
        con.close()
        return jsonify({'error': 'Submitted availability no longer covers these hours. Reload availability.'}), 409
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


# ─── Period notes + coverage checklist ────────────────────────────────────────
# A "period" is what the manager calendar currently shows, keyed as
# month:YYYY-MM, fortnight:YYYY-MM-DD, week:YYYY-MM-DD or day:YYYY-MM-DD.

_PERIOD_KEY_RE = _re.compile(r'^(month:\d{4}-\d{2}|(fortnight|week|day):\d{4}-\d{2}-\d{2})$')


def _require_period_key():
    key = (request.args.get('key') or '').strip()
    if not _PERIOD_KEY_RE.match(key):
        return None, (jsonify({'error': 'key must be month:YYYY-MM, fortnight:YYYY-MM-DD, week:YYYY-MM-DD or day:YYYY-MM-DD'}), 400)
    return key, None


@bp.route('/api/schedule/notes', methods=['GET'])
@role_required('manager')
def schedule_notes_get():
    key, err = _require_period_key()
    if err:
        return err
    con = get_db()
    row = con.execute('SELECT * FROM schedule_notes WHERE period_key = ?', (key,)).fetchone()
    con.close()
    if not row:
        return jsonify({'period_key': key, 'content': '', 'updated_by': '', 'updated_at': None})
    return jsonify(dict(row))


@bp.route('/api/schedule/notes', methods=['PUT'])
@role_required('manager')
def schedule_notes_put():
    data = request.get_json(silent=True) or {}
    key  = str(data.get('period_key') or '').strip()
    if not _PERIOD_KEY_RE.match(key):
        return jsonify({'error': 'period_key must be month:YYYY-MM, fortnight:YYYY-MM-DD, week:YYYY-MM-DD or day:YYYY-MM-DD'}), 400
    content    = str(data.get('content') or '')[:5000]
    updated_by = request.jwt_payload.get('sub', '')
    con = get_db()
    con.execute('''
        INSERT INTO schedule_notes (period_key, content, updated_by, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(period_key) DO UPDATE SET
            content    = excluded.content,
            updated_by = excluded.updated_by,
            updated_at = datetime('now')
    ''', (key, content, updated_by))
    con.commit()
    row = con.execute('SELECT * FROM schedule_notes WHERE period_key = ?', (key,)).fetchone()
    con.close()
    return jsonify(dict(row))


@bp.route('/api/schedule/checklist', methods=['GET'])
@role_required('manager')
def schedule_checklist_get():
    key, err = _require_period_key()
    if err:
        return err
    con  = get_db()
    rows = con.execute(
        'SELECT * FROM schedule_checklist WHERE period_key = ?', (key,)
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/schedule/checklist', methods=['PUT'])
@role_required('manager')
def schedule_checklist_put():
    """Mark one person as considered (or not) for a period, with optional note."""
    data = request.get_json(silent=True) or {}
    key  = str(data.get('period_key') or '').strip()
    if not _PERIOD_KEY_RE.match(key):
        return jsonify({'error': 'period_key must be month:YYYY-MM, fortnight:YYYY-MM-DD, week:YYYY-MM-DD or day:YYYY-MM-DD'}), 400
    try:
        employee_id = int(data.get('employee_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'employee_id must be an integer'}), 400
    considered = 1 if data.get('considered') else 0
    note       = str(data.get('note') or '')[:500]
    updated_by = request.jwt_payload.get('sub', '')
    con = get_db()
    emp = con.execute('SELECT id FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    con.execute('''
        INSERT INTO schedule_checklist (period_key, employee_id, considered, note, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(period_key, employee_id) DO UPDATE SET
            considered = excluded.considered,
            note       = excluded.note,
            updated_by = excluded.updated_by,
            updated_at = datetime('now')
    ''', (key, employee_id, considered, note, updated_by))
    con.commit()
    row = con.execute(
        'SELECT * FROM schedule_checklist WHERE period_key = ? AND employee_id = ?',
        (key, employee_id)
    ).fetchone()
    con.close()
    return jsonify(dict(row))


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
            'is_trainee':  emp['is_trainee'] if 'is_trainee' in emp.keys() else 0,
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
        position    = s['position'] if 'position' in s.keys() else ''
        summary  = f'POPCORE shift — {store_label}'
        if position:
            summary += f' ({position})'
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
