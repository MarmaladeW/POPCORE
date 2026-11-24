"""异常提醒 Watcher alerts in a real browser against the real Flask app and a disposable database (owner, 2026-11-24:
"monitor any abnormal behavior within the app and notify the admin and the manager"). The admin sees every alert,
Claude's notes with the real names back in, what Claude marked normal folded apart, and exactly what was sent to
Claude; checking one moves it to the settled list. DT's manager sees only DT's alert, on the page and on the home.
设置 has the watcher's switches, review time and daily cap."""
import asyncio
import json
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'watch-alerts'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def open_as(browser, fixture, width, role):
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}));")
    page = await context.new_page()

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       method=route.request.method, headers=fixture.headers(role), json=body)
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)
    return context, page


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_watcher import AccessTests
    fixture = AccessTests()
    fixture.setUp()
    # Last night's review: a note on the DT loss, the MK cash gap marked normal, and what was sent.
    with closing(fixture.connect()) as con:
        con.execute("""UPDATE watch_alerts SET verdict='keep', reviewed_at=datetime('now'),
                           note_zh='DT 的 坐坐派对 被偷 1 盒，30 天内第一次；请看监控。',
                           note_en='First write-off at DT in 30 days; check the camera.' WHERE kind='stock_loss'""")
        con.execute("""UPDATE watch_alerts SET verdict='low', reviewed_at=datetime('now'),
                           note_zh='差 $30，只占应收的 5.7%。', note_en='A $30 gap, 5.7% of expected.' WHERE kind='cash_gap'""")
        sent = {'alerts': [{'id': 'A1', 'kind': 'stock_loss', 'severity': 'high', 'store': 'S1', 'product': 'P1',
                            'days_ago': 1, 'quantity': 1, 'unit': 'box', 'reason': 'stolen', 'losses_30d': 0},
                           {'id': 'A2', 'kind': 'cash_gap', 'severity': 'normal', 'store': 'S2', 'days_ago': 1,
                            'gap_dollars': -30, 'gap_pct': -5.7, 'gaps_30d': 0}]}
        con.execute("""INSERT INTO watch_runs(kind, business_date, outcome, found, model, input_tokens, output_tokens,
                                              cost_usd, sent, summary_zh, summary_en)
                       VALUES ('review', date('now'), 'ok', 2, 'claude-opus-5-5', 1450, 1800, 0.0418, ?,
                               'DT 有一盒被偷，要看；MK 现金差不大。', 'A box stolen at DT needs a look; the MK cash gap is small.')""",
                    (json.dumps(sent),))
        con.commit()
    try:
        context, page = await open_as(browser, fixture, width, 'admin')
        await page.goto(BASE + '/watch')
        status = page.get_by_role('region', name='Watcher status')
        await expect(status).to_contain_text('监控已开启 · Watching · 没有密钥 no key')
        await expect(status).to_contain_text('Claude 已看过 Claude reviewed · $0.042')
        await expect(status).to_contain_text('A box stolen at DT needs a look')
        open_alerts = page.get_by_role('region', name='Open alerts')
        await expect(open_alerts).to_contain_text('要紧 Urgent坐坐派对 损耗 1盒 · 被偷')
        await expect(open_alerts).to_contain_text('请看监控')
        await expect(open_alerts).to_contain_text('图鉴同步一直出错（75 分钟）')
        await expect(open_alerts).not_to_contain_text('现金')                     # folded under "looked normal"
        quiet = page.locator('details', has_text='Claude 认为正常')
        await quiet.locator('summary').click()
        await expect(quiet).to_contain_text('日报现金少 $30.00')
        sent_box = page.locator('details', has_text='发给 Claude 的全部内容')
        await sent_box.locator('summary').click()
        await expect(sent_box.locator('pre')).to_contain_text('"store": "S1"')
        await expect(sent_box.locator('pre')).not_to_contain_text('DT')
        await fits(page, f'admin-{width}.png')
        await quiet.get_by_role('button', name='已查看 Checked').click()
        settled = page.locator('details', has_text='最近两周已处理')
        await settled.locator('summary').click()
        await expect(settled).to_contain_text('已查看 Checked · Owner Olive')
        await fits(page, f'admin-checked-{width}.png')

        await page.goto(BASE + '/settings')
        tab = page.get_by_role('tab', name='异常提醒 / Alerts')
        if width < 600:  # the phone's tab bar is full: pick it from its ⋯ menu, as a person would
            await page.locator('.ant-tabs-nav-more').hover()
            await page.get_by_role('option', name='异常提醒 / Alerts').click()
        else:
            await tab.click()
        await expect(tab).to_have_attribute('aria-selected', 'true')
        panel = page.get_by_role('tabpanel', name='异常提醒 / Alerts')
        await expect(panel.get_by_role('switch').first).to_be_checked()
        await expect(panel.get_by_text('Claude sees only stand-in codes')).to_be_visible()
        await fits(page, f'settings-{width}.png')
        await context.close()

        context, page = await open_as(browser, fixture, width, 'manager')
        await page.goto(BASE + '/watch')
        open_alerts = page.get_by_role('region', name='Open alerts')
        await expect(open_alerts).to_contain_text('坐坐派对 损耗 1盒')
        await expect(open_alerts).not_to_contain_text('图鉴')                    # a sync alert is for admins
        await expect(page.get_by_role('region', name='Watcher status')).to_have_count(0)
        await fits(page, f'manager-{width}.png')
        await page.goto(BASE + '/')
        await expect(page.get_by_text('1 条异常提醒')).to_be_visible()
        await fits(page, f'manager-home-{width}.png')
        await context.close()
    finally:
        fixture.tearDown()
        fixture.doCleanups()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config',
                                    str(HERE / 'vite.config.mjs'), '--port', str(PORT)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite stopped')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', PORT)
                    writer.close(); await writer.wait_closed(); break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError('Vite did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390, 1440):
                        await run(browser, width)
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: watcher alerts — the admin sees every alert with Claude\'s notes, the normal-looking one folded, and '
          'exactly what was sent (codes only); checking moves it to settled; 设置 has the switches; DT\'s manager sees '
          'only DT\'s alert, on the page and the home, at 390 and 1440 px')
