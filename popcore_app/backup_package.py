"""Small SQLite-plus-referenced-attachments backup/restore helper."""
from __future__ import annotations
import hashlib,json,shutil,sqlite3
from contextlib import closing
from pathlib import Path

KINDS={
 'product':('hidden_images','filename'),
 'payment':('payment_evidence','object_id'),
 'condition':('condition_evidence','object_id'),
}
def _references(con,kind,table,column):
    query=f'SELECT {column} FROM {table}'
    if kind=='payment' and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkout_evidence'").fetchone():
        query+=' UNION SELECT object_id FROM checkout_evidence'
    return con.execute(query)

def _safe(root,value):
    relative=Path(value)
    if relative.is_absolute() or '..' in relative.parts: raise ValueError('unsafe attachment path')
    path=(Path(root)/relative).resolve()
    if Path(root).resolve() not in path.parents: raise ValueError('unsafe attachment path')
    return path,relative
def _hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()
def create_package(db_path,roots,output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    snapshot=output/'database.db'
    with closing(sqlite3.connect(db_path)) as source,closing(sqlite3.connect(snapshot)) as target:source.backup(target)
    manifest={'version':1,'database':'database.db','attachments':[]}
    with closing(sqlite3.connect(snapshot)) as con:
        for kind,(table,column) in KINDS.items():
            for (value,) in _references(con,kind,table,column):
                source,relative=_safe(roots[kind],value)
                if not source.is_file():raise FileNotFoundError(f'missing referenced {kind} attachment')
                destination=output/'attachments'/kind/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                before=(source.stat().st_size,_hash(source));shutil.copyfile(source,destination);after=(source.stat().st_size,_hash(source))
                if before!=after or (destination.stat().st_size,_hash(destination))!=before:raise OSError('attachment changed during backup')
                manifest['attachments'].append({'kind':kind,'path':relative.as_posix(),'size':before[0],'sha256':before[1]})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest
def restore_package(package,target):
    package=Path(package).resolve();target=Path(target).resolve()
    if target.exists():raise FileExistsError('restore target already exists')
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version')!=1 or manifest.get('database')!='database.db':raise ValueError('invalid backup manifest')
    items=manifest.get('attachments',[])
    if not isinstance(items,list):raise ValueError('invalid backup manifest')
    manifest_refs=[]
    for item in items:
        if item.get('kind') not in KINDS:raise ValueError('invalid backup manifest')
        source,relative=_safe(package/'attachments'/item['kind'],item['path'])
        if not source.is_file() or source.stat().st_size!=item['size'] or _hash(source)!=item['sha256']:raise ValueError('attachment manifest mismatch')
        manifest_refs.append((item['kind'],relative.as_posix()))
    with closing(sqlite3.connect(package/'database.db')) as con:
        required_refs=[]
        for kind,(table,column) in KINDS.items():
            required_refs.extend((kind,_safe(package/'attachments'/kind,value)[1].as_posix()) for (value,) in _references(con,kind,table,column))
    if len(set(manifest_refs))!=len(manifest_refs) or set(manifest_refs)!=set(required_refs):
        raise ValueError('attachment manifest does not match database references')
    target.mkdir(parents=True)
    shutil.copyfile(package/'database.db',target/'database.db')
    for item in items:
        source,relative=_safe(package/'attachments'/item['kind'],item['path'])
        destination=target/'attachments'/item['kind']/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    with closing(sqlite3.connect(target/'database.db')) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('restored database integrity failed')
        if con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('restored database foreign keys failed')
    return manifest
