from contextlib import closing

from support import IsolatedApiCase

from inventory_opening import reconcile_inventory


class IdentityAfterGoLiveTests(IsolatedApiCase):
    """Verifying a product its live store never counted must not leave untrusted legacy numbers."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            # DT went live; TEST-1 (verified, counted) has its balances, as an opening leaves it.
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            con.execute("""UPDATE products SET stock_form='ordinary', stock_unit='piece',
                           identity_status='verified' WHERE id=?""", (self.product_id,))
            for code, quantity in (('floor', 2), ('upstairs', 10)):
                con.execute("""INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version)
                               VALUES (?,(SELECT id FROM inventory_locations WHERE store_id=? AND code=?),'saleable',?,1)""",
                            (self.product_id, self.store_id, code, quantity))
            # PLUSH-9 was unverified and uncounted at DT, with legacy numbers at both stores.
            self.plush = con.execute(
                """INSERT INTO products (sku, name_cn_en, jizhanming, product_type, boxes_per_dan)
                   VALUES ('PLUSH-9', 'Plush', 'Plush', '周边', 1)""").lastrowid
            con.execute('INSERT INTO stock (product_id,store_id,upstairs_qty,instore_qty,claw_qty) VALUES (?,?,4,5,1)',
                        (self.plush, self.store_id))
            con.execute('INSERT INTO stock (product_id,store_id,upstairs_qty,instore_qty,claw_qty) VALUES (?,?,0,3,0)',
                        (self.plush, self.mk))
            con.commit()

    def stock(self, store_id):
        with closing(self.connect()) as con:
            return tuple(con.execute('SELECT upstairs_qty,instore_qty,claw_qty FROM stock WHERE product_id=? AND store_id=?',
                                     (self.plush, store_id)).fetchone())

    def test_verifying_after_go_live_clears_untrusted_legacy_stock_at_live_stores_only(self):
        response = self.client.patch(f'/api/products/{self.plush}', headers=self.headers('manager'), json={
            'stock_form': 'ordinary', 'stock_unit': 'piece', 'identity_status': 'verified'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.stock(self.store_id), (0, 0, 0))
        self.assertEqual(self.stock(self.mk), (0, 3, 0))  # MK is not live: its legacy numbers stay
        with closing(self.connect()) as con:
            history = con.execute("""SELECT location, qty, notes FROM stock_transactions
                                     WHERE product_id=? AND txn_type='inventory_opening' ORDER BY id""",
                                  (self.plush,)).fetchall()
            self.assertEqual([(row['location'], row['qty']) for row in history],
                             [('floor', 0), ('upstairs', 0), ('claw', 0)])
            self.assertIn('legacy instore_qty 5 is not trusted', history[0]['notes'])
            # The fixture writes balances directly, so only the stock-row comparison applies here.
            self.assertEqual(reconcile_inventory(con)['compatibility_differences'], [])
            # The counted product keeps its legacy rows and balances untouched.
            self.assertEqual(tuple(con.execute('SELECT upstairs_qty,instore_qty FROM stock WHERE product_id=? AND store_id=?',
                                               (self.product_id, self.store_id)).fetchone()), (10, 2))
