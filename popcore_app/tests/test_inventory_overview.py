from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from support import IsolatedApiCase, auth
from inventory_commands import post_inventory


def days_ago(days):
    return (datetime.now(ZoneInfo('America/Toronto')).date() - timedelta(days=days)).isoformat()


class InventoryOverviewTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Garden')").lastrowid
            self.loc = {code: con.execute('SELECT id FROM inventory_locations WHERE store_id=? AND code=?', (self.store_id, code)).fetchone()[0]
                        for code in ('floor', 'upstairs', 'claw', 'display')}
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            self.ids = {}
            for name, form, unit, series in [('Blind', 'random_box', 'box', True), ('Set', 'sealed_set', 'set', True),
                                             ('Red', 'confirmed_design', 'piece', True), ('Squishy', 'ordinary', 'piece', False),
                                             ('Gone', 'ordinary', 'piece', False), ('Old', 'ordinary', 'piece', False)]:
                self.ids[name] = con.execute(
                    '''INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,design_name,identity_status,boxes_per_dan)
                       VALUES (?,?,?,?,?,?,?,'verified',?)''',
                    ('OV-' + name, name, name, self.series_id if series else None, form, unit,
                     name if form == 'confirmed_design' else None, 12 if form == 'random_box' else None)).lastrowid
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (self.ids['Blind'], self.series_id))
            con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,12,1)',
                        (self.ids['Set'], self.ids['Blind']))
            for name, code, quantity in [('Set', 'upstairs', 2), ('Red', 'upstairs', 4), ('Squishy', 'floor', 3), ('Squishy', 'upstairs', 5),
                                         ('Old', 'floor', 7), ('Blind', 'claw', 6)]:
                con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',?,1)", (self.ids[name], self.loc[code], quantity))
            for name, ago, sold in [('Squishy', 1, 3), ('Squishy', 2, 3), ('Gone', 10, 4), ('Old', 40, 9)]:
                con.execute("INSERT INTO daily_sales(product_id,date,store,qty_sold) VALUES (?,?,'DT',?)", (self.ids[name], days_ago(ago), sold))
            con.commit()

    def get(self, query='store_code=DT', role='staff'):
        return self.client.get('/api/inventory/overview?' + query, headers=self.headers(role))

    def test_overview_reads_the_floor_sales_and_changes_without_writing(self):
        with closing(self.connect()) as con:
            post_inventory(con, {'kind': 'consume', 'business_date': days_ago(1), 'reason': '日报', 'source_type': 'daily_report',
                                 'source_id': 'fixture', 'lines': [{'product_id': self.ids['Squishy'], 'quantity': 1, 'unit': 'piece',
                                 'from_location_id': self.loc['floor'], 'from_disposition': 'saleable', 'expected_versions': {'from': 1}}]},
                           actor={'sub': 'auth0|staff', auth.ROLE_CLAIM: 'staff'}, request_key='overview-report')
            con.commit()
        before = self.snapshot(['products', 'inventory_balances', 'inventory_documents', 'daily_sales'])
        response = self.get()
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(before, self.snapshot(before.keys()))
        self.assertTrue(data['live'])
        figures = {figure['key']: figure for figure in data['figures']}
        self.assertEqual([figure['key'] for figure in data['figures']], ['floor', 'back', 'display', 'claw'])
        self.assertEqual((figures['floor']['units'], figures['floor']['by_unit']), (9, {'piece': 9}))
        self.assertEqual((figures['back']['units'], figures['back']['by_unit']), (11, {'set': 2, 'piece': 9}))
        self.assertEqual(figures['claw']['by_unit'], {'box': 6})
        # The box's whole sets upstairs refill its floor; the set and the design are never floor refills.
        self.assertEqual([(row['name'], row['back'], row['back_sets'], row['link']) for row in data['refill']],
                         [('Blind', 0, 2, {'series_id': self.series_id})])
        self.assertEqual(data['refill_count'], 1)
        # Squishy sold 6 over 2 report days: 3 a day against 2 left on the floor.
        low = data['running_low']
        self.assertEqual([(row['name'], row['floor'], row['pace'], row['days_left'], row['back']) for row in low],
                         [('Squishy', 2, 3.0, 0.7, 5)])
        self.assertEqual([(row['name'], row['sold_recent']) for row in data['sold_out']], [('Gone', 4)])
        self.assertEqual([(row['name'], row['sold_week']) for row in data['best_sellers']], [('Squishy', 6)])
        self.assertEqual((data['report_days'], data['last_report']), (2, days_ago(1)))
        self.assertEqual([(row['kind'], row['added'], row['removed'], row['documents']) for row in data['recent_changes']],
                         [('report', 0, 1, 1)])
        self.assertEqual(data['in_stock_products'], 5)
        self.assertEqual(data['not_set_up'], 1)  # the fixture's untyped TEST-1

    def test_a_box_with_loose_boxes_upstairs_or_on_the_floor(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',3,1)", (self.ids['Blind'], self.loc['floor']))
            con.commit()
        self.assertEqual(self.get().get_json()['refill'], [])
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_balances SET quantity=0 WHERE product_id=? AND location_id=?', (self.ids['Blind'], self.loc['floor']))
            con.execute('DELETE FROM inventory_balances WHERE product_id=?', (self.ids['Set'],))
            con.commit()
        self.assertEqual(self.get().get_json()['refill'], [])  # nothing upstairs either: not a refill

    def test_unreviewed_store_is_not_live_and_other_stores_are_refused(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access SELECT 'auth0|staff',id FROM stores WHERE code='MK'")
            con.commit()
        self.assertEqual(self.get('store_code=MK').get_json(), {'live': False})
        self.assertEqual(self.get(role='staff:other').status_code, 403)
        self.assertEqual(self.get('store_code=NOPE').status_code, 400)

    def test_manager_overview_uses_the_same_refill_rule(self):
        from store_insights import _current_inventory
        with closing(self.connect()) as con:
            current = _current_inventory(con, [self.store_id])
        self.assertEqual([row['product_name'] for row in current['items'] if 'replenish' in row['reasons']], ['Blind'])
        self.assertEqual(current['exception_counts']['replenish'], 1)
