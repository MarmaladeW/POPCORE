"""Request paths default to the Toronto business date, never the server clock's date.

Production's clock runs on UTC, so from 20:00 Toronto (19:00 in winter) the server's
date.today() is already tomorrow. Defaults come from checkout_access.business_date().
"""
import ast
from collections import Counter
from contextlib import ExitStack, closing
from datetime import date, datetime, timezone
from unittest.mock import patch
import unittest

from support import APP_DIR, IsolatedApiCase

# Server-clock dates that are genuinely fine: export file names. (file, function): uses allowed.
ALLOWED = {
    ('blueprints/products.py', 'export_products'): 1,
    ('blueprints/products.py', 'export_count_sheet'): 1,
    ('blueprints/products.py', 'export_identity_sheet'): 1,
    ('blueprints/stock.py', 'export_stock'): 1,
}

UTC_NOW = datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc)  # 21:30 on Oct 26 in Toronto
TORONTO_DAY = '2026-10-26'


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return '.'.join(reversed(parts))


def server_clock_dates(source):
    """(function, line, call) for date.today(), naive datetime.now() and utcnow().date()."""
    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            scope = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                target, method = child.func.value, child.func.attr
                bare = not child.args and not child.keywords
                if method == 'today' and bare:
                    found.append((function, child.lineno, f'{_dotted(target)}.today()'))
                elif method == 'now' and bare and _dotted(target).split('.')[-1] == 'datetime':
                    found.append((function, child.lineno, f'{_dotted(target)}.now()'))
                elif (method == 'date' and isinstance(target, ast.Call)
                      and isinstance(target.func, ast.Attribute) and target.func.attr == 'utcnow'):
                    found.append((function, child.lineno, f'{_dotted(target.func)}().date()'))
            visit(child, scope)

    visit(ast.parse(source), None)
    return found


class ServerClockGuardTests(unittest.TestCase):
    def test_request_paths_never_take_the_date_from_the_server_clock(self):
        files = sorted(APP_DIR.glob('blueprints/*.py')) + sorted(APP_DIR.glob('*_operations.py'))
        self.assertTrue(files)
        uses, where = Counter(), {}
        for path in files:
            relative = path.relative_to(APP_DIR).as_posix()
            for function, line, call in server_clock_dates(path.read_text(encoding='utf-8')):
                uses[(relative, function)] += 1
                where.setdefault((relative, function), []).append(f'{relative}:{line} {call} in {function}()')
        unexpected = [entry for key, count in uses.items() if count > ALLOWED.get(key, 0) for entry in where[key]]
        self.assertEqual(unexpected, [], 'Use checkout_access.business_date() for business dates')

    def test_the_guard_sees_each_form(self):
        sample = ('def f():\n    date.today(); _dt.datetime.now(); datetime.utcnow().date()\n'
                  '    datetime.now(ZoneInfo("America/Toronto")); datetime.utcnow().isoformat()\n')
        self.assertEqual(server_clock_dates(sample), [
            ('f', 2, 'date.today()'), ('f', 2, '_dt.datetime.now()'), ('f', 2, 'datetime.utcnow().date()')])


class _UtcServerClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return UTC_NOW.astimezone(tz) if tz else UTC_NOW.replace(tzinfo=None)


class _UtcServerDate(date):
    @classmethod
    def today(cls):
        return UTC_NOW.date()


class DefaultDateTests(IsolatedApiCase):
    """After 20:00 Toronto the UTC server date is tomorrow; undated writes still land on today."""

    def setUp(self):
        super().setUp()
        clock = ExitStack()
        clock.enter_context(patch('checkout_access.datetime', _UtcServerClock))
        for module in ('blueprints.stock', 'blueprints.sales'):
            # Any read of the server's own date in these routes would see Oct 27.
            clock.enter_context(patch(f'{module}.date', _UtcServerDate, create=True))
        self.addCleanup(clock.close)

    def dates(self, sql):
        with closing(self.connect()) as con:
            return [row[0] for row in con.execute(sql)]

    def test_undated_stock_and_sales_writes_use_the_toronto_day(self):
        admin, pid = self.headers('admin'), self.product_id
        for path, body in (('/api/stock/restock_upstairs', {'qty': 2}), ('/api/stock/ru_dian', {'qty': 1}),
                           ('/api/stock/adjust', {'new_qty': 5, 'location': 'instore', 'notes': 'count'})):
            with self.subTest(path=path):
                response = self.client.post(path, headers=admin, json={'store_code': 'DT', 'product_id': pid, **body})
                self.assertEqual(response.status_code, 200, response.get_json())
        batch = self.client.post('/api/stock/batch_operation', headers=admin, json={
            'store_code': 'DT', 'operation': 'restock_upstairs', 'items': [{'product_id': pid, 'qty': 1}]})
        self.assertEqual(batch.status_code, 200, batch.get_json())
        self.assertEqual(set(self.dates('SELECT date FROM stock_transactions')), {TORONTO_DAY})

        self.assertEqual(self.client.post('/api/sales/upsert', headers=admin, json={
            'store_code': 'DT', 'product_id': pid, 'qty_pos': 1}).status_code, 200)
        self.assertEqual(self.client.post('/api/sales/add_product', headers=admin, json={
            'store_code': 'MK', 'product_id': pid}).status_code, 200)
        self.assertEqual(self.client.post('/api/sales/batch_upsert', headers=admin, json={
            'store_code': 'MK', 'items': [{'product_id': pid, 'qty_cash': 2}]}).status_code, 200)
        self.assertEqual(self.dates('SELECT date FROM daily_sales'), [TORONTO_DAY, TORONTO_DAY])

    def test_undated_sales_reads_and_export_use_the_toronto_day(self):
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO daily_sales(product_id,date,store,qty_pos,qty_sold)
                           VALUES (?,?,'DT',4,4)""", (self.product_id, TORONTO_DAY))
            con.commit()
        manager = self.headers('manager')
        rows = self.client.get('/api/sales?store_code=DT', headers=manager).get_json()
        self.assertEqual([row['date'] for row in rows], [TORONTO_DAY])
        export = self.client.get('/api/sales/export?store_code=DT', headers=manager)
        self.assertIn(f'sales_DT_2026-09-26_{TORONTO_DAY}.csv', export.headers['Content-Disposition'])
        self.assertIn(TORONTO_DAY, export.get_data(as_text=True))
