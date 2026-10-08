"""Offline checks for posting a settled Clover sandbox order as one real already_paid sale."""
import io
import json
import os
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import payment_evidence
from blueprints import clover_posting as posting_bp, clover_sandbox as adapter
from checkout_access import business_date
from support import IsolatedApiCase

MERCHANT = 'M' * 13
ORDER = 'ABCDEFGHIJKLM'
CARD = 'NOPQRSTUVWXYZ'
PATH = '/api/clover-sandbox/checkouts/1'
TABLES = ('sale_documents', 'sale_lines', 'sale_sources', 'sale_payments', 'payment_evidence',
          'inventory_documents')


def photo(colour='blue'):
    image = io.BytesIO()
    Image.new('RGB', (2, 2), colour).save(image, format='PNG')
    image.seek(0)
    return {'image': (image, 'proof.png')}


class CloverSalePostingTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(adapter.bp)
        self.app.register_blueprint(posting_bp.bp)
        self.environment = {
            'CLOVER_SANDBOX_CHECKOUT_DIR': str(Path(self.tempdir.name) / 'sandbox'),
            'CLOVER_SANDBOX_STORE_ID': str(self.store_id),
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
            'CLOVER_SANDBOX_POST_SALES': '1',
            'CLOVER_SANDBOX_MERCHANT_ID': MERCHANT,
        }
        self.enable(self.environment)
        with closing(self.connect()) as con:
            con.execute("""UPDATE products SET stock_form='ordinary', stock_unit='piece',
                           identity_status='verified' WHERE id=?""", (self.product_id,))
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)",
                        (self.store_id,))
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?',
                        (self.store_id,))
            self.floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                                     (self.store_id,)).fetchone()[0]
            con.execute("""INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                           VALUES (?, ?, 'saleable', 5, 1)""", (self.product_id, self.floor))
            con.execute("""INSERT INTO product_barcodes(code,product_id,code_kind,input_unit,quantity_per_scan)
                           VALUES ('00123',?,'manufacturer','piece',1)""", (self.product_id,))
            employee = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Cashier')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                           VALUES (?,?,'00:01','00:02','test',?)""", (employee, business_date(), self.store_id))
            con.commit()
        # Two scans of the same barcode plus tax: subtotal 1000, total 1130.
        self.order = {'id': ORDER, 'currency': 'CAD', 'total': 1130, 'createdTime': 1_790_000_000_000,
                      'paymentState': 'OPEN', 'discounts': [], 'payments': [],
                      'items': [{'name': 'Scanned', 'price': 500, 'itemCode': '00123'},
                                {'name': 'Scanned', 'price': 500, 'itemCode': '00123'}]}
        self.save_order()

    def enable(self, environment):
        patcher = patch.dict(os.environ, environment, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save_order(self):
        with adapter.database() as con:
            con.execute("UPDATE feed SET fetched_at=?,error='',next_poll=?", (time.time(), time.time() + 60))
            con.execute('''INSERT INTO orders(source_id,payload,seen_at) VALUES (?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload,seen_at=excluded.seen_at''',
                        (self.order['id'], json.dumps(self.order), time.time()))
            con.commit()

    def record(self, tender, amount, payable, *, version=1):
        """Claim, start and complete one POPCORE-recorded payment; returns the order detail."""
        self.assertEqual(self.client.post(PATH + '/claim', headers=self.headers()).status_code, 200)
        started = self.client.post(PATH + '/attempts', headers=self.headers(), json={
            'tender': tender, 'amount_cents': amount, 'payable_cents': payable, 'expected_version': version})
        self.assertEqual(started.status_code, 200, started.get_json())
        attempt = next(a for a in started.get_json()['attempts'] if a['status'] == 'pending')
        done = self.client.post(PATH + '/complete', headers=self.headers(),
                                json={'attempt_id': attempt['id'], 'expected_version': version + 1})
        self.assertEqual(done.status_code, 200, done.get_json())
        return done.get_json()

    def add_manager(self, *, grant=True):
        with closing(self.connect()) as con:
            employee = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|manager','Manager')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                           VALUES (?,?,'00:01','00:02','test',?)""", (employee, business_date(), self.store_id))
            if grant:
                con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                            (self.store_id,))
            con.commit()

    def clover_card(self, amount):
        with adapter.database() as con:
            con.execute("INSERT INTO payments(order_id,source_id,tender,amount,result) VALUES (1,?,'card',?,'SUCCESS')",
                        (CARD, amount))
            con.commit()

    def post(self, role='staff'):
        return self.client.post(PATH + '/post', headers=self.headers(role))

    def counts(self):
        with closing(self.connect()) as con:
            return {table: con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES}

    def files(self):
        return sorted(p.name for p in payment_evidence.EVIDENCE_DIR.glob('*')) if payment_evidence.EVIDENCE_DIR.exists() else []

    def balance(self):
        with closing(self.connect()) as con:
            return con.execute("""SELECT quantity FROM inventory_balances
                WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                               (self.product_id, self.floor)).fetchone()[0]

    def test_posting_is_off_by_default_and_needs_the_merchant_id(self):
        self.record('cash', 1100, 1100)
        for missing in (['CLOVER_SANDBOX_POST_SALES'], ['CLOVER_SANDBOX_MERCHANT_ID']):
            environment = {k: v for k, v in self.environment.items() if k not in missing}
            with patch.dict(os.environ, environment, clear=True), self.subTest(missing=missing):
                self.assertFalse(posting_bp.posting.enabled())
                self.assertEqual(self.post().status_code, 404)
        with patch.dict(os.environ, {**self.environment, 'CLOVER_SANDBOX_POST_SALES': 'yes'}, clear=True):
            self.assertEqual(self.post().status_code, 404)
        self.assertEqual(self.counts(), {table: 0 for table in TABLES})
        # The sandbox adapter itself still works and still writes no sale.
        self.assertEqual(self.client.get(PATH, headers=self.headers()).get_json()['sale_id'], None)

    def test_first_post_creates_one_already_paid_sale_with_payment_evidence_and_stock(self):
        settled = self.record('cash', 1100, 1100)
        self.assertTrue(settled['settled_in_popcore'])
        attempt_id = settled['attempts'][0]['id']
        self.assertEqual(self.client.post(f'{PATH}/manual/{attempt_id}/evidence', headers=self.headers(),
                                          data=photo()).status_code, 201)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertTrue(result['created'])
        self.assertEqual((result['status'], result['financial_status'], result['allocation_status'],
                          result['unresolved_reasons'], result['collected_cents'], result['payment_total_cents']),
                         ('posted', 'recorded', 'allocated', [], 1100, 1100))
        self.assertEqual([(p['tender'], p['amount_cents'], p['source_reference'], p['evidence'])
                          for p in result['payments']], [('cash', 1100, f'{ORDER}/popcore/{attempt_id}', 1)])
        with closing(self.connect()) as con:
            sale = con.execute('SELECT * FROM sale_documents WHERE id=?', (result['sale_id'],)).fetchone()
            self.assertEqual((sale['entry_mode'], sale['store_id'], sale['created_by'], sale['subtotal_cents'],
                              sale['source_tax_cents'], sale['gross_cents'], sale['reduction_cents'],
                              sale['rounding_cents'], sale['collected_cents']),
                             ('already_paid', self.store_id, 'auth0|staff', 1000, 130, 1130, 30, 0, 1100))
            source = con.execute('SELECT source_system,source_account,source_reference FROM sale_sources').fetchall()
            self.assertEqual([tuple(row) for row in source], [('clover', MERCHANT, ORDER)])
            lines = con.execute('SELECT product_id,native_unit,quantity,unit_price_cents,raw_product_text '
                                'FROM sale_lines ORDER BY line_no').fetchall()
            self.assertEqual([tuple(row) for row in lines], [(self.product_id, 'piece', 2, 500, None)])
            payment = con.execute('SELECT recorded_by, source_system, source_account FROM sale_payments').fetchone()
            self.assertEqual(tuple(payment), ('auth0|staff', 'clover', MERCHANT))
            evidence = con.execute('SELECT id, object_id, mime_type, uploader_sub, status FROM payment_evidence').fetchone()
        self.assertEqual((evidence['mime_type'], evidence['uploader_sub'], evidence['status']),
                         ('image/png', 'auth0|staff', 'pending'))
        self.assertEqual(self.files(), [evidence['object_id']])
        self.assertTrue(evidence['object_id'].startswith(f'clover-{MERCHANT}-{ORDER}-popcore{attempt_id}-'))
        self.assertEqual(self.balance(), 3)
        self.assertEqual(self.counts()['inventory_documents'], 1)
        # The real evidence is readable through the existing private evidence API.
        content = self.client.get(f"/api/payment-evidence/{evidence['id']}/content", headers=self.headers())
        self.assertEqual(content.status_code, 200)
        content.close()

    def test_retry_returns_the_same_sale_and_never_posts_twice(self):
        settled = self.record('e_transfer', 1100, 1100)
        attempt_id = settled['attempts'][0]['id']
        self.client.post(f'{PATH}/manual/{attempt_id}/evidence', headers=self.headers(), data=photo())
        first = self.post().get_json()
        after_first = self.counts()
        self.add_manager()
        for role in ('staff', 'manager'):
            with self.subTest(role=role):
                again = self.post(role)
                self.assertEqual(again.status_code, 200, again.get_json())
                self.assertEqual(again.get_json()['sale_id'], first['sale_id'])
                self.assertFalse(again.get_json()['created'])
                self.assertEqual(again.get_json()['version'], first['version'])
        self.assertEqual(self.counts(), after_first)
        self.assertEqual(self.balance(), 3)
        self.assertEqual(len(self.files()), 1)
        # A photo added after posting is copied once, and only once, on a later retry.
        self.client.post(f'{PATH}/manual/{attempt_id}/evidence', headers=self.headers(), data=photo('red'))
        later = self.post().get_json()
        self.assertEqual((later['sale_id'], later['payments'][0]['evidence']), (first['sale_id'], 2))
        self.assertEqual(self.post().get_json()['version'], later['version'])
        self.assertEqual(self.counts(), {**after_first, 'payment_evidence': 2})
        self.assertEqual(len(self.files()), 2)

    def test_unmapped_line_keeps_the_money_fact_with_allocation_pending(self):
        self.order['items'] = [{'name': 'Scanned', 'price': 500, 'itemCode': '00123'},
                               {'name': 'Custom item', 'price': 500}]
        self.save_order()
        self.record('cash', 1130, 1130)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertEqual((result['financial_status'], result['allocation_status'], result['unresolved_reasons'],
                          result['inventory_document_id']), ('recorded', 'pending', ['product_mapping_required'], None))
        with closing(self.connect()) as con:
            lines = con.execute('SELECT product_id,quantity,raw_product_text,product_name_snapshot '
                                'FROM sale_lines ORDER BY line_no').fetchall()
            allocations = con.execute('SELECT status, reason FROM sale_allocations ORDER BY line_no').fetchall()
        self.assertEqual([tuple(row) for row in lines],
                         [(self.product_id, 1, None, 'Test Product'),
                          (None, 1, 'Clover item: Custom item', 'Clover item: Custom item')])
        self.assertEqual([tuple(row) for row in allocations], [('pending', 'product_mapping_required')] * 2)
        self.assertEqual(self.balance(), 5)
        self.assertEqual(self.counts()['inventory_documents'], 0)

    def test_split_card_and_cash_posts_both_payment_facts(self):
        partial = self.record('cash', 500, 1130)
        self.assertEqual((partial['status'], partial['remaining_cents']), ('open', 630))
        self.assertEqual(self.post().get_json()['code'], 'clover_not_settled')
        self.clover_card(630)
        settled = self.client.get(PATH, headers=self.headers()).get_json()
        self.assertEqual((settled['status'], settled['settled_in_popcore']), ('completed', True))
        card_id = next(a['id'] for a in settled['attempts'] if a['source'] == 'clover')
        self.assertEqual(self.client.post(f'{PATH}/attempts/{card_id}/evidence', headers=self.headers(),
                                          data=photo()).status_code, 201)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertEqual(sorted((p['tender'], p['amount_cents'], p['source_reference'], p['evidence'])
                                for p in result['payments']),
                         [('card', 630, CARD, 1), ('cash', 500, f'{ORDER}/popcore/1', 0)])
        self.assertEqual((result['collected_cents'], result['payment_total_cents'], result['allocation_status']),
                         (1130, 1130, 'allocated'))
        with closing(self.connect()) as con:
            sale = con.execute('SELECT reduction_cents, collected_cents FROM sale_documents').fetchone()
            recorded = con.execute('SELECT tender, recorded_by FROM sale_payments ORDER BY tender').fetchall()
        self.assertEqual(tuple(sale), (0, 1130))
        self.assertEqual([tuple(row) for row in recorded], [('card', 'auth0|staff'), ('cash', 'auth0|staff')])
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.counts()['sale_payments'], 2)

    def test_unsettled_or_changed_orders_are_refused_without_writing(self):
        self.client.post(PATH + '/claim', headers=self.headers())
        open_order = self.post()
        self.assertEqual((open_order.status_code, open_order.get_json()['code']), (409, 'clover_not_settled'))
        self.record('cash', 1100, 1100)
        self.clover_card(1130)  # taken on Clover by mistake after settlement
        flagged = self.post()
        self.assertEqual((flagged.status_code, flagged.get_json()['code']),
                         (409, 'clover_changed_after_settlement'))
        self.assertEqual(self.counts(), {table: 0 for table in TABLES})
        self.assertEqual(self.files(), [])

    def test_legacy_inventory_mode_records_money_with_allocation_pending(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='legacy' WHERE id=1")
            con.commit()
        self.record('cash', 1100, 1100)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertEqual((result['financial_status'], result['allocation_status'], result['unresolved_reasons']),
                         ('recorded', 'pending', ['inventory_legacy_mode']))
        self.assertEqual(self.balance(), 5)

    def test_posting_needs_the_cashier_or_a_manager_with_inventory_access(self):
        self.record('cash', 1100, 1100)
        self.assertEqual(self.post('staff:other').status_code, 403)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.counts()['sale_documents'], 0)
        self.add_manager(grant=False)
        self.assertEqual(self.post('manager').status_code, 403)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                        (self.store_id,))
            con.commit()
        posted = self.post('manager')
        self.assertEqual(posted.status_code, 201, posted.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT created_by FROM sale_documents').fetchone()[0], 'auth0|staff')


if __name__ == '__main__':
    unittest.main()
