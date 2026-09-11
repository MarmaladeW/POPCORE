from contextlib import closing
from datetime import datetime,timezone
import threading,time
from unittest.mock import patch
from support import IsolatedApiCase
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
  with patch.object(insights,'generate_daily_insights',side_effect=RuntimeError('fixture')):self.assertRaises(RuntimeError,insights.run_due_daily_insights,now)
  self.assertGreaterEqual(insights.run_due_daily_insights(now),0)
  with closing(self.connect()) as con:self.assertEqual(con.execute("SELECT status FROM insight_runs WHERE business_date='2026-09-09'").fetchone()[0],'succeeded')
 def test_two_workers_claim_one_run(self):
  now=datetime(2026,9,9,15,tzinfo=timezone.utc);calls=[];lock=threading.Lock()
  def generate(con,business_date):
   with lock:calls.append(business_date)
   time.sleep(.05);return 0
  errors=[]
  def worker():
   try:insights.run_due_daily_insights(now)
   except Exception as exc:errors.append(exc)
  with patch.object(insights,'generate_daily_insights',side_effect=generate):
   threads=[threading.Thread(target=worker) for _ in range(2)]
   for thread in threads:thread.start()
   for thread in threads:thread.join()
  self.assertEqual(errors,[]);self.assertEqual(calls,['2026-09-09'])

if __name__=='__main__':
 import unittest;unittest.main()
