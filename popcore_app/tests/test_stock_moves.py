"""移货 Move stock (owner, 2026-11-24: "simple down all of the moving around of stock … a lot of the different entries
does the same thing"): one request for any move, from where, to where, which goods and how many. Whole sets stay whole
in a back room and land anywhere else as their boxes; a move POPCORE is short for still arrives; 撤销 puts it back."""
import io
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_report_stock
from test_report_stock import _opening_cli, db

DAY = '2026-11-24'


class StockMoveTests(test_report_stock.ReportStockTests):
    """The fixture: DT live with 30 Echo boxes and 3 Bear on the floor, 2 Echo sets (12 boxes each) upstairs."""

    def setUp(self):
        super().setUp()
        clock = patch('checkout_access.business_date', return_value=DAY)
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|staff', 'Zimo')")
            # Wen has 门店操作权限 at DT but no 库存查看: 移货 is open to her, stock numbers are not.
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|wen', 'Wen')")
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|wen', ?)", (self.dt,))
            con.commit()
        # Zimo may see DT's stock (库存查看), so her answers carry by how many POPCORE was short.
        self.grant_inventory_view('auth0|staff')

    def at(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.locations[code])).fetchone()
            return row[0] if row else 0

    def move(self, source, target, lines, key, role='staff', expect=201, **extra):
        response = self.client.post('/api/stock-moves', json={'store_id': self.dt, 'from': source, 'to': target, 'lines': lines, **extra},
                                    headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def undo(self, move, key, role='staff', expect=200):
        response = self.client.post(f"/api/stock-moves/{move['id']}/undo", headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def test_the_store_offers_its_own_places_new_goods_and_gone(self):
        places = self.client.get(f'/api/stock-moves/places?store_id={self.dt}', headers=self.headers()).get_json()
        self.assertEqual([place['name'] for place in places['from']], ['楼上', '楼下', 'Display', '娃娃机', '新到货'])
        self.assertEqual([place['name'] for place in places['to']][-1], '不在了')
        self.assertEqual(places['usual'], {'from': 'upstairs', 'to': 'floor'})

    def test_a_restock_brings_a_set_down_as_its_boxes_in_one_step(self):
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-1')
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'floor')), (1, 42))
        self.assertEqual((move['from_name'], move['to_name'], move['by'], move['lines'][0]['text']), ('楼上', '楼下', 'Zimo', 'Echo 1端'))
        # The same tap again is the same move.
        self.assertEqual(self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-1')['id'], move['id'])
        self.assertEqual(self.at(self.box, 'floor'), 42)
        with closing(self.connect()) as con:
            line = con.execute("SELECT kind, summary, detail, actor_name FROM activity_log WHERE endpoint='stock_moves.stock_move_create'").fetchone()
        self.assertEqual(tuple(line), ('stock', '移货 Moved stock · 楼上 → 楼下', 'Echo 1端', 'Zimo'))

    def test_new_goods_arrive_whole_upstairs_and_as_boxes_on_the_floor(self):
        self.move('new', 'upstairs', [{'product_id': self.box, 'sets': 3, 'units': 2}, {'product_id': self.item, 'units': 5}], 'm-1')
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'upstairs'), self.at(self.item, 'upstairs')), (5, 2, 5))
        self.move('new', 'floor', [{'product_id': self.box, 'sets': 1, 'units': 1}], 'm-2')
        self.assertEqual(self.at(self.box, 'floor'), 43)

    def test_boxes_go_to_display_the_claw_and_back_upstairs(self):
        self.move('floor', 'display', [{'product_id': self.box, 'units': 1}], 'm-1')
        self.move('floor', 'claw', [{'product_id': self.box, 'units': 4}], 'm-2')
        self.move('floor', 'upstairs', [{'product_id': self.box, 'units': 5}], 'm-3')
        self.assertEqual((self.at(self.box, 'floor'), self.at(self.box, 'display'), self.at(self.box, 'claw'), self.at(self.box, 'upstairs')),
                         (20, 1, 4, 5))
        # Only boxes are kept on the floor: a set cannot be taken from there.
        self.assertEqual(self.move('floor', 'upstairs', [{'product_id': self.box, 'sets': 1}], 'm-4', expect=409)['code'], 'no_sets_here')

    def test_gone_needs_a_reason_and_takes_only_what_is_there(self):
        self.move('floor', 'claw', [{'product_id': self.box, 'units': 2}], 'm-1')
        self.move('claw', 'gone', [{'product_id': self.box, 'units': 1}], 'm-2', expect=400)
        prize = self.move('claw', 'gone', [{'product_id': self.box, 'units': 1}], 'm-3', reason='claw_prize')
        self.assertEqual((self.at(self.box, 'claw'), prize['reason']), (1, '娃娃机出奖'))
        self.move('floor', 'gone', [{'product_id': self.item, 'units': 1}], 'm-4', reason='other', expect=400)
        broken = self.move('floor', 'gone', [{'product_id': self.item, 'units': 5}], 'm-5', reason='broken')
        self.assertEqual((self.at(self.item, 'floor'), broken['lines'][0]['missing']), (0, 2))

    def test_a_claw_prize_only_leaves_the_claw_machine(self):
        """Owner, 2026-11-26: a claw prize leaves no loss record, so it cannot be how goods leave any other place."""
        for place in ('floor', 'upstairs'):
            refused = self.move(place, 'gone', [{'product_id': self.item if place == 'floor' else self.box, 'units': 1}],
                                f'c-{place}', reason='claw_prize', expect=400)
            self.assertEqual(refused['code'], 'claw_prize_source')
            self.assertIn('娃娃机', refused['error'])
        check = self.client.post('/api/stock-moves/check', json={'store_id': self.dt, 'from': 'floor', 'to': 'gone', 'reason': 'claw_prize',
                                                                  'lines': [{'product_id': self.item, 'units': 1}]}, headers=self.headers())
        self.assertEqual((check.status_code, check.get_json()['code']), (400, 'claw_prize_source'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM stock_moves').fetchone()[0], 0)
        self.assertEqual((self.at(self.item, 'floor'), self.at(self.set, 'upstairs')), (3, 2))

    def test_without_inventory_view_a_move_says_short_but_not_by_how_many(self):
        """移货 stays open to everyone with 门店操作权限 (owner, 2026-11-24); how many POPCORE holds is for 库存查看
        (owner, 2026-11-26), so without it a line is only `short`."""
        body = {'store_id': self.dt, 'from': 'upstairs', 'to': 'floor', 'lines': [{'product_id': self.box, 'sets': 3},
                                                                                 {'product_id': self.item, 'units': 1}]}
        wen = {'Authorization': 'Bearer staff:wen'}
        check = self.client.post('/api/stock-moves/check', json=body, headers=wen)
        self.assertEqual(check.status_code, 200, check.get_json())
        self.assertEqual([(line['short'], line['missing']) for line in check.get_json()['lines']], [(True, None), (True, None)])
        # Asking for far more than there is tells her nothing more.
        many = self.client.post('/api/stock-moves/check', json={**body, 'from': 'floor', 'to': 'display',
                                                                 'lines': [{'product_id': self.box, 'units': 100000}]}, headers=wen)
        self.assertEqual([(line['short'], line['missing']) for line in many.get_json()['lines']], [(True, None)])
        exact = self.client.post('/api/stock-moves/check', json={**body, 'lines': [{'product_id': self.box, 'sets': 2}]}, headers=wen)
        self.assertEqual([(line['short'], line['missing']) for line in exact.get_json()['lines']], [(False, None)])
        # With 库存查看 the same check says by how many.
        seen = self.client.post('/api/stock-moves/check', json=body, headers=self.headers())
        self.assertEqual([(line['short'], line['missing']) for line in seen.get_json()['lines']], [(True, 12), (True, 1)])
        saved = self.client.post('/api/stock-moves', json=body, headers={**wen, 'Idempotency-Key': 'w-1'})
        self.assertEqual(saved.status_code, 201, saved.get_json())
        self.assertEqual([(line['short'], line['missing']) for line in saved.get_json()['lines']], [(True, None), (True, None)])
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'floor'), self.at(self.item, 'floor')), (0, 66, 4))
        with closing(self.connect()) as con:
            detail = con.execute("SELECT detail FROM activity_log WHERE endpoint='stock_moves.stock_move_create'").fetchone()[0]
        self.assertIn('2 项电脑里不够', detail)
        # Her own list hides the numbers too; a manager's shows them.
        mine = self.client.get(f'/api/stock-moves?store_id={self.dt}', headers=wen).get_json()['moves'][0]
        self.assertEqual([(line['short'], line['missing']) for line in mine['lines']], [(True, None), (True, None)])
        managers = self.client.get(f'/api/stock-moves?store_id={self.dt}', headers=self.headers('manager')).get_json()['moves'][0]
        self.assertEqual([(line['short'], line['missing']) for line in managers['lines']], [(True, 12), (True, 1)])

    def test_below_manager_the_list_is_todays_without_other_peoples_gone_moves(self):
        """What went 不在了 is a loss record, kept for managers (owner, 2026-11-26): staff see their own, today."""
        wen = {'Authorization': 'Bearer staff:wen'}
        self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'd-1')
        self.move('floor', 'gone', [{'product_id': self.item, 'units': 1}], 'd-2', reason='lost')
        hers = self.client.post('/api/stock-moves', headers={**wen, 'Idempotency-Key': 'd-3'}, json={
            'store_id': self.dt, 'from': 'floor', 'to': 'gone', 'reason': 'broken', 'lines': [{'product_id': self.item, 'units': 1}]})
        self.assertEqual(hers.status_code, 201, hers.get_json())

        def listed(headers, date=''):
            response = self.client.get(f'/api/stock-moves?store_id={self.dt}{date}', headers=headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            return [(move['to_name'], move['by']) for move in response.get_json()['moves']]
        self.assertEqual(listed(wen), [('不在了', 'Wen'), ('楼下', 'Zimo')])
        self.assertEqual(listed(self.headers()), [('不在了', 'Zimo'), ('楼下', 'Zimo')])
        self.assertEqual(listed(self.headers('manager')), [('不在了', 'Wen'), ('不在了', 'Zimo'), ('楼下', 'Zimo')])
        # Another day is the manager's to open.
        for headers in (wen, self.headers()):
            other = self.client.get(f'/api/stock-moves?store_id={self.dt}&date=2026-11-23', headers=headers)
            self.assertEqual((other.status_code, other.get_json()['code']), (403, 'stock_moves_today_only'))
        self.assertEqual(listed(wen, f'&date={DAY}'), [('不在了', 'Wen'), ('楼下', 'Zimo')])
        self.assertEqual(listed(self.headers('manager'), '&date=2026-11-23'), [])

    def test_gone_is_a_loss_on_the_managers_page_and_broken_goods_wait_on_the_damaged_pile(self):
        """Losses stay where they always were (owner, 2026-11-03: broken goods are kept aside; managers see the
        month's losses), whoever records them and on whichever screen."""
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=9.99 WHERE id=?', (self.item,))
            con.commit()

        def losses():
            response = self.client.get(f'/api/losses?store_code=DT&month={DAY[:7]}', headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()

        def damaged(product):
            with closing(self.connect()) as con:
                row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition='damaged'""", (product, self.locations['floor'])).fetchone()
                return row[0] if row else 0
        broken = self.move('floor', 'gone', [{'product_id': self.item, 'units': 1}], 'm-1', reason='broken')
        self.move('floor', 'gone', [{'product_id': self.item, 'units': 1}], 'm-2', reason='gift', note='会员生日')
        self.move('floor', 'claw', [{'product_id': self.box, 'units': 2}], 'm-3')
        self.move('claw', 'gone', [{'product_id': self.box, 'units': 1}], 'm-4', reason='claw_prize')
        # The broken one left sale and waits on the pile; the gift left stock; the claw prize is not a loss.
        self.assertEqual((self.at(self.item, 'floor'), damaged(self.item)), (1, 1))
        page = losses()
        self.assertEqual([(row['reason'], row['quantity'], row['note'], row['value_cents'], row['actor_name'], row['undone'])
                          for row in page['records']],
                         [('other', 1, '送人 / 赠品 · 会员生日', 999, 'Zimo', False), ('broken', 1, None, 999, 'Zimo', False)])
        self.assertEqual([(row['product_id'], row['quantity']) for row in page['pile']], [(self.item, 1)])
        # It is undone on 移货, where it was recorded: the 损耗 page sends the manager there.
        refused = self.client.post(f"/api/losses/{page['records'][1]['id']}/undo", headers={**self.headers('manager'), 'Idempotency-Key': 'lu-1'})
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'recorded_on_move'))
        self.undo(broken, 'u-1')
        self.assertEqual((self.at(self.item, 'floor'), damaged(self.item)), (2, 0))
        self.assertEqual([row['undone'] for row in losses()['records']], [False, True])

    def test_loose_boxes_taken_from_whole_sets_open_the_sets_they_need(self):
        check = self.client.post('/api/stock-moves/check', json={'store_id': self.dt, 'from': 'upstairs', 'to': 'floor',
                                                                  'lines': [{'product_id': self.box, 'units': 15}]}, headers=self.headers())
        self.assertEqual((check.get_json()['lines'][0]['missing'], check.get_json()['lines'][0]['opened']), (0, 2))
        # 15 boxes from upstairs, where there are 2 whole sets of 12: both open, 15 come down, 9 stay loose upstairs.
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'units': 15}], 'm-1')
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'upstairs'), self.at(self.box, 'floor'), move['lines'][0]['missing']),
                         (0, 9, 45, 0))
        # The next boxes come from the loose ones; nothing else opens.
        self.move('upstairs', 'floor', [{'product_id': self.box, 'units': 4}], 'm-2')
        self.assertEqual((self.at(self.box, 'upstairs'), self.at(self.box, 'floor')), (5, 49))
        # 撤销 closes the sets again only while every box they held is still where the move left it.
        self.assertEqual(self.undo(move, 'u-1', expect=409)['code'], 'stock_move_gone')
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'upstairs'), self.at(self.box, 'floor')), (0, 5, 49))

    def test_a_move_popcore_is_short_for_still_arrives_and_says_so(self):
        check = self.client.post('/api/stock-moves/check', json={'store_id': self.dt, 'from': 'upstairs', 'to': 'floor',
                                                                  'lines': [{'product_id': self.box, 'sets': 3}]}, headers=self.headers())
        self.assertEqual((check.status_code, check.get_json()['lines'][0]['missing'], check.get_json()['lines'][0]['per_set']), (200, 12, 12))
        self.assertEqual(self.at(self.box, 'floor'), 30)
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 3}], 'm-1')
        # Upstairs had 2 sets on record: both come down, and the third set's boxes still land on the floor.
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'floor'), move['lines'][0]['missing']), (0, 66, 12))
        with closing(self.connect()) as con:
            detail = con.execute("SELECT detail FROM activity_log WHERE endpoint='stock_moves.stock_move_create'").fetchone()[0]
        self.assertIn('1 项电脑里不够', detail)

    def test_undo_puts_a_move_back_while_the_goods_are_still_there(self):
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1, 'units': 0}, {'product_id': self.item, 'units': 0, 'sets': 0}],
                         'm-bad', expect=400)
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-1')
        self.assertTrue(move['can_undo'])
        undone = self.undo(move, 'u-1')
        self.assertEqual((self.at(self.set, 'upstairs'), self.at(self.box, 'floor'), bool(undone['undone']), undone['can_undo']), (2, 30, True, False))
        self.assertEqual(self.undo(move, 'u-2', expect=409)['code'], 'stock_move_undone')
        # Once the goods left the place the move put them, it cannot be put back.
        gone = self.move('floor', 'display', [{'product_id': self.item, 'units': 3}], 'm-2')
        self.move('display', 'gone', [{'product_id': self.item, 'units': 3}], 'm-3', reason='lost')
        self.assertEqual(self.undo(gone, 'u-3', expect=409)['code'], 'stock_move_gone')
        self.assertEqual(self.at(self.item, 'floor'), 0)

    def test_only_the_person_that_day_or_a_manager_undoes_and_the_day_lists_every_move(self):
        move = self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-1')
        with patch('checkout_access.business_date', return_value='2026-11-25'):
            self.undo(move, 'u-late', expect=403)
            self.assertEqual(self.client.get(f'/api/stock-moves?store_id={self.dt}', headers=self.headers()).get_json()['moves'], [])
        day = self.client.get(f'/api/stock-moves?store_id={self.dt}', headers=self.headers()).get_json()
        self.assertEqual([(row['id'], row['lines'][0]['text']) for row in day['moves']], [(move['id'], 'Echo 1端')])
        self.assertTrue(self.undo(move, 'u-manager', role='manager')['undone'])

    def test_goods_sent_to_the_other_store_arrive_in_its_back_room_at_once(self):
        """No request list and no second step (owner, 2026-11-24): staff with their own store's access send; the other
        store's back room has the goods as soon as it is saved, whole sets still whole."""
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            there = {row[0]: row[1] for row in con.execute('SELECT code, id FROM inventory_locations WHERE store_id=?', (mk,))}
        # Not offered while MK is not counted in POPCORE.
        places = self.client.get(f'/api/stock-moves/places?store_id={self.dt}', headers=self.headers()).get_json()
        self.assertNotIn('store:MK', [place['code'] for place in places['to']])
        self.move('upstairs', 'store:MK', [{'product_id': self.box, 'sets': 1}], 'm-early', expect=400)
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                           VALUES (?, ?, 'ordinary', 1, 'auth0|staff')""", (there['floor'], self.item))
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|admin', ?)", (mk,))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'MK',
                          *(part for code in there if code != 'floor' for part in ('--empty-location', f'MK-{code}')),
                          '--business-date', test_report_stock.LIVE_DATE, '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-mk.db')])
        places = self.client.get(f'/api/stock-moves/places?store_id={self.dt}', headers=self.headers()).get_json()
        self.assertIn({'code': 'store:MK', 'name': 'MK 店'}, places['to'])
        self.assertNotIn('store:MK', [place['code'] for place in places['from']])
        back = there.get('upstairs') or there['warehouse']

        def at_mk(product):
            with closing(self.connect()) as con:
                row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition='saleable'""", (product, back)).fetchone()
                return row[0] if row else 0
        move = self.move('upstairs', 'store:MK', [{'product_id': self.box, 'sets': 1}], 'm-1')
        self.assertEqual((move['to_name'], self.at(self.set, 'upstairs'), at_mk(self.set), at_mk(self.box)), ('MK 店', 1, 1, 0))
        # Boxes from the floor go too; the sender needs no access to the other store.
        self.move('floor', 'store:MK', [{'product_id': self.box, 'units': 4}], 'm-2')
        self.assertEqual((self.at(self.box, 'floor'), at_mk(self.box)), (26, 4))
        # 撤销 brings it back while it is still in MK's back room.
        self.undo(move, 'u-1')
        self.assertEqual((self.at(self.set, 'upstairs'), at_mk(self.set)), (2, 0))
        # More than POPCORE holds still arrives, as the check says: the rest is added at the sender's place first,
        # so the sender still needs no access at the other store.
        body = {'store_id': self.dt, 'from': 'floor', 'to': 'store:MK', 'lines': [{'product_id': self.box, 'units': 30}]}
        check = self.client.post('/api/stock-moves/check', json=body, headers=self.headers())
        self.assertEqual((check.status_code, check.get_json()['lines'][0]['missing']), (200, 4))
        short = self.move('floor', 'store:MK', [{'product_id': self.box, 'units': 30}], 'm-3')
        self.assertEqual((short['lines'][0]['missing'], self.at(self.box, 'floor'), at_mk(self.box)), (4, 0, 34))
        sets = self.move('upstairs', 'store:MK', [{'product_id': self.box, 'sets': 3}], 'm-4')
        self.assertEqual((sets['lines'][0]['missing'], self.at(self.set, 'upstairs'), at_mk(self.set)), (12, 0, 3))
        with closing(self.connect()) as con:  # every document that touches MK also takes from DT: none is MK's alone
            alone = con.execute("""SELECT COUNT(*) FROM inventory_documents d WHERE d.source_type='stock_move'
                AND NOT EXISTS (SELECT 1 FROM inventory_movements m JOIN inventory_locations l ON l.id=m.location_id
                                WHERE m.document_id=d.id AND l.store_id=?)""", (self.dt,)).fetchone()[0]
        self.assertEqual(alone, 0)
        # 撤销 puts both back as they were: the boxes return and what POPCORE never held comes off again.
        self.undo(sets, 'u-4')
        self.undo(short, 'u-3')
        self.assertEqual((self.at(self.box, 'floor'), at_mk(self.box), self.at(self.set, 'upstairs'), at_mk(self.set)), (26, 4, 2, 0))

    def test_a_pasted_report_does_not_move_again_what_a_move_recorded(self):
        """While any 日报 is still pasted: its stock lines move only what goes beyond the day's 移货 moves."""
        import report_stock
        with closing(self.connect()) as con:
            con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('app_lines_from', '2026-10-01')")
            con.commit()
        self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-1')
        self.move('floor', 'display', [{'product_id': self.box, 'units': 2}], 'm-2')
        self.move('new', 'floor', [{'product_id': self.item, 'units': 2}], 'm-3')
        self.undo(self.move('new', 'floor', [{'product_id': self.item, 'units': 9}], 'm-4'), 'u-4')

        def plan(*items):
            with closing(self.connect()) as con:
                return {(line['section'], line['kind']): line['wanted'] for line in report_stock.plan_report_lines(
                    con, self.dt, list(enumerate(items, 1)), DAY)}
        planned = plan(
            # 出库入店 12*2 after one set came down by 移货: only the second set moves.
            {'section': 'refill', 'product_id': self.box, 'box_size': 12, 'num_boxes': 2, 'loose_qty': 0, 'qty': 24},
            # 入店 3 after 新到货 of 2 (the undone 9 does not count): 1 more arrives.
            {'section': 'stock_in', 'product_id': self.item, 'box_size': 1, 'num_boxes': 3, 'loose_qty': 0, 'qty': 3},
            # The two boxes put on display are not put there again.
            {'section': 'break_display', 'product_id': self.box, 'qty': 2})
        self.assertEqual(sorted(planned.values()), [1, 1], planned)

    def test_a_product_not_counted_or_not_reviewed_is_not_moved(self):
        with closing(self.connect()) as con:
            bag = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status,stock_counted)
                VALUES ('AC0019','Bag','购物袋','ordinary','piece','verified',0)""").lastrowid
            raw = con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,identity_status) VALUES ('X1','Raw','没核对','unverified')").lastrowid
            con.commit()
        self.assertEqual(self.move('new', 'floor', [{'product_id': bag, 'units': 1}], 'm-1', expect=409)['code'], 'stock_not_counted')
        self.assertEqual(self.move('new', 'floor', [{'product_id': raw, 'units': 1}], 'm-2', expect=409)['code'], 'identity_unverified')
        self.assertEqual(self.move('floor', 'floor', [{'product_id': self.box, 'units': 1}], 'm-3', expect=400)['code'], 'invalid_input')

    def test_new_goods_give_a_blind_box_its_first_whole_sets(self):
        with closing(self.connect()) as con:
            fresh = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status)
                VALUES ('NEW1','Nova','Nova','random_box','box','verified')""").lastrowid
            con.commit()
        self.assertEqual(self.move('new', 'upstairs', [{'product_id': fresh, 'sets': 2}], 'm-1', expect=409)['code'], 'no_whole_set')
        move = self.move('new', 'upstairs', [{'product_id': fresh, 'sets': 2, 'set_size': 8}], 'm-2')
        with closing(self.connect()) as con:
            whole = con.execute("SELECT id FROM products WHERE sku='NEW1-SET'").fetchone()[0]
        self.assertEqual((self.at(whole, 'upstairs'), move['lines'][0]['per_set']), (2, 8))
        self.move('upstairs', 'floor', [{'product_id': fresh, 'sets': 1}], 'm-3')
        self.assertEqual((self.at(whole, 'upstairs'), self.at(fresh, 'floor')), (1, 8))


for _name in [name for name in vars(test_report_stock.ReportStockTests) if name.startswith('test_')]:
    setattr(StockMoveTests, _name, None)
