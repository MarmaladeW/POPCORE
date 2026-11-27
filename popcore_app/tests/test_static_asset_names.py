"""Every file the committed build asks for is there under exactly that name.

The release build is made on a Mac, whose disk ignores letter case. When a rebuild changes only the case of a file name
(same contents, same hash), git there keeps the old name, and the Linux server answers 404 for the new one: the page
shows its error screen (popcore-work#138: webOrders-*.css tracked as WebOrders-*.css)."""
import os
import re
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'static'
ASSET_REF = re.compile(r'assets/[A-Za-z0-9_.-]+\.(?:js|css|woff2?|ttf|png|svg|jpe?g|webp|gif|ico)\b')


def tracked_names():
    """Names git tracks under static/, exactly as spelled in the index; None outside a git checkout."""
    try:
        out = subprocess.run(['git', 'ls-files', '-z', '--', '.'], cwd=STATIC, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {name.decode() for name in out.split(b'\0') if name}


@unittest.skipUnless((STATIC / 'index.html').exists(), 'no committed build')
class StaticAssetNameTests(unittest.TestCase):
    def test_every_referenced_asset_exists_under_its_exact_name(self):
        on_disk = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob('*') if p.is_file()}
        refs = set()
        for name in on_disk:
            if name.endswith(('.html', '.js', '.css')):
                refs.update(ASSET_REF.findall((STATIC / name).read_text(encoding='utf-8', errors='replace')))
        self.assertTrue(refs)

        tracked = tracked_names()
        problems = []
        for ref in sorted(refs):
            if ref not in on_disk:
                problems.append(f'{ref}: no file with exactly this name')
            elif tracked is not None and ref not in tracked:
                # A Mac checkout finds the file whatever its case; git may still hold it under another spelling.
                twins = sorted(t for t in tracked if t.lower() == ref.lower())
                if twins:
                    problems.append(f'{ref}: git tracks it as {twins[0]}')
        self.assertEqual(problems, [], 'the server is case-sensitive; rename the tracked file with git mv')


if __name__ == '__main__':
    unittest.main()
