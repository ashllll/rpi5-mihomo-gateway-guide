#!/bin/sh
#
# Install the published gateway automation layer.
#
# The layer is the executable half of this guide: systemd units for the Mihomo
# and MosDNS stack plus the health-check, subscription-update and adaptive
# policy scripts they run.  Deployment data (node labels, subscription links,
# secrets, addresses) is never part of this repository; it stays in
# /etc/mihomo-automation.env, /etc/mihomo/secrets and
# /etc/mihomo/adaptive/capabilities.yaml.
#
# Usage:
#   sudo automation/install.sh [--dry-run] [--enable-timers]
#
#   --dry-run        print every action without touching the filesystem
#   --enable-timers  also enable the eight automation timers via systemctl
#
# GATEWAY_ROOT prefixes every destination path and exists so the installer can
# be exercised in a sandbox (`GATEWAY_ROOT=/tmp/root automation/install.sh`);
# leave it unset on the gateway.  A prefixed install only writes files: systemd
# is never managed unless GATEWAY_ROOT is exactly "/", which keeps the sandbox
# run unprivileged and independent of the host init system.

set -eu

GATEWAY_ROOT=${GATEWAY_ROOT:-/}
dry_run=false
enable_timers=false

usage() {
  cat <<'EOF'
usage: install.sh [--dry-run] [--enable-timers]

  --dry-run        print planned actions without changing the filesystem
  --enable-timers  enable the automation timers with systemctl
EOF
}

for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    --enable-timers) enable_timers=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$GATEWAY_ROOT" = "/" ] && [ "$(id -u)" != 0 ]; then
  echo "error: run as root on the gateway, or set GATEWAY_ROOT to stage elsewhere" >&2
  exit 1
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

say() {
  if [ "$dry_run" = true ]; then
    echo "[dry-run] $*"
  else
    echo "==> $*"
  fi
}

# install_file <source> <absolute destination> <mode>
install_file() {
  source=$1
  target=$2
  mode=$3
  destination="${GATEWAY_ROOT%/}$target"
  say "install -m $mode $(basename "$source") -> $target"
  if [ "$dry_run" = true ]; then
    return 0
  fi
  mkdir -p -- "$(dirname -- "$destination")"
  install -m "$mode" -- "$source" "$destination"
}

# install_default <source> <absolute destination> <mode>
# Deployment data: keep whatever the operator already edited.
install_default() {
  source=$1
  target=$2
  mode=$3
  destination="${GATEWAY_ROOT%/}$target"
  if [ -e "$destination" ]; then
    say "keep existing $target"
    return 0
  fi
  install_file "$source" "$target" "$mode"
}

if [ "$dry_run" = false ]; then
  for directory in \
    /etc/mihomo/secrets \
    /etc/mihomo/adaptive \
    /var/lib/mihomo-adaptive \
    /var/lib/mihomo-extra-subscriptions \
    /var/lib/mihomo-subscription-backups; do
    install -d -m 0700 "${GATEWAY_ROOT%/}$directory"
  done
fi

for script in "$here"/sbin/*; do
  install_file "$script" "/usr/local/sbin/$(basename "$script")" 0755
done

find "$here/systemd" -type f -print | while IFS= read -r unit; do
  relative=${unit#"$here"/systemd/}
  install_file "$unit" "/etc/systemd/system/$relative" 0644
done

for baseline in "$here"/etc/dnsmasq-upstreams.*; do
  install_default "$baseline" "/etc/mihomo/$(basename "$baseline")" 0644
done
install_default "$here/etc/mosdns/config.yaml" /etc/mosdns/config.yaml 0644
for rule in "$here"/etc/mosdns/rules/*; do
  install_default "$rule" "/etc/mosdns/rules/$(basename "$rule")" 0644
done

install_default "$here/policy/capabilities.yaml" /etc/mihomo/adaptive/capabilities.yaml 0644

install_default "$here/env/automation.env.example" /etc/mihomo-automation.env.example 0600
install_default "$here/env/reconcile-targets.conf.example" /etc/mihomo/reconcile-targets.conf.example 0644

# dnsmasq reads /etc/dnsmasq-upstreams.conf, which mihomo-dns-mode rewrites on
# every switch.  Seed it from the fail-safe direct baseline so a first boot has
# working DNS even before the proxy stack starts.
if [ ! -e "${GATEWAY_ROOT%/}/etc/dnsmasq-upstreams.conf" ]; then
  say "seed /etc/dnsmasq-upstreams.conf from the direct baseline"
  if [ "$dry_run" = false ]; then
    mkdir -p -- "${GATEWAY_ROOT%/}/etc"
    install -m 0644 -- "$here/etc/dnsmasq-upstreams.direct" \
      "${GATEWAY_ROOT%/}/etc/dnsmasq-upstreams.conf"
  fi
fi

if [ "$dry_run" = false ] && [ "$GATEWAY_ROOT" = "/" ] && [ -d /run/systemd/system ]; then
  systemctl daemon-reload
  if [ "$enable_timers" = true ]; then
    for timer in \
      mihomo-healthcheck.timer \
      mihomo-adaptive-score.timer \
      mihomo-adaptive-apply.timer \
      mihomo-subscription-update.timer \
      mihomo-rules-update.timer \
      mosdns-healthcheck.timer \
      mosdns-rules-update.timer \
      mosdns-binary-update.timer; do
      systemctl enable --now "$timer"
    done
  fi
fi

cat <<EOF

Installed the automation layer from $here.

Still required before the gateway is fully functional:
  1. Write /etc/mihomo-automation.env (mode 0600) from
     /etc/mihomo-automation.env.example and set this deployment's LAN address,
     ports and interfaces.  Every script reads it.
  2. Generate the private runtime files (nftables, dnsmasq, sysctl, mihomo
     config and provider stubs) with tools/render.py and site.local.json.
  3. Store the subscription link in /etc/mihomo/secrets/subscription.url and the
     dashboard token in /etc/mihomo/secrets/dashboard.secret (both 0600).
  4. Fill the curated-pool node labels in the overlay section of
     /etc/mihomo/adaptive/capabilities.yaml, and write
     /etc/mihomo/reconcile-targets.conf for mihomo-reconcile-policy.
  5. Review automation/optional/ separately; it is not installed automatically.
EOF

if [ "$enable_timers" = false ]; then
  echo "  6. Enable the automation timers with: sudo automation/install.sh --enable-timers"
fi
