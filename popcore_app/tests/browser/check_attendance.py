"""Synthetic attendance UI regression; no real employee or attendance writes."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload

OUT = Path(__file__).resolve().parents[3] / '.local' / 'attendance' / 'browser'
ENDPOINT = '/api/schedule/attendance/today'
STORE_SCRIPT = "localStorage.setItem('popcore_selected_store', JSON.stringify({id:2,code:'MK',name:'Markham',color:'#10b981'}))"


def fixture(state):
    def payload(path, query, request):
        if path == ENDPOINT:
            if state.get('malformed_get') and request.method == 'GET':
                return {}
            if request.method == 'POST':
                assert request.post_data_json == {'shift_id': 7}
                if state.get('malformed_post'):
                    return {}
                state['attendance'] = {
                    'employee_id': 1, 'business_date': state['day'], 'store_id': 1,
                    'shift_id': 7, 'punched_in_at': state['day'] + 'T13:00:00+00:00',
                }
            return {
                'business_date': state['day'], 'attendance': state.get('attendance'),
                'shift': {'id': 7, 'date': state['day'], 'start_time': '09:00',
                          'end_time': '17:00', 'store_code': 'DT', 'store_name': 'Downtown'}
                if state.get('assigned', True) else None,
            }
        if path == '/api/today':
            return {'business_date': state['day'], 'generated_at': '', 'role': 'staff',
                    'scope': 'personal', 'store_ids': [], 'authorized_stores': [], 'sections': {}}
        if path == '/api/schedule/availability/period':
            return {'period_start': state['day'], 'store_code': 'MK', 'version': 0,
                    'submitted_at': None, 'days': []}
        return foundation_payload(path, query, False)
    return payload


async def flow(browser, viewport):
    state = {'day': '2026-09-16'}
    state['api_payload'] = fixture(state)
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'staff'})
    await context.add_init_script(STORE_SCRIPT)
    await page.clock.install(time=datetime(2026, 9, 16, 13, tzinfo=timezone.utc))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE + '/')
        panel = page.get_by_role('region', name="Today's attendance")
        punch = panel.get_by_role('button', name='Punch in', exact=True)
        await expect(punch).to_be_visible()
        await expect(panel).to_contain_text('Downtown')
        box = await punch.bounding_box()
        assert box['height'] >= 44
        shifts_box = await page.get_by_text('My shifts / 我的班次', exact=True).bounding_box()
        assert box['y'] < shifts_box['y']
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'prompt-{viewport["width"]}.png')

        state['status_for_path'] = {ENDPOINT: (503, {'error': 'Synthetic failure'})}
        async with page.expect_response(lambda response: ENDPOINT in response.url and response.request.method == 'POST'):
            await punch.click()
        await expect(panel.get_by_role('alert')).to_contain_text('could not be confirmed')
        await expect(punch).to_be_enabled()
        state['status_for_path'] = {}
        state['malformed_post'] = True
        async with page.expect_response(lambda response: ENDPOINT in response.url and response.request.method == 'POST'):
            await punch.click()
        await expect(panel.get_by_role('alert')).to_contain_text('could not be confirmed')
        await expect(punch).to_be_enabled()
        state['malformed_post'] = False
        await punch.click()
        await expect(panel).to_have_count(0)
        assert state['attendance'] is not None
        post_count = sum(r['method'] == 'POST' and r['path'] == ENDPOINT for r in state['requests'])
        assert post_count == 3

        async with page.expect_response(lambda response: ENDPOINT in response.url):
            await page.reload()
        await expect(panel).to_have_count(0)
        await page.goto(BASE + '/schedule')
        await expect(panel).to_contain_text('Punched in at 09:00 (Toronto) · 2026-09-16')
        await expect(punch).to_have_count(0)

        # Deleting a shift must not erase the recorded attendance.
        state['assigned'] = False
        state['attendance']['shift_id'] = None
        async with page.expect_response(lambda response: ENDPOINT in response.url):
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(panel).to_contain_text('Punched in at 09:00')
        await expect(punch).to_have_count(0)
        await page.screenshot(path=OUT / f'recorded-{viewport["width"]}.png')

        # Another device reads the server record; there is no client-side success flag.
        other_context, other = await context_with_api(browser, state, viewport=viewport, auth={'role': 'staff'})
        await other_context.add_init_script(STORE_SCRIPT)
        try:
            async with other.expect_response(lambda response: ENDPOINT in response.url):
                await other.goto(BASE + '/')
            await expect(other.get_by_role('region', name="Today's attendance")).to_have_count(0)
        finally:
            await other_context.close()

        # No shift, a failed read, and a malformed read remain distinct states.
        state['attendance'] = None
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(panel).to_contain_text('No assigned shift today')
        await page.goto(BASE + '/')
        await expect(panel).to_have_count(0)
        state['malformed_get'] = True
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(panel.get_by_role('alert')).to_contain_text('Unable to check')
        state['malformed_get'] = False
        state['assigned'] = True
        await panel.get_by_role('button', name='Retry attendance').click()
        await expect(punch).to_be_enabled()

        # A successful punch from another device is picked up on focus.
        state['attendance'] = {'employee_id': 1, 'business_date': state['day'], 'store_id': 1,
                               'shift_id': 7, 'punched_in_at': state['day'] + 'T13:00:00Z'}
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(panel).to_have_count(0)

        # Toronto midnight refreshes an idle page without a focus event.
        state['day'] = '2026-09-17'
        state['attendance'] = None
        await page.clock.set_system_time(datetime(2026, 9, 17, 4, tzinfo=timezone.utc))
        await page.clock.run_for(30_001)
        await expect(punch).to_be_enabled()
        await expect(panel).to_contain_text('2026-09-17')
        assert sum(r['method'] == 'POST' and r['path'] == ENDPOINT for r in state['requests']) == post_count
        assert all(r['query'] == '' for r in state['requests'] if r['path'] == ENDPOINT)
        assert not errors, errors
    except Exception:
        await page.screenshot(path=OUT / f'failure-{viewport["width"]}.png')
        raise
    finally:
        await context.close()


async def timeout_flow(browser):
    state = {'day': '2026-09-16'}
    state['api_payload'] = fixture(state)
    context, page = await context_with_api(browser, state, auth={'role': 'staff'})
    await page.clock.install(time=datetime(2026, 9, 17, 3, 59, tzinfo=timezone.utc))
    held_method = 'GET'
    held = asyncio.Event()
    held_routes = []

    async def hold_attendance(route):
        if route.request.method == held_method:
            held_routes.append(route)
            held.set()  # Leave the request unanswered; Axios must time it out.
        else:
            await route.fallback()

    await page.route('**' + ENDPOINT, hold_attendance)
    panel = page.get_by_role('region', name="Today's attendance")
    punch = panel.get_by_role('button', name='Punch in', exact=True)
    try:
        await page.goto(BASE + '/schedule')
        await asyncio.wait_for(held.wait(), 5)
        await expect(panel.get_by_role('alert')).to_contain_text('Unable to check', timeout=25_000)
        await expect(panel.get_by_role('button', name='Retry attendance')).to_be_enabled()
        held_method = None
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(punch).to_be_enabled()

        held_method = 'POST'
        held.clear()
        async with page.expect_event('requestfailed', predicate=lambda request: ENDPOINT in request.url and request.method == 'POST', timeout=25_000):
            await punch.click()
            await asyncio.wait_for(held.wait(), 5)
            await expect(punch).to_be_disabled()
            # Midnight clears yesterday's shift while the POST is still pending.
            state['day'] = '2026-09-17'
            await page.clock.set_system_time(datetime(2026, 9, 17, 4, tzinfo=timezone.utc))
            await page.clock.fast_forward(30_001)
            await expect(punch).to_have_count(0)
        # The timeout must release posting and let the new-day GET finish.
        await expect(panel).to_contain_text('2026-09-17')
        await expect(punch).to_be_enabled()
        assert state.get('attendance') is None
        held_method = None
        await punch.click()
        await expect(panel).to_contain_text('Punched in at 09:00 (Toronto) · 2026-09-17')
    finally:
        for route in held_routes:
            await route.abort()
        await context.close()


async def viewer_flow(browser):
    state = {'day': '2026-09-16'}
    state['api_payload'] = fixture(state)
    context, page = await context_with_api(browser, state, auth={'role': 'viewer'})
    try:
        for path in ('/', '/schedule'):
            await page.goto(BASE + path)
            await expect(page.get_by_role('heading', name='Schedule' if path == '/schedule' else 'Today / 今日', exact=True)).to_be_visible()
            await expect(page.get_by_role('region', name="Today's attendance")).to_have_count(0)
        assert not any(r['path'] == ENDPOINT for r in state.get('requests', []))
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'),
                                    '--config', str(HERE / 'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_server(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    await timeout_flow(browser)
                    for viewport in ({'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}):
                        await flow(browser, viewport)
                    await viewer_flow(browser)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print('Attendance browser checks passed (GET/POST timeouts, desktop/mobile, failures, reload, cross-device, focus, Toronto midnight, viewer)')


if __name__ == '__main__':
    asyncio.run(main())
