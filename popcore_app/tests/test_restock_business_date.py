"""Restock sheets and 晚盘 are dated by the store's Toronto business day, not the server clock."""
from contextlib import ExitStack, closing
from datetime import date, datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase
from test_receiving import ReceivingFixture

# 01:30 UTC on Oct 27 is 21:30 on Oct 26 in Toronto: production's own clock runs on UTC,
# so it has already moved to the next day while the store is still working.
UTC_NOW = datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc)
TORONTO_DAY = '2026-10-26'


class _UtcServerClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return UTC_NOW.astimezone(tz) if tz else UTC_NOW.replace(tzinfo=None)


class _UtcServerDate(date):
    @classmethod
    def today(cls):
        return UTC_NOW.date()


def utc_server_after_midnight():
    clock = ExitStack()
    clock.enter_context(patch('checkout_access.datetime', _UtcServerClock))
    # Any read of the server's local date in these routes would see Oct 27.
    clock.enter_context(patch('blueprints.restock.date', _UtcServerDate, create=True))
    clock.enter_context(patch('blueprints.inventory.date', _UtcServerDate))
    return clock


class LegacyStoreBusinessDateTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        # Legacy restock and 晚盘 writes need access to the store (store_access.py).
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)",
                        (self.store_id,))
            con.commit()

    def test_restock_sheet_and_its_completion_use_the_toronto_day(self):
        with utc_server_after_midnight():
            created = self.client.post('/api/restock/sessions', headers=self.headers(),
                                       json={'store_code': 'DT'})
            self.assertEqual(created.status_code, 201, created.get_json())
            sid = created.get_json()['id']
            self.assertEqual(created.get_json()['date'], TORONTO_DAY)
            listed = self.client.get('/api/restock/sessions/today?store_code=DT',
                                     headers=self.headers()).get_json()
            self.assertEqual([row['id'] for row in listed], [sid])
            pending = self.client.get('/api/restock/session/today?store_code=DT',
                                      headers=self.headers())
            self.assertEqual(pending.get_json()['id'], sid)
            added = self.client.post('/api/restock/items', headers=self.headers(), json={
                'session_id': sid, 'product_id': self.product_id, 'requested_qty': 3})
            self.assertEqual(added.status_code, 201, added.get_json())
            item_id = added.get_json()['id']
            self.assertEqual(self.client.post(f'/api/restock/session/{sid}/submit',
                                              headers=self.headers()).status_code, 200)
            self.assertEqual(self.client.patch(f'/api/restock/items/{item_id}/pick',
                                               headers=self.headers(),
                                               json={'pick_status': 'found', 'found_qty': 3}
                                               ).status_code, 200)
            completed = self.client.post(f'/api/restock/session/{sid}/complete',
                                         headers=self.headers())
            self.assertEqual(completed.status_code, 200, completed.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                "SELECT date FROM stock_transactions WHERE txn_type='ru_dian'").fetchone()[0],
                TORONTO_DAY)

    def test_evening_count_is_saved_and_read_for_the_toronto_day(self):
        with utc_server_after_midnight():
            saved = self.client.post('/api/inventory-check/submit', headers=self.headers(), json={
                'store_code': 'DT', 'checks': [{'product_id': self.product_id, 'actual_qty': 2}]})
            self.assertEqual(saved.status_code, 201, saved.get_json())
            with closing(self.connect()) as con:
                con.execute('UPDATE products SET is_bestseller=1 WHERE id=?', (self.product_id,))
                con.commit()
            tonight = self.client.get('/api/inventory-check/today?store_code=DT',
                                      headers=self.headers()).get_json()
        self.assertEqual(tonight['date'], TORONTO_DAY)
        item = next(row for row in tonight['items'] if row['product_id'] == self.product_id)
        self.assertEqual(item['actual_qty'], 2)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT date FROM inventory_checks').fetchone()[0],
                             TORONTO_DAY)


class LiveStoreBusinessDateTests(ReceivingFixture):
    def test_restock_completion_document_uses_the_toronto_day(self):
        with closing(self.connect()) as con:
            back = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                (self.store_id,)).fetchone()[0]
            con.execute(
                """INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 10, 1)""", (self.product_id, back))
            sid = con.execute(
                "INSERT INTO restock_sessions(date, status, store_id) VALUES (?, 'picking', ?)",
                (TORONTO_DAY, self.store_id)).lastrowid
            con.execute(
                """INSERT INTO restock_items (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 4, 4, 'found')""", (sid, self.product_id))
            con.commit()
        with utc_server_after_midnight():
            completed = self.client.post(f'/api/restock/session/{sid}/complete',
                                         headers=self.headers())
        self.assertEqual(completed.status_code, 200, completed.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                "SELECT business_date FROM inventory_documents WHERE kind='restock_complete'"
            ).fetchone()[0], TORONTO_DAY)
