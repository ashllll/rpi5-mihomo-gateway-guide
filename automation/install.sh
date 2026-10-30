#!/bin/sh
#
# Install the published gateway automation layer.
#
# The layer mirrors one gateway that is actually running: the scripts, units,
# dnsmasq scopes and MosDNS config are the files from that box with site data
# stripped out.  Twelve units are enabled - the state of the recorded gateway -
# and everything else is installed but left off, so the tree and the runbook
# describe a single system instead of two alternatives.
#
# Deployment data (node labels, subscription links, secrets, addresses) is never
# part of this repository; it stays in /etc/mihomo-automation.env,
# /etc/mihomo/secrets and /etc/mihomo/adaptive/capabilities.yaml.
#
# Usage:
#   sudo automation/install.sh [--dry-run]
#
#   --dry-run     print every action without touching the filesystem
#
# GATEWAY_ROOT prefixes every destination path and exists so the installer can
# be exercised in a sandbox (`GATEWAY_ROOT=/tmp/root automation/install.sh`);
# leave it unset on the gateway.  A prefixed install only writes files: systemd
# is never managed unless GATEWAY_ROOT is exactly "/", which keeps the sandbox
# run unprivileged and independent of the host init system.

set -eu

GATEWAY_ROOT=${GATEWAY_ROOT:-/}
root=${GATEWAY_ROOT%/}
dry_run=false

usage() {
  cat <<'EOF'
usage: install.sh [--dry-run]

  --dry-run  print planned actions without changing the filesystem
EOF
}

for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$GATEWAY_ROOT" = "/" ] && [ "$(id -u)" != 0 ]; then
  echo "error: run as root on the gateway, or set GATEWAY_ROOT to stage elsewhere" >&2
  exit 1
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$dry_run" = false ] && [ "${MIHOMO_INSTALL_LOCK_HELD:-false}" != true ]; then
  exec python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --lock-run sh "$0" "$@"
fi

# Recover an interrupted file transaction before checking the old baseline.
if [ "$dry_run" = true ]; then
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --dry-run
else
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --prepare
fi

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
  destination="$root$target"
  say "install -m $mode $(basename "$source") -> $target"
  if [ "$dry_run" = true ]; then
    return 0
  fi
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" \
    --install-file "$source" "$target" "$mode"
}

# install_default <source> <absolute destination> <mode>
# Deployment data: keep whatever the operator already edited.
install_default() {
  source=$1
  target=$2
  mode=$3
  destination="$root$target"
  if [ -e "$destination" ]; then
    say "keep existing $target"
    return 0
  fi
  if [ "$dry_run" = true ]; then
    install_file "$source" "$target" "$mode"
  else
    say "install default $(basename "$source") -> $target if absent"
    python3 "$here/check-release.py" --root "$GATEWAY_ROOT" \
      --default --install-file "$source" "$target" "$mode"
  fi
}

transaction_active=false
on_exit() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$transaction_active" = true ]; then
    if recovery_result=$(python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --rollback 2>&1); then
      if [ "$recovery_result" = 'no pending file transaction' ]; then
        echo 'install failed; no pending file transaction; no file rollback was needed' >&2
      else
        echo 'install failed; previous file state restored' >&2
      fi
    else
      echo 'error: install failed and automatic recovery could not finish; pending recovery data retained' >&2
      echo "$recovery_result" >&2
    fi
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$dry_run" = false ]; then
  transaction_active=true
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --begin
fi

if [ "$dry_run" = false ]; then
  for directory in \
    /etc/mihomo/secrets \
    /etc/mihomo/adaptive \
    /etc/mihomo/providers \
    /var/lib/mihomo-measurements \
    /var/lib/mihomo-extra-subscriptions \
    /var/lib/mihomo-subscription-backups; do
    python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --ensure-dir "$directory" 0700
  done
fi

for script in "$here"/sbin/*; do
  install_file "$script" "/usr/local/sbin/$(basename "$script")" 0755
done

find "$here/systemd" -type f -print | while IFS= read -r unit; do
  relative=${unit#"$here"/systemd/}
  install_file "$unit" "/etc/systemd/system/$relative" 0644
done

for setting in "$here"/etc/systemd/journald.conf.d/*.conf; do
  install_file "$setting" "/etc/systemd/journald.conf.d/$(basename "$setting")" 0644
done

# NetworkManager moves the management Wi-Fi source route.  0700 is what the
# dispatcher API requires: root-owned and executable, no group/other write.
if [ -d "$root/etc/NetworkManager" ]; then
  install_file "$here/etc/NetworkManager/dispatcher.d/90-wifi-management-routing" \
    /etc/NetworkManager/dispatcher.d/90-wifi-management-routing 0700
else
  say "skip etc/NetworkManager/dispatcher.d (NetworkManager is not installed)"
fi

# dnsmasq keeps two independent scopes.  resolver.conf only serves loopback for
# the gateway's own tools (the old downstream LAN and the Pi's AP were retired);
# the pilot scope lives on its own config path so the two cannot read each
# other's file.
for baseline in "$here"/etc/dnsmasq-upstreams.*; do
  install_default "$baseline" "/etc/mihomo/$(basename "$baseline")" 0644
done
install_default "$here/etc/dnsmasq/resolver.conf" /etc/dnsmasq.d/resolver.conf 0644
install_default "$here/etc/dnsmasq/vlan-pilot.conf" /etc/dnsmasq-vlan-pilot.conf 0644

install_default "$here/etc/mosdns/config.yaml" /etc/mosdns/config.yaml 0644
for rule in "$here"/etc/mosdns/rules/*; do
  install_default "$rule" "/etc/mosdns/rules/$(basename "$rule")" 0644
done

# Front-door firewall plus its two includes.  nftables.service loads
# /etc/nftables.conf; the includes are part of the same ruleset, so a host that
# already has local edits keeps them (install_default).
if [ "$dry_run" = false ]; then
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --ensure-dir /etc/nftables.d 0755
fi
install_default "$here/etc/nftables.conf" /etc/nftables.conf 0644
for include in "$here"/etc/nftables.d/*; do
  install_default "$include" "/etc/nftables.d/$(basename "$include")" 0600
done

install_default "$here/policy/capabilities.yaml" /etc/mihomo/adaptive/capabilities.yaml 0644
install_default "$here/policy/route-defaults.yaml" /etc/mihomo/adaptive/route-defaults.yaml 0644
install_default "$here/etc/mihomo/config.local.yaml" /etc/mihomo/config.local.yaml 0644

install_default "$here/env/automation.env.example" /etc/mihomo-automation.env.example 0600
install_default "$here/env/mihomo-alert.env.example" /etc/mihomo-alert.env.example 0600
install_default "$here/env/health-probes.conf.example" /etc/mihomo/health-probes.conf.example 0644
install_default "$here/env/reconcile-targets.conf.example" /etc/mihomo/reconcile-targets.conf.example 0644

# dnsmasq reads /etc/dnsmasq-upstreams.conf, which mihomo-dns-mode rewrites on
# every switch.  Seed it only when none exists: the shipped file is the resolver
# the recorded gateway serves (mosdns on :5335), and dnsmasq-safe-start puts the
# direct baseline back at every boot before the proxy stack comes up.
if [ ! -e "$root/etc/dnsmasq-upstreams.conf" ]; then
  say "seed /etc/dnsmasq-upstreams.conf from etc/dnsmasq/upstreams.conf"
  if [ "$dry_run" = false ]; then
    python3 "$here/check-release.py" --root "$GATEWAY_ROOT" \
        --default --install-file "$here/etc/dnsmasq/upstreams.conf" /etc/dnsmasq-upstreams.conf 0644
  fi
fi

if [ "$dry_run" = false ]; then
  python3 "$here/check-release.py" --root "$GATEWAY_ROOT" --record
  transaction_active=false
fi

if [ "$dry_run" = false ] && [ "$GATEWAY_ROOT" = "/" ] && [ -d /run/systemd/system ]; then
  service_action() {
    if ! "$@"; then
      echo 'error: automation files are committed, but systemd configuration is incomplete' >&2
      exit 1
    fi
  }
  service_action systemctl daemon-reload
  # The twelve units the recorded gateway runs.  Enabling a timer does not start
  # it, so a fresh host needs one explicit start after the private env files exist.
  for unit in \
      mihomo.service \
      mihomo-measurement-budget.service \
      mihomo-healthcheck.timer \
      mihomo-watch.timer \
      mihomo-dnsmasq-guard.timer \
      mosdns.service \
      mosdns-healthcheck.timer \
      mosdns-rules-update.timer \
      mosdns-binary-update.timer \
      dnsmasq-vlan-pilot.service \
      dnsmasq.service \
      homeassistant-access-guard.service; do
    service_action systemctl enable "$unit"
  done
  # These four ship for completeness but stay off: on the recorded gateway the
  # rule, subscription and quality timers were disabled.  Disabling them is what
  # makes an upgrade from the previous release idempotent.
  for retired in mihomo-rules-update.timer mihomo-subscription-update.timer mihomo-download-quality.timer mihomo-social-quality.timer; do
    load_state=$(systemctl show --property=LoadState --value "$retired") || {
      echo 'error: automation files are committed, but systemd configuration is incomplete' >&2
      exit 1
    }
    if [ "$load_state" != not-found ]; then
      service_action systemctl disable "$retired"
    fi
  done
fi

cat <<EOF

Installed the automation layer from $here.

Still required before the gateway is fully functional:
  1. Copy the two env examples to their private paths and fill in the real
     addresses, ports and interface names (the shipped values are placeholders):
       install -m 0600 $here/env/automation.env.example /etc/mihomo-automation.env
       install -m 0600 $here/env/mihomo-alert.env.example /etc/mihomo-alert.env
  2. Store the subscription link in /etc/mihomo/secrets/subscription.url and the
     dashboard token in /etc/mihomo/secrets/dashboard.secret (both 0600).
  3. Fill the curated-pool node labels in the overlay section of
     /etc/mihomo/adaptive/capabilities.yaml, and write
     /etc/mihomo/reconcile-targets.conf for mihomo-reconcile-policy.
  4. resolver.conf serves loopback only; set its host-record addresses to the
     gateway's real management address if the site differs from the placeholder.
  5. The NetworkManager dispatcher in etc/NetworkManager/dispatcher.d/ is
     installed only where NetworkManager exists; it takes MANAGEMENT_INTERFACE
     and MIHOMO_MANAGEMENT_CONNECTION_UUID from /etc/mihomo-automation.env.

Start the enabled units with: sudo systemctl start mihomo.service mosdns.service
dnsmasq-vlan-pilot.service mihomo-measurement-budget.service
EOF
