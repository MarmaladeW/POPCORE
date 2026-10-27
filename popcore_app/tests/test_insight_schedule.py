from contextlib import closing
from datetime import datetime,timedelta,timezone
import sqlite3,threading,time
from unittest.mock import patch
from support import IsolatedApiCase
from blueprints import insights as insight_routes
import insights

class InsightScheduleTests(IsolatedApiCase):
 def setUp(self):
  super().setUp();self.old=insights.DB_PATH;insights.DB_PATH=self.app.config.get('DB_PATH',None) or __import__('db').DB_PATH
  with closing(self.connect()) as con:con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('insight_generate_time','02:00')");con.commit()
 def tearDown(self):insights.DB_PATH=self.old;super().tearDown()
 def test_toronto_business_date_boundaries(self):
  self.assertEqual(insights.insight_business_date(datetime(2026,9,9,2,tzinfo=timezone.utc)),'2026-09-08')
  self.assertEqual(insights.insight_business_date(datetime(2026,3,8,6,30,tzinfo=timezone.utc)),'2026-03-08')
  self.assertEqual(insights.insight_business_date(datetime(2026,11,1,5,30,tzinfo=timezone.utc)),'2026-11-01')
 def test_missed_time_runs_once_and_restart_is_safe(self):
  now=datetime(2026,9,9,15,tzinfo=timezone.utc)
  self.assertGreaterEqual(insights.run_due_daily_insights(now),0);self.assertEqual(insights.run_due_daily_insights(now),0)
  with closing(self.connect()) as con:self.assertEqual(con.execute("SELECT status FROM insight_runs WHERE business_date='2026-09-09'").fetchone()[0],'succeeded')
 def test_failed_run_retries(self):
  now=datetime(2026,9,9,15,tzinfo=timezone.utc)
  with patch.object(insights,'compute_daily_insights',side_effect=RuntimeError('fixture')):self.assertRaises(RuntimeError,insights.run_due_daily_insights,now)
  with closing(self.connect()) as con:self.assertEqual(tuple(con.execute("SELECT status,error FROM insight_runs WHERE business_date='2026-09-09'").fetchone()),('failed','fixture'))
  self.assertGreaterEqual(insights.run_due_daily_insights(now),0)
  with closing(self.connect()) as con:self.assertEqual(con.execute("SELECT status FROM insight_runs WHERE business_date='2026-09-09'").fetchone()[0],'succeeded')
 def test_two_workers_claim_one_run(self):
  now=datetime(2026,9,9,15,tzinfo=timezone.utc);calls=[];lock=threading.Lock()
  def generate(con,business_date):
   with lock:calls.append(business_date)
   time.sleep(.05);return []
  errors=[]
  def worker():
   try:insights.run_due_daily_insights(now)
   except Exception as exc:errors.append(exc)
  with patch.object(insights,'compute_daily_insights',side_effect=generate):
   threads=[threading.Thread(target=worker) for _ in range(2)]
   for thread in threads:thread.start()
   for thread in threads:thread.join()
  self.assertEqual(errors,[]);self.assertEqual(calls,['2026-09-09'])


class InsightWriteLockTests(IsolatedApiCase):
 """Generation claims in one short transaction, computes from a read snapshot and writes in
 a second short transaction, so staff writes never wait for the checks."""
 NOW=datetime(2026,9,9,15,tzinfo=timezone.utc)

 def setUp(self):
  super().setUp();self.old=insights.DB_PATH;insights.DB_PATH=__import__('db').DB_PATH
  self.app.register_blueprint(insight_routes.bp)
  with closing(self.connect()) as con:  # a stale legacy stock record, so every run has an insight to write
   con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('insight_generate_time','02:00')")
   con.execute("UPDATE stock SET last_updated='2020-01-01' WHERE product_id=?",(self.product_id,));con.commit()
 def tearDown(self):insights.DB_PATH=self.old;super().tearDown()

 def run_row(self,job='daily_insights',date='2026-09-09'):
  with closing(self.connect()) as con:
   row=con.execute('SELECT status,started_at FROM insight_runs WHERE job=? AND business_date=?',(job,date)).fetchone()
   return tuple(row) if row else None
 def insight_count(self):
  with closing(self.connect()) as con:return con.execute('SELECT COUNT(*) FROM insights').fetchone()[0]
 def claim(self,job,started):
  with closing(self.connect()) as con:
   con.execute("INSERT INTO insight_runs(job,business_date,status,started_at) VALUES (?,?,'running',?)",(job,'2026-09-09',started.isoformat()));con.commit()

 def test_staff_write_succeeds_while_checks_run(self):
  real=insights.compute_daily_insights;seen=[]
  def compute(con,business_date):
   staff=sqlite3.connect(insights.DB_PATH,timeout=0)  # no waiting: fails at once if the lock is held
   try:staff.execute("UPDATE stock SET notes='counted' WHERE product_id=?",(self.product_id,));staff.commit();seen.append('ok')
   finally:staff.close()
   return real(con,business_date)
  with patch.object(insights,'compute_daily_insights',side_effect=compute):count=insights.run_due_daily_insights(self.NOW)
  self.assertEqual(seen,['ok']);self.assertGreater(count,0);self.assertEqual(self.insight_count(),count)
  self.assertEqual(self.run_row()[0],'succeeded')

 def test_checks_only_read_and_close_their_snapshot(self):
  with closing(insights._get_db()) as con:
   rows=insights.compute_daily_insights(con,'2026-09-09')
   self.assertTrue(rows);self.assertFalse(con.in_transaction);self.assertEqual(con.total_changes,0)

 def test_stale_running_claim_is_retried(self):
  self.claim('daily_insights',self.NOW-timedelta(hours=2))
  self.assertGreater(insights.run_due_daily_insights(self.NOW),0)
  self.assertEqual(self.run_row(),('succeeded',self.NOW.isoformat()))

 def test_fresh_running_claim_is_left_alone(self):
  started=self.NOW-timedelta(minutes=5);self.claim('daily_insights',started)
  self.assertEqual(insights.run_due_daily_insights(self.NOW),0)
  self.assertEqual(self.run_row(),('running',started.isoformat()));self.assertEqual(self.insight_count(),0)

 def test_failed_write_leaves_no_partial_results_and_retries(self):
  def broken(con,rows):
   con.execute("INSERT INTO insights(store,check_type,severity,title,body) VALUES ('DT','X','info','partial','partial')")
   raise RuntimeError('disk full')
  with patch.object(insights,'_store_insights',side_effect=broken):self.assertRaises(RuntimeError,insights.run_due_daily_insights,self.NOW)
  self.assertEqual(self.insight_count(),0);self.assertEqual(self.run_row()[0],'failed')
  self.assertGreater(insights.run_due_daily_insights(self.NOW),0);self.assertEqual(self.run_row()[0],'succeeded')

 def test_run_whose_claim_was_taken_over_writes_nothing(self):
  real=insights.compute_daily_insights;other=(self.NOW+timedelta(minutes=40)).isoformat()
  def compute(con,business_date):
   with closing(self.connect()) as takeover:
    takeover.execute("UPDATE insight_runs SET started_at=? WHERE job='daily_insights'",(other,));takeover.commit()
   return real(con,business_date)
  with patch.object(insights,'compute_daily_insights',side_effect=compute):self.assertEqual(insights.run_due_daily_insights(self.NOW),0)
  self.assertEqual(self.insight_count(),0);self.assertEqual(self.run_row(),('running',other))

 def test_admin_generate_shares_the_claim(self):
  self.claim('daily_insights',datetime.now(timezone.utc)-timedelta(minutes=1))
  busy=self.client.post('/api/insights/generate',headers=self.headers('admin'))
  self.assertEqual(busy.status_code,409);self.assertEqual(self.insight_count(),0)
  self.assertEqual(self.client.post('/api/insights/generate',headers=self.headers('manager')).status_code,403)
  with closing(self.connect()) as con:con.execute("UPDATE insight_runs SET status='succeeded'");con.commit()
  done=self.client.post('/api/insights/generate',headers=self.headers('admin'))
  self.assertEqual(done.status_code,200);self.assertEqual(done.get_json()['generated'],self.insight_count())
  with closing(self.connect()) as con:
   self.assertEqual(con.execute("SELECT status FROM insight_runs WHERE job='manual_insights'").fetchone()[0],'succeeded')
  self.assertEqual(self.client.post('/api/insights/generate',headers=self.headers('admin')).status_code,200)  # again later the same day

 def test_nightly_waits_while_admin_generation_runs(self):
  self.claim('manual_insights',self.NOW-timedelta(minutes=1))
  self.assertEqual(insights.run_due_daily_insights(self.NOW),0);self.assertIsNone(self.run_row())

if __name__=='__main__':
 import unittest;unittest.main()
