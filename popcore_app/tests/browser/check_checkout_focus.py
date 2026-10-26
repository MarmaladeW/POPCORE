"""Isolated checkout UI contract checks; no live provider or real authentication."""
import asyncio
import json
import re
import subprocess
import time
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
            can_upload=True,photos=[])],pricing=manual_pricing(4520,cash=4400,split=4500))

def manual_pricing(total,*,cash,split):
    # What the server sends for a manual order: each way of paying already priced.
    return dict(total_cents=total,card_cents=0,other_cents=0,can_receive=True,undo_attempt_ids=[],collect=dict(
        pay=dict(due_cents=cash,mode='cash_discount',label='Cash discount',rest_cents=cash),
        split_card=dict(due_cents=split,mode='split_card',label='Rounding',rest_cents=split),
        card=dict(due_cents=total,rest_cents=total)))

async def mobile_navigation(browser):
    context,page=await context_with_api(browser,{'mode':'data'},viewport={'width':390,'height':844},auth={'role':'admin'})
    try:
        await page.goto(BASE+'/')
        # 更多 is its own page (Figma Menus · Phone · More), not a drawer.
        await page.get_by_role('navigation',name='Primary navigation').get_by_role('link',name='更多 More').click()
        await expect(page).to_have_url(BASE+'/more')
        assert (await page.get_by_role('heading',name='更多').bounding_box())['y'] >= 0, 'More header starts above the phone viewport'
        await expect(page.get_by_role('navigation',name='Primary navigation').get_by_role('link',name='更多 More')).to_have_attribute('aria-current','page')
        await page.get_by_role('navigation',name='More navigation').get_by_role('link',name='产品 Products').click()
        await expect(page).to_have_url(BASE+'/products')
    finally:
        await context.close()

async def clover_real_api_checks(browser):
    # Real Flask routes and a disposable DB; only Clover itself is faked. The browser plays the cashier.
    import re
    import sys
    sys.path.insert(0,str(ROOT/'popcore_app/tests'))
    from test_clover_checkout import CloverCheckoutTests, card, clover_order
    from contextlib import closing
    fixture=CloverCheckoutTests(); fixture.setUp()
    clover=fixture.clover
    from werkzeug.serving import make_server
    from threading import Thread
    server=make_server('127.0.0.1',5058,fixture.app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    config=ROOT/'.local/checkout-focus/clover-vite.mjs'
    config.parent.mkdir(parents=True,exist_ok=True)
    config.write_text(f"import config from {json.dumps(str(HERE/'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5178,proxy:{{'/api':'http://127.0.0.1:5058'}}}}}}")
    vite=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--config',str(config)],cwd=FRONTEND,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                _,writer=await asyncio.open_connection('127.0.0.1',5178);writer.close();await writer.wait_closed();break
            except OSError:await asyncio.sleep(.1)
        else:raise RuntimeError('Clover test Vite did not start')
        context=await browser.new_context(viewport={'width':390,'height':844})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'}")
        page=await context.new_page()
        async def local_only(route):
            from urllib.parse import urlsplit
            if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost'):await route.abort()
            else:await route.continue_()
        await page.route('**/*',local_only)
        await page.goto('http://127.0.0.1:5178/checkout')
        await expect(page.get_by_text('Clover connected · sandbox',exact=True)).to_be_visible()
        await expect(page.get_by_role('heading',name='Ready for your next customer',exact=True)).to_be_visible()

        # A newly scanned order opens by itself on the idle screen, picked up by this cashier.
        clover.put(clover_order(mapped='all'))
        order=page.get_by_role('article',name='Checkout ORDERAAAAAAAA')
        await expect(order).to_be_visible(timeout=20000)  # the first page load also compiles the screen
        await expect(page).to_have_url(re.compile(r'/checkout/\d+$'))
        await expect(order.get_by_role('heading',name='ORDERAAAAAAAA')).to_be_visible()
        await expect(order).to_contain_text('Staff Cashier')
        await expect(order.locator('.co-items')).to_contain_text('$17.70')
        await expect(order.locator('.co-items')).to_contain_text('$19.99')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        OUT.mkdir(parents=True,exist_ok=True)
        await page.screenshot(path=str(OUT/'clover-order-phone.png'),full_page=True)

        # The cash price is automatic; one tap records the sale and pays the Clover order.
        await order.get_by_role('button',name=re.compile(r'Cash\b')).click()
        await expect(order.locator('.co-collect strong')).to_have_text('$41.00')
        await expect(order.locator('.co-collect small')).to_contain_text('Cash discount −$1.59')
        await order.get_by_role('button',name='已收 $41.00 · Received',exact=True).click()
        await expect(order.get_by_text('Labelled paid on Clover',exact=True)).to_be_visible()
        await expect(order).to_contain_text('Sale recorded')
        await expect(order).to_contain_text('tap Review Order, then Save to clear the screen')
        await expect(order.get_by_label('Clover: Open')).to_be_visible()
        (_,path,body,_),=clover.writes()
        assert path=='/orders/ORDERAAAAAAAA/label' and re.fullmatch(r'Paid \d{4}-\d{2}-\d{2} \d{2}:\d{2}',body['label'])
        assert clover.orders['ORDERAAAAAAAA']['paymentState']=='OPEN' and not clover.settles()
        await page.screenshot(path=str(OUT/'clover-paid-phone.png'),full_page=True)

        # Split with card: the card part on Clover first, the rest here.
        await page.get_by_role('link',name='Back to current orders').click()
        clover.put(clover_order('ORDERBBBBBBBB',mapped=False))
        order=page.get_by_role('article',name='Checkout ORDERBBBBBBBB')
        await expect(order).to_be_visible(timeout=10000)
        await order.get_by_role('button',name='Split payment',exact=True).click()
        await order.get_by_label('Card part (CAD)').fill('22.00')
        await expect(order).to_contain_text('Pay → ⋮ → Split Custom Amount → $22.00 → Card')
        await expect(order.locator('.co-collect strong')).to_have_text('$20.00')
        await page.screenshot(path=str(OUT/'clover-split-phone.png'),full_page=True)
        clover.put(clover_order('ORDERBBBBBBBB',mapped=False,payments=[card('CARDPAYMENT01',2200)],state='PARTIALLY_PAID'))
        await expect(order.get_by_text('Card $22.00 received on Clover',exact=False)).to_be_visible(timeout=10000)
        await expect(order.locator('.co-collect strong')).to_have_text('$20.00')
        await order.get_by_role('button',name=re.compile(r'Cash\b')).click()
        await order.get_by_role('button',name='已收 $20.00 · Received',exact=True).click()
        await expect(order.get_by_text('Labelled paid on Clover',exact=True)).to_be_visible()
        await expect(order).to_contain_text('tap Pay more later to clear the screen')
        assert [call[1] for call in clover.writes()]==['/orders/ORDERAAAAAAAA/label','/orders/ORDERBBBBBBBB/label']

        # Cancelling an unpaid order deletes it on Clover.
        await page.get_by_role('link',name='Back to current orders').click()
        clover.put(clover_order('ORDERCCCCCCCC'))
        order=page.get_by_role('article',name='Checkout ORDERCCCCCCCC')
        await expect(order).to_be_visible(timeout=10000)
        await order.get_by_text('Cancel this order',exact=True).click()
        await order.get_by_role('button',name='Cancel order',exact=True).click()
        await expect(order).to_contain_text('Deleted on Clover')
        assert 'ORDERCCCCCCCC' in clover.deleted

        # With automatic opening off the order waits in the list; the Clover ID finds it.
        await page.get_by_role('link',name='Back to current orders').click()
        await page.get_by_role('switch').click()
        clover.put(clover_order('ORDERDDDDDDDD'))
        await expect(page.get_by_role('button',name='ORDERDDDDDDDD',exact=False)).to_be_visible(timeout=10000)
        await page.wait_for_timeout(1500)
        await expect(page).to_have_url('http://127.0.0.1:5178/checkout')
        await page.screenshot(path=str(OUT/'clover-queue-phone.png'),full_page=True)
        await page.get_by_label('Clover order ID').fill('orderdddddddd')
        await page.get_by_role('button',name='查找 Find',exact=True).click()
        order=page.get_by_role('article',name='Checkout ORDERDDDDDDDD')
        await expect(order).to_be_visible()
        await expect(order).to_contain_text('Not picked up yet')

        # Clover being unavailable never blocks the sale; Retry finishes it on Clover.
        clover.fail['POST label']=(502,{'error':'Clover is limiting requests.','code':'clover_unavailable'})
        await order.get_by_role('button',name=re.compile(r'Cash\b')).click()
        await order.get_by_role('button',name='已收 $41.00 · Received',exact=True).click()
        await expect(order.get_by_text('Not labelled on Clover yet',exact=True)).to_be_visible()
        await expect(order).to_contain_text('Sale recorded')
        del clover.fail['POST label']
        await order.get_by_role('button',name='Retry',exact=True).click()
        await expect(order.get_by_text('Labelled paid on Clover',exact=True)).to_be_visible()

        # A sale started in POPCORE goes to Clover for card and comes back to the same order.
        created=fixture.client.post('/api/checkouts',headers={**fixture.headers(),'Idempotency-Key':'send-create'},json=dict(
            store_id=fixture.store_id,business_date='2026-10-20',subtotal_cents=1770,source_tax_cents=230,gross_cents=2000,
            reduction_cents=0,rounding_cents=0,collected_cents=2000,lines=[dict(product_id=fixture.product_id,unit='piece',quantity=1,unit_price_cents=1770)])).get_json()
        await page.goto(f"http://127.0.0.1:5178/checkout/{created['id']}")
        order=page.get_by_role('article',name=f"Checkout {created['reference']}")
        await order.get_by_role('button',name='刷卡 · Send to Clover for card',exact=True).click()
        await expect(order.locator('h2.co-clover-id')).to_be_visible(timeout=15000)
        sent_id=next(order_id for order_id,row in clover.orders.items() if row.get('externalReferenceId'))
        await expect(order.get_by_role('heading',name=sent_id)).to_be_visible()
        await expect(order).to_contain_text(created['reference'])
        await order.get_by_role('button',name=re.compile(r'Card\b')).click()
        await expect(order).to_contain_text(f"Orders → {created['reference']} → Add Payment → Card")
        await page.screenshot(path=str(OUT/'clover-sent-phone.png'),full_page=True)
        clover.put({**clover.orders[sent_id],'paymentState':'PAID','payments':[card('CARDPAYMENT02',2000)]})
        await expect(order).to_contain_text('Sale recorded',timeout=15000)
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM checkout_orders WHERE reference=?',(created['reference'],)).fetchone()[0]==1
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==4
            assert con.execute("SELECT count(*) FROM clover_orders WHERE writeback_state='done'").fetchone()[0]==3
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await context.close()
    finally:
        vite.terminate();vite.wait(timeout=10)
        server.shutdown();server.server_close();thread.join(timeout=5)
        fixture.tearDown();fixture.doCleanups()

async def checks(browser):
    state={'mode':'data','empty_queue':True}
    orders={1:checkout(),2:checkout(2)}
    pricing=checkout(3)
    pricing.update(remaining_cents=4134,attempts=[],pricing=manual_pricing(4134,cash=4000,split=4100))
    pricing['order'].update(subtotal_cents=4134,source_tax_cents=0,gross_cents=4134,
        collected_cents=4134)
    orders[3]=pricing
    def api(path,query,request):
        if path=='/api/checkouts/access':
            stores=[dict(id=1,code='DT',name='Downtown')]
            return dict(business_date='2026-09-14',role='staff',live_stores=[] if state.get('off_duty') else stores,history_stores=stores)
        if path=='/api/checkouts':
            return dict(orders=[] if state.get('empty_queue') else list(orders.values()),staff=[],next_before_id=None,clover=dict(connected=False))
        if path in ('/api/checkouts/1','/api/checkouts/2','/api/checkouts/3'):
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
    await expect(page.get_by_role('heading',name='买单',exact=True)).to_be_visible()
    await expect(page.get_by_role('heading',name='Ready for your next customer',exact=True)).to_be_visible()
    await expect(page.get_by_role('link',name='补录 Record sale',exact=True)).to_have_attribute('href','/sales/entry')
    await page.get_by_role('button',name='手动开单 Manual checkout',exact=True).click()
    await expect(page).to_have_url(BASE+'/checkout/new')
    await expect(page.get_by_role('heading',name='New manual checkout · Downtown',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Create pending checkout',exact=True)).to_be_enabled()
    await page.get_by_label('Operations store').select_option('MK')
    await expect(page.get_by_role('button',name='Create pending checkout',exact=True)).to_have_count(0)
    await expect(page.get_by_role('alert')).to_contain_text('Choose a store where you have checkout access today.')
    await page.get_by_label('Operations store').select_option('DT')
    await expect(page.get_by_role('button',name='Create pending checkout',exact=True)).to_be_enabled()
    assert not [r for r in state['requests'] if r['method']=='POST']
    state['empty_queue']=False
    await page.get_by_role('link',name='Back to checkouts',exact=True).click()
    await expect(page.get_by_role('button',name='手动开单 Manual checkout',exact=True)).to_be_visible()
    await page.get_by_role('button',name='Register 1',exact=False).click()
    await expect(page.get_by_text('Alex Chen',exact=False).first).to_be_visible()
    await expect(page.get_by_text('Smiski Museum Series',exact=False).last).to_be_visible()
    OUT.mkdir(parents=True,exist_ok=True)
    await page.evaluate('document.querySelectorAll("*").forEach(el=>{if(el.scrollTop)el.scrollTop=0});window.scrollTo(0,0)')
    await page.screenshot(path=str(OUT/'checkout-phone-ready.png'))
    # Each tender shows its price, with the cash discount applied automatically, as at a Clover order.
    first=page.get_by_role('article',name='Checkout ORDER-1',exact=True)
    await expect(first.get_by_role('button',name=re.compile(r'Card\b'))).to_contain_text('$45.20')
    await expect(first.get_by_role('button',name=re.compile(r'Cash\b'))).to_contain_text('$44.00')
    await first.get_by_role('button',name=re.compile(r'Cash\b')).click()
    await expect(first.locator('.co-collect strong')).to_have_text('$44.00')
    await expect(first.locator('.co-collect small')).to_contain_text('Cash discount −$1.20')
    async def switch_order(number):
        await page.get_by_role('link',name='Back to current orders').click()
        await page.get_by_role('button',name=f'Register {number}',exact=False).click()
    async def slow_second(route):
        await asyncio.sleep(.4)
        await route.fulfill(json=orders[2])
    await page.route('**/api/checkouts/2',slow_second)
    await switch_order(2)
    await expect(page.get_by_role('article',name='Checkout ORDER-1',exact=True)).to_have_count(0)
    await expect(page.get_by_role('article',name='Checkout ORDER-2',exact=True)).to_be_visible()
    await page.unroute('**/api/checkouts/2',slow_second)
    await switch_order(1)
    # The tender chosen on an order survives switching away and back.
    await expect(first.get_by_role('button',name=re.compile(r'Cash\b'))).to_have_attribute('aria-pressed','true')
    await switch_order(3)
    third=page.get_by_role('article',name='Checkout ORDER-3',exact=True)
    await expect(third).to_be_visible()
    await third.get_by_role('button',name=re.compile(r'Card\b')).click()
    await expect(third.locator('.co-collect strong')).to_have_text('$41.34')
    await third.get_by_role('button',name='Split payment',exact=True).click()
    await expect(third).to_contain_text('$41.00 · Rounding −$0.34')
    await third.get_by_role('button',name='Split without card',exact=True).click()
    await expect(third).to_contain_text('$40.00 · Cash discount −$1.34')
    await switch_order(1)
    await expect(page.get_by_role('article',name='Checkout ORDER-1',exact=True)).to_be_visible()
    file=ROOT/'.local/checkout-focus/proof.png'
    from PIL import Image
    file.parent.mkdir(parents=True,exist_ok=True); Image.new('RGB',(64,64),'#4f46e5').save(file)
    await expect(page.get_by_role('button',name='Upload photo',exact=True)).to_be_visible()
    await page.get_by_label('Payment evidence photo').set_input_files(str(file))
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Retake',exact=True)).to_be_visible()
    await expect(page.get_by_alt_text('Payment evidence preview')).to_be_visible()
    async with page.expect_file_chooser() as chooser:
        await page.get_by_role('button',name='Retake',exact=True).click()
    await (await chooser.value).set_files(str(file))
    before=len([r for r in state['requests'] if r['method']=='POST'])
    await switch_order(2)
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_have_count(0)
    await switch_order(1)
    await expect(page.get_by_role('button',name='Use photo',exact=True)).to_be_visible()
    state['status_for_path']={'/api/checkouts/1/attempts/1/evidence':(503,{'error':'Upload response interrupted'})}
    await page.get_by_role('button',name='Use photo',exact=True).click()
    await expect(page.get_by_role('button',name='Retry',exact=True)).to_be_visible()
    await expect(page.get_by_alt_text('Payment evidence preview')).to_be_visible()
    await expect(page.get_by_label('Operations store')).to_be_disabled()
    state['status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('Photo saved',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Take another photo',exact=True)).to_be_enabled()
    await expect(page.get_by_role('button',name='Upload another photo',exact=True)).to_be_enabled()
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
    await expect(page.get_by_label('Payment evidence photo')).to_have_count(2)
    await page.get_by_label('Payment evidence photo').nth(0).set_input_files(str(file))
    await page.get_by_label('Payment evidence photo').nth(1).set_input_files(str(file))
    await expect(page.get_by_alt_text('Payment evidence preview')).to_have_count(2)
    await switch_order(2)
    await switch_order(1)
    await expect(page.get_by_role('article',name='Checkout ORDER-1',exact=True)).to_be_visible()
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
    await expect(page.get_by_role('button',name='手动开单 Manual checkout',exact=True)).to_have_count(0)
    await expect(page.get_by_role('link',name='补录 Record sale',exact=True)).to_have_count(0)
    await page.get_by_role('link',name='View your order history',exact=True).click()
    await expect(page.get_by_role('heading',name='订单记录',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Register 1',exact=False)).to_be_visible()
    await expect(page.get_by_role('button',name='手动开单 Manual checkout',exact=True)).to_have_count(0)
    await page.goto(BASE+'/checkout/new?view=history')
    await expect(page.get_by_role('heading',name='No checkout shift today',exact=True)).to_be_visible()
    await expect(page.get_by_role('button',name='Create pending checkout',exact=True)).to_have_count(0)
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
        context=await browser.new_context(viewport={'width':390,'height':844})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'}")
        page=await context.new_page()
        async def local_only(route):
            from urllib.parse import urlsplit
            if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost'):await route.abort()
            else:await route.continue_()
        await page.route('**/*',local_only)
        await page.goto('http://127.0.0.1:5177/checkout')
        await page.get_by_role('button',name='手动开单 Manual checkout',exact=True).click()
        await page.get_by_label('Order reference (optional)',exact=True).fill('LOCAL-BROWSER-ONLY')
        await page.get_by_label('Business date',exact=True).fill('2026-09-08')
        await page.get_by_label('Item 1',exact=True).click()
        await page.locator('.ant-select-item-option-content',has_text='Test Product').click()
        await page.get_by_label('Quantity',exact=True).fill('2')
        await page.get_by_label('Unit price ($)',exact=True).fill('20.00')
        # 13% HST fills itself in from the item prices; the form shows the final total.
        await expect(page.get_by_label('Agreed tax total ($)',exact=True)).to_have_value('5.20')
        await expect(page.get_by_text('Customer pays $45.20',exact=False)).to_be_visible()
        async with page.expect_response(lambda response: response.url.endswith('/api/checkouts') and response.request.method=='POST') as creation:
            await page.get_by_role('button',name='Create pending checkout',exact=True).click()
        order=await (await creation.value).json()
        assert order['status']=='open' and order['received_cents']==0 and order['sale_id'] is None
        from contextlib import closing
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==0
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.product_id,fixture.floor)).fetchone()[0]==5
        await expect(page.get_by_text('Staff Cashier',exact=True)).to_be_visible()
        await page.get_by_role('textbox',name='Order note').fill('Customer requested a later pickup')
        await page.get_by_role('button',name='Save note').click()
        await expect(page.get_by_role('textbox',name='Order note')).to_have_value('Customer requested a later pickup')
        # Paid like a Clover order: the e-transfer price includes the cash discount, then one tap.
        await page.get_by_role('button',name=re.compile(r'E-transfer')).click()
        await expect(page.locator('.co-collect strong')).to_have_text('$44.00')
        await page.get_by_role('button',name='已收 $44.00 · Received',exact=True).click()
        await expect(page.get_by_role('heading',name='Checkout complete',exact=True)).to_be_visible()
        # An existing picture (a screenshot of the transfer) goes through Upload photo, not the camera.
        await page.get_by_label('Upload payment evidence').set_input_files(str(ROOT/'.local/checkout-focus/proof.png'))
        await expect(page.get_by_role('button',name='Choose another',exact=True)).to_be_visible()
        await page.get_by_role('button',name='Use photo',exact=True).click()
        await expect(page.get_by_text('Photo saved',exact=True)).to_be_visible()
        detail=fixture.client.get(f'/api/checkouts/{order["id"]}',headers=fixture.headers()).get_json()
        assert detail['status']=='completed' and detail['received_cents']==4400 and detail['sale_id']
        assert len(detail['attempts'][0]['photos'])==1
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==1
            assert con.execute('SELECT sum(amount_cents) FROM sale_payments').fetchone()[0]==4400
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.product_id,fixture.floor)).fetchone()[0]==3
        await page.get_by_role('link',name='Back to current orders').click()
        await page.get_by_role('link',name='订单记录 Order history',exact=True).first.click()
        await page.get_by_role('button',name='LOCAL-BROWSER-ONLY',exact=False).click()
        await expect(page.get_by_role('heading',name='Checkout complete',exact=True)).to_be_visible()
        await expect(page.get_by_role('textbox',name='Order note')).to_have_value('Customer requested a later pickup')
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
        await page.get_by_label('Refund amount ($)',exact=True).fill('-2')
        await page.get_by_label('Refund date',exact=True).fill('2026-09-08')
        await page.get_by_label('Refund reference',exact=True).fill('LOCAL-REFUND-RECEIPT')
        await page.get_by_label('Reason',exact=True).fill('Cash returned to customer')
        await page.get_by_role('checkbox',name='I checked the original receipt and returned this money.').check()
        await page.get_by_role('button',name='Record refund',exact=True).click()
        # An unusable amount reaches the server and its message shows, instead of the form doing nothing.
        await expect(page.get_by_role('alert').filter(has_text='Refund must be within the original payment remaining refundable amount')).to_be_visible()
        # The rejected request leaves the button ready under its own name (no leftover loading label).
        await expect(page.get_by_role('button',name='Record refund',exact=True)).to_be_enabled(timeout=3000)
        await page.get_by_label('Refund amount ($)',exact=True).fill('10.00')
        await page.get_by_role('button',name='Record refund',exact=True).click()
        await expect(page.get_by_role('heading',name='Checkout cancelled',exact=True)).to_be_visible()
        detail=fixture.client.get(f'/api/checkouts/{partial["id"]}',headers=fixture.headers('manager')).get_json()
        assert detail['refunded_cents']==1000 and detail['sale_id'] is None
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT count(*) FROM sale_documents').fetchone()[0]==1
            assert con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(fixture.product_id,fixture.floor)).fetchone()[0]==3
        await context.close()
        # WeChat Pay is charged in yuan at the rate shown; the payment keeps that amount and its rate.
        with closing(fixture.connect()) as con:
            con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('checkout_cny_per_cad','4.7528')")
            con.commit()
        wechat,_=fixture.create_checkout(reference='LOCAL-WECHAT-ONLY')
        context=await browser.new_context(viewport={'width':390,'height':844})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'}")
        page=await context.new_page()
        await page.route('**/*',local_only)
        await page.goto(f'http://127.0.0.1:5177/checkout/{wechat["id"]}')
        await page.get_by_role('button',name=re.compile(r'WeChat Pay')).click()
        await expect(page.get_by_label('Chinese yuan collection amount').locator('strong')).to_have_text('¥209.12')
        await page.get_by_role('button',name='已收 $44.00 · Received',exact=True).click()
        await expect(page.get_by_role('heading',name='Checkout complete',exact=True)).to_be_visible()
        await expect(page.get_by_text('¥209.12 charged at 1 CAD = 4.7528 CNY',exact=True)).to_be_visible()
        await expect(page.get_by_text('WeChat Pay · $44.00 · ¥209.12',exact=True)).to_be_visible()  # beside the payment photo
        await page.screenshot(path=str(ROOT/'.local/checkout-focus/browser/wechat-yuan-charged.png'),full_page=True)
        with closing(fixture.connect()) as con:
            assert tuple(con.execute("SELECT tender,amount_cents,cny_fen,cny_per_cad FROM checkout_attempts WHERE checkout_id=? AND status='completed'",(wechat['id'],)).fetchone())==('wechat',4400,20912,'4.7528')
        await page.get_by_role('link',name='Back to current orders').click()
        await page.get_by_role('link',name='订单记录 Order history',exact=True).first.click()
        await expect(page.get_by_role('button',name='LOCAL-WECHAT-ONLY',exact=False)).to_contain_text('WeChat Pay ¥209.12')
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
            await mobile_navigation(browser)
            await checks(browser)
            await real_api_checks(browser)
            await clover_real_api_checks(browser)
            await browser.close()
        print('PASS: checkout navigation, scoped manual checkout and off-duty history, order switching with the chosen tender kept, automatic tender prices and splits, scoped photo preview/upload, mobile width and access revocation. Actual Flask manual creation without payment or stock movement, order note, upload, payment, finalization, history, abandoned-checkout refund, stock posting and a WeChat Pay payment keeping the yuan charged and its rate also passed with disposable data. Clover orders against actual Flask routes and a fake Clover: automatic pick-up, cash price, Clover order labelled paid and left open, split with card, cancel and delete, find by ID, label retry and a POPCORE order sent to Clover for card; no live Clover/auth verification.')
    finally:
        process.terminate();process.wait(timeout=10)

if __name__=='__main__':asyncio.run(main())
