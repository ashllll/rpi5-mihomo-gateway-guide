#!/usr/bin/env python3
"""Run the full-tree publication scan inside the preflight gate.

`tools/validate_public.py` is the scanner that reads every tracked file (and,
since F-02, the history scope). `scripts/preflight.sh` only auto-discovers
`tools/check_*.py`, so without this wrapper a real identifier could reach a
commit on a machine that never ran the GitHub workflow. This wrapper is the
local gate entry; `validate_public.py` stays the single scanner and stays the
entry used by `.github/workflows/validate.yml`.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / 'tools' / 'validate_public.py'


def main():
    if not SCANNER.is_file():
        print('check_publication_tree: missing {}'.format(SCANNER), file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, str(SCANNER)] + sys.argv[1:])


if __name__ == '__main__':
    sys.exit(main())
