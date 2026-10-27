"""GET /api/health: an unauthenticated check for deploy scripts and uptime monitors.

200 {"ok": true, "release": "<commit>"} after one cheap read of the database, and
503 {"ok": false, "release": "<commit>"} when the database cannot be read. Nothing
else is exposed: no paths, no error text, no counts.
"""
import os
import re
import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, jsonify

import db

bp = Blueprint('health', __name__)

_COMMIT = re.compile(r'[0-9a-f]{40}')


def git_commit(start):
    """The commit checked out at or above `start`, read from .git without running git.

    Reading the files works for any service user, including a git worktree, a
    detached HEAD and packed refs. Returns None when it cannot tell."""
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        dot_git = folder / '.git'
        if dot_git.exists():
            break
    else:
        return None
    try:
        if dot_git.is_file():  # a linked worktree: "gitdir: <path>"
            pointer = dot_git.read_text(encoding='utf-8').strip()
            if not pointer.startswith('gitdir:'):
                return None
            git_dir = (folder / pointer[len('gitdir:'):].strip()).resolve()
        else:
            git_dir = dot_git
        head = (git_dir / 'HEAD').read_text(encoding='utf-8').strip()
        if _COMMIT.fullmatch(head):
            return head
        if not head.startswith('ref: '):
            return None
        ref = head[len('ref: '):].strip()
        common_dir = git_dir
        if (git_dir / 'commondir').is_file():
            common_dir = (git_dir / (git_dir / 'commondir').read_text(encoding='utf-8').strip()).resolve()
        for base in (git_dir, common_dir):
            if (base / ref).is_file():
                value = (base / ref).read_text(encoding='utf-8').strip()
                return value if _COMMIT.fullmatch(value) else None
        packed = common_dir / 'packed-refs'
        if packed.is_file():
            for line in packed.read_text(encoding='utf-8').splitlines():
                value, _, name = line.partition(' ')
                if name == ref:
                    return value if _COMMIT.fullmatch(value) else None
    except (OSError, UnicodeDecodeError):
        return None
    return None


def detect_release():
    """The commit this process started from, else APP_RELEASE, else None."""
    return git_commit(db.BASE_DIR) or os.environ.get('APP_RELEASE', '').strip() or None


# Read once at startup, so a deploy can tell that the new code is the one answering.
RELEASE = detect_release()


def _database_readable():
    path = Path(db.DB_PATH)
    if not path.is_file():  # never let a health check create an empty database
        return False
    try:
        # A plain connection that only reads: some SQLite builds refuse mode=ro on a WAL
        # database whose -shm file is absent, which happens whenever the app is idle.
        con = sqlite3.connect(str(path), timeout=2)
        try:
            con.execute('SELECT COUNT(*) FROM _migrations').fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        current_app.logger.warning('Health check could not read the database', exc_info=True)
        return False
    return True


@bp.route('/api/health', methods=['GET'])
def health():
    ok = _database_readable()
    response = jsonify({'ok': ok, 'release': RELEASE})
    response.status_code = 200 if ok else 503
    response.headers['Cache-Control'] = 'no-store'
    return response
