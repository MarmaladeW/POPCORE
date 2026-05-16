import json
from flask import Blueprint, jsonify, request
from auth import login_required, role_required
from db import get_db

bp = Blueprint('insights', __name__)


@bp.route('/api/insights', methods=['GET'])
@login_required
def list_insights():
    store             = request.args.get('store')
    include_dismissed = request.args.get('include_dismissed', 'false').lower() == 'true'

    db     = get_db()
    params = []
    q      = '''
        SELECT i.*, p.jizhanming, p.sku
        FROM insights i
        LEFT JOIN products p ON p.id = i.product_id
        WHERE 1=1
    '''
    if store and store != 'ALL':
        q += ' AND i.store = ?'
        params.append(store)
    if not include_dismissed:
        q += ' AND i.dismissed_at IS NULL'
    q += ' ORDER BY i.generated_at DESC LIMIT 100'

    result = []
    for row in db.execute(q, params).fetchall():
        d = dict(row)
        try:
            d['meta'] = json.loads(d.get('meta') or '{}')
        except Exception:
            d['meta'] = {}
        result.append(d)
    return jsonify(result)


@bp.route('/api/insights/count', methods=['GET'])
@login_required
def insight_count():
    store  = request.args.get('store')
    db     = get_db()
    params = []
    q      = 'SELECT COUNT(*) AS cnt FROM insights WHERE dismissed_at IS NULL'
    if store and store != 'ALL':
        q += ' AND store = ?'
        params.append(store)
    cnt = db.execute(q, params).fetchone()['cnt']
    return jsonify({'count': cnt})


@bp.route('/api/insights/<int:insight_id>/dismiss', methods=['POST'])
@login_required
def dismiss_insight(insight_id):
    sub = getattr(request, 'jwt_payload', {}).get('sub', 'unknown')
    db  = get_db()
    db.execute(
        "UPDATE insights SET dismissed_at = datetime('now'), dismissed_by = ? WHERE id = ?",
        (sub, insight_id),
    )
    db.commit()
    return jsonify({'ok': True})


@bp.route('/api/insights/generate', methods=['POST'])
@role_required('admin')
def trigger_generate():
    from insights import generate_daily_insights
    count = generate_daily_insights()
    return jsonify({'generated': count})


@bp.route('/api/insights/thresholds', methods=['GET'])
@role_required('staff')
def get_thresholds():
    db   = get_db()
    rows = db.execute('SELECT * FROM insight_thresholds ORDER BY key').fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/insights/thresholds', methods=['PUT'])
@role_required('manager')
def update_thresholds():
    body    = request.get_json(force=True) or {}
    updates = body.get('thresholds', {})
    if not isinstance(updates, dict):
        return jsonify({'error': 'expected {"thresholds": {key: value}}'}), 400

    db = get_db()
    for key, value in updates.items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            return jsonify({'error': f'invalid value for {key}'}), 400
        db.execute(
            "UPDATE insight_thresholds SET value = ?, updated_at = datetime('now') WHERE key = ?",
            (value, key),
        )
    db.commit()
    return jsonify({'ok': True, 'updated': list(updates.keys())})
