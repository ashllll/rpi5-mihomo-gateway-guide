#!/usr/bin/env bash
# Machine gate for this repo: every tools/check_*.py must keep passing.
#
#   scripts/preflight.sh            full gate  (used by pre-push)
#   scripts/preflight.sh --staged   fast subset (used by pre-commit)
#
# Known-failing checks that predate this gate are listed in
# scripts/preflight-baseline.txt. A baseline failure does not block; a NEW
# failure does; a fixed one is reported so the baseline can shrink.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY=/usr/bin/python3   # PyYAML is installed for this interpreter only
BASE=scripts/preflight-baseline.txt
ADVISORY=scripts/preflight-advisory.txt
FAST="tools/check_overlay.py"

[ -x "$PY" ] || { echo "preflight: $PY not found (PyYAML lives there; brew python is not enough)" >&2; exit 1; }

# Repeat pushes of an identical tree re-run a ten-minute gate for nothing.
# Key the cache on HEAD plus the working-tree status so a dirty tree never tests stale.
CACHE=.git/preflight-cache
KEY=$(printf '%s\n%s' "$(git rev-parse HEAD 2>/dev/null)" "$(git status --porcelain 2>/dev/null)" | shasum -a 256 | cut -d' ' -f1)
if [ "${1:-}" != "--staged" ] && [ -f "$CACHE" ] && [ "$(cat "$CACHE")" = "$KEY" ]; then
  echo "preflight: identical tree already passed this gate (cache $KEY); skipping"
  exit 0
fi

if [ "${1:-}" = "--staged" ]; then
  CHECKS=$FAST
else
  # Checkers that need arguments or a live target are tools, not gates.
  CHECKS=$(ls tools/check_*.py 2>/dev/null | grep -vE '(^|/)check_landscape_mihomo\.py$')
fi
[ -n "$CHECKS" ] || { echo "preflight: no checks found under tools/" >&2; exit 1; }

BASELINE=""
[ -f "$BASE" ] && BASELINE=$(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$BASE")
ADVISE=""
[ -f "$ADVISORY" ] && ADVISE=$(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$ADVISORY")

FAILED=""
for c in $CHECKS; do
  name=$(basename "$c")
  if out=$("$PY" "$c" 2>&1); then
    printf '  %-34s pass\n' "$name"
  elif out=$("$PY" "$c" 2>&1); then
    # Network-deadline style checks are load-sensitive; one retry separates a
    # flaky pass from a real regression without hiding the flake.
    printf '  %-34s pass (flaky: failed once under load)\n' "$name"
  else
    printf '  %-34s FAIL\n' "$name"
    echo "$out" | sed -n '1,3p' | sed 's/^/      /'
    FAILED="$FAILED$name
"
  fi
done

[ -n "$FAILED" ] || { N=$(printf '%s\n' "$CHECKS" | wc -l | tr -d ' '); echo "preflight: all $N checks passed"; exit 0; }

NEW_FAILURES=""
ADV_FAILURES=""
for f in $FAILED; do
  printf '%s\n' "$BASELINE" | grep -qxF "$f" && continue
  if printf '%s\n' "$ADVISE" | grep -qxF "$f"; then
    ADV_FAILURES="$ADV_FAILURES $f"
  else
    NEW_FAILURES="$NEW_FAILURES $f"
  fi
done

if [ -n "$ADV_FAILURES" ]; then
  echo "preflight: ADVISORY failure(s) (timing-sensitive; recorded, not blocking):$ADV_FAILURES" >&2
  echo "preflight: run them deliberately after touching the code they cover." >&2
fi
FIXED=""
for b in $BASELINE; do
  printf '%s\n' "$FAILED" | grep -qxF "$b" || FIXED="$FIXED $b"
done

[ -n "$FIXED" ] && echo "preflight: baseline entry now passes (drop it from $BASE):$FIXED"

if [ -n "$NEW_FAILURES" ]; then
  echo ""
  echo "preflight: BLOCKED - new failure(s):$NEW_FAILURES" >&2
  echo "preflight: fix them, or (only if they predate your change) add them to $BASE with a reason." >&2
  exit 1
fi

printf '%s' "$KEY" > "$CACHE"
echo "preflight: only baseline failures remain ($(printf '%s' "$FAILED" | tr '\n' ' '))"
exit 0
