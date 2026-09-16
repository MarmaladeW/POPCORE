from datetime import date
from urllib.parse import parse_qs


STORES = [
    {'id': 1, 'code': 'DT', 'name': 'Downtown', 'color': '#6366f1'},
    {'id': 2, 'code': 'MK', 'name': 'Markham', 'color': '#10b981'},
]


def product(store_code='DT'):
    return {
        'id': 1, 'product_id': 1, 'sku': f'{store_code}-FOUNDATION',
        'jizhanming': f'{store_code} Foundation Item',
        'name_cn_en': f'{store_code} Foundation Item', 'price': 20,
        'ip_series': 'Foundation', 'product_type': 'Figure', 'brand': 'POPCORE',
        'upstairs_qty': 2, 'instore_qty': 1, 'claw_qty': 0,
        'boxes_per_dan': 1, 'notes': '', 'hidden_count': '',
        'hidden_has_small': 0, 'hidden_has_large': 0,
    }


def payload(path, query_string='', empty=False):
    query = parse_qs(query_string)
    store_code = query.get('store_code', ['DT'])[0]
    item = product(store_code)
    rows = [] if empty else [item]
    today = date.today().isoformat()
    if path == '/api/series': return ['Foundation']
    if path == '/api/product_types': return ['Figure']
    if path == '/api/stores': return STORES
    if path == '/api/products/count': return {'count': 0 if empty else 1}
    if path == '/api/products/search': return rows
    if path == '/api/products/sync-sheet/last-sync': return {'last_sync_at': None, 'last_sync_count': None}
    if path == '/api/stock/summary':
        return {'products_tracked': len(rows), 'total_upstairs_qty': 0 if empty else 2,
                'total_instore_qty': 0 if empty else 1, 'low_stock_count': len(rows),
                'out_of_stock_count': 0}
    if path == '/api/stock':
        return {'items': rows, 'total': len(rows)} if 'page' in query else rows
    if path == '/api/stock/transactions':
        return [] if empty else [{
            'id': 10, 'product_id': 1, 'txn_type': 'ru_dian', 'qty': 1,
            'location': 'upstairs->instore', 'date': today, 'notes': '',
            'created_at': today, 'jizhanming': f'{store_code} History Item',
            'sku': f'{store_code}-HISTORY',
        }]
    if path == '/api/sales':
        return [] if empty else [{**item, 'date': today, 'qty_pos': 1, 'qty_cash': 0, 'qty_sold': 1}]
    if path == '/api/sales/report-metadata':
        return {'cash_actual': None, 'cash_expected': None, 'cash_difference': None,
                'employee_discounts': [], 'display_sales': [], 'claw_prizes': []}
    if path == '/api/sales/summary':
        return [] if empty else [{'date': today, 'product_count': 1, 'total_sold': 1, 'total_pos': 1, 'total_cash': 0}]
    if path == '/api/sales/recorded-dates': return [] if empty else [today]
    if path in ('/api/insights', '/api/history/restock', '/api/history/inventory-check'): return []
    if path == '/api/insights/count': return {'count': 0}
    if path == '/api/schedule/attendance/today':
        return {'business_date': today, 'shift': None, 'attendance': None}
    if path == '/api/schedule/me':
        return {'id': 1, 'auth0_id': 'fixture|admin', 'name': 'Foundation User', 'email': 'foundation@example.invalid'}
    if path == '/api/schedule/employees':
        return [{'id': 1, 'auth0_id': 'fixture|admin', 'name': 'Foundation User', 'email': 'foundation@example.invalid', 'is_schedulable': 1, 'is_trainee': 0}]
    if path == '/api/schedule/trainees': return []
    if path in ('/api/schedule/shifts', '/api/schedule/shifts/me'):
        return [] if empty else [{'id': 1, 'employee_id': 1, 'employee_name': 'Foundation User',
            'auth0_id': 'fixture|admin', 'date': today, 'start_time': '10:00', 'end_time': '18:00',
            'assigned_by': 'fixture|admin', 'notes': '', 'position': 'Sales', 'store_code': 'DT'}]
    if path in ('/api/schedule/availability', '/api/schedule/availability/me'): return []
    if path == '/api/schedule/notes': return {'period_key': 'month', 'content': '', 'updated_by': '', 'updated_at': None}
    if path == '/api/schedule/checklist': return []
    if path == '/api/schedule/calendar-feed': return {'token': 'fixture', 'path': '/fixture.ics'}
    if path == '/api/schedule/conflicts': return {'has_conflict': False, 'conflicts': []}
    if path in ('/api/settings', '/api/schedule/config'):
        return {'schedule_month_start_day': '1', 'schedule_required_staff': '{}',
                'schedule_open_hours': '{}', 'schedule_shift_presets': '[]', 'schedule_positions': '{}'}
    if path == '/api/employees/stores': return [{'employee_id': 1, 'auth0_id': 'fixture|admin', 'name': 'Foundation User', 'color': '#6366f1', 'is_schedulable': 1, 'stores': ['DT', 'MK']}]
    if path == '/api/restock/sessions/today': return []
    if path == '/api/inventory-check/today': return {'items': [], 'completed': False}
    return []
