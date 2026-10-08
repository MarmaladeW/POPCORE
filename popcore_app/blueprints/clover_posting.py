"""Flag-gated route that posts a settled sandbox order as a real sale (rehearsal step 3)."""
import json

from flask import Blueprint, jsonify, request

import clover_posting as posting
from auth import role_required
from blueprints import clover_sandbox as adapter
from db import get_db
from inventory_commands import InventoryError

bp = Blueprint('clover_sale_posting', __name__, url_prefix='/api/clover-sandbox/checkouts')


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
def error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='Clover sale posting access denied'), 403
    return jsonify(error=str(exc)), 400


@bp.before_request
def configured():
    if not (adapter.enabled() and posting.enabled()):
        return jsonify(error='Clover sale posting is not enabled'), 404


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.post('/<int:order_id>/post')
@role_required('staff')
def post(order_id):
    with adapter.database() as sandbox:
        access = adapter.scope(sandbox)
        row = adapter.order_row(sandbox, order_id)
        value = adapter.detail(sandbox, row, access)
        owner = value['cashier_sub'] == request.jwt_payload['sub']
        if not (access['live_stores'] and (owner or access['role'] in ('admin', 'manager'))):
            raise PermissionError('Checkout cashier access denied')
        result = posting.post_settled_order(get_db(), sandbox, value, json.loads(row['payload']),
                                            request.jwt_payload, account=posting.merchant())
    return jsonify(result), 201 if result['created'] else 200
