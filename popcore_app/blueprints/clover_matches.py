"""
blueprints/clover_matches.py — Review old products against the Clover product master (manager).

Decisions stay editable until a manager applies them; see clover_match_review.py.
"""
from flask import Blueprint, jsonify, request

import db
from auth import role_required
from clover_match_review import apply_reviews, list_reviews, update_review
from db import get_db

bp = Blueprint('clover_matches', __name__)
MAX_APPLY = 200


@bp.route('/api/catalog/clover-matches')
@role_required('manager')
def get_matches():
    return jsonify(list_reviews(get_db()))


@bp.route('/api/catalog/clover-matches/<int:legacy_id>', methods=['PUT'])
@role_required('manager')
def put_match(legacy_id):
    con = get_db()
    try:
        update_review(con, legacy_id, request.get_json(silent=True) or {}, request.jwt_payload.get('sub'))
    except LookupError as exc:
        return jsonify({'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    con.commit()
    item = next(i for i in list_reviews(con)['items'] if i['legacy_product_id'] == legacy_id)
    return jsonify(item)


@bp.route('/api/catalog/clover-matches/apply', methods=['POST'])
@role_required('manager')
def apply_matches():
    ids = (request.get_json(silent=True) or {}).get('legacy_product_ids')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return jsonify({'error': 'Choose at least one product to apply.'}), 400
    if len(ids) > MAX_APPLY:
        return jsonify({'error': f'Apply at most {MAX_APPLY} products at a time.'}), 400
    results = apply_reviews(db.DB_PATH, list(dict.fromkeys(ids)), request.jwt_payload.get('sub'))
    return jsonify({'results': results})
