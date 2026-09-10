"""Real browser -> Flask -> SQLite Build 6 pilot smoke, with no response mocks."""
from __future__ import annotations
import asyncio,json,socket,sqlite3,subprocess,sys,time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright,expect
from check_foundation import BASE,FRONTEND,HERE,wait_for_server

ROOT=Path(__file__).resolve().parents[3]
def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

async def main():
    stamp=time.strftime('%Y%m%d-%H%M%S');out=ROOT/'.local'/'build6'/'integration'/stamp
    out.mkdir(parents=True,exist_ok=False);api_port=free_port();ui_port=5174
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',ui_port))==0: raise RuntimeError('Port 5174 is already in use')
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    api_log=(out/'api.log').open('w',encoding='utf-8');ui_log=(out/'vite.log').open('w',encoding='utf-8')
    api=subprocess.Popen([sys.executable,str(HERE/'local_app.py'),'--root',str(out),'--port',str(api_port)],cwd=ROOT,stdout=api_log,stderr=subprocess.STDOUT,creationflags=flags)
    ui=None
    try:
        ready=out/'ready.json'
        for _ in range(200):
            if ready.exists(): break
            if api.poll() is not None: raise RuntimeError('Disposable API exited before ready')
            await asyncio.sleep(.05)
        config=json.loads(ready.read_text(encoding='utf-8'))
        ui=subprocess.Popen(['node',str(FRONTEND/'node_modules'/'vite'/'bin'/'vite.js'),'--config',str(HERE/'vite.config.mjs')],cwd=FRONTEND,stdout=ui_log,stderr=subprocess.STDOUT,creationflags=flags)
        await wait_for_server(ui)
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            context=await browser.new_context(viewport={'width':390,'height':844})
            page=await context.new_page()
            staff_token=json.dumps(config['tokens']['staff'])
            await page.add_init_script(f"""window.__FOUNDATION_AUTH={{role:'staff',token:{staff_token}}};localStorage.setItem('popcore_selected_store',JSON.stringify({{id:1,code:'DT',name:'Downtown Toronto',color:'#6366f1'}}))""")
            async def real_api(route):
                parsed=urlsplit(route.request.url)
                if not parsed.path.startswith('/api/'):
                    await route.continue_();return
                response=await route.fetch(url=f'http://127.0.0.1:{api_port}{parsed.path}'+(f'?{parsed.query}' if parsed.query else ''))
                if response.status>=400:
                    print(f'{route.request.method} {parsed.path} -> {response.status}: {await response.text()}',flush=True)
                await route.fulfill(response=response)
            await page.route('**/api/**',real_api)
            await page.goto(BASE+'/sales/entry')
            await expect(page.get_by_text('Enter completed sale',exact=True)).to_be_visible()
            await page.get_by_label('Receipt or order reference').fill('PILOT-REAL-1')
            await page.get_by_label('Product').click();await page.get_by_text('Pilot Box',exact=False).last.click()
            await page.get_by_label('Quantity').fill('2');await page.get_by_label('Actual unit price (cents)').fill('1000')
            await page.get_by_label('Actual collected total (cents)').fill('2000');await page.get_by_label('Cash').fill('2000')
            await page.get_by_role('button',name='Save draft').click();await expect(page.get_by_text('Draft saved',exact=True)).to_be_visible()
            await page.get_by_role('button',name='Record sale').click();await expect(page.get_by_text('Sale recorded',exact=True)).to_be_visible()
            await page.get_by_role('button',name='View sale').click();await expect(page.get_by_text('Sale #1',exact=True)).to_be_visible()
            await page.get_by_role('button',name='Add evidence').click()
            png=bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c020000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082')
            await page.locator('input[type=file]').set_input_files({'name':'pilot.png','mimeType':'image/png','buffer':png})
            await page.get_by_role('button',name='Upload privately').click();await expect(page.get_by_text('Evidence saved for review',exact=True)).to_be_visible()
            await page.goto(BASE+'/sales/documents/1');await expect(page.get_by_text('Evidence #1',exact=True)).to_be_visible()
            await page.evaluate("([token])=>{window.__FOUNDATION_AUTH={role:'manager',token};window.dispatchEvent(new Event('foundation-auth'))}",[config['tokens']['manager']])
            await page.get_by_label('Payment 1 reason').fill('Cash counted');await page.get_by_role('button',name='Verify').click()
            await expect(page.get_by_text('verified',exact=True)).to_be_visible()
            await page.get_by_label('Evidence 1 reason').fill('Readable receipt');await page.get_by_role('button',name='Accept').click()
            await expect(page.get_by_text('accepted',exact=True)).to_be_visible()
            await page.goto(BASE+'/reports?report=tenders')
            await page.evaluate("([token])=>{window.__FOUNDATION_AUTH={role:'manager',token};window.dispatchEvent(new Event('foundation-auth'))}",[config['tokens']['manager']])
            await expect(page.get_by_text('Operational reports',exact=True)).to_be_visible()
            await expect(page.get_by_role('cell',name='2000').first).to_be_visible()
            await page.goto(BASE+'/closing');await page.get_by_role('button',name='Start closing').click()
            await expect(page.get_by_text('Source documents',exact=True)).to_be_visible()
            await page.screenshot(path=out/'real-closing-390.png',full_page=True)
            await context.close();await browser.close()
        con=sqlite3.connect(config['db_path'])
        facts={'sale_documents':con.execute('SELECT COUNT(*) FROM sale_documents').fetchone()[0],
               'sale_movements':con.execute("SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'").fetchone()[0],
               'payments':con.execute('SELECT COUNT(*) FROM sale_payments').fetchone()[0],
               'verified_payments':con.execute("SELECT COUNT(*) FROM sale_payments WHERE state='verified'").fetchone()[0],
               'accepted_evidence':con.execute("SELECT COUNT(*) FROM payment_evidence WHERE status='accepted'").fetchone()[0],
               'closing_sessions':con.execute('SELECT COUNT(*) FROM closing_sessions').fetchone()[0]}
        con.close();assert facts=={'sale_documents':1,'sale_movements':1,'payments':1,'verified_payments':1,'accepted_evidence':1,'closing_sessions':1},facts
        (out/'result.json').write_text(json.dumps({'passed':True,'facts':facts},indent=2),encoding='utf-8')
        print(f'Real release pilot passed; evidence: {out}',flush=True)
    finally:
        for process in (ui,api):
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        api_log.close();ui_log.close()

if __name__=='__main__': asyncio.run(main())
