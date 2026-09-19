from checkout_access import require_checkout, require_linked_sale, require_checkout_history_store
from datetime import datetime
from zoneinfo import ZoneInfo

from auth import ROLE_CLAIM
from inventory_commands import InventoryValidationError


def _section(rows, limit=50):
    rows = list(rows)
    return {'total': len(rows), 'rows': rows[:limit]}


def _row(row, kind, link):
    data = dict(row)
    source_id = data.pop('id')
    return {'source_id': source_id, 'type': kind, 'link': link.format(source_id), **data}


def get_today(con, *, actor, store_code=None, business_date=None):
    role, subject = actor.get(ROLE_CLAIM, 'viewer'), actor.get('sub')
    if not subject:
        raise PermissionError('Today access denied')
    business_date = business_date or datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    try:
        datetime.strptime(business_date, '%Y-%m-%d')
    except (TypeError, ValueError) as exc:
        raise InventoryValidationError('business_date must be YYYY-MM-DD') from exc
    authorized = [dict(r) for r in con.execute(
        "SELECT s.id,s.code,s.name FROM inventory_access a JOIN stores s ON s.id=a.store_id WHERE a.auth0_sub=? AND s.is_active=1 ORDER BY s.id", (subject,))]
    requested = (store_code or 'ALL').strip().upper()
    if requested != 'ALL':
        chosen = [s for s in authorized if s['code'] == requested]
        if not chosen:
            if con.execute('SELECT 1 FROM stores WHERE code=? AND is_active=1', (requested,)).fetchone():
                raise PermissionError('Today store access denied')
            raise InventoryValidationError('Unknown store_code')
        authorized = chosen
    ids = [s['id'] for s in authorized]
    sections = {}
    catalog = [_row(r, 'catalog_identity', '/products') for r in con.execute(
        "SELECT id,'unresolved' status,NULL store_id,COALESCE(name_cn_en,jizhanming,sku) label,NULL version,NULL updated_at FROM products WHERE identity_status!='verified' ORDER BY id LIMIT 51")]
    if role in {'viewer', 'admin'}:
        sections['catalog'] = _section(catalog)
    mine, operations = [], []
    if ids:
        marks = ','.join('?' for _ in ids)
        mine = [_row(r, 'sale', '/sales/documents/{}') for r in con.execute(
            f"SELECT id,store_id,status,version,created_at updated_at FROM sale_documents WHERE store_id IN ({marks}) AND created_by=? AND (status='draft' OR allocation_status!='allocated') ORDER BY business_date,id", (*ids, subject))]
        if role in {'staff','manager','admin'}:
            mine.extend(_row(r,'checkout','/checkout/{}') for r in con.execute(
                f"""SELECT o.id,o.store_id,o.status,o.version,o.reference label,o.created_at updated_at
                    FROM checkout_orders o WHERE o.store_id IN ({marks}) AND o.status='open'
                    AND (o.created_by=? OR EXISTS(SELECT 1 FROM checkout_attempts a
                        WHERE a.checkout_id=o.id AND a.assigned_to=? AND a.status!='cancelled'))
                    ORDER BY o.business_date,o.id""",(*ids,subject,subject)))
        specs = (
            ('receipt','goods_receipts',"status='draft'",'/goods/receiving?receipt_id={}'),
            ('count','inventory_counts',"status IN ('draft','submitted','returned')",'/goods/counts?count_id={}'),
            ('closing','closing_sessions',"status!='closed'",'/closing?closing_id={}'),
            ('condition_case','condition_cases',"status='open'",'/trades/cases/{}'),
        )
        for kind, table, where, link in specs:
            operations.extend(_row(r, kind, link) for r in con.execute(
                f"SELECT id,store_id,status,version,created_at updated_at FROM {table} WHERE store_id IN ({marks}) AND {where} ORDER BY created_at,id", ids))
        operations.extend(_row(r, 'delivery', '/goods/transfers?transfer_id={}') for r in con.execute(
            f"""SELECT d.id,sl.store_id,d.status,d.version,d.created_at updated_at
                FROM inventory_deliveries d JOIN inventory_locations sl ON sl.id=d.source_location_id
                JOIN inventory_locations dl ON dl.id=d.destination_location_id
                WHERE sl.store_id IN ({marks}) AND dl.store_id IN ({marks}) AND d.status NOT IN ('completed','cancelled')
                ORDER BY d.business_date,d.id""", (*ids,*ids)))
        operations.extend({'source_id':r['id'],'type':'restock','link':f"/restock?session_id={r['id']}",'store_id':r['store_id'],'status':r['status'],'version':None,'updated_at':r['created_at']} for r in con.execute(
            f"SELECT id,store_id,status,created_at FROM restock_sessions WHERE store_id IN ({marks}) AND status NOT IN ('completed','cancelled') ORDER BY date,id", ids))
        mine.extend({'source_id':r['payment_id'],'type':'payment_evidence','link':f"/sales/payments/{r['payment_id']}/evidence",'store_id':r['store_id'],'status':'evidence_pending','version':r['sale_version'],'updated_at':r['created_at']} for r in con.execute(
            f"""SELECT e.payment_id,s.store_id,s.version sale_version,e.created_at FROM payment_evidence e
                JOIN sale_payments p ON p.id=e.payment_id JOIN sale_documents s ON s.id=p.sale_id
                WHERE s.store_id IN ({marks}) AND e.uploader_sub=? AND e.status='pending' ORDER BY e.created_at,e.id""", (*ids,subject)))
        if role == 'viewer':
            notices = [{'source_id':r['product_id'],'type':'inventory_notice','link':'/products','store_id':r['store_id'],'status':'out_of_stock','version':r['version'],'updated_at':None} for r in con.execute(
                f"""SELECT b.product_id,l.store_id,b.version FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
                    WHERE l.store_id IN ({marks}) AND b.disposition='saleable' AND b.quantity=0 ORDER BY l.store_id,b.product_id""", ids)]
            sections['inventory'] = _section(notices)
    if role in {'staff','manager','admin'}:
        sections['my_work'] = _section(mine)
        sections['operations'] = _section(sorted(operations, key=lambda r: (r.get('updated_at') or '', r['type'], r['source_id'])))
    if role in {'manager','admin'}:
        financial = []
        if ids:
            marks = ','.join('?' for _ in ids)
            for r in con.execute(f"""SELECT s.id,s.store_id,s.status,s.version,s.posted_at updated_at,s.gross_cents,s.collected_cents,
                SUM(CASE WHEN p.amount_cents IS NULL THEN 1 ELSE 0 END) unknown_tender_count,
                SUM(CASE WHEN p.amount_cents IS NOT NULL THEN p.amount_cents ELSE 0 END) tender_total_cents
                FROM sale_documents s LEFT JOIN sale_payments p ON p.sale_id=s.id
                WHERE s.store_id IN ({marks}) AND s.business_date=? AND s.status='posted' GROUP BY s.id ORDER BY s.id""", (*ids,business_date)):
                item = _row(r, 'financial_sale', '/sales/documents/{}')
                item['tender_totals_cents'] = item.pop('tender_total_cents')
                financial.append(item)
            financial.extend({'source_id':r['id'],'type':'allocation_exception','link':f"/sales/documents/{r['id']}",'store_id':r['store_id'],'status':r['allocation_status'],'version':r['version'],'updated_at':r['posted_at']} for r in con.execute(
                f"SELECT id,store_id,allocation_status,version,posted_at FROM sale_documents WHERE store_id IN ({marks}) AND allocation_status!='allocated' ORDER BY business_date,id", ids))
            financial.extend({'source_id':r['id'],'type':'payment_exception','link':f"/sales/documents/{r['sale_id']}",'store_id':r['store_id'],'status':r['state'],'version':r['version'],'updated_at':r['created_at']} for r in con.execute(
                f"""SELECT p.id,p.sale_id,s.store_id,p.state,s.version,p.created_at FROM sale_payments p JOIN sale_documents s ON s.id=p.sale_id
                    WHERE s.store_id IN ({marks}) AND (p.amount_cents IS NULL OR p.state!='verified') ORDER BY p.created_at,p.id""", ids))
            financial.extend({'source_id':r['closing_id'],'type':'cash_variance','link':f"/closing?closing_id={r['closing_id']}",'store_id':r['store_id'],'status':'variance','version':r['version'],'updated_at':r['created_at'],'cash_variance_cents':r['variance_cents']} for r in con.execute(
                f"""SELECT c.id closing_id,c.store_id,c.version,x.variance_cents,x.created_at FROM closing_sessions c
                    JOIN closing_cash_counts x ON x.closing_session_id=c.id
                    WHERE c.store_id IN ({marks}) AND x.revision=(SELECT MAX(y.revision) FROM closing_cash_counts y WHERE y.closing_session_id=c.id)
                      AND x.variance_cents!=0 ORDER BY c.business_date,c.id""", ids))
        sections['financial'] = _section(financial)
    def visible(row):
        try:
            kind, source = row['type'], row['source_id']
            if kind == 'checkout':
                order = con.execute('SELECT * FROM checkout_orders WHERE id=?', (source,)).fetchone()
                require_checkout(con, order, actor)
            elif kind in ('sale','financial_sale','allocation_exception'):
                require_linked_sale(con, source, actor)
            elif kind in ('payment_evidence','payment_exception'):
                payment = con.execute('SELECT sale_id FROM sale_payments WHERE id=?', (source,)).fetchone()
                if payment:
                    require_linked_sale(con, payment['sale_id'], actor)
            elif kind == 'cash_variance':
                require_checkout_history_store(con, row['store_id'], actor)
            return True
        except PermissionError:
            return False
    # Filter before truncation so hidden rows cannot leak through total counts.
    if role in {'staff','manager','admin'}:
        sections['my_work'] = _section(row for row in mine if visible(row))
    if role in {'manager','admin'}:
        sections['financial'] = _section(row for row in financial if visible(row))
    return {'business_date':business_date,'generated_at':datetime.now(ZoneInfo('UTC')).isoformat(),'role':role,'scope':requested,'authorized_stores':authorized,'store_ids':ids,'sections':sections}
