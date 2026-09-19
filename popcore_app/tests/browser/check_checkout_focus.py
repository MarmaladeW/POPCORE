"""Isolated checkout UI contract checks; no live provider or real authentication."""
import asyncio
import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs
from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'.local/checkout-focus/browser'

def checkout(number=1):
    return dict(id=number,store_id=1,reference=f'ORDER-{number}',register_name=f'Register {number}',
        business_date='2026-09-14',status='open',version=1,sale_id=None,can_manage=True,can_process=True,
        can_refund=False,cashier_name='Alex Chen',cashier_sub='fixture|staff',received_cents=0,
        remaining_cents=4520,refunded_cents=0,refund_due_cents=0,refunds=[],abandoned_reason=None,
        order=dict(subtotal_cents=4000,source_tax_cents=520,gross_cents=4520,collected_cents=4520,reduction_cents=0,
            lines=[dict(quantity=2,unit='piece',product_name_snapshot='Smiski Museum Series' if number==1 else 'Mofusand Cat')]),
        attempts=[dict(id=number,tender='e_transfer',amount_cents=4520,status='pending',assigned_to='fixture|staff',
            can_upload=True,photos=[])])

async def checks(browser):
    state={'mode':'data'}
    orders={1:checkout(),2:checkout(2)}
    def api(path,query,request):
        if path=='/api/checkouts/access':
            stores=[dict(id=1,code='DT',name='Downtown')]
            return dict(business_date='2026-09-14',role='staff',live_stores=[] if state.get('off_duty') else stores,history_stores=stores)
        if path=='/api/checkouts':
            return dict(orders=list(orders.values()),staff=[],next_before_id=None,clover=dict(connected=False))
        if path in ('/api/checkouts/1','/api/checkouts/2'):
            return orders[int(path.rsplit('/',1)[1])]
        if path.endswith('/evidence'):
            number=int(path.split('/')[3]); photo=dict(id=99,uploader_sub='fixture|staff',created_at='2026-09-14',evidence_id=None)
            orders[number]['attempts'][0]['photos']=[photo]
            orders[number]['version']+=1
            return photo
        return payload(path,query)
    state['api_payload']=api
    context,page=await context_with_api(browser,state,viewport={'width':390,'height':844},auth={'role':'staff'})
    await page.goto(BASE+'/checkout')
    await expect(page).to_have_url(BASE+'/checkout')
    await expect(page.get_by_role('link',name='Today',exact=True)).to_have_count(0)
    await expect(page.get_by_role('heading',name='Checkout',exact=True)).to_be_visible()
    await page.get_by_role('button',name='Register 1',exact=False).click()
    await expect(page.get_by_text('Alex Chen',exact=False).first).to_be_visible()
    await expect(page.get_by_text('Smiski Museum Series',exact=False).last).to_be_visible()
    OUT.mkdir(parents=True,exist_ok=True)
    await page.evaluate('document.querySelectorAll("*").forEach(el=>{if(el.scrollTop)el.scrollTop=0});window.scrollTo(0,0)')
    await page.screenshot(path=str(OUT/'checkout-phone-ready.png'))
    await page.get_by_label('Customer pays').fill('30.00')
    await expect(page.get_by_role('alert').filter(has_text='20%')).to_be_visible()
    await expect(page.get_by_text('Waiting for confirmed Clover total',exact=False)).to_be_visible()
    async def slow_second(route):
        await asyncio.sleep(.4)
        await route.fulfill(json=orders[2])
    await page.route('**/api/checkouts/2',slow_second)
    await page.get_by_role('button',name='Register 2',exact=False).click()
    await expect(page.get_by_role('article',name='Checkout ORDER-1',exact=True)).to_have_count(0)
    await expect(page.get_by_role('article',name='Checkout ORDER-2',exact=True)).to_be_visible()
    await page.unroute('**/api/checkouts/2',slow_second)
    await page.get_by_role('button',name='Register 1',exact=False).click()
    await expect(page.get_by_label('Customer pays')).to_have_value('30.00')
    await page.get_by_label('Customer pays').fill('45.20')
    file=ROOT/'.local/checkout-focus/proof.png'
    from PIL import Image
    file.parent.mkdir(parents=True,exist_ok=True); Image.new('RGB',(64,64),'#4f46e5').save(file)
    await page.locator('input[type=file]').set_input_files(str(file))
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Retake',exact=True)).to_be_visible()
    await expect(page.get_by_alt_text('Payment evidence preview')).to_be_visible()
    async with page.expect_file_chooser() as chooser:
        await page.get_by_role('button',name='Retake',exact=True).click()
    await (await chooser.value).set_files(str(file))
    before=len([r for r in state['requests'] if r['method']=='POST'])
    await page.get_by_role('button',name='Register 2',exact=False).click()
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_have_count(0)
    await page.get_by_role('button',name='Register 1',exact=False).click()
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_be_visible()
    state['status_for_path']={'/api/checkouts/1/attempts/1/evidence':(503,{'error':'Upload response interrupted'})}
    await page.get_by_role('button',name='Use photo',exact=True).click()
    await expect(page.get_by_role('button',name='Retry',exact=True)).to_be_visible()
    await expect(page.get_by_alt_text('Payment evidence preview')).to_be_visible()
    await expect(page.get_by_label('Operations store')).to_be_disabled()
    state['status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('Photo saved',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Add another photo',exact=True)).to_be_enabled()
    posts=[r for r in state['requests'] if r['method']=='POST']
    assert len(posts)==before+2 and posts[-1]['path']=='/api/checkouts/1/attempts/1/evidence'
    assert posts[-1]['idempotency_key']==posts[-2]['idempotency_key'] and posts[-1]['idempotency_key']
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    OUT.mkdir(parents=True,exist_ok=True)
    await page.evaluate('document.querySelectorAll("*").forEach(el => { if (el.scrollTop) el.scrollTop=0 }); window.scrollTo(0,0)')
    await page.wait_for_timeout(200)
    await page.screenshot(path=str(OUT/'checkout-phone.png'),full_page=False)
    await page.set_viewport_size({'width':1280,'height':900})
    await page.screenshot(path=str(OUT/'checkout-desktop.png'),full_page=False)
    orders[1]['attempts'].append(dict(id=3,tender='wechat',amount_cents=100,status='completed',can_upload=True,photos=[]))
    await page.get_by_role('button',name='Refresh orders').click()
    await expect(page.locator('input[type=file]')).to_have_count(2)
    await page.locator('input[type=file]').nth(0).set_input_files(str(file))
    await page.locator('input[type=file]').nth(1).set_input_files(str(file))
    await expect(page.get_by_alt_text('Payment evidence preview')).to_have_count(2)
    await page.get_by_role('button',name='Register 2',exact=False).click()
    await page.get_by_role('button',name='Register 1',exact=False).click()
    await expect(page.get_by_alt_text('Payment evidence preview')).to_have_count(2)
    state['get_status_for_path']={'/api/checkouts/1':(503,{'error':'Temporary refresh failure'})}
    await page.get_by_role('button',name='Refresh orders').click()
    await expect(page.get_by_role('alert')).to_contain_text('Temporary refresh failure')
    await expect(page.get_by_alt_text('Payment evidence preview')).to_have_count(2)
    state['get_status_for_path']={'/api/checkouts/1':(403,{'error':'Checkout access denied'})}
    await page.get_by_role('button',name='Refresh orders').click()
    await expect(page.get_by_text('Smiski Museum Series',exact=False)).to_have_count(0)
    state['get_status_for_path']={};state['off_duty']=True
    await page.get_by_role('button',name='Refresh orders').click()
    await expect(page.get_by_role('heading',name='No checkout shift today',exact=True)).to_be_visible()
    await page.get_by_role('link',name='View your order history',exact=True).click()
    await expect(page.get_by_role('heading',name='Order history',exact=True)).to_be_visible()
    await context.close()

async def real_api_checks(browser):
    # The browser sends each API request through the actual Flask routes and a disposable DB.
    import sys
    sys.path.insert(0,str(ROOT/'popcore_app/tests'))
    from test_checkout import CheckoutTests
    fixture=CheckoutTests(); fixture.setUp()
    from werkzeug.serving import make_server
    from threading import Thread
    server=make_server('127.0.0.1',5057,fixture.app)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    config=ROOT/'.local/checkout-focus/real-vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE/'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5177,proxy:{{'/api':'http://127.0.0.1:5057'}}}}}}")
    vite=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--config',str(config)],cwd=FRONTEND,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                _,writer=await asyncio.open_connection('127.0.0.1',5177);writer.close();await writer.wait_closed();break
            except OSError:await asyncio.sleep(.1)
        else:raise RuntimeError('Real API test Vite did not start')
        order,_=fixture.create_checkout(reference='LOCAL-BROWSER-ONLY')
        fixture.action(order,'attempts','browser-attempt',tender='e_transfer',amount_cents=4520)
        context=await browser.new_context(viewport={'width':390,'height':844})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'}")
        page=await context.new_page()
        async def local_only(route):
            from urllib.parse import urlsplit
            if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost'):await route.abort()
            else:await route.continue_()
        await page.route('**/*',local_only)
        await page.goto(f'http://127.0.0.1:5177/checkout/{order["id"]}')
        await expect(page.get_by_text('Staff Cashier',exact=True)).to_be_visible()
        await page.locator('input[type=file]').set_input_files(str(ROOT/'.local/checkout-focus/proof.png'))
        await page.get_by_role('button',name='Use photo',exact=True).click()
        await expect(page.get_by_text('Photo saved',exact=True)).to_be_visible()
        await expect(page.get_by_role('button',name='Record $45.20 received',exact=True)).to_be_enabled()
        await page.get_by_role('button',name='Record $45.20 received',exact=True).click()
        await expect(page.get_by_role('heading',name='Checkout complete',exact=True)).to_be_visible()
        detail=fixture.client.get(f'/api/checkouts/{order["id"]}',headers=fixture.headers()).get_json()
        assert detail['status']=='completed' and detail['received_cents']==4520 and detail['sale_id']
        assert len(detail['attempts'][0]['photos'])==1
        from contextlib import closing
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==1
            assert con.execute('SELECT sum(amount_cents) FROM sale_payments').fetchone()[0]==4520
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.product_id,fixture.floor)).fetchone()[0]==3
        await page.get_by_role('link',name='Order history',exact=True).first.click()
        await page.get_by_role('button',name='LOCAL-BROWSER-ONLY',exact=False).click()
        await expect(page.get_by_role('heading',name='Checkout complete',exact=True)).to_be_visible()
        partial,_=fixture.create_checkout(reference='LOCAL-REFUND-ONLY')
        partial=fixture.action(partial,'attempts','partial-attempt',tender='cash',amount_cents=1000)
        fixture.action(partial,'complete','partial-paid',attempt_id=partial['attempts'][0]['id'])
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'manager',token:'manager'}")
        await page.goto(f'http://127.0.0.1:5177/checkout/{partial["id"]}')
        await page.get_by_text('Cancel or refund this order',exact=True).click()
        await page.get_by_label('Reason',exact=True).fill('Customer left during payment')
        await page.get_by_role('button',name='Abandon checkout',exact=True).click()
        await expect(page.get_by_label('Original payment',exact=True)).to_be_visible()
        await page.get_by_label('Original payment',exact=True).select_option(str(partial['attempts'][0]['id']))
        await page.get_by_label('Refund amount ($)',exact=True).fill('10.00')
        await page.get_by_label('Refund date',exact=True).fill('2026-09-08')
        await page.get_by_label('Refund reference',exact=True).fill('LOCAL-REFUND-RECEIPT')
        await page.get_by_label('Reason',exact=True).fill('Cash returned to customer')
        await page.get_by_role('checkbox',name='I checked the original receipt and returned this money.').check()
        await page.get_by_role('button',name='Record refund',exact=True).click()
        await expect(page.get_by_role('heading',name='Checkout cancelled',exact=True)).to_be_visible()
        detail=fixture.client.get(f'/api/checkouts/{partial["id"]}',headers=fixture.headers('manager')).get_json()
        assert detail['refunded_cents']==1000 and detail['sale_id'] is None
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==1
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.product_id,fixture.floor)).fetchone()[0]==3
        await context.close()
    finally:
        vite.terminate();vite.wait(timeout=10)
        server.shutdown();server.server_close();thread.join(timeout=5)
        fixture.tearDown();fixture.doCleanups()

async def main():
    process=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--config',str(HERE/'vite.config.mjs')],cwd=FRONTEND,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        await wait_for_server(process)
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            await checks(browser)
            await real_api_checks(browser)
            await browser.close()
        print('PASS: checkout navigation, order switching, warning, scoped photo preview/upload, mobile width and access revocation. Actual Flask upload, payment, finalization, history, abandoned-checkout refund and stock posting also passed with disposable data; no provider/auth verification.')
    finally:
        process.terminate();process.wait(timeout=10)

if __name__=='__main__':asyncio.run(main())
