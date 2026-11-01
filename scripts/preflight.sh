#!/bin/sh
# Single local gate. The logic lives in scripts/preflight.py so the hook, the
# developer and CI all execute the same code and the same verification matrix.
set -eu
unset CDPATH
here=$(cd -- "$(dirname -- "$0")" && pwd)
PY=${PREFLIGHT_PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "preflight: MISSING DEPENDENCY: python3 (set PREFLIGHT_PYTHON)" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "preflight: python3 >= 3.9 is required" >&2
  exit 1
fi
if ! "$PY" -c 'import yaml' >/dev/null 2>&1; then
  echo "preflight: MISSING DEPENDENCY: PyYAML (Debian: python3-yaml; pip: pyyaml)" >&2
  exit 1
fi
exec "$PY" -u "$here/preflight.py" "$@"
