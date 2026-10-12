#!/bin/sh
# Bootstrap only Python; gateway/setup.py detects and installs runtime packages.
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
case " ${*} " in
  *' --help '*|*' -h '*) exec python3 "$here/gateway/setup.py" "$@" ;;
esac
if [ "$(uname -s)" != Linux ] || [ ! -d /run/systemd/system ]; then
  echo 'error: requires Linux with systemd (Debian/Ubuntu/Raspberry Pi OS, Fedora, Arch or openSUSE)' >&2
  exit 1
fi
if [ "$(id -u)" != 0 ]; then
  echo 'error: run sudo sh setup.sh on the intended gateway' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  case " ${*} " in
    *' --dry-run '*) echo 'error: dry-run needs Python 3; no packages were installed' >&2; exit 1 ;;
  esac
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3
  elif command -v pacman >/dev/null 2>&1; then
    pacman -S --noconfirm --needed python
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install python3
  else
    echo 'error: no supported package manager; Python 3.9+ is required' >&2
    exit 1
  fi
fi
exec python3 "$here/gateway/setup.py" "$@"
