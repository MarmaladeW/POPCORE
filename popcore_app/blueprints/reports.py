import csv,io
from flask import Blueprint,jsonify,request,Response
from auth import login_required
from db import get_db,esc_csv
from inventory_commands import InventoryError
from operational_reports import query_report

bp=Blueprint('reports',__name__)
def _run(name): return query_report(get_db(),name,actor=request.jwt_payload,filters=request.args.to_dict())
def _error(exc):
    if isinstance(exc,KeyError): return jsonify({'error':'Unknown report'}),404
    if isinstance(exc,PermissionError): return jsonify({'error':'Report access denied'}),403
    if isinstance(exc,(InventoryError,ValueError)): return jsonify({'error':str(exc)}),400
    raise exc
@bp.get('/api/reports/<name>')
@login_required
def report(name):
    try:return jsonify(_run(name))
    except (KeyError,PermissionError,InventoryError,ValueError) as exc:return _error(exc)
@bp.get('/api/reports/<name>/export.csv')
@login_required
def export(name):
    try:
        args=request.args.to_dict();args['page']='1';args['page_size']='500';data=query_report(get_db(),name,actor=request.jwt_payload,filters=args)
        if data['total_rows']>500:return jsonify({'error':'Export exceeds 500 rows'}),413
        cols=sorted({k for row in data['items'] for k in row})
        output=io.StringIO();output.write(','.join(esc_csv(c) for c in cols)+'\r\n')
        for row in data['items']:output.write(','.join(esc_csv(row.get(c)) for c in cols)+'\r\n')
        response=Response(output.getvalue(),content_type='text/csv; charset=utf-8');response.headers['Content-Disposition']=f'attachment; filename="{name}.csv"';response.headers['Cache-Control']='private, no-store';return response
    except (KeyError,PermissionError,InventoryError,ValueError) as exc:return _error(exc)
