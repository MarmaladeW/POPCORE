"""Build 6 complete-backup rehearsal. Creates and restores synthetic data only."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,sqlite3,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];APP=ROOT/'popcore_app';sys.path.insert(0,str(APP))
os.environ.setdefault('AUTH0_DOMAIN','recovery.invalid');os.environ.setdefault('DISABLE_SCHEDULER','1')
import db
from backup_package import create_package,restore_package

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
 output=(ROOT/args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output).resolve()
 local=(ROOT/'.local').resolve()
 if local not in output.parents:raise SystemExit('output must be a new directory under repository .local')
 if output.exists():raise SystemExit(f'refusing existing output: {output}')
 work=output.parent/(output.name+'-work');work.mkdir(parents=True,exist_ok=False)
 database=work/'source.db';old=db.DB_PATH;db.DB_PATH=str(database)
 try:db.migrate_db()
 finally:db.DB_PATH=old
 roots={kind:work/'source-attachments'/kind for kind in ('product','payment','condition')}
 for root in roots.values():root.mkdir(parents=True)
 with sqlite3.connect(database) as con:
  store=con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
  product=con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,product_type,boxes_per_dan) VALUES ('REC-1','Recovery','Recovery','ordinary',1)").lastrowid
  con.execute("INSERT INTO hidden_images(product_id,image_type,filename) VALUES (?, 'general','1/product.bin')",(product,))
  sale=con.execute("INSERT INTO sale_documents(store_id,business_date,status,entry_mode,created_by) VALUES (?,'2026-09-09','posted','already_paid','fixture')",(store,)).lastrowid
  con.execute("INSERT INTO sale_lines(sale_id,line_no,product_id,raw_product_text,quantity) VALUES (?,1,?,NULL,1)",(sale,product))
  payment=con.execute("INSERT INTO sale_payments(sale_id,tender,amount_cents,recorded_by) VALUES (?,'card',1000,'fixture')",(sale,)).lastrowid
  con.execute("INSERT INTO payment_evidence(payment_id,object_id,mime_type,byte_size,uploader_sub) VALUES (?,'payment.bin','image/png',7,'fixture')",(payment,))
  case=con.execute("INSERT INTO condition_cases(store_id,sale_id,sale_line_no,observed_condition,created_by) VALUES (?,?,1,'sealed','fixture')",(store,sale)).lastrowid
  con.execute("INSERT INTO condition_evidence(case_id,object_id,mime_type,byte_size,content_hash,uploaded_by) VALUES (?,'condition.bin','image/png',9,?,'fixture')",(case,'0'*64))
  closing=con.execute("INSERT INTO closing_sessions(store_id,business_date,status,created_by) VALUES (?,'2026-09-09','closed','fixture')",(store,)).lastrowid
  con.execute("INSERT INTO closing_snapshots(closing_session_id,source_token,snapshot_json,reviewed_by) VALUES (?,'token','{\"store_id\":1}','fixture')",(closing,))
  con.execute('CREATE TABLE recovery_probe(id INTEGER PRIMARY KEY,value TEXT NOT NULL)');con.commit()
 files={'product':('1/product.bin',b'product'),'payment':('payment.bin',b'payment'),'condition':('condition.bin',b'condition')}
 for kind,(name,content) in files.items():path=roots[kind]/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
 stop=threading.Event();started=threading.Event()
 def writer():
  with sqlite3.connect(database,timeout=5) as con:
   value=0
   while not stop.is_set():value+=1;con.execute('INSERT INTO recovery_probe(value) VALUES (?)',(str(value),));con.commit();started.set();time.sleep(.002)
 thread=threading.Thread(target=writer);thread.start();assert started.wait(5)
 try:manifest=create_package(database,roots,output)
 finally:stop.set();thread.join()
 restored=output.parent/(output.name+'-restored');restore_package(output,restored)
 with sqlite3.connect(restored/'database.db') as con:
  facts={'integrity':con.execute('PRAGMA integrity_check').fetchone()[0],
   'foreign_key_errors':len(con.execute('PRAGMA foreign_key_check').fetchall()),
   'sale_id':con.execute('SELECT id FROM sale_documents').fetchone()[0],
   'closing_snapshots':con.execute('SELECT COUNT(*) FROM closing_snapshots').fetchone()[0],
   'probe_rows':con.execute('SELECT COUNT(*) FROM recovery_probe').fetchone()[0]}
 assert facts['integrity']=='ok' and facts['foreign_key_errors']==0 and facts['sale_id']==sale and facts['closing_snapshots']==1 and facts['probe_rows']>=1
 corrupt=output.parent/(output.name+'-corrupt');shutil.copytree(output,corrupt);target=next((corrupt/'attachments').rglob('*.bin'));target.write_bytes(b'changed')
 try:restore_package(corrupt,output.parent/(output.name+'-corrupt-restore'));raise AssertionError('corrupt attachment accepted')
 except ValueError:pass
 missing=output.parent/(output.name+'-missing');shutil.copytree(output,missing);next((missing/'attachments').rglob('*.bin')).unlink()
 try:restore_package(missing,output.parent/(output.name+'-missing-restore'));raise AssertionError('missing attachment accepted')
 except ValueError:pass
 result={'passed':True,'facts':facts,'attachment_count':len(manifest['attachments']),'package':str(output)}
 (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':main()
