"""Staff close the store day (owner, 2026-10-27): "staff can def close, but give them as little information
as possible". A staff member with a shift at the store that day opens the closing, counts the cash and submits
it, whoever took the day's orders; a manager still signs off. The server sends staff the day's totals and the
blockers and exceptions as labels, never another cashier's order, amount or reference."""
import json
from contextlib import closing

from test_sales_refunds_and_returns import TODAY, YESTERDAY, StoreDayCase


class StaffClosingTests(StoreDayCase):
    def order(self, role, reference, quantity=1):
        """A manual checkout order paid by card: (checkout, sale_id)."""
        total = 1000 * quantity
        created = self.send('/api/checkouts', {
            'store_id': self.store_id, 'business_date': TODAY, 'reference': reference,
            'subtotal_cents': total, 'source_tax_cents': 0, 'gross_cents': total, 'reduction_cents': 0,
            'rounding_cents': 0, 'collected_cents': total,
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'quantity': quantity, 'unit_price_cents': 1000}],
        }, role)
        self.assertEqual(created.status_code, 201, created.get_json())
        paid = self.send(f"/api/checkouts/{created.get_json()['id']}/receive", {
            'tender': 'card', 'amount_cents': total, 'plan': 'pay',
            'expected_version': created.get_json()['version']}, role)
        self.assertEqual((paid.status_code, paid.get_json().get('status')), (200, 'completed'), paid.get_json())
        return paid.get_json()['sale_id']

    def test_staff_on_shift_close_a_day_two_cashiers_took_orders(self):
        self.order('staff', 'ORDER-MINE')
        self.order('staff:other', 'ORDER-THEIRS')
        opened = self.open_closing(TODAY)
        self.assertEqual(opened.status_code, 201, opened.get_json())
        session = opened.get_json()
        ready = self.client.patch(f"/api/closing/{session['closing_id']}", json={
            'expected_version': session['version'], 'intake_complete': True},
            headers={**self.headers(), 'Idempotency-Key': 'intake'}).get_json()
        counts = {key: 0 for key in ('10000', '5000', '2000', '1000', '500', '200', '100', '25', '10', '5')}
        counted = self.send(f"/api/closing/{session['closing_id']}/cash-counts", {
            'expected_version': ready['version'], 'source_token': ready['source_token'],
            'opening_coin_cents': 0, 'retained_coin_cents': 0, 'denomination_counts': {**counts, '5000': 13}})
        self.assertEqual(counted.status_code, 200, counted.get_json())
        submitted = self.send(f"/api/closing/{session['closing_id']}/submit", {
            'expected_version': counted.get_json()['version'], 'source_token': counted.get_json()['source_token']})
        self.assertEqual((submitted.status_code, submitted.get_json().get('status')), (200, 'submitted'),
                         submitted.get_json())
        # The manager still signs off: staff cannot, and the manager reviews every exception by name.
        body = {'expected_version': submitted.get_json()['version'], 'source_token': submitted.get_json()['source_token']}
        self.assertEqual(self.send(f"/api/closing/{session['closing_id']}/close",
                                   {**body, 'accepted_exceptions': []}).status_code, 403)
        manager = self.client.get(f"/api/closing/{session['closing_id']}", headers=self.headers('manager')).get_json()
        closed = self.send(f"/api/closing/{session['closing_id']}/close", {**body, 'accepted_exceptions': [
            {'code': code, 'reason': 'Matched the card terminal'} for code in manager['review_exceptions']]}, 'manager')
        self.assertEqual((closed.status_code, closed.get_json().get('status')), (200, 'closed'), closed.get_json())

    def test_staff_see_totals_and_labels_but_not_another_cashiers_order(self):
        mine = self.order('staff', 'ORDER-MINE')
        theirs = self.order('staff:other', 'ORDER-THEIRS', quantity=20)  # too few on hand: waits for allocation
        manager = self.closing(TODAY, 'manager')
        staff = self.closing(TODAY, 'staff')
        self.assertIn(f'pending_allocation:{theirs}', manager['hard_blockers'])
        self.assertEqual(manager['issue_targets'][f'pending_allocation:{theirs}']['href'], f'/sales/documents/{theirs}')
        self.assertIn('unknown_payment_ids', manager)
        # Staff: the same day totals, every blocker and exception as a label, and only their own sale.
        self.assertEqual(staff['tender_totals_cents'], manager['tender_totals_cents'])
        self.assertEqual(staff['tender_totals_cents']['card'], 21000)
        self.assertIn('pending_allocation', staff['hard_blockers'])
        self.assertEqual(staff['review_exceptions'], ['payment_unverified', 'payment_unverified'])
        self.assertEqual([sale['id'] for sale in staff['source_documents']['sales']], [mine])
        for key in ('unknown_payment_ids', 'checkout_funds', 'late_adjustments'):
            self.assertNotIn(key, staff)
        self.assertNotIn('unknown_cash_payment_ids', staff['cash'])
        text = json.dumps(staff)
        self.assertNotIn('ORDER-THEIRS', text)
        self.assertNotIn(f'/sales/documents/{theirs}', text)
        self.assertFalse([code for code in staff['hard_blockers'] + staff['review_exceptions'] if ':' in code
                          and code.split(':')[0] in {'pending_allocation', 'payment_unverified'}])
        # Submitting is refused while it waits, naming the kind of blocker, not the sale.
        refused = self.send(f"/api/closing/{staff['closing_id']}/submit", {
            'expected_version': staff['version'], 'source_token': staff['source_token']})
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'closing_blocked'))
        self.assertIn('pending_allocation', refused.get_json()['error'])
        self.assertNotIn(f'pending_allocation:{theirs}', refused.get_json()['error'])

    def test_staff_need_a_shift_at_the_store_that_day(self):
        with closing(self.connect()) as con:
            con.execute("""DELETE FROM shifts WHERE date=? AND employee_id=
                           (SELECT id FROM employees WHERE auth0_id='auth0|staff')""", (YESTERDAY,))
            con.execute("UPDATE employees SET is_active=0 WHERE auth0_id='auth0|other'")
            con.commit()
        self.assertEqual(self.open_closing(YESTERDAY).status_code, 403)
        self.assertEqual(self.open_closing(YESTERDAY, 'staff:other').status_code, 403)  # no longer employed
        self.assertEqual(self.open_closing(TODAY).status_code, 201)
        self.assertEqual(self.open_closing(YESTERDAY, 'manager').status_code, 201)


if __name__ == '__main__':
    import unittest
    unittest.main()
