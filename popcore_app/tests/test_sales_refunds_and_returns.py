"""Money and stock that come back after a sale (owner, 2026-10-27).

A refund recorded on a sale counts once, on the Toronto business date it is recorded: the day the money
leaves. A physical return is dated the day it happens. The sale's own day keeps its receipts, and a closed
day is never rewritten: it gets a late adjustment instead."""
from contextlib import closing
from unittest.mock import patch

from test_receiving import ReceivingFixture

TODAY = '2026-09-14'
YESTERDAY = '2026-09-13'
OPENING = 65000  # the fixed bill float, with no opening coins


class StoreDayCase(ReceivingFixture):
    """One store with stock, two cashiers and a manager on shift yesterday and today. No tests of its own."""

    def setUp(self):
        super().setUp()
        clock = patch('checkout_access.business_date', return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version)
                           VALUES (?,?,'saleable',10,1)""", (self.product_id, self.floor))
            for subject, name in (('staff', 'Staff Cashier'), ('other', 'Other Cashier'), ('manager', 'Manager')):
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)',
                                       ('auth0|' + subject, name)).lastrowid
                for day in (YESTERDAY, TODAY):
                    con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                                   VALUES (?,?,'10:00','18:00','test',?)""", (employee, day, self.store_id))
                if subject != 'staff':  # the fixture already gave staff access
                    con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)',
                                ('auth0|' + subject, self.store_id))
            con.commit()
        self.keys = 0

    def send(self, path, body, role='staff', key=None):
        self.keys += 1
        return self.client.post(path, json=body, headers={
            **self.headers(role), 'Idempotency-Key': key or f'request-{self.keys}'})

    def paid_sale(self, reference, day, amount, tender='cash', role='staff'):
        """A recorded sale already paid in one tender: (sale_id, payment_id)."""
        body = {'store_id': self.store_id, 'business_date': day, 'entry_mode': 'already_paid',
                'source': {'system': 'register', 'account': 'DT', 'reference': reference},
                'subtotal_cents': amount, 'source_tax_cents': 0, 'gross_cents': amount,
                'reduction_cents': 0, 'rounding_cents': 0, 'collected_cents': amount,
                'lines': [{'product_id': self.product_id, 'unit': 'piece', 'quantity': 1,
                           'unit_price_cents': amount}]}
        sale = self.send('/api/sale-documents', body, role).get_json()
        posted = self.send(f"/api/sale-documents/{sale['sale_id']}/post", {'expected_version': sale['version']}, role)
        self.assertEqual(posted.status_code, 200, posted.get_json())
        paid = self.send(f"/api/sale-documents/{sale['sale_id']}/payments", {
            'expected_version': posted.get_json()['version'],
            'payments': [{'tender': tender, 'amount_cents': amount}]}, role)
        self.assertEqual(paid.status_code, 200, paid.get_json())
        return sale['sale_id'], paid.get_json()['payments'][0]['id']

    def sale(self, sale_id):
        return self.client.get(f'/api/sale-documents/{sale_id}', headers=self.headers('manager')).get_json()

    def review(self, payment_id, sale_id, event, reason='Checked', **body):
        """A manager's verify, reject or refund on a payment."""
        path = f'/api/payments/{payment_id}/' + (event if event in ('verify', 'reject') else 'events')
        if event not in ('verify', 'reject'):
            body['event_type'] = event
        return self.send(path, {'expected_version': self.sale(sale_id)['version'], 'reason': reason, **body},
                         'manager')

    def open_closing(self, day, role='staff'):
        return self.send('/api/closing', {'store_id': self.store_id, 'business_date': day}, role)

    def closing(self, day, role='manager'):
        response = self.open_closing(day, role)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def signed_off(self, day):
        """Stands in for a store day a manager already closed."""
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO closing_sessions(store_id,business_date,status,created_by)
                           VALUES (?,?,'closed','auth0|manager')
                           ON CONFLICT(store_id,business_date) DO UPDATE SET status='closed'""",
                        (self.store_id, day))
            con.commit()

    def late_adjustments(self, day):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute(
                """SELECT a.source_type, a.source_id FROM closing_adjustments a
                   JOIN closing_sessions c ON c.id=a.closing_session_id
                   WHERE c.store_id=? AND c.business_date=? ORDER BY a.id""", (self.store_id, day))]

    def tenders(self, day):
        response = self.client.get(f'/api/reports/tenders?store_code=DT&from={day}&to={day}',
                                   headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {row['tender']: row for row in response.get_json()['items']}


class RefundAndReturnDateTests(StoreDayCase):
    def test_a_cash_refund_of_yesterdays_sale_leaves_todays_drawer_once(self):
        sale_id, payment_id = self.paid_sale('B-1', YESTERDAY, 5000)
        self.assertEqual(self.review(payment_id, sale_id, 'verify').status_code, 200)
        self.signed_off(YESTERDAY)
        before = self.closing(YESTERDAY)
        refund = self.review(payment_id, sale_id, 'refund', 'Customer returned it today', amount_cents=5000)
        self.assertEqual(refund.status_code, 200, refund.get_json())
        yesterday, today = self.closing(YESTERDAY), self.closing(TODAY)
        # The sale's day keeps its receipts; today's drawer expects the refunded cash gone.
        self.assertEqual((yesterday['cash']['verified_cash_receipts_cents'], yesterday['cash']['expected_drawer_cents'],
                          yesterday['tender_totals_cents']['cash']),
                         (5000, before['cash']['expected_drawer_cents'], 5000))
        self.assertEqual((today['cash']['event_totals_cents']['refund'], today['cash']['expected_drawer_cents'],
                          today['tender_totals_cents']['cash']), (5000, OPENING - 5000, -5000))
        # The sale page keeps what was received and lists the refund on its own date.
        sale = self.sale(sale_id)
        self.assertEqual((sale['payment_total_cents'], sale['payment_difference_cents'], sale['refunded_cents']),
                         (5000, 0, 5000))
        self.assertEqual(sale['payments'][0]['events'][-1]['business_date'], TODAY)
        # The closed day is not rewritten; it gets a late adjustment, and the report has the refund today.
        with closing(self.connect()) as con:
            event_id = con.execute("SELECT id FROM payment_events WHERE event_type='refund'").fetchone()[0]
        self.assertIn(('payment_refund', str(event_id)), self.late_adjustments(YESTERDAY))
        self.assertEqual((self.tenders(YESTERDAY)['cash']['refund_cents'], self.tenders(TODAY)['cash']['refund_cents']),
                         (0, 5000))

    def test_a_same_day_cash_refund_is_one_refund_and_no_tender_mismatch(self):
        sale_id, payment_id = self.paid_sale('A-1', TODAY, 5000)
        self.review(payment_id, sale_id, 'verify')
        self.assertEqual(self.review(payment_id, sale_id, 'refund', 'Changed mind', amount_cents=5000).status_code, 200)
        today = self.closing(TODAY)
        self.assertEqual((today['cash']['verified_cash_receipts_cents'], today['cash']['event_totals_cents']['refund'],
                          today['cash']['expected_drawer_cents']), (5000, 5000, OPENING))
        self.assertNotIn(f'payment_mismatch:{sale_id}', today['review_exceptions'])

    def test_a_refund_recorded_after_its_day_closed_moves_that_days_closing(self):
        sale_id, payment_id = self.paid_sale('S-1', TODAY, 3000, 'card')
        session = self.closing(TODAY, 'staff')
        self.review(payment_id, sale_id, 'refund', 'Card refund on the terminal', amount_cents=1000)
        self.assertNotEqual(self.closing(TODAY, 'staff')['source_token'], session['source_token'])
        self.signed_off(TODAY)
        self.review(payment_id, sale_id, 'refund', 'Second card refund', amount_cents=500)
        with closing(self.connect()) as con:
            last = con.execute("SELECT MAX(id) FROM payment_events WHERE event_type='refund'").fetchone()[0]
        self.assertIn(('payment_refund', str(last)), self.late_adjustments(TODAY))

    def test_a_refund_needs_money_received_and_cannot_predate_its_sale(self):
        sale_id, payment_id = self.paid_sale('R-1', TODAY, 3000, 'e_transfer')
        self.assertEqual(self.review(payment_id, sale_id, 'reject', 'Never arrived').status_code, 200)
        refused = self.review(payment_id, sale_id, 'refund', 'Refund anyway', amount_cents=1000)
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'payment_rejected'))
        future_id, future_payment = self.paid_sale('F-1', '2026-09-15', 3000, 'card')
        early = self.review(future_payment, future_id, 'refund', 'Too early', amount_cents=1000)
        self.assertEqual(early.status_code, 400, early.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM payment_events WHERE event_type='refund'").fetchone()[0], 0)

    def test_closings_own_cash_refund_is_for_money_with_no_sale_and_needs_a_note(self):
        session = self.closing(TODAY, 'staff')
        body = {'expected_version': session['version'], 'event_type': 'refund', 'amount_cents': 500}
        missing = self.send(f"/api/closing/{session['closing_id']}/cash-events", {**body, 'reason': ' '})
        self.assertEqual(missing.status_code, 400, missing.get_json())
        self.assertIn('no sale in POPCORE', missing.get_json()['error'])
        noted = self.send(f"/api/closing/{session['closing_id']}/cash-events",
                          {**body, 'reason': 'Deposit taken before POPCORE, returned in cash'})
        self.assertEqual(noted.status_code, 200, noted.get_json())
        self.assertEqual(noted.get_json()['cash']['expected_drawer_cents'], OPENING - 500)

    def test_a_physical_return_is_dated_the_day_it_happens(self):
        sale_id, _ = self.paid_sale('C-1', YESTERDAY, 2000, 'card')
        self.signed_off(YESTERDAY)
        today_token = self.closing(TODAY)['source_token']
        returned = self.send(f'/api/sale-documents/{sale_id}/returns', {
            'expected_version': self.sale(sale_id)['version'], 'line_no': 1, 'quantity': 1,
            'disposition': 'saleable', 'reason': 'Brought back today'}, 'manager')
        self.assertEqual(returned.status_code, 200, returned.get_json())
        with closing(self.connect()) as con:
            dated = con.execute('SELECT business_date FROM inventory_documents WHERE id=?',
                                (returned.get_json()['inventory_document_id'],)).fetchone()[0]
        self.assertEqual(dated, TODAY)
        self.assertEqual(self.sale(sale_id)['returns'][0]['business_date'], TODAY)
        # Yesterday is closed: the change is a late adjustment. Today's stock moved: today's closing sees it.
        self.assertIn(('sale_return', str(returned.get_json()['return_id'])), self.late_adjustments(YESTERDAY))
        self.assertNotEqual(self.closing(TODAY)['source_token'], today_token)


if __name__ == '__main__':
    import unittest
    unittest.main()
