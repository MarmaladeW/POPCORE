"""User access checkboxes with real Flask APIs, disposable data and mocked Auth0."""
import asyncio
from contextlib import closing
import json
import socket
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server

sys.path.insert(0, str(HERE.parent))
from support import IsolatedApiCase

OUT = HERE.parents[2] / '.local' / 'user-access' / 'browser'


async def check(browser, case):
    state = {'fail_read': False, 'fail_write': False}
    context, page = await context_with_api(browser, {'mode': 'data'}, auth={'role': 'admin'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    async def users(route):
        await route.fulfill(json=[{'id': 'fixture|admin', 'username': 'Admin', 'role': 'admin', 'is_active': 1},
                                 {'id': 'fixture|staff', 'username': 'Staff', 'role': 'staff', 'is_active': 1}])
    async def api(route):
        parsed = urlparse(route.request.url)
        method = route.request.method
        if parsed.path == '/api/inventory/access' and method == 'GET' and state['fail_read']:
            await route.fulfill(status=503, json={'error': 'Access service unavailable'})
            return
        with patch('auth._decode_token', return_value={'sub': 'fixture|admin', 'https://popcore/role': 'admin'}):
            response = case.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''), method=method,
                headers=case.headers('admin'), json=route.request.post_data_json if route.request.post_data else None)
        if method == 'POST' and state['fail_write']:
            # Server saved, but response was lost: reload must show the saved grant.
            await route.fulfill(status=503, json={'error': 'Could not confirm access change'})
        else:
            await route.fulfill(status=response.status_code, json=response.get_json())
    await page.route(BASE + '/api/users', users)
    await page.route(BASE + '/api/inventory/access', api)
    await page.route(BASE + '/api/today*', api)
    await page.goto(BASE + '/today')
    await expect(page.get_by_text('Today store access denied', exact=True)).to_be_visible()
    await page.goto(BASE + '/users')
    dt = page.get_by_role('checkbox', name='Admin: DT operations access', exact=True)
    staff_dt = page.get_by_role('checkbox', name='Staff: DT operations access', exact=True)
    await expect(dt).not_to_be_checked()
    await dt.click()
    await expect(dt).to_be_checked()
    await expect(staff_dt).not_to_be_checked()
    await page.goto(BASE + '/today')
    await expect(page.get_by_text('Today store access denied', exact=True)).to_have_count(0)
    await expect(page.get_by_text('No inventory store access', exact=True)).to_have_count(0)
    await page.goto(BASE + '/users')
    await expect(dt).to_be_checked()
    await dt.click()
    await expect(dt).not_to_be_checked()
    state['fail_read'] = True
    await page.get_by_role('button', name='刷新', exact=True).click()
    await expect(page.get_by_role('alert')).to_contain_text('Access service unavailable')
    await expect(dt).to_have_count(0)
    state['fail_read'] = False
    await page.get_by_role('button', name='Retry access', exact=True).click()
    await expect(dt).not_to_be_checked()
    state['fail_write'] = True
    await dt.click()
    await expect(page.get_by_text('Could not confirm access change', exact=True)).to_be_visible()
    await expect(dt).to_be_checked()
    state['fail_write'] = False
    await dt.click()
    await expect(dt).not_to_be_checked()
    for width in (1440, 390):
        await page.set_viewport_size({'width': width, 'height': 980})
        await expect(dt).to_be_visible()
        assert await page.evaluate('document.body.scrollWidth') <= width + 2
        await page.screenshot(path=OUT/f'users-{width}.png', full_page=True, animations='disabled')
    with closing(case.connect()) as con:
        assert con.execute('SELECT COUNT(*) FROM inventory_access').fetchone()[0] == 0
    await page.goto(BASE + '/today')
    await expect(page.get_by_text('Today store access denied', exact=True)).to_be_visible()
    await context.close()
    for role in ('manager', 'staff'):
        context, page = await context_with_api(browser, {'mode': 'data'}, auth={'role': role})
        await page.goto(BASE + '/users')
        await expect(page.get_by_text('Access Denied', exact=True)).to_be_visible()
        await expect(page.get_by_role('checkbox')).to_have_count(0)
        await context.close()


async def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 5174)) == 0:
            raise RuntimeError('Port 5174 is already in use')
    OUT.mkdir(parents=True, exist_ok=True)
    case = IsolatedApiCase(); case.setUp()
    with (OUT/'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND/'node_modules/vite/bin/vite.js'), '--config', str(HERE/'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_server(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    await check(browser, case)
                except Exception:
                    for context in browser.contexts:
                        if context.pages:
                            await context.pages[-1].screenshot(path=OUT/'failure.png', full_page=True)
                            break
                    raise
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10); case.tearDown()


if __name__ == '__main__':
    asyncio.run(main())
    print('User operations access browser checks passed')
