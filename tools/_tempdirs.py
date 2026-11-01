"""Choose a writable temporary root for the checks.

Checks must not assume ``/tmp`` exists or is writable: restricted sandboxes,
some containers, and CI images differ.  The order is:

1. ``AUDIT_TMP_ROOT`` when the caller set one (the audit shim uses it);
2. the platform temp directory (``TMPDIR`` on POSIX);
3. a short relative ``.preflight-tmp`` directory when the platform temp path
   is too long for an ``AF_UNIX`` socket (macOS limits sun_path to 104 bytes
   and long workspace paths overflow it).

The fallback lives in the repository and is removed by the checks' context
managers; ``.preflight-tmp/`` is ignored by git.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Leave room for a prefix, a random suffix and a short socket name.
_MAX_TEMP_ROOT = 72


def temp_root() -> str:
    """Return an existing directory suitable for TemporaryDirectory(dir=...)."""
    explicit = os.environ.get("AUDIT_TMP_ROOT")
    if explicit:
        Path(explicit).mkdir(parents=True, exist_ok=True)
        return explicit
    system = tempfile.gettempdir()
    if len(system) <= _MAX_TEMP_ROOT:
        return system
    fallback = Path(".preflight-tmp")
    fallback.mkdir(exist_ok=True)
    return str(fallback)
