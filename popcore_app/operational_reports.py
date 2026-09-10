from datetime import date, datetime
from zoneinfo import ZoneInfo
from auth import ROLE_CLAIM, ROLE_HIERARCHY
from inventory_commands import InventoryValidationError

REPORTS={'inventory','movements','goods-exceptions','sales','tenders','evidence-exceptions','cash-variance','count-discrepancies','closed-days'}
FINANCIAL={'sales','tenders','evidence-exceptions','cash-variance','closed-days'}

def _scope(con,actor,code):
    rows=[dict(r) for r in con.execute("SELECT s.id,s.code FROM inventory_access a JOIN stores s ON s.id=a.store_id WHERE a.auth0_sub=? AND s.is_active=1 ORDER BY s.id",(actor.get('sub'),))]
    code=(code or 'ALL').upper()
    if code!='ALL':
        rows=[r for r in rows if r['code']==code]
        if not rows: raise PermissionError('Report store access denied')
    return rows,code

def query_report(con,name,*,actor,filters):
    if name not in REPORTS: raise KeyError(name)
    if name in FINANCIAL and ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM,'viewer'),0)<ROLE_HIERARCHY['manager']:
        raise PermissionError('Manager report access required')
    page=int(filters.get('page') or 1);size=int(filters.get('page_size') or 100)
    if page<1 or size<1 or size>500: raise InventoryValidationError('page/page_size is out of range')
    start=filters.get('from') or '0001-01-01';end=filters.get('to') or '9999-12-31'
    try: start_date=date.fromisoformat(start);end_date=date.fromisoformat(end)
    except ValueError as exc: raise InventoryValidationError('from/to must be ISO dates') from exc
    if start_date>end_date: raise InventoryValidationError('from must not be after to')
    stores,scope=_scope(con,actor,filters.get('store_code'))
    ids=[s['id'] for s in stores];items=[];totals={}
    if ids:
        marks=','.join('?' for _ in ids)
        if name=='inventory':
            items=[dict(r) for r in con.execute(f"""SELECT b.product_id,p.sku,p.stock_unit,l.store_id,l.id location_id,l.name location,b.disposition,b.quantity,b.version
              FROM inventory_balances b JOIN products p ON p.id=b.product_id JOIN inventory_locations l ON l.id=b.location_id
              WHERE l.store_id IN ({marks}) ORDER BY l.store_id,p.sku,l.id,b.disposition""",ids)]
        elif name=='movements':
            items=[dict(r) for r in con.execute(f"""SELECT m.id,m.document_id,d.business_date,d.kind,d.status,m.product_id,p.stock_unit native_unit,m.quantity,m.location_id,m.disposition,l.from_location_id,l.to_location_id,l.from_disposition,l.to_disposition
              FROM inventory_movements m JOIN products p ON p.id=m.product_id JOIN inventory_documents d ON d.id=m.document_id JOIN inventory_document_lines l ON l.document_id=m.document_id AND l.line_no=m.line_no
              WHERE d.business_date BETWEEN ? AND ? AND m.location_id IN (SELECT id FROM inventory_locations WHERE store_id IN ({marks})) ORDER BY d.business_date,m.id""",(start,end,*ids))]
        elif name=='goods-exceptions':
            items=[dict(r) for r in con.execute(f"SELECT id,store_id,business_date,status,version,shipment_reference FROM goods_receipts WHERE store_id IN ({marks}) AND business_date BETWEEN ? AND ? AND (status!='posted' OR EXISTS(SELECT 1 FROM goods_receipt_lines l WHERE l.receipt_id=goods_receipts.id AND (l.damaged_quantity>0 OR l.hold_quantity>0 OR l.discrepancy_note IS NOT NULL))) ORDER BY business_date,id",(*ids,start,end))]
        elif name=='sales':
            items=[dict(r) for r in con.execute(f"""SELECT s.store_id,
              CASE WHEN p.identity_status='verified' THEN COALESCE(ps.name,p.ip_series) END series,
              CASE WHEN p.identity_status='verified' THEN COALESCE(p.design_name,l.product_name_snapshot,p.jizhanming,p.name_cn_en,p.sku) END design,
              CASE WHEN p.identity_status='verified' THEN NULL ELSE COALESCE(l.raw_product_text,l.product_name_snapshot,'Unknown product') END unverified_product,
              l.product_id,l.native_unit,SUM(l.quantity) quantity,COUNT(*) line_count,
              CASE WHEN p.identity_status='verified' THEN 1 ELSE 0 END identity_complete
              FROM sale_documents s JOIN sale_lines l ON l.sale_id=s.id LEFT JOIN products p ON p.id=l.product_id LEFT JOIN product_series ps ON ps.id=p.series_id
              WHERE s.store_id IN ({marks}) AND s.status='posted' AND s.business_date BETWEEN ? AND ?
              GROUP BY s.store_id,series,design,unverified_product,l.product_id,l.native_unit,identity_complete
              ORDER BY s.store_id,series,design,unverified_product,l.product_id,l.native_unit""",(*ids,start,end))]
            money=[dict(r) for r in con.execute(f"SELECT gross_cents FROM sale_documents WHERE store_id IN ({marks}) AND status='posted' AND business_date BETWEEN ? AND ?",(*ids,start,end))]
            known=[r['gross_cents'] for r in money if r['gross_cents'] is not None];totals={'known_gross_cents':sum(known),'incomplete_count':sum(r['gross_cents'] is None for r in money),'gross_complete':len(known)==len(money)}
        elif name=='tenders':
            items=[dict(r) for r in con.execute(f"""WITH adjustments AS (
              SELECT payment_id,SUM(CASE WHEN event_type='refund' THEN amount_cents ELSE 0 END) refund_cents
              FROM payment_events GROUP BY payment_id)
              SELECT p.tender,SUM(CASE WHEN p.amount_cents IS NOT NULL THEN p.amount_cents ELSE 0 END) recorded_cents,
              SUM(CASE WHEN p.state='verified' AND p.amount_cents IS NOT NULL THEN p.amount_cents ELSE 0 END) verified_cents,
              SUM(p.amount_cents IS NULL) unknown_count,SUM(p.state='recorded') pending_count,SUM(COALESCE(a.refund_cents,0)) refund_cents
              FROM sale_payments p JOIN sale_documents s ON s.id=p.sale_id LEFT JOIN adjustments a ON a.payment_id=p.id
              WHERE s.store_id IN ({marks}) AND s.business_date BETWEEN ? AND ? GROUP BY p.tender ORDER BY p.tender""",(*ids,start,end))]
        elif name=='evidence-exceptions':
            items=[dict(r) for r in con.execute(f"""SELECT e.id evidence_id,e.payment_id,s.id sale_id,s.store_id,e.status,e.created_at
              FROM payment_evidence e JOIN sale_payments p ON p.id=e.payment_id JOIN sale_documents s ON s.id=p.sale_id
              WHERE s.store_id IN ({marks}) AND s.business_date BETWEEN ? AND ? AND e.status!='accepted' ORDER BY e.created_at,e.id""",(*ids,start,end))]
        elif name=='cash-variance':
            items=[dict(r) for r in con.execute(f"""SELECT c.id closing_id,c.store_id,c.business_date,c.status,c.version,x.variance_cents,x.counted_cents,x.expected_cents
              FROM closing_sessions c JOIN closing_cash_counts x ON x.closing_session_id=c.id WHERE c.store_id IN ({marks}) AND c.business_date BETWEEN ? AND ? AND x.revision=(SELECT MAX(y.revision) FROM closing_cash_counts y WHERE y.closing_session_id=c.id) ORDER BY c.business_date,c.id""",(*ids,start,end))]
        elif name=='count-discrepancies':
            items=[dict(r) for r in con.execute(f"""SELECT c.id count_id,c.store_id,c.business_date,c.status,c.version,l.product_id,l.native_unit,l.expected_quantity,l.observed_quantity,(l.observed_quantity-l.expected_quantity) discrepancy
              FROM inventory_counts c JOIN inventory_count_lines l ON l.count_id=c.id WHERE c.store_id IN ({marks}) AND c.business_date BETWEEN ? AND ? AND l.observed_quantity!=l.expected_quantity ORDER BY c.business_date,c.id,l.line_no""",(*ids,start,end))]
        elif name=='closed-days':
            items=[dict(r) for r in con.execute(f"""SELECT c.id closing_id,c.store_id,c.business_date,c.version,s.snapshot_json,s.created_at,
              (SELECT COUNT(*) FROM closing_adjustments a WHERE a.closing_session_id=c.id) later_adjustment_count FROM closing_sessions c JOIN closing_snapshots s ON s.closing_session_id=c.id WHERE c.store_id IN ({marks}) AND c.business_date BETWEEN ? AND ? ORDER BY c.business_date,c.id""",(*ids,start,end))]
    total=len(items);items=items[(page-1)*size:page*size]
    return {'report':name,'scope':scope,'store_ids':ids,'filters':dict(filters),'generated_at':datetime.now(ZoneInfo('UTC')).isoformat(),'items':items,'total_rows':total,**totals}
