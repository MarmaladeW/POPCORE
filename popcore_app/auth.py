"""
auth.py — Auth0 JWT verification, decorators, and Management API helpers.
"""
import os
import time
import urllib.parse
from functools import wraps
from flask import request, jsonify
from jose import jwt as jose_jwt
import requests as http_req

from db import get_db

AUTH0_DOMAIN             = os.environ.get('AUTH0_DOMAIN', '')
AUTH0_AUDIENCE           = os.environ.get('AUTH0_AUDIENCE', 'https://popcore/api')
AUTH0_MGMT_CLIENT_ID     = os.environ.get('AUTH0_MGMT_CLIENT_ID', '')
AUTH0_MGMT_CLIENT_SECRET = os.environ.get('AUTH0_MGMT_CLIENT_SECRET', '')
AUTH0_MGMT_AUDIENCE      = f'https://{AUTH0_DOMAIN}/api/v2/'
AUTH0_CONNECTION         = 'Username-Password-Authentication'
ROLE_CLAIM               = 'https://popcore/role'
ALGORITHMS               = ['RS256']

ROLE_HIERARCHY = {'viewer': 0, 'staff': 1, 'manager': 2, 'admin': 3}

# ─── JWT helpers ──────────────────────────────────────────────────────────────

_jwks_cache: dict | None = None
# Unknown key ids (rotation, or anyone's forged token) refetch JWKS at most once per interval.
JWKS_REFETCH_INTERVAL = 300
_jwks_refetch_after = 0.0


def _get_jwks(refresh: bool = False) -> dict:
    global _jwks_cache
    if _jwks_cache is None or refresh:
        resp = http_req.get(
            f'https://{AUTH0_DOMAIN}/.well-known/jwks.json', timeout=10
        )
        resp.raise_for_status()
        jwks = resp.json()
        _jwks_cache = jwks  # replaced only by a good fetch; a failed refetch keeps the cached keys
    return _jwks_cache


def _decode_token(token: str) -> dict:
    global _jwks_refetch_after
    jwks = _get_jwks()
    header = jose_jwt.get_unverified_header(token)
    key = next((k for k in jwks['keys'] if k['kid'] == header['kid']), None)
    if key is None and time.monotonic() >= _jwks_refetch_after:
        # Refresh JWKS once in case of key rotation
        _jwks_refetch_after = time.monotonic() + JWKS_REFETCH_INTERVAL
        jwks = _get_jwks(refresh=True)
        key = next((k for k in jwks['keys'] if k['kid'] == header['kid']), None)
    if key is None:
        raise ValueError(f'Unknown key id: {header.get("kid")}')
    return jose_jwt.decode(
        token,
        key,
        algorithms=ALGORITHMS,
        audience=AUTH0_AUDIENCE,
        issuer=f'https://{AUTH0_DOMAIN}/',
        options={'require_exp': True, 'require_sub': True, 'require_iss': True},
    )


def _no_role():
    # An account no manager gave a role (e.g. a public Auth0 sign-up) gets no access, not viewer.
    return jsonify({'error': 'This account has no POPCORE role; ask a manager to set it up',
                    'code': 'no_role'}), 403


def _account_disabled(payload):
    """A 403 when this sign-in's employees row exists and is inactive (removed or blocked in Users).

    Accounts without a row yet (rows are created lazily), trainees and service subjects pass.
    Its token can stay valid for hours after removal, so this is checked on every request.
    """
    subject = payload.get('sub')
    if not isinstance(subject, str) or not subject:
        return None
    row = get_db().execute(
        'SELECT is_active FROM employees WHERE auth0_id = ?', (subject,)
    ).fetchone()
    if row is None or row[0]:
        return None
    return jsonify({'error': 'This account has been removed; ask a manager',
                    'code': 'account_disabled'}), 403


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
        try:
            request.jwt_payload = _decode_token(auth[7:])
        except http_req.RequestException:
            return jsonify({
                'error': 'Authentication service unavailable',
                'code': 'authentication_unavailable',
            }), 503
        except Exception:
            return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
        if request.jwt_payload.get(ROLE_CLAIM) not in ROLE_HIERARCHY:
            return _no_role()
        disabled = _account_disabled(request.jwt_payload)
        if disabled:
            return disabled
        return f(*args, **kwargs)
    return decorated


def role_required(*allowed_roles):
    min_level = min(ROLE_HIERARCHY.get(r, 99) for r in allowed_roles)
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer '):
                return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
            try:
                payload = _decode_token(auth[7:])
                request.jwt_payload = payload
            except http_req.RequestException:
                return jsonify({
                    'error': 'Authentication service unavailable',
                    'code': 'authentication_unavailable',
                }), 503
            except Exception:
                return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
            role = payload.get(ROLE_CLAIM)
            if role not in ROLE_HIERARCHY:
                return _no_role()
            disabled = _account_disabled(payload)
            if disabled:
                return disabled
            if ROLE_HIERARCHY[role] < min_level:
                return jsonify({'error': 'Forbidden'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Auth0 Management API helpers ─────────────────────────────────────────────

_mgmt_token_cache: dict = {'token': None, 'expiry': 0.0}
_role_ids_cache:   dict | None = None


def _get_mgmt_token() -> str:
    if _mgmt_token_cache['token'] and time.time() < _mgmt_token_cache['expiry']:
        return _mgmt_token_cache['token']
    resp = http_req.post(
        f'https://{AUTH0_DOMAIN}/oauth/token',
        json={
            'client_id':     AUTH0_MGMT_CLIENT_ID,
            'client_secret': AUTH0_MGMT_CLIENT_SECRET,
            'audience':      AUTH0_MGMT_AUDIENCE,
            'grant_type':    'client_credentials',
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _mgmt_token_cache['token']  = data['access_token']
    _mgmt_token_cache['expiry'] = time.time() + data.get('expires_in', 86400) - 60
    return _mgmt_token_cache['token']


def _mgmt_headers() -> dict:
    return {'Authorization': f'Bearer {_get_mgmt_token()}',
            'Content-Type': 'application/json'}


def _mgmt_get(path: str, **kw):
    return http_req.get(
        f'{AUTH0_MGMT_AUDIENCE}{path}',
        headers={'Authorization': f'Bearer {_get_mgmt_token()}'},
        timeout=10, **kw,
    )


def _mgmt_post(path: str, **kw):
    return http_req.post(
        f'{AUTH0_MGMT_AUDIENCE}{path}', headers=_mgmt_headers(), timeout=10, **kw
    )


def _mgmt_patch(path: str, **kw):
    return http_req.patch(
        f'{AUTH0_MGMT_AUDIENCE}{path}', headers=_mgmt_headers(), timeout=10, **kw
    )


def _mgmt_delete(path: str, **kw):
    return http_req.delete(
        f'{AUTH0_MGMT_AUDIENCE}{path}',
        headers={'Authorization': f'Bearer {_get_mgmt_token()}'},
        timeout=10, **kw,
    )


def _get_role_map() -> dict:
    """Return {role_name: role_id} for our 4 roles (cached per process)."""
    global _role_ids_cache
    if _role_ids_cache:
        return _role_ids_cache
    resp = _mgmt_get('roles', params={'per_page': 100})
    resp.raise_for_status()
    _role_ids_cache = {r['name']: r['id'] for r in resp.json()
                       if r['name'] in ROLE_HIERARCHY}
    return _role_ids_cache
