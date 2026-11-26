"""动态 Activity: every change anyone makes in the app, as one line like a group-chat message (owner, 2026-11-20:
"in WeChat it is super easy to check who did what … account tracking/leaving mark is really important").

Every successful change request (POST, PUT, PATCH, DELETE) leaves a row on its own, so nothing slips through even
where no one wrote a sentence for it: a label from LABELS (or the endpoint's name), the thing it was about, a
short detail and the store when one can be told. A route can add to its line with `note(...)`. A retried request
(the same Idempotency-Key from the same account) is kept once. Writing the row never fails the request.

Who sees what (owner, 2026-11-26: only the admin sees 动态): admins every line of every store. Everyone else,
managers included, only the lines they did themselves (`actor_sub` is their own account, a change made on the store
computer under their name + PIN included), at the stores they work at. The store computer's shared account did
none of its lines, so it sees none. `audience` is still written on every row, but no longer decides who reads it.
"""
import sqlite3
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, g, jsonify, request

import db
import people
from auth import ROLE_CLAIM, ROLE_HIERARCHY, login_required

bp = Blueprint('activity', __name__)
MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}
PAGE = 100

# endpoint: (kind, 中文, English, audience). Kinds are the filter chips on the 动态 screen.
LABELS = {
    # 日报
    'sales.submit_daily_report': ('report', '贴日报', 'Pasted daily report', 'store'),
    'sales.retry_report_stock': ('report', '再扣一次', 'Took off again', 'store'),
    'sales.check_report_stock_notice': ('report', '核对了', 'Checked a notice', 'store'),
    'sales.save_name_pick': ('report', '记住名称', 'Saved a name pick', 'store'),
    'sales.forget_name_pick': ('report', '忘掉名称', 'Removed a name pick', 'store'),
    'sales.ignore_name': ('report', '忽略名称', 'Ignored a name', 'store'),
    'sales.clear_sales_day': ('report', '清空当天销售', 'Cleared a sales day', 'manager'),
    'sales.delete_sales_record': ('report', '删除销售', 'Deleted a sale line', 'manager'),
    'sales.upsert_sale': ('report', '改销售', 'Edited a sale line', 'manager'),
    'sales.batch_upsert_sales': ('report', '改销售', 'Edited sale lines', 'manager'),
    'sales.add_product_to_sales': ('report', '加销售', 'Added a sale line', 'manager'),
    'sales.remap_historical_sale': ('report', '改匹配', 'Re-matched a sale', 'manager'),
    # 库存
    'restock.create_restock_session': ('stock', '新建补货单', 'Started a restock sheet', 'store'),
    'restock.add_restock_item': ('stock', '补货加货', 'Added to a restock sheet', 'store'),
    'restock.save_restock_series': ('stock', '补货加货', 'Added to a restock sheet', 'store'),
    'restock.delete_restock_item': ('stock', '补货删货', 'Removed from a restock sheet', 'store'),
    'restock.submit_restock_session': ('stock', '提交补货', 'Submitted a restock', 'store'),
    'restock.pick_restock_item': ('stock', '拣货', 'Picked restock goods', 'store'),
    'restock.restock_goods_action': ('stock', '补货', 'Restock', 'store'),
    'restock.complete_restock_session': ('stock', '补货完成', 'Finished a restock', 'store'),
    'restock.delete_restock_session': ('stock', '删除补货单', 'Deleted a restock sheet', 'store'),
    'goods.receipt_create': ('stock', '新建收货单', 'Started a receipt', 'store'),
    'goods.receipt_update': ('stock', '改收货单', 'Edited a receipt', 'store'),
    'goods.receipt_post': ('stock', '收货入库', 'Posted a receipt', 'store'),
    'goods.receipt_cancel': ('stock', '取消收货单', 'Cancelled a receipt', 'store'),
    'goods.transfer_create': ('stock', '新建调拨', 'Started a transfer', 'store'),
    'goods.transfer_action': ('stock', '调拨', 'Transfer', 'store'),
    'goods.count_create': ('stock', '盘点', 'Count', 'store'),
    'goods.count_update': ('stock', '改盘点', 'Edited a count', 'store'),
    'goods.count_action': ('stock', '盘点', 'Count', 'store'),
    'counting.save': ('stock', '盘点', 'Counted', 'store'),
    'store_events.create': ('stock', '门店记录', 'Store record', 'store'),
    'stock.post_inventory_command': ('stock', '改库存', 'Changed stock', 'store'),
    'stock.ru_dian': ('stock', '入店', 'Received goods', 'store'),
    'stock.restock_upstairs': ('stock', '入库', 'Stocked upstairs', 'store'),
    'stock.adjust_stock': ('stock', '改库存', 'Adjusted stock', 'store'),
    'stock.patch_stock': ('stock', '改库存', 'Adjusted stock', 'store'),
    'sales.batch_stock_operation': ('stock', '改库存', 'Changed stock', 'store'),
    'inventory.submit_inventory_check': ('stock', '晚盘', 'Evening count', 'store'),
    'goods.design_identify': ('stock', '认款式', 'Identified designs', 'store'),
    'goods.target_save': ('stock', '改楼下目标', 'Changed floor targets', 'store'),
    'losses.record': ('stock', '记损耗', 'Recorded a loss', 'manager'),
    'losses.undo': ('stock', '撤销损耗', 'Undid a loss', 'manager'),
    'losses.pile': ('stock', '损坏处理', 'Cleared the damaged pile', 'manager'),
    # 产品
    'products.create_product': ('product', '新产品', 'Added a product', 'store'),
    'products.update_product': ('product', '改产品', 'Edited a product', 'store'),
    'products.edit_product_design': ('product', '改款式', 'Edited a design', 'store'),
    'products.add_product_designs': ('product', '加款式', 'Added designs', 'store'),
    'products.remove_product_design': ('product', '删款式', 'Removed a design', 'store'),
    'products.remove_product_designs': ('product', '删款式', 'Removed designs', 'store'),
    'products.add_product_sealed_set': ('product', '加整端', 'Added a whole set', 'store'),
    'products.patch_product_inventory_identity': ('product', '改库存类型', 'Changed stock form', 'store'),
    'products.bulk_delete_products': ('product', '删除产品', 'Deleted products', 'manager'),
    'products.save_alias': ('product', '加别名', 'Added a name', 'store'),
    'products.delete_alias': ('product', '删别名', 'Removed a name', 'store'),
    'products.catalog_import_confirm': ('product', '导入产品', 'Imported products', 'manager'),
    'products.identity_import_confirm': ('product', '导入类型', 'Imported stock forms', 'manager'),
    'products.sync_sheet_confirm': ('product', '同步记账名', 'Synced the name sheet', 'manager'),
    'inventory.set_bestseller': ('product', '改畅销', 'Changed bestseller', 'store'),
    'design_prices.design_price_save': ('product', '改款式价格', 'Set design prices', 'manager'),
    # 网站订单、可疑人员、员工折扣
    'website.web_orders_step': ('web', '网站订单', 'Web order', 'store'),
    'website.website_order_dismiss': ('web', '网站订单不处理', 'Dismissed a web order', 'manager'),
    'website.website_order_retry': ('web', '网站订单重试', 'Retried a web order', 'manager'),
    'website.website_price_ok': ('web', '网站价格 OK', 'OK’d a website price', 'manager'),
    'website.website_switch': ('web', '网站同步开关', 'Website sync switch', 'manager'),
    'website.website_refresh': ('web', '网站刷新', 'Refreshed the website', 'manager'),
    'watch_list.create': ('watch', '加可疑人员', 'Added to the watch list', 'store'),
    'watch_list.update': ('watch', '改可疑人员', 'Edited the watch list', 'store'),
    'watch_list.add_photos': ('watch', '加照片', 'Added photos', 'store'),
    'watch_list.remove_photo': ('watch', '删照片', 'Removed a photo', 'store'),
    'watch_list.resolve': ('watch', '已解决', 'Resolved', 'store'),
    'watch_list.reopen': ('watch', '重新打开', 'Reopened', 'store'),
    'watch_list.delete': ('watch', '删除可疑人员', 'Deleted from the watch list', 'store'),
    'staff_discounts.add': ('discount', '记员工折扣', 'Added a staff discount', 'store'),
    'staff_discounts.fix': ('discount', '改员工折扣', 'Fixed a staff discount', 'manager'),
    'staff_discounts.ignore': ('discount', '不算员工折扣', 'Ignored a staff discount', 'manager'),
    'staff_discounts.restore': ('discount', '恢复员工折扣', 'Restored a staff discount', 'manager'),
    # 排班
    'schedule.schedule_shifts_create': ('schedule', '排班', 'Added a shift', 'store'),
    'schedule.schedule_shifts_update': ('schedule', '改班', 'Changed a shift', 'store'),
    'schedule.schedule_shifts_delete': ('schedule', '删班', 'Removed a shift', 'store'),
    'schedule.schedule_attendance_today': ('schedule', '打卡', 'Punch', 'manager'),
    'schedule.schedule_avail_upsert': ('schedule', '改可上班时间', 'Changed availability', 'manager'),
    'schedule.schedule_avail_delete': ('schedule', '删可上班时间', 'Removed availability', 'manager'),
    'schedule.schedule_availability_period': ('schedule', '改可上班时间', 'Changed availability', 'manager'),
    'schedule.schedule_employee_patch': ('people', '改员工', 'Edited an employee', 'manager'),
    'schedule.schedule_notes_put': ('schedule', '排班备注', 'Schedule notes', 'manager'),
    'schedule.schedule_checklist_put': ('schedule', '排班清单', 'Schedule checklist', 'manager'),
    # 员工、设置
    'users.create_user': ('people', '新员工账号', 'Added an account', 'manager'),
    'users.update_user': ('people', '改员工账号', 'Edited an account', 'manager'),
    'users.delete_user': ('people', '删除员工账号', 'Removed an account', 'manager'),
    'users.patch_employee_status': ('people', '改员工状态', 'Changed employee status', 'manager'),
    'users.put_employee_stores': ('people', '改员工门店', 'Changed employee stores', 'manager'),
    'users.patch_employee_schedulable': ('people', '改可排班', 'Changed schedulable', 'manager'),
    'stores.change_inventory_viewer': ('people', '库存查看', 'Inventory view', 'manager'),
    'stores.grant_inventory_access': ('people', '门店操作权限', 'Store operations access', 'manager'),
    'settings.put_settings': ('settings', '改设置', 'Changed settings', 'manager'),
    'pins.set_my_pin': ('people', '设置 PIN', 'Set a PIN', 'manager'),
    'pins.clear_pin': ('people', '重设 PIN', 'Reset a PIN', 'manager'),
    # 买单 (parked for staff, kept for managers)
    'checkout.create': ('checkout', '买单', 'Checkout', 'manager'),
    'stock_moves.stock_move_create': ('stock', '移货', 'Moved stock', 'store'),
    'stock_moves.stock_move_undo': ('stock', '撤销移货', 'Undid a move', 'store'),
    'checkout.quick_sale_save': ('sale', '记一笔', 'Recorded a sale', 'store'),
    'checkout.quick_sale_undo': ('sale', '撤销', 'Undid a sale', 'store'),
    'checkout.upload': ('sale', '加照片', 'Added a payment photo', 'store'),
    'checkout.change': ('checkout', '买单', 'Checkout', 'manager'),
    'special_orders.create': ('checkout', '特殊订单', 'Special order', 'manager'),
    'closing.submit': ('checkout', '收工', 'Closing', 'manager'),
    'closing.close': ('checkout', '收工', 'Closing', 'manager'),
}
# Requests that change nothing worth a line: previews, lookups and phone notification settings.
SKIP = {
    'sales.parse_daily_report', 'products.catalog_import_preview', 'products.identity_import_preview',
    'products.sync_sheet_preview', 'design_prices.design_price_check', 'products.batch_match_products',
    'website.web_orders_code', 'website.web_orders_notify_me', 'website.web_orders_notify_off',
    'website.web_orders_notify_test', 'schedule.schedule_calendar_feed_reset', 'insights.trigger_generate',
    'pins.check_pin', 'checkout.quick_sale_quote', 'stock_moves.stock_move_check',
}
# The in-between steps of 搬下来了 Done (one person does a whole restock in one tap) may ask to stay quiet; the
# line people see is the restock arriving on the floor, which is never quiet.
QUIET = {'restock.submit_restock_session', 'restock.pick_restock_item', 'restock.restock_goods_action:pick'}
KIND_NAMES = {
    'report': '日报 Daily report', 'stock': '库存 Stock', 'product': '产品 Products', 'web': '网站订单 Web orders',
    'watch': '可疑人员 Watch list', 'discount': '员工折扣 Staff discounts', 'schedule': '排班 Schedule',
    'people': '员工 People', 'settings': '设置 Settings', 'checkout': '买单 Checkout', 'sale': '记一笔 Sales',
    'other': '其他 Other',
}
# An action with no label of its own from these areas is about money, people or settings: managers only.
MANAGER_AREAS = {'users', 'settings', 'closing', 'payments', 'checkout', 'special_orders', 'sale_documents', 'insights',
                 'losses', 'clover_matches', 'design_prices', 'website', 'pins', 'stores'}
ACTIONS = {'pick': '发出 Sent', 'dispatch': '发出 Sent', 'receive': '收到 Received', 'return': '退回 Returned',
           'resolve-loss': '记为丢失 Lost', 'resolve_loss': '记为丢失 Lost', 'short-close': '关闭 Closed short',
           'short_close': '关闭 Closed short', 'post': '入库 Posted', 'cancel': '取消 Cancelled',
           'submit': '提交 Submitted', 'approve': '通过 Approved', 'ready': '备好 Ready',
           'handed-over': '已取货 Handed over', 'handed_over': '已取货 Handed over'}


def note(**fields):
    """A route adds to its own line: summary, detail, subject, store_id, kind, audience."""
    g.activity = {**getattr(g, 'activity', {}), **fields}


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS activity_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        at            TEXT    NOT NULL DEFAULT (datetime('now')),
        business_date TEXT    NOT NULL,
        store_id      INTEGER REFERENCES stores(id) ON DELETE SET NULL,
        actor_sub     TEXT    NOT NULL,
        actor_name    TEXT,
        via_sub       TEXT,
        via_name      TEXT,
        kind          TEXT    NOT NULL,
        endpoint      TEXT    NOT NULL,
        summary       TEXT    NOT NULL,
        detail        TEXT    NOT NULL DEFAULT '',
        link          TEXT,
        audience      TEXT    NOT NULL DEFAULT 'store' CHECK (audience IN ('store', 'manager')),
        request_key   TEXT,
        UNIQUE (actor_sub, request_key)
    )""")
    cur.execute('CREATE INDEX IF NOT EXISTS idx_activity_store_day ON activity_log(store_id, business_date, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_activity_day ON activity_log(business_date, id)')


def _one(con, sql, *args):
    try:
        row = con.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None
    return row


def _product_label(con, product_id):
    row = _one(con, 'SELECT sku, jizhanming, name_cn_en FROM products WHERE id=?', product_id)
    if row is None:
        return f'#{product_id}'
    name = row['jizhanming'] or row['name_cn_en'] or ''
    return ' '.join(filter(None, (row['sku'], name))) or f'#{product_id}'


def _store_of_code(con, code):
    row = _one(con, 'SELECT id FROM stores WHERE code=?', str(code).strip().upper()) if code else None
    return row['id'] if row else None


def _before(con, endpoint, view, body):
    """What the request is about and its store, read before it runs (a deleted row can still be named)."""
    about, store = None, None
    if 'sid' in view:
        row = _one(con, 'SELECT store_id FROM restock_sessions WHERE id=?', view['sid'])
        about, store = f"补货 #{view['sid']}", row and row['store_id']
    elif 'iid' in view:
        row = _one(con, """SELECT s.id, s.store_id, i.product_id FROM restock_items i JOIN restock_sessions s ON s.id=i.session_id
                           WHERE i.id=?""", view['iid'])
        if row:
            about, store = f"补货 #{row['id']} · {_product_label(con, row['product_id'])}", row['store_id']
    elif 'receipt_id' in view:
        row = _one(con, 'SELECT store_id FROM goods_receipts WHERE id=?', view['receipt_id'])
        about, store = f"收货 #{view['receipt_id']}", row and row['store_id']
    elif 'delivery_id' in view:
        row = _one(con, """SELECT d.kind, d.restock_session_id, l.store_id FROM inventory_deliveries d
                           JOIN inventory_locations l ON l.id=d.source_location_id WHERE d.id=?""", view['delivery_id'])
        if row:
            about = (f"补货 #{row['restock_session_id']}" if row['kind'] == 'restock' and row['restock_session_id']
                     else f"调拨 #{view['delivery_id']}")
            store = row['store_id']
    elif 'count_id' in view:
        row = _one(con, 'SELECT store_id FROM inventory_counts WHERE id=?', view['count_id'])
        about, store = f"盘点 #{view['count_id']}", row and row['store_id']
    elif 'notice_id' in view:
        row = _one(con, 'SELECT store_id, business_date, product_id FROM report_stock_issues WHERE id=?', view['notice_id'])
        if row:
            about, store = f"{_product_label(con, row['product_id'])} · {row['business_date']}", row['store_id']
    elif 'shift_id' in view:
        row = _one(con, """SELECT s.store_id, s.date, s.start_time, s.end_time, e.name FROM shifts s
                           LEFT JOIN employees e ON e.id=s.employee_id WHERE s.id=?""", view['shift_id'])
        if row:
            about = f"{row['name'] or ''} {row['date']} {row['start_time']}–{row['end_time']}".strip()
            store = row['store_id']
    elif 'wc_order_id' in view:
        row = _one(con, 'SELECT number, store_id FROM website_orders WHERE wc_order_id=?', view['wc_order_id'])
        if row:
            about, store = f"#{row['number']}", row['store_id']
    elif 'pid' in view:
        about = _product_label(con, view['pid'])
    elif 'employee_id' in view or 'emp_id' in view:
        row = _one(con, 'SELECT name FROM employees WHERE id=?', view.get('employee_id') or view.get('emp_id'))
        about = row and row['name']
    elif 'entry_id' in view and endpoint.startswith('watch_list.'):
        about = f"#{view['entry_id']}"
    elif 'loss_id' in view:
        about = f"#{view['loss_id']}"
    elif 'store_id' in view:
        store = view['store_id']
    for key in ('store_id',):
        if store is None and isinstance(body.get(key), int):
            store = body[key]
    if store is None:
        store = _store_of_code(con, body.get('store_code') or body.get('store') or request.args.get('store_code'))
    if store is None and isinstance(body.get('location_id'), int):
        row = _one(con, 'SELECT store_id FROM inventory_locations WHERE id=?', body['location_id'])
        store = row and row['store_id']
    if store is None and isinstance(body.get('source_location_id'), int):
        row = _one(con, 'SELECT store_id FROM inventory_locations WHERE id=?', body['source_location_id'])
        store = row and row['store_id']
    return about, store


def _detail(con, endpoint, view, body, result):
    """A few words on what changed, for the actions people look for most."""
    result = result if isinstance(result, dict) else {}
    action = view.get('action') or request.path.rstrip('/').rsplit('/', 1)[-1]
    if endpoint == 'sales.submit_daily_report':
        stock = result.get('stock') or {}
        moved = sum(1 for line in stock.get('lines') or [] if line.get('moving'))
        bits = [str(body.get('date') or ''), f"{len(body.get('items') or [])} 行 lines"]
        if stock:
            bits.append(f"扣 {stock.get('taken', 0)} off floor")
        if moved:
            bits.append(f'{moved} 库存行 stock lines')
        return ' · '.join(filter(None, bits))
    if endpoint in ('restock.restock_goods_action', 'goods.transfer_action') and action in ('receive', 'pick', 'dispatch'):
        # What came down or went out, by name, so the line reads like "补货 #24 收到: 坐坐 ×1、马卡龙 ×1".
        delivery = _one(con, 'SELECT id FROM inventory_deliveries WHERE restock_session_id=?', view['sid']) if 'sid' in view \
            else {'id': view.get('delivery_id')}
        goods = []
        for line in body.get('lines') or []:
            row = delivery and _one(con, 'SELECT product_id FROM inventory_delivery_lines WHERE delivery_id=? AND line_no=?',
                                    delivery['id'], line.get('line_no'))
            if row:
                goods.append(f"{_product_label(con, row['product_id'])} ×{line.get('quantity')}")
        return ' · '.join(filter(None, [ACTIONS.get(action, action), '、'.join(goods[:6]) + ('…' if len(goods) > 6 else '')]))
    if endpoint in ('restock.restock_goods_action', 'goods.transfer_action', 'goods.receipt_post', 'goods.count_action',
                    'restock.submit_restock_session', 'website.web_orders_step'):
        return ACTIONS.get(action, action)
    if endpoint == 'counting.save':
        names = []
        for row in result.get('results') or []:
            before, after = row.get('popcore_quantity'), row.get('quantity')
            label = _product_label(con, row.get('product_id'))
            names.append(f'{label} {before} → {after}' if before is not None and before != after else f'{label} {after}')
        where = body.get('location_code') or body.get('location') or ''
        return ' · '.join(filter(None, [str(where), '，'.join(names[:4]) + ('…' if len(names) > 4 else '')]))
    if endpoint == 'store_events.create':
        kind = {'claw_prize': '娃娃机出奖 Claw prize', 'claw_refill': '娃娃机补货 Claw refill', 'display_in': '入 Display',
                'cash_exchange': '换现金 Cash exchange'}.get(body.get('kind'), body.get('kind') or '')
        lines = body.get('lines') or ([{'product_id': body.get('product_id'), 'quantity': body.get('quantity')}]
                                      if body.get('product_id') else [])
        goods = '，'.join(f"{_product_label(con, line.get('product_id'))} ×{line.get('quantity')}" for line in lines[:4])
        return ' · '.join(filter(None, [kind, goods]))
    if endpoint in ('restock.add_restock_item',):
        return f"{_product_label(con, body.get('product_id'))} ×{body.get('requested_qty') or body.get('quantity') or ''}"
    if endpoint == 'products.create_product':
        return ' '.join(filter(None, [str(body.get('sku') or result.get('sku') or ''),
                                      str(body.get('jizhanming') or body.get('name_cn_en') or '')]))
    if endpoint == 'products.update_product':
        fields = sorted(key for key in body if key not in ('expected_version', 'version'))
        price = f"价格 price → {body['price']}" if 'price' in body else ''
        return ' · '.join(filter(None, ['、'.join(fields[:6]), price]))
    if endpoint == 'sales.save_name_pick':
        return f"「{body.get('raw_name') or ''}」 → {_product_label(con, (body.get('product_ids') or [body.get('product_id')])[0])}"
    if endpoint in ('stores.change_inventory_viewer', 'stores.grant_inventory_access'):
        who = people.name(con, body.get('auth0_sub') or body.get('sub'))
        return f"{'取消 Off' if request.method == 'DELETE' else '打开 On'} · {who or ''}"
    if endpoint == 'inventory.submit_inventory_check' and 'differ' in result:
        return f"{result.get('saved', 0)} 项 items" + (f" · {result['differ']} 🥲" if result['differ'] else ' · ✅')
    if endpoint == 'settings.put_settings':
        return '、'.join(sorted(body))
    if endpoint == 'schedule.schedule_attendance_today':
        return {'in': '上班 In', 'out': '下班 Out'}.get(body.get('action'), str(body.get('action') or ''))
    if endpoint == 'schedule.schedule_shifts_create':
        who = _one(con, 'SELECT name FROM employees WHERE id=?', body.get('employee_id'))
        return ' '.join(filter(None, [who and who['name'], str(body.get('date') or ''),
                                      f"{body.get('start_time') or ''}–{body.get('end_time') or ''}".strip('–')]))
    return ''


def _link(endpoint, view, result):
    result = result if isinstance(result, dict) else {}
    if 'sid' in view or 'iid' in view or endpoint.startswith('restock.'):
        return '/restock'
    if 'receipt_id' in view or endpoint == 'goods.receipt_create':
        return f"/goods/receiving?receipt_id={view.get('receipt_id') or result.get('id')}"
    if 'delivery_id' in view or endpoint == 'goods.transfer_create':
        return f"/goods/transfers?transfer_id={view.get('delivery_id') or result.get('id')}"
    if 'count_id' in view:
        return f"/goods/counts?count_id={view['count_id']}"
    if endpoint == 'sales.submit_daily_report' and isinstance(request.get_json(silent=True), dict):
        day = request.get_json(silent=True).get('date')
        return f'/sales/day/{day}' if day else '/sales'
    if endpoint.startswith('sales.') and 'notice' in endpoint:
        return '/sales/report-stock'
    if 'pid' in view or endpoint == 'products.create_product':
        return '/products'
    if endpoint.startswith('watch_list.'):
        return '/watch-list'
    if endpoint.startswith('website.web_orders'):
        return '/web-orders'
    if endpoint.startswith('staff_discounts.'):
        return '/staff-discounts'
    if endpoint.startswith('schedule.schedule_shifts'):
        return '/schedule'
    return None


def _business_date():
    return datetime.now(ZoneInfo('America/Toronto')).date().isoformat()


def before_request():
    if request.method not in MUTATING or not request.endpoint or request.endpoint in SKIP:
        return None
    try:
        with closing(sqlite3.connect(db.DB_PATH, timeout=db.SQLITE_BUSY_TIMEOUT)) as con:
            con.row_factory = sqlite3.Row
            body = request.get_json(silent=True)
            g.activity_before = _before(con, request.endpoint, request.view_args or {}, body if isinstance(body, dict) else {})
    except Exception:  # noqa: BLE001 - the log never stops a request
        g.activity_before = (None, None)
    return None


def after_request(response):
    try:
        _record(response)
    except Exception:  # noqa: BLE001 - the log never stops a request
        current_app.logger.exception('activity log write failed')
    return response


def _record(response):
    actor = getattr(request, 'jwt_payload', None)
    endpoint = request.endpoint or ''
    if (request.method not in MUTATING or response.status_code >= 400 or not actor or not actor.get('sub')
            or endpoint in SKIP or not endpoint):
        return
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    if body.get('dry_run'):
        return
    result = response.get_json(silent=True) if response.is_json else None
    if isinstance(result, dict) and (result.get('dry_run') or result.get('needs_confirmation')):
        return
    view = request.view_args or {}
    step = endpoint + (':' + request.path.rstrip('/').rsplit('/', 1)[-1] if endpoint == 'restock.restock_goods_action' else '')
    if request.headers.get('X-Popcore-Quiet') == '1' and step in QUIET:
        return
    extra = getattr(g, 'activity', {})
    about, store = getattr(g, 'activity_before', (None, None))
    kind, zh, en, audience = LABELS.get(endpoint, (
        'other', '', endpoint.replace('.', ' · ').replace('_', ' '),
        'manager' if endpoint.split('.')[0] in MANAGER_AREAS else 'store'))
    with closing(sqlite3.connect(db.DB_PATH, timeout=db.SQLITE_BUSY_TIMEOUT)) as con:
        con.row_factory = sqlite3.Row
        if store is None and isinstance(result, dict) and isinstance(result.get('store_id'), int):
            store = result['store_id']
        acting = getattr(g, 'acting_person', None)   # a name + PIN on the store computer (see people.py)
        actor_sub = acting['sub'] if acting else actor['sub']
        detail = extra.get('detail') or _detail(con, endpoint, view, body, result)
        con.execute(
            """INSERT OR IGNORE INTO activity_log(business_date, store_id, actor_sub, actor_name, via_sub, via_name, kind,
                   endpoint, summary, detail, link, audience, request_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_business_date(), extra.get('store_id', store), actor_sub, people.name(con, actor_sub),
             actor['sub'] if acting else None, people.name(con, actor['sub']) if acting else None,
             extra.get('kind', kind), endpoint,
             extra.get('summary') or ' '.join(filter(None, (zh, en))) + (f' · {about}' if about else ''),
             detail or '', extra.get('link') or _link(endpoint, view, result), extra.get('audience', audience),
             request.headers.get('Idempotency-Key') or None))
        con.commit()


def init_app(app):
    app.before_request(before_request)
    app.after_request(after_request)
    app.register_blueprint(bp)


# ── 动态 screen ────────────────────────────────────────────────────────────────

def _stores_seen(con, actor):
    """The stores whose 动态 this account may read: every store for managers and admins; for anyone else the
    stores they work at (their schedule stores, a shift, or 门店操作权限)."""
    role = actor.get(ROLE_CLAIM, 'viewer')
    stores = [dict(row) for row in con.execute('SELECT id, code, name FROM stores WHERE is_active=1 ORDER BY id')]
    if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
        return stores
    sub = actor.get('sub')
    mine = {row[0] for row in con.execute('SELECT store_id FROM inventory_access WHERE auth0_sub=?', (sub,))}
    mine |= {row[0] for row in con.execute(
        """SELECT es.store_id FROM employee_stores es JOIN employees e ON e.id=es.employee_id WHERE e.auth0_id=?""", (sub,))}
    mine |= {row[0] for row in con.execute(
        """SELECT DISTINCT s.store_id FROM shifts s JOIN employees e ON e.id=s.employee_id WHERE e.auth0_id=?""", (sub,))}
    return [store for store in stores if store['id'] in mine]


@bp.get('/api/activity')
@login_required
def feed():
    """动态 Activity of one store (or every store the account reads), newest first, by day."""
    actor = request.jwt_payload
    admin = ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM, 'viewer'), 0) >= ROLE_HIERARCHY['admin']
    con = db.get_db()
    stores = _stores_seen(con, actor)
    code = (request.args.get('store_code') or 'ALL').strip().upper()
    if code != 'ALL' and code not in {store['code'] for store in stores}:
        return jsonify(error='This store’s activity is not yours to read', code='activity_forbidden'), 403
    ids = [store['id'] for store in stores if code in ('ALL', store['code'])]
    day = request.args.get('date') or _business_date()
    where = ['a.business_date=?', f"(a.store_id IN ({','.join('?' * len(ids)) or 'NULL'}) OR a.store_id IS NULL)"]
    values = [day, *ids]
    if not admin:
        # Owner, 2026-11-26: only the admin reads everyone's lines; anyone else reads their own.
        where.append('a.actor_sub=?')
        values.append(actor.get('sub'))
    base, base_values = list(where), list(values)
    limit = min(PAGE, int(request.args['limit'])) if request.args.get('limit', '').isdigit() else PAGE
    for field, column in (('person', 'a.actor_sub'), ('kind', 'a.kind')):
        if request.args.get(field):
            where.append(f'{column}=?')
            values.append(request.args[field])
    if request.args.get('before_id', '').isdigit():
        where.append('a.id<?')
        values.append(int(request.args['before_id']))
    rows = [dict(row) for row in con.execute(
        f"""SELECT a.id, a.at, a.business_date, a.store_id, s.code AS store_code, a.actor_sub, a.actor_name, a.via_name,
                   a.kind, a.summary, a.detail, a.link
            FROM activity_log a LEFT JOIN stores s ON s.id=a.store_id
            WHERE {' AND '.join(where)} ORDER BY a.id DESC LIMIT ?""", (*values, limit + 1))]
    names = people.names(con, [row['actor_sub'] for row in rows])
    for row in rows:
        row['actor_name'] = names.get(row['actor_sub']) or row['actor_name']
    who = con.execute(f"SELECT DISTINCT a.actor_sub FROM activity_log a WHERE {' AND '.join(base)}", base_values).fetchall()
    present = {row[0] for row in con.execute(f"SELECT DISTINCT a.kind FROM activity_log a WHERE {' AND '.join(base)}",
                                             base_values)}
    return jsonify({'date': day, 'stores': stores, 'entries': rows[:limit],
                    'next_before_id': rows[limit - 1]['id'] if len(rows) > limit else None,
                    'people': sorted(({'sub': row[0], 'name': people.name(con, row[0])} for row in who),
                                     key=lambda person: person['name'] or ''),
                    # Only the kinds that happened that day, in a fixed order, so the chips stay short on a phone.
                    'kinds': [[key, label] for key, label in KIND_NAMES.items() if key in present]})
