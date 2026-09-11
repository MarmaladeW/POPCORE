"""Create one complete POPCORE backup package from configured runtime paths."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'popcore_app'))
from backup_package import create_package
p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--product-dir',required=True);p.add_argument('--payment-dir',required=True);p.add_argument('--condition-dir',required=True);p.add_argument('--output',required=True)
a=p.parse_args();create_package(a.database,{'product':a.product_dir,'payment':a.payment_dir,'condition':a.condition_dir},a.output);print(a.output)
