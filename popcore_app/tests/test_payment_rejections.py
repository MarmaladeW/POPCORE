"""A payment a manager rejected (an e-transfer that never arrived) was not received (owner, 2026-10-27).

It stays on the sale for history but counts in no total: the sale shows the money as still owed, closing's
tender totals leave it out and its payment_mismatch exception catches the shortfall, and the Tenders
report shows it in its own Rejected column."""
from test_sales_refunds_and_returns import TODAY, StoreDayCase


class RejectedPaymentTests(StoreDayCase):
    def test_a_rejected_payment_is_shown_but_not_counted_as_received(self):
        sale_id, payment_id = self.paid_sale('R-1', TODAY, 10000, 'e_transfer')
        card_sale, _ = self.paid_sale('K-1', TODAY, 2500, 'card')
        rejected = self.review(payment_id, sale_id, 'reject', 'E-transfer never arrived')
        self.assertEqual(rejected.status_code, 200, rejected.get_json())
        sale = self.sale(sale_id)
        self.assertEqual(sale['payments'][0]['state'], 'rejected')  # kept for history
        self.assertEqual((sale['payment_total_cents'], sale['payment_difference_cents'], sale['rejected_cents']),
                         (0, -10000, 10000))
        closing = self.closing(TODAY)
        self.assertEqual((closing['tender_totals_cents']['e_transfer'], closing['tender_totals_cents']['card']),
                         (0, 2500))
        self.assertIn(f'payment_mismatch:{sale_id}', closing['review_exceptions'])
        self.assertNotIn(f'payment_unverified:{payment_id}', closing['review_exceptions'])  # it was reviewed
        self.assertNotIn(f'payment_mismatch:{card_sale}', closing['review_exceptions'])
        row = self.tenders(TODAY)['e_transfer']
        self.assertEqual((row['recorded_cents'], row['verified_cents'], row['rejected_cents'], row['pending_count'],
                          row['unknown_count']), (0, 0, 10000, 0, 0))
        self.assertEqual((self.tenders(TODAY)['card']['recorded_cents'], self.tenders(TODAY)['card']['rejected_cents']),
                         (2500, 0))

    def test_a_rejected_cash_payment_does_not_hold_the_drawer_or_the_day(self):
        sale_id, payment_id = self.paid_sale('R-2', TODAY, 2000, 'cash')
        self.assertEqual(self.review(payment_id, sale_id, 'reject', 'Counterfeit note').status_code, 200)
        closing = self.closing(TODAY)
        self.assertNotIn(f'cash_payment_unresolved:{payment_id}', closing['hard_blockers'])
        self.assertEqual((closing['cash']['verified_cash_receipts_cents'], closing['tender_totals_cents']['cash']), (0, 0))
        self.assertIn(f'payment_mismatch:{sale_id}', closing['review_exceptions'])


if __name__ == '__main__':
    import unittest
    unittest.main()
