"""
blueprints/schedule.py — employee profiles, availability, shifts, monthly hours report.
"""
from flask import Blueprint, request, jsonify

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
    query = 'SELECT * FROM availability WHERE employee_id = ? AND store_id = ?'
    params: list = [emp['id'], store_id]
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
        SELECT a.*, e.name AS employee_name, e.auth0_id
        FROM availability a
        JOIN employees e ON e.id = a.employee_id
        WHERE e.is_active = 1 AND a.store_id = ?
    '''
    params: list = [store_id]
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
        SELECT s.*, e.name AS employee_name, e.auth0_id
        FROM shifts s JOIN employees e ON e.id = s.employee_id
        WHERE s.employee_id = ? AND s.store_id = ?
    '''
    params: list = [emp['id'], store_id]
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
    try:
        year  = int(request.args.get('year',  dt.date.today().year))
        month = int(request.args.get('month', dt.date.today().month))
    except ValueError:
        return jsonify({'error': 'year and month must be integers'}), 400

    month_str = f'{year}-{month:02d}'
    first_day = dt.date(year, month, 1)
    if month == 12:
        last_day = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        last_day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err

    emps = con.execute('SELECT * FROM employees WHERE is_active = 1 ORDER BY name').fetchall()
    emp_ids = [e['id'] for e in emps]

    if not emp_ids:
        con.close()
        return jsonify({'month': month_str, 'employees': []})

    placeholders = ','.join('?' * len(emp_ids))
    shifts = con.execute(
        f'''SELECT * FROM shifts
            WHERE employee_id IN ({placeholders})
              AND store_id = ?
              AND date >= ? AND date <= ?
            ORDER BY date''',
        emp_ids + [store_id, str(first_day), str(last_day)]
    ).fetchall()
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

    return jsonify({'month': month_str, 'employees': result})
