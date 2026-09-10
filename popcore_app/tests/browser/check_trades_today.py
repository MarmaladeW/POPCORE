from __future__ import annotations
import asyncio, socket, subprocess, sys, time
from datetime import date, timedelta
from pathlib import Path
from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as base_payload

ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/'.local'/'build-5'/'browser'

def api_for(role, state):
    def api(path, query, request):
        today=date.today().isoformat(); future=(date.today()+timedelta(days=45)).isoformat()
        if path=='/api/today':
            sections={'catalog':{'total':1,'rows':[{'source_id':7,'type':'catalog_identity','status':'unresolved','store_id':None,'version':None,'updated_at':None,'link':'/products','label':'Catalog identity'}]}}
            if role!='viewer': sections.update({'my_work':{'total':1,'rows':[{'source_id':41,'type':'sale','status':'draft','store_id':1,'version':1,'updated_at':today,'link':'/sales/documents/41'}]},'operations':{'total':0,'rows':[]}})
            if role in ('manager','admin'): sections['financial']={'total':1,'rows':[{'source_id':41,'type':'financial_sale','status':'posted','store_id':1,'version':2,'updated_at':today,'link':'/sales/documents/41'}]}
            return {'business_date':today,'generated_at':today,'role':role,'scope':'DT','store_ids':[1] if role!='viewer' else [],'authorized_stores':[],'sections':sections}
        if path=='/api/schedule/shifts/me':
            state['shift_reads']=state.get('shift_reads',0)+1
            return [{'id':1,'employee_id':1,'date':today,'start_time':'09:00','end_time':'17:00','assigned_by':'manager','notes':'','position':'Floor','created_at':'','updated_at':'','store_code':'MK'},{'id':2,'employee_id':1,'date':future,'start_time':'10:00','end_time':'18:00','assigned_by':'manager','notes':'','created_at':'','updated_at':'','store_code':'DT'}]
        if path=='/api/trade-setup': return {'store_id':1,'floor_location_id':2,'floor_name':'Floor','series':[{'series_id':5,'name':'Long bilingual series / 长系列'}]}
        if path=='/api/trade-slots': return [{'slot_id':9,'store_id':1,'series_id':5,'location_id':2,'occupant_unit_id':11,'version':4}]
        if path=='/api/trade-slots/9': return {'slot_id':9,'store_id':1,'series_id':5,'location_id':2,'occupant_unit_id':11,'version':4,'occupant':{'unit_id':11,'design_product_id':3,'design_name':'Long bilingual design / 长名称','condition_disclosure':'Complete'},'products':[{'product_id':3,'name':'Long bilingual design / 长名称','stock_form':'confirmed_design','stock_unit':'piece','quantity':1,'balance_version':2}]}
        return base_payload(path,query,False)
    return api

async def checks(browser):
    for role in ('viewer','staff','manager','admin'):
        state={'mode':'data'};state['api_payload']=api_for(role,state)
        context,page=await context_with_api(browser,state,viewport={'width':390,'height':844},auth={'role':role})
        await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
        await page.goto(BASE+'/');await expect(page.get_by_text('Today / 今日')).to_be_visible()
        if role=='viewer': await expect(page.get_by_text('My shifts / 我的班次')).to_have_count(0)
        else:
            shifts=page.get_by_text('My shifts / 我的班次');await expect(shifts).to_be_visible()
            assert (await shifts.bounding_box())['y'] < (await page.get_by_text('My work',exact=False).bounding_box())['y']
            await expect(page.get_by_role('main').get_by_text('MK',exact=True)).to_be_visible()
            before=state['shift_reads'];await page.evaluate("window.dispatchEvent(new Event('focus'))");await page.wait_for_timeout(100);assert state['shift_reads']>before
            await page.get_by_text('View Schedule',exact=True).click();await expect(page.get_by_text('Schedule',exact=True).first).to_be_visible();await page.goto(BASE+'/')
        if role in ('manager','admin'): await expect(page.get_by_text('Sales and tender checks',exact=False)).to_be_visible()
        width=await page.evaluate('document.body.scrollWidth');assert width<=392
        await context.close()
    state={'mode':'data'}
    normal=api_for('staff',state)
    state['api_payload']=lambda path,query,request: [] if path=='/api/schedule/shifts/me' else normal(path,query,request)
    context,page=await context_with_api(browser,state,auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:0,code:'ALL',name:'All Stores',color:'#6366f1'}))")
    await page.goto(BASE+'/');await expect(page.get_by_text('No shift today',exact=True)).to_be_visible()
    selector=page.locator('select').first
    for code in ('DT','MK','ALL'):
        await selector.select_option(code);await expect(page.get_by_text('Today / 今日')).to_be_visible()
    await context.close()
    context,page=await context_with_api(browser,{'mode':'error'},auth={'role':'staff'})
    await page.goto(BASE+'/');await expect(page.get_by_text('synthetic failure',exact=True)).to_be_visible();await context.close()
    state={'mode':'data','delay_first_today':True}
    def account_api(path,query,request):
        result=api_for('manager' if state.get('today_reads',0)>1 else 'staff',state)(path,query,request)
        if path=='/api/today':
            result['sections']['my_work']['rows'][0]['label']='New Account' if state.get('today_reads',0)>1 else 'Old Account'
        return result
    state['api_payload']=account_api
    context,page=await context_with_api(browser,state,auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/')
    for _ in range(50):
        if state.get('today_reads',0)>=1: break
        await asyncio.sleep(.05)
    assert state.get('today_reads',0)>=1
    await page.evaluate("window.__FOUNDATION_AUTH={role:'manager'};window.dispatchEvent(new Event('foundation-auth'))")
    await expect(page.get_by_text('New Account',exact=True)).to_be_visible();await page.wait_for_timeout(700);await expect(page.get_by_text('Old Account',exact=True)).to_have_count(0);await context.close()
    state={'mode':'data'};state['api_payload']=api_for('staff',state)
    context,page=await context_with_api(browser,state,viewport={'width':768,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/trades');await expect(page.get_by_text('Trades',exact=True).first).to_be_visible();await expect(page.get_by_text('Occupied:',exact=False)).to_be_visible()
    await page.get_by_label('Proof reference').focus();await page.keyboard.type('STICKER-1');await expect(page.get_by_label('Proof reference')).to_have_value('STICKER-1')
    state['status_for_path']={'/api/trade-slots/9/sale':(409,{'error':'Trade slot version changed','code':'trade_slot_stale'})}
    await page.get_by_label('Reviewed sale ID').fill('41');await page.get_by_label('Sale version').fill('1');await page.get_by_role('button',name='Sell current unit').click();await expect(page.get_by_text('Trade slot version changed',exact=True)).to_be_visible()
    await page.evaluate("document.documentElement.style.zoom='2'");assert await page.evaluate('document.body.scrollWidth')<=770
    await context.close()
    state={'mode':'data'};state['api_payload']=api_for('staff',state)
    context,page=await context_with_api(browser,state,viewport={'width':1440,'height':1000},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/trades');await expect(page.get_by_text('Trades',exact=True).first).to_be_visible();assert await page.evaluate('document.body.scrollWidth')<=1442;await context.close()

async def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',5174))==0: raise RuntimeError('Port 5174 is already in use')
    OUT.mkdir(parents=True,exist_ok=True);log=(OUT/'vite.log').open('w',encoding='utf-8');flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    process=subprocess.Popen(['node',str(FRONTEND/'node_modules'/'vite'/'bin'/'vite.js'),'--config',str(HERE/'vite.config.mjs')],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
    try:
        await wait_for_server(process)
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            try: await checks(browser)
            except Exception:
                for c in browser.contexts:
                    if c.pages: await c.pages[-1].screenshot(path=OUT/'failure.png',full_page=True);break
                raise
            finally: await browser.close()
    finally: process.terminate();process.wait(timeout=10);log.close()

if __name__=='__main__':
    started=time.perf_counter();asyncio.run(main());print(f'Trades/Today browser checks passed in {time.perf_counter()-started:.1f}s',flush=True)
