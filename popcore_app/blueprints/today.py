from flask import Blueprint, jsonify, request
from auth import login_required
from db import get_db
from inventory_commands import InventoryError
from today_operations import get_today

bp = Blueprint('today', __name__)

@bp.get('/api/today')
@login_required
def today():
    try:
        return jsonify(get_today(get_db(), actor=request.jwt_payload, store_code=request.args.get('store_code'), business_date=request.args.get('business_date')))
    except InventoryError as exc:
        return jsonify({'error':str(exc),'code':exc.code}), exc.status
    except PermissionError:
        return jsonify({'error':'Today store access denied','code':'today_forbidden'}), 403
    except ValueError as exc:
        return jsonify({'error':str(exc),'code':'invalid_input'}), 400
