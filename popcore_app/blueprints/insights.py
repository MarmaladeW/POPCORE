import json
from flask import Blueprint, jsonify, request
from auth import role_required
from db import get_db
from operational_reports import _scope

bp = Blueprint('insights', __name__)


def _authorized_codes(db, code=None):
    stores, _ = _scope(db, request.jwt_payload, code)
    if not stores:
        raise PermissionError('Insight store access denied')
    return [store['code'] for store in stores]


@bp.route('/api/insights', methods=['GET'])
@role_required('manager')
def list_insights():
    db = get_db()
    try: codes = _authorized_codes(db, request.args.get('store'))
    except PermissionError: return jsonify({'error':'Insight access denied'}),403
    marks = ','.join('?' for _ in codes)
    include_dismissed = request.args.get('include_dismissed', 'false').lower() == 'true'
    query = f'''SELECT i.*,p.jizhanming,p.sku FROM insights i LEFT JOIN products p ON p.id=i.product_id
                 WHERE i.store IN ({marks})'''
    if not include_dismissed: query += ' AND i.dismissed_at IS NULL'
    query += ' ORDER BY i.generated_at DESC,i.id DESC LIMIT 100'
    result=[]
    for row in db.execute(query,codes):
        item=dict(row)
        try:item['meta']=json.loads(item.get('meta') or '{}')
        except (ValueError,TypeError):item['meta']={}
        result.append(item)
    return jsonify(result)


@bp.route('/api/insights/count', methods=['GET'])
@role_required('manager')
def insight_count():
    db = get_db()
    try: codes = _authorized_codes(db, request.args.get('store'))
    except PermissionError: return jsonify({'error':'Insight access denied'}),403
    marks = ','.join('?' for _ in codes)
    count=db.execute(f'SELECT COUNT(*) FROM insights WHERE dismissed_at IS NULL AND store IN ({marks})',codes).fetchone()[0]
    return jsonify({'count':count})


@bp.route('/api/insights/<int:insight_id>/dismiss', methods=['POST'])
@role_required('manager')
def dismiss_insight(insight_id):
    db=get_db()
    insight=db.execute('SELECT store FROM insights WHERE id=?',(insight_id,)).fetchone()
    if insight is None:return jsonify({'error':'Insight not found'}),404
    try:_authorized_codes(db,insight['store'])
    except PermissionError:return jsonify({'error':'Insight access denied'}),403
    db.execute("UPDATE insights SET dismissed_at=datetime('now'),dismissed_by=? WHERE id=?",(request.jwt_payload['sub'],insight_id))
    db.commit()
    return jsonify({'ok':True})


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
