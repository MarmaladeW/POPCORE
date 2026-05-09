"""
blueprints/users.py — user management (Auth0 Management API) + hidden image serving.
"""
import os
import urllib.parse
from flask import Blueprint, request, jsonify, send_from_directory

from db import get_db, HIDDEN_IMG_DIR
from auth import (
    login_required, role_required,
    ROLE_HIERARCHY, AUTH0_MGMT_CLIENT_ID, AUTH0_MGMT_CLIENT_SECRET,
    AUTH0_CONNECTION,
    _mgmt_get, _mgmt_post, _mgmt_patch, _mgmt_delete, _get_role_map,
)

bp = Blueprint('users', __name__)

_ORDER = ['admin', 'manager', 'staff', 'viewer']


def _highest_role(user_roles: list, id_to_name: dict) -> str:
    for r in _ORDER:
        if any(id_to_name.get(ur['id']) == r for ur in user_roles):
            return r
    return 'viewer'


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

    role_map   = _get_role_map()
    id_to_name = {v: k for k, v in role_map.items()}

    result = []
    for u in users:
        uid = u['user_id']
        uid_enc = urllib.parse.quote(uid, safe='')
        roles_resp = _mgmt_get(f'users/{uid_enc}/roles')
        user_roles = roles_resp.json() if roles_resp.ok else []
        result.append({
            'id':         uid,
            'username':   u.get('username') or u.get('nickname') or uid,
            'role':       _highest_role(user_roles, id_to_name),
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

    try:
        role_map = _get_role_map()
        role_id  = role_map.get(role)
        if role_id:
            _mgmt_post(
                f'users/{urllib.parse.quote(user_id, safe="")}/roles',
                json={'roles': [role_id]},
            )
    except Exception:
        pass
    return jsonify({'ok': True, 'id': user_id}), 201


@bp.route('/api/users/<string:uid>', methods=['PATCH'])
@role_required('admin')
def update_user(uid):
    uid_enc = urllib.parse.quote(uid, safe='')
    data    = request.get_json() or {}

    if 'role' in data:
        new_role = data['role']
        if new_role not in ROLE_HIERARCHY:
            return jsonify({'error': '无效角色'}), 400
        role_map    = _get_role_map()
        new_role_id = role_map.get(new_role)
        if not new_role_id:
            return jsonify({'error': 'Role not found in Auth0'}), 500
        cur_resp = _mgmt_get(f'users/{uid_enc}/roles')
        if cur_resp.ok:
            cur_ids = [r['id'] for r in cur_resp.json()]
            if cur_ids:
                _mgmt_delete(f'users/{uid_enc}/roles', json={'roles': cur_ids})
        _mgmt_post(f'users/{uid_enc}/roles', json={'roles': [new_role_id]})

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
    """Return all active employees with their assigned store codes."""
    con = get_db()
    rows = con.execute('''
        SELECT e.id AS employee_id, e.auth0_id, e.name,
               GROUP_CONCAT(s.code) AS store_codes
        FROM employees e
        LEFT JOIN employee_stores es ON es.employee_id = e.id
        LEFT JOIN stores s ON s.id = es.store_id AND s.is_active = 1
        WHERE e.is_active = 1
        GROUP BY e.id, e.auth0_id, e.name
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
            'stores':      codes,
        })
    return jsonify(result)


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
