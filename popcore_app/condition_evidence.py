"""Private storage for normalized condition-case images."""
import os
from pathlib import Path


EVIDENCE_DIR = Path(os.environ.get(
    'POPCORE_CONDITION_EVIDENCE_DIR',
    Path(__file__).resolve().parent / 'uploads' / 'condition_evidence',
))


def publish(prepared):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    final_path = EVIDENCE_DIR / prepared['object_id']
    part_path = final_path.with_suffix(final_path.suffix + '.part')
    with part_path.open('xb') as output:
        output.write(prepared['content'])
    os.replace(part_path, final_path)
    return final_path


def evidence_path(object_id):
    if Path(object_id).name != object_id:
        raise ValueError('Evidence object is invalid')
    return EVIDENCE_DIR / object_id


def remove_unreferenced(path, con):
    if con.execute(
        'SELECT 1 FROM condition_evidence WHERE object_id=?', (path.name,)
    ).fetchone() is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
