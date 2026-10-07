"""Real Flask restock receipt and ambiguous-response retry using disposable data."""
import asyncio
import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from check_foundation import FRONTEND, HERE

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local/restock-receiving/browser'
BASE = 'http://127.0.0.1:5186'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_restock_lifecycle import RestockLifecycleTests


async def checks(browser, fixture):
    context = await browser.new_context(viewport={'width':390,'height':844})
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    page = await context.new_page()
    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*',local_only)
    requests = []
    async def uncertain_receive(route):
        requests.append((route.request.post_data_json, route.request.headers.get('idempotency-key')))
        response = await route.fetch()
        assert response.status == 200, await response.text()
        if len(requests) == 1:
            await route.abort('failed')  # The server committed; the browser did not receive its reply.
        else:
            await route.fulfill(response=response)
    path = f'/api/restock/session/{fixture.session_id}'
    await page.route('**'+path+'/receive',uncertain_receive)
    await page.goto(BASE+f'/restock?session_id={fixture.session_id}')
    modal = page.get_by_role('dialog')
    await modal.get_by_role('tab',name='Receive',exact=True).click()
    amount = modal.get_by_label('Line 1 quantity',exact=True)
    # Substring names: on a slow runner Ant Design can leave its zero-width loading icon
    # (aria-label "loading") inside these buttons after a request, which changes the exact name.
    receive = modal.get_by_role('button',name='Receive quantities')
    return_to_source = modal.get_by_role('button',name='Return quantities to source')
    await expect(amount).to_have_value('4')
    async def reopen_receive():
        # Every saved action reloads the session, which switches back to the picking tab.
        # Wait for that switch before returning to Receive, or it lands after the click.
        await expect(modal.get_by_role('tab',name='仓库拣货',exact=False)).to_have_attribute('aria-selected','true')
        await modal.get_by_role('tab',name='Receive',exact=True).click()
        await expect(modal.get_by_role('tab',name='Receive',exact=True)).to_have_attribute('aria-selected','true')
    await amount.fill('0')
    await expect(receive).to_be_disabled()
    await amount.fill('5')
    await expect(receive).to_be_disabled()
    await amount.fill('2')
    await receive.click()
    await expect(modal.get_by_text('Unable to confirm this request. Retry to check its result.',exact=True)).to_be_visible()
    await expect(amount).to_be_disabled()
    await expect(return_to_source).to_be_disabled()
    await expect(modal.get_by_placeholder('Shortage reason')).to_be_disabled()
    await expect(modal.get_by_role('button',name='Close',exact=True)).to_have_count(0)
    await expect(modal.get_by_role('tab',name='仓库拣货',exact=False)).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(modal).to_be_visible()
    detail = fixture.client.get(path,headers=fixture.headers()).get_json()
    assert detail['delivery']['lines'][0]['received_quantity']==2
    assert detail['delivery']['lines'][0]['outstanding_transit']==2
    await modal.get_by_role('button',name='Retry',exact=True).click()
    await reopen_receive()
    await expect(amount).to_have_value('2')
    assert len(requests)==2 and requests[0]==requests[1] and requests[0][1]
    assert requests[0][0]['lines']==[{'line_no':1,'quantity':2}]
    assert requests[0][0]['expected_version']==2
    await page.screenshot(path=str(OUT/'partial-received.png'),full_page=True)
    await amount.fill('1')
    await return_to_source.click()
    await reopen_receive()
    await expect(amount).to_have_value('1')
    await receive.click()
    await reopen_receive()
    close = modal.get_by_role('button',name='Close unfilled quantities')
    await expect(close).to_be_disabled()
    await modal.get_by_placeholder('Shortage reason').fill('One requested item unavailable')
    await close.click()
    await reopen_receive()
    await expect(modal.get_by_text('Restock delivery is complete.',exact=True)).to_be_visible()
    detail = fixture.client.get(path,headers=fixture.headers()).get_json()
    line = detail['delivery']['lines'][0]
    assert (line['received_quantity'],line['returned_quantity'],line['short_quantity'],line['outstanding_transit'])==(3,1,1,0)
    with closing(fixture.connect()) as con:
        quantities={(row['location_id'],row['disposition']):row['quantity'] for row in con.execute(
            'SELECT location_id,disposition,quantity FROM inventory_balances WHERE product_id=?',(fixture.product_id,))}
    assert quantities[(fixture.floor,'saleable')]==3
    assert quantities[(fixture.back,'saleable')]==7
    assert quantities[(fixture.back,'transit')]==0
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    fixture=RestockLifecycleTests();fixture.setUp()
    picked=fixture.client.post(f'/api/restock/session/{fixture.session_id}/pick',headers={**fixture.headers(),'Idempotency-Key':'browser-restock-pick'},json={'expected_version':1,'lines':[{'line_no':1,'quantity':4}]})
    assert picked.status_code==200,picked.get_json()
    server=make_server('127.0.0.1',5058,fixture.app)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    config=OUT/'vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE/'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5186,proxy:{{'/api':'http://127.0.0.1:5058'}}}}}}")
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--config',str(config)],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5186);writer.close();await writer.wait_closed();break
                except OSError:await asyncio.sleep(.1)
            else:raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch()
                try:await checks(browser,fixture)
                finally:await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)
            server.shutdown();server.server_close();thread.join(timeout=5)
            fixture.tearDown();fixture.doCleanups()


if __name__=='__main__':
    asyncio.run(main())
    print('PASS: partial restock receipt, identical ambiguous retry, partial return, shortage reason, and exact stock balances.')
