"""Create a review-only allowlisted Build 6 candidate; never deploys it."""
from __future__ import annotations
import argparse,hashlib,json,platform,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=['README.md','AGENTS.md','requirements-dev.txt','requirements-linux.constraints.txt','requirements-windows.constraints.txt','setup_production.sh',
 'popcore_app/requirements.txt','popcore_app/.env.example','popcore_app/backup.sh','popcore_app/nginx.conf','popcore_app/popcore.service','popcore_app/gunicorn.conf.py','popcore_app/logrotate.conf',
 'scripts/create_backup_package.py','scripts/check_release_recovery.py','scripts/rehearse_inventory_migration.py',
 'docs/operational-reports.md','docs/build6-workflow-checklist.md','docs/build6-interface-validation.md','docs/build6-pilot-candidate.md','docs/writer-inventory.md','docs/release-and-recovery.md','docs/production-readiness.md','docs/inventory-core.md','docs/goods-handling.md','docs/sales-and-closing.md','docs/trades-and-today.md']
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(*args):return subprocess.run(['git',*args],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip()
def evidence(path):
 data=json.loads(path.read_text(encoding='utf-8'))
 return {'path':path.relative_to(ROOT).as_posix(),'result':{key:data[key] for key in ('passed','facts','attachment_count','change') if key in data}}
def main():
 p=argparse.ArgumentParser();p.add_argument('--frontend-build',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 output=(ROOT/a.output).resolve() if not Path(a.output).is_absolute() else Path(a.output).resolve();local=(ROOT/'.local').resolve()
 if local not in output.parents or output.exists():raise SystemExit('output must be a new directory under repository .local')
 build=(ROOT/a.frontend_build).resolve() if not Path(a.frontend_build).is_absolute() else Path(a.frontend_build).resolve()
 if not (build/'index.html').is_file():raise SystemExit('frontend build is incomplete')
 output.mkdir(parents=True);selected=[ROOT/item for item in FILES]
 selected += sorted((ROOT/'popcore_app').glob('*.py'))+sorted((ROOT/'popcore_app'/'blueprints').glob('*.py'))
 for source in selected:
  if not source.is_file():raise SystemExit(f'missing allowlisted file: {source.relative_to(ROOT)}')
  destination=output/source.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
 static=output/'popcore_app'/'static';shutil.copytree(build,static)
 forbidden=[];private_key=re.compile(
  rb'-----BEGIN (?:(?:RSA|EC|OPENSSH) )?PRIVATE KEY-----\s+'
  rb'(?:[A-Za-z0-9+/=]{20,}\s+){2,}'
  rb'-----END (?:(?:RSA|EC|OPENSSH) )?PRIVATE KEY-----'
 )
 for path in output.rglob('*'):
  if not path.is_file():continue
  rel=path.relative_to(output).as_posix().lower()
  if Path(rel).suffix in {'.db','.sqlite','.log','.key','.pem'} or '/.env' in '/'+rel and not rel.endswith('.env.example'):forbidden.append(rel)
  if private_key.search(path.read_bytes()):forbidden.append(rel)
 if forbidden:raise SystemExit(f'candidate contains forbidden files: {sorted(set(forbidden))}')
 html=(static/'index.html').read_text(encoding='utf-8')
 for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',html):
  if not (static/asset.lstrip('/')).is_file():raise SystemExit(f'missing built asset: {asset}')
 integrations=sorted((ROOT/'.local'/'build6'/'integration').glob('*/result.json'),key=lambda path:path.stat().st_mtime)
 recoveries=sorted((ROOT/'.local'/'build6').glob('recovery*/result.json'),key=lambda path:path.stat().st_mtime)
 performance=ROOT/'.local'/'build6'/'performance-measurement.json'
 if not integrations or not recoveries or not performance.is_file():raise SystemExit('required Build 6 evidence is missing')
 files=[{'path':path.relative_to(output).as_posix(),'size':path.stat().st_size,'sha256':sha(path)} for path in sorted(output.rglob('*')) if path.is_file()]
 manifest={'repository':git('remote','get-url','origin'),'branch':git('branch','--show-current'),'baseline_head':git('rev-parse','HEAD'),'working_tree':git('status','--short'),
  'python':platform.python_version(),'node':subprocess.run(['node','--version'],text=True,capture_output=True,check=True).stdout.strip(),
  'build_commands':['python -m unittest discover -s popcore_app/tests -v','npm test','npm run build -- --outDir ../../.local/frontend-build-build6','python popcore_app/tests/browser/check_release_pilot.py','python scripts/check_release_recovery.py --output .local/build6/recovery-new'],
  'evidence':[evidence(performance),evidence(integrations[-1]),evidence(recoveries[-1])],
  'live_gates':['approved store access and physical openings','opening and retained-coin approval policy','trade proof and condition policy','real scanner and camera','host ownership and off-host backup credentials/retention','real Auth0 callbacks/origins and CSP','prior working release and backup location'],
  'files':files}
 (output/'candidate-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');print(output)
if __name__=='__main__':main()
