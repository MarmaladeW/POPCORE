"""Create one complete POPCORE backup package from configured runtime paths."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'popcore_app'))
from backup_package import create_package
p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--product-dir',required=True);p.add_argument('--payment-dir',required=True);p.add_argument('--condition-dir',required=True);p.add_argument('--output',required=True)
# 可疑人员 Watch list photos sit beside payment evidence unless POPCORE_WATCH_LIST_DIR moved them (watch_list.py).
p.add_argument('--watch-list-dir')
a=p.parse_args();watch=a.watch_list_dir or str(Path(a.payment_dir).parent/'watch_list')
create_package(a.database,{'product':a.product_dir,'payment':a.payment_dir,'condition':a.condition_dir,'watch':watch},a.output);print(a.output)
