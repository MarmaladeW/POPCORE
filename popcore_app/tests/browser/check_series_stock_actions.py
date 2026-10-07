"""Guided stock drawers against isolated API responses; no production writes."""
import asyncio
import json
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api

BASE = 'http://127.0.0.1:5193'
OUT = ROOT / '.local' / 'series-stock-actions'
FIXTURE = FRONTEND / '.local' / 'series-stock-actions.tsx'


def data():
    locations = [{'id':1,'store_id':1,'store_code':'DT','name':'Floor','code':'floor','opening_verified':True},
                 {'id':2,'store_id':1,'store_code':'DT','name':'Upstairs','code':'upstairs','opening_verified':True},
                 {'id':3,'store_id':1,'store_code':'DT','name':'Unreviewed shelf','code':'shelf','opening_verified':False}]
    products = []
    for pid,name,form,unit,quantity,version in ((11,'Garden set','sealed_set','set',2,5),(12,'Garden blind box','random_box','box',8,9),(13,'Garden · Red','confirmed_design','piece',4,7)):
        products.append({'id':pid,'name':name,'sku':str(pid),'series_id':7,'stock_form':form,'stock_unit':unit,'identity_status':'verified','design_name':'Red' if pid==13 else None,
                         'open_sets':[{'id':61,'location_id':1,'purpose':'customer_tray','remaining_qty':6,'opening_document_id':41}] if pid==12 else [],
                         'balances':[{'location_id':location['id'],'disposition':'saleable','quantity':quantity if location['id']==1 else 1 if location['id']==2 else None,'version':version if location['id']==1 else 3 if location['id']==2 else None} for location in locations]})
    return {'mode':'authoritative','locations':locations,'unassigned_count':0,'series':[{'id':7,'name':'Garden','products':products}]}


def payload(path,query,request):
    if path=='/api/products/11/inventory-identity':
        return {'id':11,'stock_form':'sealed_set','stock_unit':'set','identity_status':'verified','series_id':7,
                'conversions':[{'id':71,'target_product_id':12,'output_per_input':9,'version':2},{'id':72,'target_product_id':999,'output_per_input':6,'version':1}]}
    if path=='/api/inventory/commands': return {'document_id':91,'balances':[]}
    return {}


async def open_fixture(browser,width,kind='move',product=13,inventory=None,extra=None):
    state={'api_payload':payload,**(extra or {})}
    context,page=await context_with_api(browser,state,viewport={'width':width,'height':900})
    await context.add_init_script('window.__STOCK_ACTION_DATA='+json.dumps(inventory or data()))
    await page.route('**/stock-action-fixture.html*',lambda route:route.fulfill(content_type='text/html',body='<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/.local/series-stock-actions.tsx"></script></body></html>'))
    await page.goto(f'{BASE}/stock-action-fixture.html?kind={kind}&product={product}')
    await expect(page.get_by_role('dialog',name='Move stock' if kind=='move' else 'Open a sealed set',exact=True)).to_be_visible(timeout=5000)
    return context,page,state


async def layout(page):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    drawer=page.get_by_role('dialog')
    assert await drawer.evaluate('element=>element.scrollWidth<=element.clientWidth')
    await expect(drawer).to_be_in_viewport(ratio=.99)


async def move_checks(browser,width):
    context,page,state=await open_fixture(browser,width)
    await page.get_by_label('From location',exact=True).select_option('1')
    await page.get_by_label('To location',exact=True).select_option('2')
    await expect(page.get_by_label('From location',exact=True).locator('option[value="3"]')).to_have_count(0)
    await page.get_by_label('Quantity to move',exact=True).fill('2')
    await page.get_by_label('Reason',exact=True).fill('Refill upstairs shelf')
    await expect(page.get_by_text('−2 pieces at Floor → +2 pieces at Upstairs',exact=True)).to_be_visible()
    await layout(page)
    await page.screenshot(animations='disabled',path=OUT/f'move-{width}.png')
    state['status_for_path']={'/api/inventory/commands':(503,{'error':'Move result unconfirmed'})}
    await page.get_by_role('button',name='Confirm move',exact=True).click()
    await expect(page.get_by_text('Move result unconfirmed',exact=True)).to_be_visible()
    await expect(page.get_by_label('Reason',exact=True)).to_be_disabled()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
    await expect(page.get_by_role('button',name='Cancel',exact=True)).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog')).to_be_visible()
    state['status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('Saved and refreshed',exact=True)).to_be_visible()
    await expect(page.get_by_role('dialog')).not_to_be_visible()
    saves=[r for r in state['requests'] if r['method']=='POST']
    assert len(saves)==2 and saves[0]['idempotency_key']==saves[1]['idempotency_key'] and saves[0]['post_data']==saves[1]['post_data']
    body=saves[0]['post_data'];line=body['lines'][0]
    assert body['kind']=='move' and body['reason']=='Refill upstairs shelf'
    assert line=={'product_id':13,'unit':'piece','quantity':2,'from_location_id':1,'to_location_id':2,'from_disposition':'saleable','to_disposition':'saleable','expected_versions':{'from':7,'to':3}}
    await context.close()


async def open_set_checks(browser,width):
    extra={'get_status_for_path':{'/api/products/11/inventory-identity':(503,{'error':'Conversion list unavailable'})}}
    context,page,state=await open_fixture(browser,width,'open_set',11,extra=extra)
    await expect(page.get_by_text('Conversion list unavailable',exact=True)).to_be_visible()
    state['get_status_for_path']={}
    await page.get_by_role('button',name='Retry conversion list',exact=True).click()
    await page.get_by_label('Reviewed conversion',exact=True).select_option('71')
    await expect(page.get_by_label('Reviewed conversion',exact=True).locator('option[value="72"]')).to_have_count(0)
    await page.get_by_label('Location',exact=True).select_option('1')
    await page.get_by_label('Purpose',exact=True).select_option('customer_tray')
    await page.get_by_label('Sets to open',exact=True).fill('1')
    await page.get_by_label('Reason',exact=True).fill('Prepare customer tray')
    await expect(page.get_by_text('−1 set → +9 boxes',exact=True)).to_be_visible()
    await layout(page)
    await page.screenshot(animations='disabled',path=OUT/f'open-set-{width}.png')
    await page.get_by_role('button',name='Confirm opening',exact=True).click()
    await expect(page.get_by_text('Saved and refreshed',exact=True)).to_be_visible()
    saves=[r for r in state['requests'] if r['method']=='POST']
    assert len(saves)==1
    body=saves[0]['post_data'];line=body['lines'][0]
    assert body['kind']=='open_set' and body['reason']=='Prepare customer tray'
    assert line=={'product_id':11,'unit':'set','quantity':1,'from_location_id':1,'to_location_id':1,'from_disposition':'saleable','to_disposition':'saleable','expected_versions':{'from':5,'to':9},'conversion_id':71,'conversion_factor':9,'purpose':'customer_tray'}
    await context.close()


async def guard_checks(browser):
    context,page,state=await open_fixture(browser,390,product=12)
    await page.get_by_label('From location',exact=True).select_option('1')
    await page.get_by_label('To location',exact=True).select_option('2')
    await page.get_by_label('Reason',exact=True).fill('Move loose boxes')
    await page.get_by_label('Quantity to move',exact=True).fill('3')
    await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_disabled()
    await page.get_by_label('Quantity to move',exact=True).fill('2')
    await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_enabled()
    state['status_for_path']={'/api/inventory/commands':(409,{'code':'stale_version','error':'Inventory balance version changed'})}
    await page.get_by_role('button',name='Confirm move',exact=True).click()
    await expect(page.get_by_text('Inventory balance version changed',exact=True)).to_be_visible()
    await expect(page.get_by_label('Reason',exact=True)).to_have_value('Move loose boxes')
    await page.get_by_role('button',name='Cancel',exact=True).click()
    await expect(page.get_by_text('Closed',exact=True)).to_be_visible()
    await context.close()
    context,page,state=await open_fixture(browser,1440)
    await page.get_by_label('From location',exact=True).select_option('1')
    await page.get_by_label('To location',exact=True).select_option('2')
    await page.get_by_label('Reason',exact=True).fill('Move stock')
    state['status_for_path']={'/api/inventory/commands':(403,{'error':'Inventory access denied'})}
    await page.get_by_role('button',name='Confirm move',exact=True).click()
    await expect(page.get_by_text('Inventory access denied',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_disabled()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_enabled()
    await page.keyboard.press('Escape')
    await expect(page.get_by_text('Closed',exact=True)).to_be_visible()
    assert len([r for r in state['requests'] if r['method']=='POST'])==1
    await context.close()
    inventory=data();inventory['mode']='legacy'
    context,page,state=await open_fixture(browser,390,inventory=inventory)
    await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_disabled()
    assert not [r for r in state.get('requests',[]) if r['method']=='POST']
    await context.close()


async def unknown_checks(browser):
    for change in ('quantity','version','provenance','mixed_stores'):
        inventory=data()
        product=inventory['series'][0]['products'][1]
        if change in ('quantity','version'): product['balances'][0][change]=None
        if change=='provenance': del product['open_sets']
        if change=='mixed_stores': inventory['locations'][1]['store_id']=2
        context,page,state=await open_fixture(browser,390,product=12,inventory=inventory)
        await page.get_by_label('From location',exact=True).select_option('1')
        await page.get_by_label('To location',exact=True).select_option('2')
        await page.get_by_label('Reason',exact=True).fill('Attempt unverified move')
        await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_disabled()
        assert not [r for r in state.get('requests',[]) if r['method']=='POST']
        await context.close()
    def missing_conversion(path,query,request):
        result=payload(path,query,request)
        if path.endswith('/inventory-identity'): result['conversions']=[]
        return result
    context,page,state=await open_fixture(browser,1440,'open_set',11,extra={'api_payload':missing_conversion})
    await expect(page.get_by_text('No reviewed conversion is available',exact=False)).to_be_visible()
    await expect(page.get_by_role('button',name='Confirm opening',exact=True)).to_be_disabled()
    assert not [r for r in state.get('requests',[]) if r['method']=='POST']
    await context.close()


async def mounted_flask_checks(browser):
    sys.path.insert(0,str(ROOT/'popcore_app/tests'))
    from test_design_identification import DesignIdentificationTests
    fixture=DesignIdentificationTests();fixture.setUp()
    context=None
    try:
        with closing(fixture.connect()) as con:
            upstairs=con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",(fixture.store_id,)).fetchone()[0]
            set_id=con.execute("INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,identity_status) VALUES ('ACTION-SET','Reviewed set','Reviewed set',?,'sealed_set','set','verified')",(fixture.series_id,)).lastrowid
            conversion=con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,6,1)',(set_id,fixture.product_id)).lastrowid
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',2,1)",(set_id,fixture.floor))
            con.commit()
        context=await browser.new_context(viewport={'width':390,'height':900})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
        page=await context.new_page();calls=[]
        async def route_real_api(route):
            url=urlsplit(route.request.url)
            if url.hostname not in ('127.0.0.1','localhost'):
                await route.abort();return
            if not url.path.startswith('/api/'):
                await route.continue_();return
            headers={**fixture.headers(),'Idempotency-Key':route.request.headers.get('idempotency-key','')}
            body=route.request.post_data_json if route.request.method=='POST' else None
            response=fixture.client.open(url.path+('?' +url.query if url.query else ''),method=route.request.method,headers=headers,json=body)
            if url.path=='/api/inventory/commands':
                assert response.status_code==(200 if len(calls)==1 else 201),response.get_json()
                calls.append((body,headers['Idempotency-Key']))
                if len(calls)==1:
                    await route.abort('failed');return
            await route.fulfill(status=response.status_code,content_type=response.content_type,body=response.get_data())
        await page.route('**/*',route_real_api)
        await page.goto(BASE+f'/stock?series_id={fixture.series_id}')
        await page.get_by_label('Stock details for B',exact=True).click()
        await page.get_by_role('button',name='Move stock',exact=True).click()
        await expect(page.get_by_role('dialog',name='Stock details',exact=True)).not_to_be_visible()
        await expect(page.get_by_role('dialog',name='Move stock',exact=True)).to_be_visible()
        await page.get_by_label('From location',exact=True).select_option(str(fixture.floor))
        await page.get_by_label('To location',exact=True).select_option(str(upstairs))
        await page.get_by_label('Quantity to move',exact=True).fill('2')
        await page.get_by_label('Reason',exact=True).fill('Move two confirmed B pieces upstairs')
        await page.get_by_role('button',name='Confirm move',exact=True).click()
        await expect(page.get_by_role('button',name='Retry',exact=True)).to_be_visible()
        with closing(fixture.connect()) as con:
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.design_ids[1],upstairs)).fetchone()[0]==2
        await page.get_by_role('button',name='Retry',exact=True).click()
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        assert len(calls)==2 and calls[0]==calls[1] and calls[0][1]
        assert calls[0][0]['lines'][0]['expected_versions']=={'from':1,'to':0}
        assert calls[0][0]['lines'][0]['unit']=='piece'
        await page.set_viewport_size({'width':1440,'height':1000})
        await page.get_by_label('Stock details for Reviewed set',exact=True).click()
        await page.get_by_role('button',name='Open sealed set',exact=True).click()
        await expect(page.get_by_role('dialog',name='Stock details',exact=True)).not_to_be_visible()
        await page.get_by_label('Reviewed conversion',exact=True).select_option(str(conversion))
        await page.get_by_label('Location',exact=True).select_option(str(fixture.floor))
        await page.get_by_label('Purpose',exact=True).select_option('customer_tray')
        await page.get_by_label('Reason',exact=True).fill('Open a reviewed six-box customer tray')
        await expect(page.get_by_text('−1 set → +6 boxes',exact=True)).to_be_visible()
        await page.get_by_role('button',name='Confirm opening',exact=True).click()
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        assert calls[-1][0]['lines'][0]['conversion_id']==conversion
        assert calls[-1][0]['lines'][0]['conversion_factor']==6
        assert calls[-1][0]['lines'][0]['expected_versions']=={'from':1,'to':1}
        await page.get_by_label('Stock details for Test Product',exact=True).click()
        await page.get_by_role('button',name='Move stock',exact=True).click()
        await expect(page.get_by_role('dialog',name='Stock details',exact=True)).not_to_be_visible()
        await page.get_by_label('From location',exact=True).select_option(str(fixture.floor))
        await page.get_by_label('To location',exact=True).select_option(str(upstairs))
        await page.get_by_label('Reason',exact=True).fill('Move three loose boxes, keep the tray')
        await page.get_by_label('Quantity to move',exact=True).fill('6')
        await expect(page.get_by_role('button',name='Confirm move',exact=True)).to_be_disabled()
        await page.get_by_label('Quantity to move',exact=True).fill('3')
        await layout(page)
        await page.screenshot(animations='disabled',path=OUT/'mounted-real-provenance-1440.png')
        await page.get_by_role('button',name='Confirm move',exact=True).click()
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        assert calls[-1][0]['lines'][0]['expected_versions']=={'from':2,'to':0}
        assert calls[-1][0]['lines'][0]['unit']=='box'
        with closing(fixture.connect()) as con:
            balances={(row['product_id'],row['location_id']):row['quantity'] for row in con.execute("SELECT * FROM inventory_balances WHERE disposition='saleable'")}
            assert balances[fixture.design_ids[1],fixture.floor]==5 and balances[fixture.design_ids[1],upstairs]==2
            assert balances[set_id,fixture.floor]==1
            assert balances[fixture.product_id,fixture.floor]==8 and balances[fixture.product_id,upstairs]==3
            opened=con.execute('SELECT remaining_qty,purpose FROM inventory_open_sets').fetchone()
            assert tuple(opened)==(6,'customer_tray')
            assert con.execute('SELECT COUNT(*) FROM inventory_documents').fetchone()[0]==3
            assert con.execute('SELECT COUNT(*) FROM trade_units').fetchone()[0]==0
            assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert con.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:
        if context:await context.close()
        fixture.tearDown();fixture.doCleanups()


async def main():
    OUT.mkdir(parents=True,exist_ok=True);FIXTURE.parent.mkdir(parents=True,exist_ok=True)
    FIXTURE.write_text('''import React,{useEffect,useState} from 'react';import{createRoot}from'react-dom/client';import{BrowserRouter}from'react-router-dom';import{ConfigProvider}from'antd';import SeriesStockAction from '../src/pages/Stock/SeriesStockAction';import{setTokenGetter}from'../src/api/client';setTokenGetter(async()=> 'fixture');function App(){const[open,setOpen]=useState(true),[busy,setBusy]=useState(false),[message,setMessage]=useState('');useEffect(()=>{const listener=(event)=>setBusy(event.detail);window.addEventListener('popcore:checkout-busy',listener);return()=>window.removeEventListener('popcore:checkout-busy',listener)},[]);const params=new URLSearchParams(location.search),data=window.__STOCK_ACTION_DATA,product=data.series[0].products.find(p=>p.id===Number(params.get('product')));return <BrowserRouter><ConfigProvider><select aria-label="Operations store" disabled={busy}><option>Downtown</option></select><p>{message}</p>{open&&<SeriesStockAction kind={params.get('kind')} product={product} data={data} onClose={()=>{setOpen(false);setMessage('Closed')}} onSaved={()=>{setOpen(false);setMessage('Saved and refreshed')}}/>}</ConfigProvider></BrowserRouter>}createRoot(document.getElementById('root')).render(<App/>);''')
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5193'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5193);writer.close();await writer.wait_closed();break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width in (390,1440):
                        await move_checks(browser,width);await open_set_checks(browser,width)
                    await guard_checks(browser)
                    await unknown_checks(browser)
                    await mounted_flask_checks(browser)
                finally: await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)
            FIXTURE.unlink(missing_ok=True)

if __name__=='__main__':
    asyncio.run(main())
    print('Series stock action browser checks passed')
