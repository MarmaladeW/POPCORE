from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from fixtures import payload


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / 'popcore_app' / 'frontend'
HERE = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:5174'
OUT = ROOT / '.local' / 'build-1' / 'browser'


async def wait_for_server(process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Vite test server stopped before it became ready')
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', 5174)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(.1)
    raise RuntimeError('Vite test server did not start')


async def context_with_api(browser, state, viewport=None, auth=None):
    context = await browser.new_context(viewport=viewport or {'width': 1280, 'height': 800})
    await context.add_init_script(
        "window.__FOUNDATION_AUTH = " + json.dumps(auth or {}) + "; window.__FOUNDATION_REDIRECTS = 0;"
    )
    page = await context.new_page()

    async def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort()
            return
        if parsed.path.startswith('/api/'):
            state['api_count'] = state.get('api_count', 0) + 1
            recorded_request = {
                'method': route.request.method, 'path': parsed.path,
                'query': parsed.query,
                'idempotency_key': route.request.headers.get('idempotency-key'),
            }
            if route.request.headers.get('content-type', '').startswith('application/json'):
                recorded_request['post_data'] = route.request.post_data_json
            state.setdefault('requests', []).append(recorded_request)
            if (parsed.path.startswith('/api/schedule/shifts/')
                    and route.request.method in {'PATCH', 'DELETE'}):
                state.setdefault('schedule_mutations', []).append(route.request.method)
                await route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
                return
            bootstrap = parsed.path in ('/api/series', '/api/product_types', '/api/stores')
            if state.get('bootstrap_error') and bootstrap:
                await route.fulfill(status=503, content_type='application/json', body='{"error":"unavailable"}')
                return
            if state.get('mode') == 'error' and not bootstrap:
                await route.fulfill(status=503, content_type='application/json', body='{"error":"synthetic failure"}')
                return
            if state.get('mode') == 'forbidden' and not bootstrap:
                await route.fulfill(status=403, content_type='application/json', body='{"error":"forbidden"}')
                return
            if state.get('mode') == 'unauthorized' and not bootstrap:
                await route.fulfill(status=401, content_type='application/json', body='{"error":"invalid token"}')
                return
            forced = state.get('status_for_path', {}).get(parsed.path)
            if forced and route.request.method != 'GET':
                await route.fulfill(status=forced[0], content_type='application/json', body=json.dumps(forced[1]))
                return
            forced_get = state.get('get_status_for_path', {}).get(parsed.path)
            if forced_get and route.request.method == 'GET':
                await route.fulfill(status=forced_get[0], content_type='application/json', body=json.dumps(forced_get[1]))
                return
            if state.get('delay_dt') and 'store_code=DT' in parsed.query and parsed.path == '/api/stock':
                await asyncio.sleep(.6)
            custom_payload = state.get('api_payload')
            body = (custom_payload(parsed.path, parsed.query, route.request)
                    if custom_payload else payload(
                        parsed.path, parsed.query, state.get('mode') == 'empty'
                    ))
            if parsed.path == '/api/today' and state.get('delay_first_today'):
                state['today_reads'] = state.get('today_reads', 0) + 1
                if state['today_reads'] == 1:
                    await asyncio.sleep(.6)
            await route.fulfill(status=200, content_type='application/json', body=json.dumps(body))
            return
        await route.continue_()

    await page.route('**/*', route_request)
    return context, page


async def page_state_checks(browser):
    pages = (
        ('/stock', 'Unable to load stock data.', 'Foundation Item', 'No data'),
        ('/products', 'Unable to load products.', 'Foundation Item', 'No data'),
        ('/sales', 'Unable to load sales data.', 'Foundation Item', 'Sales Log'),
    )
    for path, error_text, marker, empty_text in pages:
        state = {'mode': 'error'}
        context, page = await context_with_api(browser, state)
        await page.goto(BASE + path)
        await expect(page.get_by_role('alert')).to_contain_text(error_text)
        await expect(page.get_by_text(marker, exact=False)).to_have_count(0)
        state['mode'] = 'data'
        await page.get_by_role('button', name='Retry', exact=True).click()
        await expect(page.get_by_role('alert')).to_have_count(0)
        await expect(page.get_by_text(marker, exact=False).first).to_be_visible()

        state['mode'] = 'error'
        await page.get_by_role('button', name='Refresh', exact=True).click()
        await expect(page.get_by_role('alert')).to_contain_text('previously loaded')
        await expect(page.get_by_text(marker, exact=False).first).to_be_visible()
        state['mode'] = 'data'
        await page.get_by_role('button', name='Retry', exact=True).click()
        await expect(page.get_by_role('alert')).to_have_count(0)
        await context.close()

        state = {'mode': 'empty'}
        context, page = await context_with_api(browser, state)
        await page.goto(BASE + path)
        await expect(page.get_by_role('alert')).to_have_count(0)
        empty_locator = (
            page.locator('.ant-empty-description').get_by_text('No data', exact=True)
            if empty_text == 'No data'
            else page.get_by_text(empty_text, exact=False).first
        )
        await expect(empty_locator).to_be_visible()
        await context.close()


async def auth_checks(browser):
    state = {'mode': 'data', 'api_count': 0}
    context, page = await context_with_api(browser, state, auth={'tokenReject': True})
    await page.goto(BASE + '/')
    await expect(page.get_by_text('Unable to load store setup')).to_be_visible()
    assert state['api_count'] == 0, f"token rejection allowed {state['api_count']} API requests"
    await context.close()

    context, page = await context_with_api(browser, {'mode': 'data'}, auth={'authenticated': False})
    await page.goto(BASE + '/')
    await page.wait_for_timeout(300)
    assert await page.evaluate('window.__FOUNDATION_REDIRECTS') == 1
    await context.close()

    context, page = await context_with_api(
        browser, {'mode': 'data'},
        auth={'authenticated': False, 'redirectReject': True},
    )
    await page.goto(BASE + '/')
    await expect(page.get_by_text('Synthetic redirect failure', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Retry sign-in', exact=True)).to_be_visible()
    assert await page.evaluate('window.__FOUNDATION_REDIRECTS') == 1
    await context.close()

    context, page = await context_with_api(browser, {'mode': 'unauthorized'})
    await page.goto(BASE + '/stock')
    await expect(page.locator('.ant-modal-confirm-title:visible', has_text='Sign-in rejected')).to_have_count(1)
    await expect(page.get_by_role('button', name='Sign in again', exact=True)).to_have_count(1)
    assert await page.evaluate('window.__FOUNDATION_REDIRECTS') == 0
    await context.close()

    context, page = await context_with_api(browser, {'mode': 'forbidden'})
    await page.goto(BASE + '/stock')
    await expect(page.get_by_role('alert')).to_be_visible()
    assert await page.evaluate('window.__FOUNDATION_REDIRECTS') == 0
    await context.close()


async def wrong_store_check(browser):
    state = {'mode': 'data', 'delay_dt': True}
    context, page = await context_with_api(browser, state)
    await page.goto(BASE + '/stock')
    selector = page.locator('select').first
    await selector.select_option('DT')
    await selector.select_option('MK')
    await expect(page.get_by_text('MK Foundation Item', exact=False).first).to_be_visible()
    await page.wait_for_timeout(800)
    await expect(page.get_by_text('DT Foundation Item', exact=False)).to_have_count(0)

    await selector.select_option('DT')
    await page.get_by_role('button', name=re.compile('History')).click()
    await expect(page.get_by_text('DT History Item', exact=True)).to_be_visible()
    await selector.select_option('MK')
    await expect(page.get_by_text('MK History Item', exact=True)).to_be_visible()
    await expect(page.get_by_text('DT History Item', exact=True)).to_have_count(0)
    await context.close()


async def schedule_checks(browser):
    for viewport in ({'width': 1280, 'height': 800}, {'width': 390, 'height': 844}):
        context, page = await context_with_api(browser, {'mode': 'data'}, viewport=viewport)
        await page.goto(BASE + '/schedule')
        await expect(page.get_by_text('Schedule', exact=True).first).to_be_visible()
        await expect(page.get_by_text('Foundation User', exact=False).first).to_be_visible()
        width = await page.evaluate('document.body.scrollWidth')
        assert width <= viewport['width'] + 2, f"Schedule overflowed at {viewport['width']}px"
        await context.close()

    state = {'mode': 'data', 'schedule_mutations': []}
    context, page = await context_with_api(browser, state)
    await page.goto(BASE + '/schedule')
    await expect(page.get_by_role('heading', name='Schedule', exact=True)).to_be_visible()
    await page.locator('.fc-event:not(.fc-bg-event)').first.click()
    await expect(page.get_by_role('button', name='Save', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Save', exact=True).click()
    await page.wait_for_timeout(100)
    assert 'PATCH' in state['schedule_mutations']
    await page.locator('.fc-event:not(.fc-bg-event)').first.click()
    await page.get_by_role('button', name='Delete', exact=True).click()
    await page.wait_for_timeout(100)
    assert 'DELETE' in state['schedule_mutations']
    await context.close()

    context, page = await context_with_api(browser, {'mode': 'data'}, auth={'role': 'viewer'})
    await page.goto(BASE + '/sales')
    await expect(page.get_by_text('Access Denied', exact=True)).to_be_visible()
    await page.goto(BASE + '/schedule')
    await expect(page.get_by_text('Schedule', exact=True).first).to_be_visible()
    selector = page.locator('select').first
    await selector.select_option('DT')
    await selector.select_option('MK')
    await expect(selector).to_have_value('MK')
    await expect(page.get_by_text('Schedule', exact=True).first).to_be_visible()
    await context.close()


async def navigation_checks(browser):
    evidence = ROOT / '.local' / 'frontend-alignment' / 'after'
    evidence.mkdir(parents=True, exist_ok=True)
    for viewport in ({'width': 390, 'height': 844}, {'width': 768, 'height': 900},
                     {'width': 1440, 'height': 1000}):
        context, page = await context_with_api(
            browser, {'mode': 'data'}, viewport=viewport, auth={'role': 'staff'},
        )
        await page.goto(BASE + '/goods/receiving')
        inventory = page.get_by_role('link', name='Inventory', exact=True)
        await expect(inventory).to_have_attribute('aria-current', 'page')
        if viewport['width'] == 390:
            more = page.get_by_role('button', name='More', exact=True)
            await more.focus()
            await page.keyboard.press('Enter')
            await expect(page.get_by_role('dialog', name='More')).to_be_visible()
            await page.keyboard.press('Escape')
            await expect(more).to_be_focused()
        assert await page.evaluate('document.body.scrollWidth') <= viewport['width'] + 2
        await page.screenshot(path=evidence / f"operations-{viewport['width']}.png", full_page=True)
        await page.goto(BASE + '/schedule')
        await expect(page.get_by_text('Schedule', exact=True).first).to_be_visible()
        await page.screenshot(path=evidence / f"schedule-{viewport['width']}.png", full_page=True)
        await context.close()


async def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 5174)) == 0:
            raise RuntimeError('Port 5174 is already in use')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'failure.png').unlink(missing_ok=True)
    log = (OUT / 'vite.log').open('w', encoding='utf-8')
    command = ['node', str(FRONTEND / 'node_modules' / 'vite' / 'bin' / 'vite.js'), '--config', str(HERE / 'vite.config.mjs')]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    process = subprocess.Popen(command, cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    try:
        await wait_for_server(process)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                await page_state_checks(browser)
                await auth_checks(browser)
                await wrong_store_check(browser)
                await schedule_checks(browser)
                await navigation_checks(browser)
            except Exception:
                for context in browser.contexts:
                    if context.pages:
                        await context.pages[-1].screenshot(path=OUT / 'failure.png', full_page=True)
                        break
                raise
            finally:
                await browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    started = time.perf_counter()
    asyncio.run(main())
    print(f'Foundation browser checks passed in {time.perf_counter() - started:.1f}s')
