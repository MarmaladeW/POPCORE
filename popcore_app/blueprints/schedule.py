"""
blueprints/schedule.py — employee profiles, availability, shifts, monthly hours report.
"""
import datetime as _dt
import json as _json
import math
import secrets
import re as _re
from zoneinfo import ZoneInfo

from functools import wraps

from flask import Blueprint, request, jsonify, Response

from db import get_db
import activity
import people
import ranks
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


# An actual finish at or before the start, up to this time, is on the next day.
_LATEST_NEXT_DAY_FINISH = '01:00'


def _shift_hours(shift) -> float:
    """Worked hours for a shift: to the recorded actual finish, else the planned end."""
    start, actual = shift['start_time'], shift['actual_end_time']
    hours = _hours_between(start, actual or shift['end_time'])
    if actual and actual <= start and actual <= _LATEST_NEXT_DAY_FINISH:
        hours = _hours_between(start, '24:00') + _hours_between('00:00', actual)
    return hours


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


def _toronto_today():
    """Today in Toronto; the server clock is UTC, which is already tomorrow from 20:00 (19:00 in winter)."""
    return _dt.datetime.now(_dt.timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()


def _current_wage_anchor(start_day: int):
    """Return (year, month) of the wage period containing today."""
    today = _toronto_today()
    year, month = today.year, today.month
    if today.day < start_day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return year, month


# ─── 店长/副店长 only look, at the next two weeks (2026-11-28: managers must not edit the schedule; they always see
# the next two weeks, today and the 13 days after it, no past days; replaces the wage month picked earlier that day
# and 2026-11-27's this month + next, in which they could still change shifts) ───────────────────────────────────────
# Only an admin adds, changes or removes shifts, the period notes, the checklist, the reminder and schedule-only
# trainees. A manager reads the team's schedule, availability and notes only for the next two weeks; admins see
# every month. 月工时 is one's own (same day: "manager can only see their own monthly report"): the team report,
# another person's month and another person's hours are the admin's. Everyone's own shifts, own availability,
# own 月工时, 打卡 and own calendar feed stay theirs in every month.

MANAGER_DAYS = 14
MONTH_CLOSED = '店长只能看接下来两周的排班 · Managers see only the next two weeks of the schedule.'
READ_ONLY = '排班只能由管理员修改 · Only an admin changes the schedule.'


def _is_admin():
    return ROLE_HIERARCHY.get(request.jwt_payload.get(ROLE_CLAIM, 'viewer'), 0) >= ROLE_HIERARCHY['admin']


def admin_changes(f):
    """A change to the team's 排班: staff get role_required's 403, a 店长/副店长 403 schedule_read_only."""
    @role_required('manager')
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _is_admin():
            return jsonify({'code': 'schedule_read_only', 'error': READ_ONLY}), 403
        return f(*args, **kwargs)
    return decorated


def _manager_window(con):
    """(first, last) ISO dates a manager may see, or None for an admin: today in Toronto and the 13 days after it."""
    if _is_admin():
        return None
    today = _toronto_today()
    return str(today), str(today + _dt.timedelta(days=MANAGER_DAYS - 1))


def _within_window(con, start, end):
    """The asked date range cut to the manager's window (an admin's stays as asked)."""
    window = _manager_window(con)
    if window is None:
        return start, end
    return max(start or window[0], window[0]), min(end or window[1], window[1])


def _month_closed(con, first, last=None):
    """A 403 answer when the dates first..last lie wholly outside the manager's window, else None."""
    window = _manager_window(con)
    first, last = str(first)[:10], str(last or first)[:10]
    if window and (last < window[0] or first > window[1]):
        return jsonify({'code': 'schedule_month_closed', 'error': MONTH_CLOSED}), 403
    return None


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
    # The months a manager's 排班 opens on (None for an admin, who sees every month, and for staff, who see their own).
    role = ROLE_HIERARCHY.get(request.jwt_payload.get(ROLE_CLAIM, 'viewer'), 0)
    window = _manager_window(con) if role == ROLE_HIERARCHY['manager'] else None
    result['manager_window'] = {'first': window[0], 'last': window[1]} if window else None
    con.close()
    return jsonify(result)


@bp.route('/api/schedule/reminder', methods=['PUT'])
@admin_changes
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
    return jsonify(people.public(emp))


@bp.route('/api/schedule/me', methods=['PATCH'])
@login_required
def schedule_me_patch():
    auth0_id = request.jwt_payload.get('sub', '')
    data     = request.get_json(silent=True) or {}
    # Owner, 2026-11-26: the name is what every mark of who did something shows, so nobody renames themselves.
    if ('name' in data or 'email' in data) and \
            ROLE_HIERARCHY.get(request.jwt_payload.get(ROLE_CLAIM), 0) < ROLE_HIERARCHY['admin']:
        return jsonify({'error': '姓名和邮箱只能由管理员修改 Only an admin can change a name or email',
                        'code': 'name_locked'}), 403
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
    return jsonify(people.public(row))


@bp.route('/api/schedule/employees', methods=['GET'])
@role_required('manager')
def schedule_employees():
    con  = get_db()
    rows = con.execute(
        f"SELECT * FROM employees WHERE is_active = 1 ORDER BY {ranks.order_by('')}"
    ).fetchall()
    employees = [people.public(r) for r in rows]

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
    # 店长 first, then by rank (owner, 2026-11-19), names fetched above included; old trainee records last.
    order = {rank: index for index, rank in enumerate(ranks.RANKS)}
    employees.sort(key=lambda e: (e.get('is_trainee') or 0, order.get(e.get('rank'), len(ranks.RANKS)),
                                  (e.get('name') or e.get('email') or '').lower()))
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
    return jsonify(people.public(updated))


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
    return jsonify([people.public(r) for r in rows])


@bp.route('/api/schedule/trainees', methods=['POST'])
@admin_changes
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
    return jsonify(people.public(row)), 201


@bp.route('/api/schedule/trainees/<int:trainee_id>', methods=['DELETE'])
@admin_changes
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
    start, end = _within_window(con, start, end)
    query = '''
        SELECT a.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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
    query += f" ORDER BY a.date, {ranks.order_by('e')}"
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

    if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
        start, end = _within_window(con, start, end)
    if store_code == 'ALL':
        if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
            query  = '''
                SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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
                SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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
            SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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
            SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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

    query += f" ORDER BY s.date, {ranks.order_by('e')}"
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
        SELECT s.*, e.name AS employee_name, e.auth0_id, e.is_trainee, e.regular_since, e.is_training, e.rank,
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


# ─── 打卡 only at the store (owner, 2026-11-27: "make sure they dont click 打卡 when they are not in the store yet";
# Markham has no store computer, so the owner picked GPS) ──────────────────────────────────────────────────────
# Tapping 打卡 sends the phone's location. A punch counts only within PUNCH_RADIUS_M of the shift's store, when the
# phone itself says it knows where it is to within PUNCH_ACCURACY_M, and from PUNCH_EARLY_MINUTES before the shift
# starts. An admin saves each store's spot in 设置 → 打卡定位 (one app_settings row, JSON keyed by store code); a
# store with no spot saved takes no punches. The staff member's coordinates are never stored: only the distance,
# on the punch's 动态 line.

PUNCH_EARLY_MINUTES = 30
PUNCH_RADIUS_M = 150
PUNCH_ACCURACY_M = 150
PUNCH_PLACES_KEY = 'punch_places'
_PUNCH_LOCATION = ('lat', 'lng', 'accuracy')


def _real_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _real_place(lat, lng):
    return _real_number(lat) and _real_number(lng) and -90 <= lat <= 90 and -180 <= lng <= 180


def _punch_places(con):
    row = con.execute('SELECT value FROM app_settings WHERE key=?', (PUNCH_PLACES_KEY,)).fetchone()
    try:
        saved = _json.loads(row['value']) if row else {}
    except ValueError:
        saved = {}
    return {code: place for code, place in saved.items() if isinstance(place, dict)} if isinstance(saved, dict) else {}


def _metres_between(lat1, lng1, lat2, lng2):
    """Distance over the Earth's surface (haversine), in metres."""
    lat1, lng1, lat2, lng2 = map(math.radians, (lat1, lng1, lat2, lng2))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _far(metres):
    return (f'{metres} 米', f'{metres} m') if metres < 1000 else (f'{metres / 1000:.1f} 公里', f'{metres / 1000:.1f} km')


def _punch_status(con, shift, now):
    """From when this shift can punch in, and whether its store has a spot saved."""
    hour, minute = map(int, shift['start_time'].split(':'))
    opens = max(0, hour * 60 + minute - PUNCH_EARLY_MINUTES)
    opens_at = f'{opens // 60:02d}:{opens % 60:02d}'
    return {'opens_at': opens_at, 'radius_m': PUNCH_RADIUS_M,
            'too_early': now.astimezone(ZoneInfo('America/Toronto')).strftime('%H:%M') < opens_at,
            'place_set': shift['store_code'] in _punch_places(con)}


def _punch_refusal(con, shift, punch, data):
    """(None, metres from the store) when this punch counts, else ((code, message), None)."""
    if punch['too_early']:
        return ('punch_too_early', f"{punch['opens_at']} 起才能打卡（上班前 {PUNCH_EARLY_MINUTES} 分钟）· Punch in from "
                f"{punch['opens_at']}, {PUNCH_EARLY_MINUTES} minutes before your shift."), None
    place = _punch_places(con).get(shift['store_code'])
    if not place or not _real_place(place.get('lat'), place.get('lng')):
        return ('punch_place_unset', '这家店还没设置打卡位置，请找管理员 · This store has no punch-in location yet. '
                'Ask an admin.'), None
    if 'lat' not in data:
        return ('punch_needs_location', '打卡要用定位，请允许 popcore.store 使用你的位置 · Punch-in needs your location: '
                'allow popcore.store to use it.'), None
    if data['accuracy'] > PUNCH_ACCURACY_M:
        zh, en = _far(round(data['accuracy']))
        return ('punch_location_rough', f'手机的定位不够准（±{zh}），走到门口再试 · Your phone only knows where you are to '
                f'±{en}. Step to the door and try again.'), None
    metres = round(_metres_between(data['lat'], data['lng'], place['lat'], place['lng']))
    if metres > PUNCH_RADIUS_M:
        zh, en = _far(metres)
        return ('punch_too_far', f'你离店还有 {zh}，到店再打卡 · You are {en} from the store. Punch in when you get '
                'there.'), None
    return None, metres


@bp.route('/api/schedule/attendance/today', methods=['GET', 'POST'])
@role_required('staff')
def schedule_attendance_today():
    data = request.get_json(silent=True) if request.method == 'POST' else None
    if request.method == 'POST' and (
        not isinstance(data, dict) or 'shift_id' not in data or not set(data) <= {'shift_id', *_PUNCH_LOCATION}
        or type(data['shift_id']) is not int or data['shift_id'] <= 0
        # The location comes whole or not at all: a retry of a saved punch may send none.
        or (set(data) & set(_PUNCH_LOCATION) and not (
            set(_PUNCH_LOCATION) <= set(data) and _real_place(data['lat'], data['lng'])
            and _real_number(data['accuracy']) and data['accuracy'] >= 0))
    ):
        return jsonify({'error': 'Send shift_id, and the phone\'s lat, lng and accuracy; time and employee are '
                                 'recorded automatically.'}), 400

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
        punch = _punch_status(con, shift, now) if shift else None
        if request.method == 'POST':
            if not employee or not shift or data['shift_id'] != shift['id']:
                return jsonify({'error': 'An active assigned shift for today is required. Refresh your schedule.'}), 403
            # A punch already saved stays saved: a retry answers with it wherever the phone is now.
            if attendance is None:
                refusal, metres = _punch_refusal(con, shift, punch, data)
                if refusal:
                    return jsonify({'code': refusal[0], 'error': refusal[1], 'punch': punch}), 403
                con.execute('''INSERT INTO schedule_attendance
                    (employee_id,business_date,store_id,shift_id,punched_in_at)
                    VALUES (?,?,?,?,?)''',
                    (employee['id'], today, shift['store_id'], shift['id'], now.isoformat()))
                attendance = con.execute('''SELECT * FROM schedule_attendance
                    WHERE employee_id=? AND business_date=?''', (employee['id'], today)).fetchone()
                activity.note(store_id=shift['store_id'], detail=f'上班 In · 离店 {metres} m from the store')
        con.commit()
        return jsonify({'business_date': today, 'shift': dict(shift) if shift else None,
                        'attendance': dict(attendance) if attendance else None, 'punch': punch})
    finally:
        con.close()


def _punch_places_view(con):
    saved = _punch_places(con)
    return {'radius_m': PUNCH_RADIUS_M, 'stores': [
        {'code': store['code'], 'name': store['name'], 'saved': saved.get(store['code'])}
        for store in con.execute('SELECT code, name FROM stores WHERE is_active=1 ORDER BY id').fetchall()]}


@bp.route('/api/schedule/punch-places', methods=['GET'])
@role_required('admin')
def punch_places_get():
    con = get_db()
    try:
        return jsonify(_punch_places_view(con))
    finally:
        con.close()


@bp.route('/api/schedule/punch-places/<store_code>', methods=['PUT', 'DELETE'])
@role_required('admin')
def punch_place_set(store_code):
    """PUT {lat, lng} saves where the store is (the admin's phone standing in it, or a pin copied from a map);
    DELETE forgets it, and that store takes no punches until a spot is saved again."""
    data = request.get_json(silent=True) if request.method == 'PUT' else None
    if request.method == 'PUT' and (not isinstance(data, dict) or set(data) != {'lat', 'lng'}
                                    or not _real_place(data['lat'], data['lng'])):
        return jsonify({'error': 'Send the store\'s lat and lng.'}), 400
    con = get_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        store = con.execute('SELECT id FROM stores WHERE code=? AND is_active=1', (store_code,)).fetchone()
        if not store:
            return jsonify({'error': 'Unknown store'}), 404
        saved = _punch_places(con)
        if data is None:
            saved.pop(store_code, None)
        else:
            saved[store_code] = {'lat': round(float(data['lat']), 6), 'lng': round(float(data['lng']), 6),
                                 'saved_at': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}
        con.execute('INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (PUNCH_PLACES_KEY, _json.dumps(saved, sort_keys=True)))
        con.commit()
        place = saved.get(store_code)
        activity.note(store_id=store['id'], detail=f"{place['lat']}, {place['lng']}" if place else '清除 Cleared')
        return jsonify(_punch_places_view(con))
    finally:
        con.close()


@bp.route('/api/schedule/shifts', methods=['POST'])
@admin_changes
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
@admin_changes
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
    if 'actual_end_time' in data:
        updates['actual_end_time'] = data['actual_end_time']
    try:
        start_time = updates.get('start_time', row['start_time'])
        _schedule_times(start_time, updates.get('end_time', row['end_time']))
        actual_end = updates.get('actual_end_time', row['actual_end_time'])
        if actual_end is not None and ('actual_end_time' in updates or 'start_time' in updates):
            if not isinstance(actual_end, str) or not _re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', actual_end) \
                    or (actual_end <= start_time and actual_end > _LATEST_NEXT_DAY_FINISH):
                raise ValueError('Use an HH:MM actual finish after the start time, or by 01:00 the next day')
        if updates.get('actual_end_time') is not None:
            today = _dt.datetime.now(_dt.timezone.utc).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
            if row['date'] > today:
                raise ValueError('Actual finish cannot be recorded before the shift date')
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
@admin_changes
def schedule_shifts_delete(shift_id):
    con = get_db()
    row = con.execute('SELECT id, date FROM shifts WHERE id = ?', (shift_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    con.execute('DELETE FROM shifts WHERE id = ?', (shift_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Conflict check ────────────────────────────────────────────────────────────

@bp.route('/api/schedule/conflicts', methods=['GET'])
@admin_changes
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


def _period_closed(key):
    """A 403 (or 400 for a date that does not exist) when a manager asks for a period wholly outside their two weeks."""
    kind, _, day = key.partition(':')
    try:
        first = _dt.date.fromisoformat(day + '-01' if kind == 'month' else day)
    except ValueError:
        return jsonify({'error': 'The period key is not a real date'}), 400
    if kind == 'month':
        last = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
    else:
        last = first + _dt.timedelta(days={'fortnight': 13, 'week': 6, 'day': 0}[kind])
    return _month_closed(get_db(), first, last)


def _require_period_key():
    key = (request.args.get('key') or '').strip()
    if not _PERIOD_KEY_RE.match(key):
        return None, (jsonify({'error': 'key must be month:YYYY-MM, fortnight:YYYY-MM-DD, week:YYYY-MM-DD or day:YYYY-MM-DD'}), 400)
    closed = _period_closed(key)
    return (None, closed) if closed else (key, None)


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
@admin_changes
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
@admin_changes
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
@role_required('admin')
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

    shift_filter = 'date >= ? AND date <= ?'
    filter_params: list = [str(first_day), str(last_day)]
    if store_code != 'ALL':
        shift_filter += ' AND store_id = ?'
        filter_params.append(store_id)

    # Only people who worked in this period and scope (owner, 2026-11-04); removed employees
    # keep the hours they worked.
    emps = con.execute(f'''SELECT * FROM employees
        WHERE id IN (SELECT employee_id FROM shifts WHERE {shift_filter})
        ORDER BY {ranks.order_by('')}''', filter_params).fetchall()
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
                  AND {shift_filter}
                ORDER BY date'''
    shifts = con.execute(query, emp_ids + filter_params).fetchall()
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
            # Rounded per shift, so the per-employee breakdown adds up to these totals.
            h  = round(_shift_hours(s), 2)
            total_hours += h
            d  = dt.date.fromisoformat(s['date'])
            wk = f'{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}'
            if wk not in weeks:
                weeks[wk] = {'total': 0.0, 'days': {}}
            weeks[wk]['total'] = round(weeks[wk]['total'] + h, 2)
            weeks[wk]['days'][s['date']] = round(
                weeks[wk]['days'].get(s['date'], 0.0) + h, 2
            )
        if total_hours <= 0:
            continue
        result.append({
            'id':          emp['id'],
            'name':        emp['name'],
            'email':       emp['email'],
            'is_trainee':  emp['is_trainee'] if 'is_trainee' in emp.keys() else 0,
            'regular_since': emp['regular_since'],
            'is_training': emp['is_training'],
            'rank':        emp['rank'],
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


@bp.route('/api/schedule/reports/monthly/<int:emp_id>', methods=['GET'])
@role_required('admin')
def schedule_report_monthly_employee(emp_id):
    """One employee's shifts in the monthly report's wage period and store scope,
    each with the hours it counts, so the admin can send the breakdown to them."""
    return _report_monthly_employee(emp_id)


@bp.route('/api/schedule/reports/monthly/me', methods=['GET'])
@login_required
def schedule_report_monthly_me():
    """The caller's own month, in the same shape (owner, 2026-11-26: each employee sees only their own monthly
    hours). Every store unless store_code names one."""
    emp = _get_or_create_employee(get_db(), request.jwt_payload.get('sub', ''))
    return _report_monthly_employee(emp['id'], own=True)


def _report_monthly_employee(emp_id, own=False):
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

    if own and not (request.args.get('store_code') or '').strip():
        store_id, store_code = None, 'ALL'
    else:
        store_id, store_code, err = _require_store_param(con)
        if err:
            con.close()
            return err

    emp = con.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404

    query = '''SELECT s.*, COALESCE(st.code, '') AS store_code
                 FROM shifts s
                 LEFT JOIN stores st ON st.id = s.store_id
                 WHERE s.employee_id = ? AND s.date >= ? AND s.date <= ?'''
    params: list = [emp_id, str(first_day), str(last_day)]
    if store_code != 'ALL':
        query += ' AND s.store_id = ?'
        params.append(store_id)
    rows = con.execute(query + ' ORDER BY s.date, s.start_time', params).fetchall()
    con.close()

    shifts = []
    for r in rows:
        shifts.append({
            'id':              r['id'],
            'date':            r['date'],
            'store_code':      r['store_code'],
            'start_time':      r['start_time'],
            'end_time':        r['end_time'],
            'actual_end_time': r['actual_end_time'],
            'position':        r['position'] or '',
            'hours':           round(_shift_hours(r), 2),
        })

    return jsonify({
        'month':           f'{year}-{month:02d}',
        'period_start':    str(first_day),
        'period_end':      str(last_day),
        'month_start_day': start_day,
        'store_code':      store_code,
        'employee': {
            'id':            emp['id'],
            'name':          emp['name'],
            'email':         emp['email'],
            'color':         emp['color'],
            'is_trainee':    emp['is_trainee'],
            'regular_since': emp['regular_since'],
            'is_training':   emp['is_training'],
            'rank':          emp['rank'],
        },
        'total_hours': round(sum(s['hours'] for s in shifts), 2),
        'shifts':      shifts,
    })


# ─── Per-employee hours (wage period) ─────────────────────────────────────────

@bp.route('/api/schedule/employees/<int:emp_id>/hours', methods=['GET'])
@role_required('admin')
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
        h = _shift_hours(r)
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
    # A removed or blocked employee's feed stops at once, even if the URL was shared.
    emp = con.execute(
        'SELECT * FROM employees WHERE ical_token = ? AND is_active = 1', (token,)
    ).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Not found'}), 404

    window_start = _toronto_today() - _dt.timedelta(days=90)
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
