"""
blueprints/users.py — user management (Auth0 Management API) + hidden image serving.
"""
import os
import re
import urllib.parse
from flask import Blueprint, request, jsonify, send_from_directory

_HEX_RE = re.compile(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def _is_valid_hex(color: str) -> bool:
    return bool(_HEX_RE.match(color))

from db import get_db, HIDDEN_IMG_DIR
from auth import (
    login_required, role_required,
    ROLE_HIERARCHY, AUTH0_MGMT_CLIENT_ID, AUTH0_MGMT_CLIENT_SECRET,
    AUTH0_CONNECTION,
    _mgmt_get, _mgmt_post, _mgmt_patch, _mgmt_delete, _get_role_map,
)

bp = Blueprint('users', __name__)

_ORDER = ['admin', 'manager', 'staff', 'viewer']


def _fetch_role_member_ids(role_id: str):
    """Return the user_ids holding role_id, or None if Auth0 could not be reached."""
    for _ in range(2):
        try:
            resp = _mgmt_get(f'roles/{role_id}/users', params={'per_page': 100})
            if resp.ok:
                return [u['user_id'] for u in resp.json()]
        except Exception:
            pass
    return None


# ─── Serve hidden images (stored outside static/) ────────────────────────────

@bp.route('/hidden_imgs/<path:filename>')
@login_required
def serve_hidden_img(filename):
    safe = os.path.normpath(filename).lstrip(os.sep)
    return send_from_directory(HIDDEN_IMG_DIR, safe)


# ─── User Management API (Admin only) ─────────────────────────────────────────

@bp.route('/api/users')
@role_required('admin')
def list_users():
    if not AUTH0_MGMT_CLIENT_ID or not AUTH0_MGMT_CLIENT_SECRET:
        return jsonify({'error': 'Auth0 Management API not configured on this server'}), 503
    try:
        resp = _mgmt_get('users', params={
            'q': f'identities.connection:"{AUTH0_CONNECTION}"',
            'search_engine': 'v3',
            'fields': 'user_id,username,nickname,blocked,created_at,last_login',
            'include_fields': 'true',
            'per_page': 100,
        })
    except Exception as exc:
        return jsonify({'error': f'Auth0 Management API request failed: {exc}'}), 502
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch users from Auth0'}), 502
    users = resp.json()

    # Resolve each user's highest role with one query per role (4 total)
    # instead of one query per user — the old N+1 pattern hit Auth0 rate
    # limits and silently misreported affected users as viewer.
    try:
        role_map = _get_role_map()
    except Exception as exc:
        return jsonify({'error': f'Failed to fetch roles from Auth0: {exc}'}), 502

    role_by_user: dict = {}
    for role_name in reversed(_ORDER):          # lowest first, higher overwrites
        role_id = role_map.get(role_name)
        if not role_id:
            continue
        member_ids = _fetch_role_member_ids(role_id)
        if member_ids is None:
            return jsonify({'error': f'Failed to fetch {role_name} role members from Auth0'}), 502
        for member_id in member_ids:
            role_by_user[member_id] = role_name

    result = []
    for u in users:
        uid = u['user_id']
        result.append({
            'id':         uid,
            'username':   u.get('username') or u.get('nickname') or uid,
            'role':       role_by_user.get(uid, 'viewer'),
            'is_active':  0 if u.get('blocked') else 1,
            'created_at': u.get('created_at', ''),
            'last_login': u.get('last_login', ''),
        })
    return jsonify(result)


@bp.route('/api/users', methods=['POST'])
@role_required('admin')
def create_user():
    if not AUTH0_MGMT_CLIENT_ID or not AUTH0_MGMT_CLIENT_SECRET:
        return jsonify({'error': 'Auth0 Management API not configured on this server'}), 503
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role     = (data.get('role') or 'viewer').strip()
    if not username or not password:
        return jsonify({'error': '用户名和密码必填'}), 400
    if len(password) < 8:
        return jsonify({'error': '密码至少8位'}), 400
    if role not in ROLE_HIERARCHY:
        return jsonify({'error': '无效角色'}), 400

    try:
        create_resp = _mgmt_post('users', json={
            'connection':     AUTH0_CONNECTION,
            'username':       username,
            'email':          f'{username}@popcore.internal',
            'password':       password,
            'email_verified': True,
        })
    except Exception as exc:
        return jsonify({'error': f'Auth0 Management API request failed: {exc}'}), 502
    if not create_resp.ok:
        err = create_resp.json().get('message', 'Create failed')
        code = 409 if ('already exists' in err.lower() or create_resp.status_code == 409) else 502
        return jsonify({'error': f'用户名 {username} 已存在' if code == 409 else err}), code

    user_id = create_resp.json()['user_id']

    # Import schedule helper here to avoid circular import
    from blueprints.schedule import _get_or_create_employee
    con = get_db()
    _get_or_create_employee(con, user_id, name=username, email=f'{username}@popcore.internal')
    con.close()

    # Assign the requested role, verifying the Auth0 response — a swallowed
    # failure here used to leave the account silently created as viewer.
    assigned = False
    try:
        role_map = _get_role_map()
        role_id  = role_map.get(role)
    except Exception:
        role_id = None
    if role_id:
        for _ in range(2):
            try:
                assign_resp = _mgmt_post(
                    f'users/{urllib.parse.quote(user_id, safe="")}/roles',
                    json={'roles': [role_id]},
                )
                if assign_resp.ok:
                    assigned = True
                    break
            except Exception:
                pass
    warning = None
    if not assigned and role != 'viewer':
        warning = (f'用户已创建，但角色 {role} 分配失败，请在"编辑"中重新设置角色 / '
                   f'User created, but assigning the {role} role failed — set it again via Edit.')
    return jsonify({'ok': True, 'id': user_id, 'warning': warning}), 201


@bp.route('/api/users/<string:uid>', methods=['PATCH'])
@role_required('admin')
def update_user(uid):
    uid_enc = urllib.parse.quote(uid, safe='')
    data    = request.get_json() or {}

    if 'role' in data:
        if uid == request.jwt_payload.get('sub', ''):
            return jsonify({'error': '不能修改当前登录账户的角色'}), 400
        new_role = data['role']
        if new_role not in ROLE_HIERARCHY:
            return jsonify({'error': '无效角色'}), 400
        # Add the new role before removing old ones and verify every Auth0
        # response — the old remove-then-add flow could strip all roles on a
        # mid-way failure while still reporting success.
        try:
            role_map    = _get_role_map()
            new_role_id = role_map.get(new_role)
            if not new_role_id:
                return jsonify({'error': 'Role not found in Auth0'}), 500
            cur_resp = _mgmt_get(f'users/{uid_enc}/roles')
            if not cur_resp.ok:
                return jsonify({'error': '读取当前角色失败，请重试 / Failed to read current roles'}), 502
            cur_ids  = [r['id'] for r in cur_resp.json()]
            add_resp = _mgmt_post(f'users/{uid_enc}/roles', json={'roles': [new_role_id]})
            if not add_resp.ok:
                return jsonify({'error': '角色更新失败，请重试 / Role update failed'}), 502
            old_ids = [i for i in cur_ids if i != new_role_id]
            if old_ids:
                del_resp = _mgmt_delete(f'users/{uid_enc}/roles', json={'roles': old_ids})
                if not del_resp.ok:
                    return jsonify({'error': '新角色已添加，但移除旧角色失败，请再保存一次 / '
                                             'New role added but removing the old one failed — save again'}), 502
        except Exception as exc:
            return jsonify({'error': f'Auth0 request failed: {exc}'}), 502

    body = {}
    if 'is_active' in data:
        body['blocked'] = not bool(data['is_active'])
    if data.get('password'):
        if len(data['password']) < 8:
            return jsonify({'error': '密码至少8位'}), 400
        body['password']   = data['password']
        body['connection'] = AUTH0_CONNECTION
    if body:
        resp = _mgmt_patch(f'users/{uid_enc}', json=body)
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Update failed')}), 502
    return jsonify({'ok': True})


@bp.route('/api/users/<string:uid>', methods=['DELETE'])
@role_required('admin')
def delete_user(uid):
    if uid == request.jwt_payload.get('sub', ''):
        return jsonify({'error': '不能删除当前登录账户'}), 400
    uid_enc = urllib.parse.quote(uid, safe='')
    resp    = _mgmt_delete(f'users/{uid_enc}')
    if not resp.ok and resp.status_code != 404:
        return jsonify({'error': 'Delete failed'}), 502
    con = get_db()
    con.execute('UPDATE employees SET is_active = 0 WHERE auth0_id = ?', (uid,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/employees/stores')
@role_required('manager')
def get_employee_stores():
    """Return all active employees with their assigned store codes and color."""
    con = get_db()
    rows = con.execute('''
        SELECT e.id AS employee_id, e.auth0_id, e.name,
               COALESCE(e.color, '#6366f1') AS color,
               e.is_schedulable,
               GROUP_CONCAT(s.code) AS store_codes
        FROM employees e
        LEFT JOIN employee_stores es ON es.employee_id = e.id
        LEFT JOIN stores s ON s.id = es.store_id AND s.is_active = 1
        WHERE e.is_active = 1
        GROUP BY e.id, e.auth0_id, e.name, e.color, e.is_schedulable
        ORDER BY e.name
    ''').fetchall()
    con.close()
    result = []
    for r in rows:
        codes = sorted(r['store_codes'].split(',')) if r['store_codes'] else []
        result.append({
            'employee_id': r['employee_id'],
            'auth0_id':    r['auth0_id'],
            'name':        r['name'],
            'color':       r['color'],
            'is_schedulable': r['is_schedulable'],
            'stores':      codes,
        })
    return jsonify(result)


@bp.route('/api/employees/<int:employee_id>/color', methods=['PATCH'])
@role_required('manager')
def patch_employee_color(employee_id):
    data  = request.get_json() or {}
    color = (data.get('color') or '').strip()
    if not _is_valid_hex(color):
        return jsonify({'error': 'color must be a valid hex color (#RGB or #RRGGBB)'}), 400
    con = get_db()
    row = con.execute(
        'SELECT id FROM employees WHERE id = ? AND is_active = 1', (employee_id,)
    ).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    con.execute('UPDATE employees SET color = ? WHERE id = ?', (color, employee_id))
    con.commit()
    updated = con.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
    con.close()
    return jsonify(dict(updated))


@bp.route('/api/employees/<int:employee_id>/schedulable', methods=['PATCH'])
@role_required('manager')
def patch_employee_schedulable(employee_id):
    data = request.get_json(silent=True) or {}
    value = data.get('is_schedulable')
    if isinstance(value, bool):
        normalized = int(value)
    elif isinstance(value, int) and value in (0, 1):
        normalized = value
    else:
        return jsonify({'error': 'is_schedulable must be a boolean or 0/1'}), 400

    con = get_db()
    row = con.execute(
        'SELECT id FROM employees WHERE id = ? AND is_active = 1',
        (employee_id,),
    ).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    con.execute(
        'UPDATE employees SET is_schedulable = ? WHERE id = ?',
        (normalized, employee_id),
    )
    con.commit()
    updated = con.execute(
        'SELECT * FROM employees WHERE id = ?', (employee_id,)
    ).fetchone()
    con.close()
    return jsonify(dict(updated))


@bp.route('/api/employees/<int:employee_id>/stores', methods=['PUT'])
@role_required('manager')
def put_employee_stores(employee_id):
    """Replace all store assignments for an employee."""
    data        = request.get_json() or {}
    store_codes = data.get('store_codes', [])
    if not isinstance(store_codes, list):
        return jsonify({'error': 'store_codes must be an array'}), 400

    con = get_db()
    emp = con.execute(
        'SELECT id FROM employees WHERE id = ? AND is_active = 1', (employee_id,)
    ).fetchone()
    if not emp:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404

    store_ids = []
    for code in store_codes:
        row = con.execute(
            'SELECT id FROM stores WHERE code = ? AND is_active = 1', (code,)
        ).fetchone()
        if not row:
            con.close()
            return jsonify({'error': f'Invalid store code: {code}'}), 400
        store_ids.append(row['id'])

    con.execute('DELETE FROM employee_stores WHERE employee_id = ?', (employee_id,))
    for sid in store_ids:
        con.execute(
            'INSERT INTO employee_stores (employee_id, store_id) VALUES (?, ?)',
            (employee_id, sid)
        )
    con.commit()

    rows = con.execute('''
        SELECT s.code FROM employee_stores es
        JOIN stores s ON s.id = es.store_id
        WHERE es.employee_id = ?
        ORDER BY s.code
    ''', (employee_id,)).fetchall()
    con.close()
    return jsonify({'employee_id': employee_id, 'stores': [r['code'] for r in rows]})



@role_required('admin')
def cleanup_employees():
    """Deactivate local employee records whose Auth0 account no longer exists."""
    try:
        resp = _mgmt_get('users', params={'per_page': 100, 'fields': 'user_id', 'include_fields': 'true'})
        resp.raise_for_status()
        live_ids = {u['user_id'] for u in resp.json()}
    except Exception as exc:
        return jsonify({'error': f'Auth0 lookup failed: {exc}'}), 502
    con = get_db()
    rows = con.execute('SELECT id, auth0_id FROM employees WHERE is_active = 1').fetchall()
    deactivated = []
    for row in rows:
        if row['auth0_id'] not in live_ids:
            con.execute('UPDATE employees SET is_active = 0 WHERE id = ?', (row['id'],))
            deactivated.append(row['auth0_id'])
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deactivated': deactivated, 'count': len(deactivated)})
