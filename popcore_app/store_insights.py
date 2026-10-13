"""Read-only, source-separated manager overview; never derives money from catalog prices."""
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import require_checkout_history_store
from inventory_commands import InventoryValidationError
from operational_reports import _scope, _inventory_identity_valid, _product_name
from validation import read_date


def _current_inventory(con, ids):
    marks=','.join('?' for _ in ids)
    mode=con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()['mode']
    locations=[dict(row) for row in con.execute(f'''SELECT l.id,l.store_id,l.code,COALESCE(ss.opening_verified,0) opening_verified
        FROM inventory_locations l LEFT JOIN inventory_scope_state ss ON ss.location_id=l.id
        WHERE l.is_active=1 AND l.store_id IN ({marks})''',ids)]
    balances={(r['product_id'],r['location_id'],r['disposition']):r['quantity'] for r in con.execute(
        f'''SELECT b.* FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
            WHERE l.is_active=1 AND l.store_id IN ({marks})''',ids)}
    items=[];counts=Counter({key:0 for key in ('out_of_stock','replenish','condition','unverified')})
    for row in con.execute('SELECT p.*,ps.name series_name FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id ORDER BY p.id'):
        valid=mode=='authoritative' and _inventory_identity_valid(row)
        def available(location):
            return balances.get((row['id'],location['id'],'saleable'),0) if valid and location['opening_verified'] else None
        known=bool(locations) and all(available(location) is not None for location in locations)
        reasons=[]
        if not known: reasons.append('unverified')
        elif all(available(location)==0 for location in locations): reasons.append('out_of_stock')
        if any(floor['code']=='floor' and available(floor)==0 and any(back['store_id']==floor['store_id'] and back['code']!='floor' and (available(back) or 0)>0 for back in locations) for floor in locations):
            reasons.append('replenish')
        if valid and any(location['opening_verified'] and any(balances.get((row['id'],location['id'],kind),0)>0 for kind in ('hold','damaged')) for location in locations):
            reasons.append('condition')
        if reasons:
            counts.update(reasons)
            items.append({'product_id':row['id'],'product_name':_product_name(row),'series_id':row['series_id'],
                          'native_unit':row['stock_unit'],'reasons':reasons})
    items.sort(key=lambda row:('replenish' not in row['reasons'],'condition' not in row['reasons'],'out_of_stock' not in row['reasons'],row['product_name'],row['product_id']))
    return {'mode':mode,'reviewed_location_count':sum(bool(row['opening_verified']) for row in locations),
            'unreviewed_location_count':sum(not row['opening_verified'] for row in locations),
            'unknown_product_count':counts['unverified'],'exception_counts':dict(counts),
            'items':items[:20],'total_exception_products':len(items)}


def query_overview(con, *, actor, filters):
    if ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM,'viewer'),0)<ROLE_HIERARCHY['manager']:
        raise PermissionError('Manager overview access required')
    start=filters.get('from');end=filters.get('to')
    if read_date(start,'from')!=start or read_date(end,'to')!=end:
        raise InventoryValidationError('from/to must use YYYY-MM-DD')
    days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
    if not 1<=days<=366:
        raise InventoryValidationError('Choose an ordered date range of at most 366 days')
    con.execute('BEGIN')
    stores,scope=_scope(con,actor,filters.get('store_code'))
    if not stores: raise PermissionError('Report store access denied')
    for store in stores:
        require_checkout_history_store(con,store['id'],actor,start,end)
    ids=[store['id'] for store in stores];codes=[store['code'] for store in stores];marks=','.join('?' for _ in ids)
    dates=[(date.fromisoformat(start)+timedelta(days=offset)).isoformat() for offset in range(days)]
    sales=[dict(row) for row in con.execute(f'''SELECT id,store_id,business_date,gross_cents FROM sale_documents
        WHERE store_id IN ({marks}) AND status='posted' AND business_date BETWEEN ? AND ? ORDER BY business_date,id''',(*ids,start,end))]
    sale_daily={day:[] for day in dates}
    for row in sales:sale_daily[row['business_date']].append(row)
    products={}
    for row in con.execute(f'''SELECT l.product_id,l.product_name_snapshot,l.raw_product_text,l.native_unit,l.quantity,p.series_id,p.identity_status
        FROM sale_lines l JOIN sale_documents s ON s.id=l.sale_id LEFT JOIN products p ON p.id=l.product_id
        WHERE s.store_id IN ({marks}) AND s.status='posted' AND s.business_date BETWEEN ? AND ?
        ORDER BY s.business_date,s.id,l.line_no''',(*ids,start,end)):
        name=row['product_name_snapshot'] or row['raw_product_text'] or 'Unknown product'
        key=(row['product_id'] if row['product_id'] is not None else name,row['native_unit'])
        item=products.setdefault(key,{'product_id':row['product_id'],'product_name':name,'native_unit':row['native_unit'],
            'quantity':0,'line_count':0,'identity_complete':row['identity_status']=='verified','series_id':row['series_id'],'recorded_names':[]})
        item['product_name']=name
        if name not in item['recorded_names']:item['recorded_names'].append(name)
        item['quantity']+=row['quantity'];item['line_count']+=1
    top_products=[];unit_counts=Counter()
    for item in sorted(products.values(),key=lambda row:(row['native_unit'] or '',-row['quantity'],row['product_name'])):
        if unit_counts[item['native_unit']]<20:
            top_products.append(item);unit_counts[item['native_unit']]+=1
    top_products.sort(key=lambda row:(row['native_unit'] or '',row['product_name'],row['product_id'] or 0))
    known=[row['gross_cents'] for row in sales if row['gross_cents'] is not None]
    posted={'document_count':len(sales),'known_gross_cents':sum(known),'unknown_gross_count':len(sales)-len(known),
        'gross_complete':len(known)==len(sales),'days_with_documents':sum(bool(rows) for rows in sale_daily.values()),
        'daily':[{'date':day,'document_count':len(rows),'known_gross_cents':sum(row['gross_cents'] or 0 for row in rows) if rows else None,
                  'unknown_gross_count':sum(row['gross_cents'] is None for row in rows),'coverage':'recorded' if rows else 'no_records',
                  'stores_with_documents':len({row['store_id'] for row in rows})} for day,rows in sale_daily.items()],
        'top_products':top_products}
    checkouts=[dict(row) for row in con.execute(f'''SELECT o.status,o.payload,o.sale_id,s.status sale_status
        FROM checkout_orders o LEFT JOIN sale_documents s ON s.id=o.sale_id
        WHERE o.store_id IN ({marks}) AND o.business_date BETWEEN ? AND ?''',(*ids,start,end))]
    counts=Counter(row['status'] for row in checkouts);open_totals=[];unknown=0
    for row in checkouts:
        if row['status']!='open':continue
        try:payload=json.loads(row['payload']);amount=payload.get('gross_cents') if isinstance(payload,dict) else None
        except (ValueError,TypeError):amount=None
        if type(amount) is int and amount>=0:open_totals.append(amount)
        else:unknown+=1
    linked=sum(row['status']=='completed' and row['sale_status']=='posted' for row in checkouts)
    activity={'order_count':len(checkouts),'open_count':counts['open'],'completed_count':counts['completed'],'cancelled_count':counts['cancelled'],
        'completed_in_posted_sales':linked,'completed_without_posted_sale':counts['completed']-linked,
        'open_snapshot_gross_cents':sum(open_totals),'open_unknown_gross_count':unknown}
    historical=[dict(row) for row in con.execute(f'''SELECT ds.*,p.jizhanming,p.name_cn_en,p.sku,p.stock_form,p.design_name,ps.name series_name FROM daily_sales ds
        LEFT JOIN products p ON p.id=ds.product_id LEFT JOIN product_series ps ON ps.id=p.series_id WHERE ds.store IN ({marks}) AND ds.date BETWEEN ? AND ? ORDER BY ds.date,ds.id''',(*codes,start,end))]
    metadata=[dict(row) for row in con.execute(f'SELECT date,store FROM daily_report_metadata WHERE store IN ({marks}) AND date BETWEEN ? AND ?',(*codes,start,end))]
    historical_daily={day:[] for day in dates};metadata_daily=Counter(row['date'] for row in metadata);historical_products={}
    for row in historical:
        historical_daily[row['date']].append(row)
        item=historical_products.setdefault(row['product_id'],{'product_id':row['product_id'],'product_name':_product_name({**row,'id':row['product_id']}),
            'raw_names':[],'reported_quantity':0,'dates':set()})
        if row['raw_name'] and row['raw_name'] not in item['raw_names']:item['raw_names'].append(row['raw_name'])
        item['reported_quantity']+=row['qty_sold'];item['dates'].add(row['date'])
    for item in historical_products.values():item['days_reported']=len(item.pop('dates'))
    imported={'row_count':len(historical),'product_count':len(historical_products),'days_with_rows':sum(bool(rows) for rows in historical_daily.values()),
        'days_with_metadata':len(metadata_daily),'daily':[{'date':day,'row_count':len(rows),'metadata_count':metadata_daily[day],
            'reported_quantity':sum(row['qty_sold'] for row in rows) if rows else None,
            'coverage':'report_rows' if rows else 'metadata_only' if metadata_daily[day] else 'no_report'} for day,rows in historical_daily.items()],
        'top_products':sorted(historical_products.values(),key=lambda row:(-row['reported_quantity'],row['product_name']))[:20]}
    return {'scope':scope,'stores':stores,'period':{'from':start,'to':end,'calendar_days':days},
        'generated_at':datetime.now(timezone.utc).isoformat(),'posted_sales':posted,'checkout_activity':activity,
        'historical_reports':imported,'current_inventory':_current_inventory(con,ids),'limitations':[
            'Posted sales use recorded gross amounts before refunds. Unknown amounts are excluded from the known total.',
            'Completed checkouts with a linked posted sale are already in posted sales. Open checkout snapshots are not revenue or remaining balances.',
            'Historical daily reports are separate imported quantities; their prices and unit meanings are not reliable revenue or native-unit stock facts.',
            'A date with records does not prove every store or sales channel is complete. Missing dates are not confirmed zero-sales days.',
            'Inventory exceptions describe the current reviewed snapshot, not stock at the selected period end.',
            'Product rankings keep native units separate. Costs and profit are not calculated.'
        ]}
