"""Series inventory UX regression with isolated API fixtures; no live data."""
import asyncio
import subprocess
from urllib.parse import parse_qs
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from fixtures import payload as base_payload

BASE = 'http://127.0.0.1:5187'
OUT = ROOT / '.local' / 'design-inventory'


def payload(path, query, request):
    if path == '/api/inventory/series':
        store = parse_qs(query).get('store_code', ['DT'])[0]
        unknown = store == 'MK'
        locations = [{'id':1,'store_id':1,'store_code':store,'name':'Floor','code':'floor','opening_verified':not unknown}]
        products = []
        for pid, name, form, unit, quantity in ((11,'Sealed display','sealed_set','set',2),(12,'Unidentified box','random_box','box',8),(13,'Moon','confirmed_design','piece',3),(14,'Sun','confirmed_design','piece',0),(15,'Display stand','ordinary','piece',4),(16,'Other unidentified box','random_box','box',5)):
            products.append({'id':pid,'sku':str(pid),'name':name,'series_id':7,'stock_form':form,'stock_unit':unit,'design_name':name if form=='confirmed_design' else None,'identity_status':'verified','open_sets':[{'id':61,'location_id':1,'purpose':'fresh','remaining_qty':4,'opening_document_id':51},{'id':62,'location_id':2,'purpose':'fresh','remaining_qty':2,'opening_document_id':52}] if pid==12 else [{'id':63,'location_id':1,'purpose':'fresh','remaining_qty':3,'opening_document_id':53}] if pid==16 else [],'balances':[{'location_id':1,'disposition':kind,'quantity':None if unknown else quantity if kind=='saleable' else 1 if kind=='hold' and pid==13 else 0,'version':None if unknown else 2 if kind=='saleable' and quantity else 0} for kind in ('saleable','hold','damaged','trade','transit','display')]})
        return {'mode':'authoritative','locations':locations,'unassigned_count':4,'series':[{'id':7,'name':'Night Garden','products':products}]}
    if path == '/api/inventory/series/7/history':
        return {'items':[{'id':90,'document_id':80,'kind':'consume','product_id':12,'product_name':'Unidentified box','location_id':1,'location_name':'Floor','disposition':'saleable','quantity':-2,'business_date':'2026-09-30','posted_at':'2026-09-30T12:00:00Z','reason':'Visually confirmed Sun','open_set_id':61,'native_unit':'box','actor_sub':'auth0|staff','store_code':'DT'}],'has_more':False,'next_before_id':None}
    if path == '/api/inventory/locations':
        return [{'id':1,'store_id':1,'store_code':'DT','name':'Floor','code':'floor','opening_verified':True}]
    if path == '/api/products/14':
        return {'id':14,'sku':'14','jizhanming':'Sun','name_cn_en':'Sun','series_name':'Night Garden','design_name':'Sun','stock_form':'confirmed_design','stock_unit':'piece','identity_status':'verified'}
    if path == '/api/product-series':
        return [{'id':7,'name':'Night Garden'}]
    if path == '/api/product-series/setup':
        return {'id':7,'name':'Night Garden','product_ids':[15,16]}
    if path == '/api/goods/identify':
        return {'consume_document_id':81,'receipt_document_id':82}
    return base_payload(path,query)


async def close_drawer(page):
    drawer = page.get_by_role('dialog')
    await expect(drawer).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(drawer).not_to_be_visible()


async def no_overflow(page):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    drawer = page.get_by_role('dialog')
    if await drawer.is_visible():
        await expect(drawer).to_be_in_viewport(ratio=.99)
        assert await drawer.evaluate('element => element.scrollWidth <= element.clientWidth')


async def checks(browser, width, role):
    state = {'api_payload':payload,'mode':'data'}
    context,page = await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':role})
    await context.add_init_script("if (!localStorage.getItem('popcore_selected_store')) localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock')
    await expect(page.get_by_role('heading',name='Inventory by series',exact=True)).to_be_visible()
    await page.get_by_role('link',name='Night Garden',exact=False).click()
    await expect(page.get_by_role('heading',name='Confirmed designs',exact=True)).to_be_visible()
    await expect(page.get_by_role('heading',name='Other products',exact=True)).to_be_visible()
    headings=await page.get_by_role('heading').all_text_contents()
    groups=[name for name in headings if name in ('Confirmed designs','Sealed sets','Unidentified boxes','Other products')]
    assert groups[0]=='Confirmed designs',groups
    await expect(page.get_by_role('table').first).to_be_visible()
    await expect(page.get_by_text('2 sets',exact=True)).to_be_visible()
    await expect(page.get_by_text('8 boxes',exact=True)).to_be_visible()
    await expect(page.get_by_text('0 pieces',exact=True)).to_be_visible()
    await expect(page.get_by_role('link',name='Receive Sun',exact=True)).to_have_attribute('href','/goods/receiving?product_id=14')
    await expect(page.get_by_role('link',name='Count Sun',exact=True)).to_have_attribute('href','/goods/counts?product_id=14')
    await page.get_by_role('link',name='Receive Sun',exact=True).click()
    await expect(page.get_by_text('Night Garden · Sun · Confirmed',exact=True)).to_be_visible()
    assert all(r['method']=='GET' for r in state['requests'])
    await page.goto(BASE+'/stock?series_id=7')
    await page.get_by_role('button',name='Show movement history',exact=True).click()
    await expect(page.get_by_text('Document #80',exact=True)).to_be_visible()
    await expect(page.get_by_text('Opened set #61',exact=True)).to_be_visible()
    await expect(page.get_by_text('Visually confirmed Sun',exact=True)).to_be_visible()
    await expect(page.get_by_role('dialog',name='Movement history',exact=True)).to_be_visible()
    await no_overflow(page)
    await page.screenshot(animations='disabled',path=OUT/f'history-{role}-{width}.png')
    await close_drawer(page)
    await page.get_by_role('heading',name='Night Garden',exact=True).scroll_into_view_if_needed()
    await page.screenshot(animations='disabled',path=OUT/f'{role}-{width}.png')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.get_by_role('button',name='Identify boxes',exact=True).click()
    await close_drawer(page)
    await page.get_by_role('button',name='Identify boxes',exact=True).click()
    await expect(page.get_by_role('dialog',name='Identify boxes',exact=True)).to_be_visible()
    await page.get_by_label('Source boxes',exact=True).select_option('12')
    await page.get_by_label('Named design',exact=True).select_option('14')
    await page.get_by_label('Location',exact=True).select_option('1')
    await expect(page.get_by_label('Box source',exact=True)).to_have_value('')
    await expect(page.get_by_label('Box source',exact=True).locator('option[value="61"]')).to_have_count(1)
    await expect(page.get_by_label('Box source',exact=True).locator('option[value="62"], option[value="63"]')).to_have_count(0)
    await page.get_by_label('Box source',exact=True).select_option('61')
    await page.get_by_label('Source boxes',exact=True).select_option('16')
    await expect(page.get_by_label('Box source',exact=True)).to_have_value('')
    await expect(page.get_by_label('Box source',exact=True).locator('option[value="61"]')).to_have_count(0)
    await page.get_by_label('Source boxes',exact=True).select_option('12')
    await page.get_by_label('Box source',exact=True).select_option('61')
    await page.get_by_label('Location',exact=True).select_option('')
    await expect(page.get_by_label('Box source',exact=True)).to_have_value('')
    await page.get_by_label('Location',exact=True).select_option('1')
    await page.get_by_label('Box source',exact=True).select_option('61')
    await page.get_by_label('Identification quantity',exact=True).fill('2')
    await page.get_by_label('Identification reason',exact=True).fill('Opened and visually confirmed')
    await expect(page.get_by_text('−2 boxes → +2 Sun pieces',exact=True)).to_be_visible()
    state['status_for_path']={'/api/goods/identify':(503,{'error':'Identification result unconfirmed'})}
    await page.get_by_role('button',name='Confirm identification',exact=True).click()
    await expect(page.get_by_text('Identification result unconfirmed',exact=True)).to_be_visible()
    await expect(page.get_by_label('Named design',exact=True)).to_be_disabled()
    await expect(page.get_by_label('Box source',exact=True)).to_be_disabled()
    await expect(page.get_by_label('Box source',exact=True)).to_have_value('61')
    await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog')).to_be_visible()
    await no_overflow(page)
    state['status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('Identification saved. Inventory refreshed.',exact=True)).to_be_visible()
    saves=[r for r in state['requests'] if r['path']=='/api/goods/identify' and r['method']=='POST']
    assert len(saves)==2 and saves[0]['idempotency_key']==saves[1]['idempotency_key'] and saves[0]['post_data']==saves[1]['post_data']
    assert saves[0]['post_data']['open_set_id']==61
    assert saves[0]['post_data']['source_product_id']==12 and saves[0]['post_data']['design_product_id']==14
    assert saves[0]['post_data']['source_version']==2 and saves[0]['post_data']['target_version']==0
    await page.get_by_role('button',name='Identify boxes',exact=True).click()
    await page.get_by_label('Source boxes',exact=True).select_option('12')
    await page.get_by_label('Named design',exact=True).select_option('14')
    await page.get_by_label('Location',exact=True).select_option('1')
    await page.get_by_label('Identification reason',exact=True).fill('Confirm exact design')
    state['status_for_path']={'/api/goods/identify':(409,{'code':'fresh_set_selection_required','error':'Select a retained opened set before consuming protected units'})}
    await page.get_by_role('button',name='Confirm identification',exact=True).click()
    await expect(page.get_by_text('This stock needs its retained opened-set record.',exact=True)).to_be_visible()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_enabled()
    await page.get_by_role('button',name='Cancel identification',exact=True).click()
    state['status_for_path']={}
    if role=='manager':
        await page.get_by_role('button',name='Set up series',exact=True).click()
        await expect(page.get_by_role('dialog',name='Set up a named design roster',exact=True)).to_be_visible()
        await page.get_by_label('Series to update',exact=True).select_option('7')
        await page.get_by_label('Design names',exact=True).fill('Rain\nCloud')
        state['status_for_path']={'/api/product-series/setup':(503,{'error':'Catalog result unconfirmed'})}
        await page.get_by_role('button',name='Save named designs',exact=True).click()
        await expect(page.get_by_text('Catalog result unconfirmed',exact=True)).to_be_visible()
        await expect(page.get_by_label('Design names',exact=True)).to_be_disabled()
        await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
        await page.keyboard.press('Escape')
        await expect(page.get_by_role('dialog')).to_be_visible()
        await no_overflow(page)
        state['status_for_path']={}
        await page.get_by_role('button',name='Retry',exact=True).click()
        await expect(page.get_by_text('Series saved. No stock quantities were added.',exact=True)).to_be_visible()
        saves=[r for r in state['requests'] if r['path']=='/api/product-series/setup' and r['method']=='POST']
        assert len(saves)==2 and saves[0]['idempotency_key']==saves[1]['idempotency_key'] and saves[0]['post_data']==saves[1]['post_data']
    else:
        await expect(page.get_by_role('button',name='Set up series',exact=True)).to_have_count(0)
    state['get_status_for_path']={'/api/inventory/series':(503,{'error':'Inventory unavailable'})}
    await page.get_by_role('button',name='Refresh inventory',exact=True).click()
    await expect(page.get_by_text('Inventory unavailable',exact=True)).to_be_visible()
    await expect(page.get_by_text('8 boxes',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await page.get_by_role('button',name='Retry inventory',exact=True).click()
    await expect(page.get_by_text('8 boxes',exact=True)).to_be_visible()
    state['get_status_for_path']={'/api/inventory/series':(403,{'error':'Inventory access denied'})}
    changed_role='staff' if role=='manager' else 'manager'
    await page.evaluate("role=>{window.__FOUNDATION_AUTH={role};window.dispatchEvent(new Event('foundation-auth'))}",changed_role)
    await expect(page.get_by_text('Inventory access denied',exact=True)).to_be_visible()
    await expect(page.get_by_text('8 boxes',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await page.evaluate("role=>{window.__FOUNDATION_AUTH={role};window.dispatchEvent(new Event('foundation-auth'))}",role)
    await expect(page.get_by_text('8 boxes',exact=True)).to_be_visible()
    await page.get_by_label('Operations store',exact=True).select_option('MK')
    await expect(page.get_by_role('cell').filter(has_text='Not yet verified').first).to_be_visible()
    await page.get_by_role('button',name='Identify boxes',exact=True).click()
    await expect(page.get_by_role('button',name='Confirm identification',exact=True)).to_be_disabled()
    await close_drawer(page)
    await page.get_by_label('Operations store',exact=True).select_option('ALL')
    await expect(page.get_by_role('button',name='Identify boxes',exact=True)).to_have_count(0)
    await expect(page.get_by_role('link',name='Receive Sun',exact=True)).to_have_count(0)
    await context.close()


async def twelve_design_checks(browser,width):
    def twelve_payload(path,query,request):
        data=payload(path,query,request)
        if path=='/api/inventory/series':
            roster=data['series'][0]['products']
            example=next(product for product in roster if product['id']==14)
            for number in range(3,13):
                roster.append({**example,'id':100+number,'name':f'Design {number}','design_name':f'Design {number}','sku':f'GARDEN-{number}'})
        return data
    state={'api_payload':twelve_payload,'mode':'data'}
    context,page=await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock?series_id=7')
    await expect(page.get_by_role('heading',name='Confirmed designs',exact=True)).to_be_visible()
    await expect(page.get_by_role('link',name='Receive Design 12',exact=True)).to_be_visible()
    await expect(page.get_by_role('table',name='Confirmed design inventory',exact=True).get_by_role('row')).to_have_count(13)
    for name in ('Moon','Sun',*[f'Design {number}' for number in range(3,13)]):
        trigger=page.get_by_label(f'Stock details for {name}',exact=True)
        await trigger.scroll_into_view_if_needed()
        await expect(trigger).to_be_visible()
    await page.get_by_label('Stock details for Design 12',exact=True).click()
    await expect(page.get_by_role('dialog',name='Stock details',exact=True)).to_be_visible()
    await expect(page.get_by_text('GARDEN-12 · Reviewed identity',exact=True)).to_be_visible()
    await no_overflow(page)
    await page.screenshot(animations='disabled',path=OUT/f'twelve-designs-details-{width}.png')
    await close_drawer(page)
    await no_overflow(page)
    await page.get_by_role('heading',name='Confirmed designs',exact=True).scroll_into_view_if_needed()
    await page.screenshot(animations='disabled',path=OUT/f'twelve-designs-{width}.png')
    await context.close()


async def mixed_quantities_checks(browser):
    def mixed_payload(path,query,request):
        data=payload(path,query,request)
        if path=='/api/inventory/series':
            data['locations'].append({'id':2,'store_id':1,'store_code':'DT','name':'Upstairs','code':'upstairs','opening_verified':False})
            for product in data['series'][0]['products']:
                product['balances'].extend({'location_id':2,'disposition':kind,'quantity':None,'version':None} for kind in ('saleable','hold','damaged','trade','transit','display'))
        return data
    state={'api_payload':mixed_payload,'mode':'data'}
    context,page=await context_with_api(browser,state,viewport={'width':768,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock?series_id=7')
    moon=page.get_by_role('row').filter(has=page.get_by_label('Stock details for Moon',exact=True))
    await expect(moon.get_by_text('On hold: 1 piece · Floor',exact=True)).to_be_visible()
    available=moon.get_by_role('cell').filter(has_text='Not yet verified')
    await expect(available.get_by_text('Unknown',exact=True)).to_be_visible()
    await expect(moon.get_by_role('link',name='Count Moon',exact=True)).to_be_in_viewport()
    await no_overflow(page)
    await page.screenshot(animations='disabled',path=OUT/'mixed-quantities-768.png')
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5187'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5187);writer.close();await writer.wait_closed();break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width,role in ((390,'staff'),(768,'manager'),(1440,'manager')): await checks(browser,width,role)
                    for width in (390,768,1440): await twelve_design_checks(browser,width)
                    await mixed_quantities_checks(browser)
                finally: await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)

if __name__=='__main__':
    asyncio.run(main())
    print('Design inventory browser checks passed')
