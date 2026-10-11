# Security and privacy

This repository intentionally excludes operational secrets.

The current publication contains fictional, configurable LAN examples, a
render-only tool, and the sanitized automation layer that runs on the gateway
(`automation/`). Generated candidates contain a fresh controller secret and
must remain private. The renderer never installs files or changes a running
gateway; the automation installer only writes the script, unit and example
files it owns and keeps deployment data that already exists.

Deployment data is never part of the repository: subscription links, node
labels, node credentials, dashboard tokens and this deployment's real
addresses stay in `/etc/mihomo-automation.env`, `/etc/mihomo/secrets` and
`/etc/mihomo/adaptive/capabilities.yaml`. The published capability manifest
ships with empty curated-pool (`overlay`) lists, and every published script
reads addresses, ports and interfaces from the uncommitted environment file.

The LAN-trusting firewall is intentional for this local deployment. It permits
Mihomo's dynamically allocated redirect listener and local integrations; it is
not an isolation boundary between untrusted LAN clients. SSH password login is
a deployment choice; the renderer does not alter sshd. Upstream SSH is limited
to the configured private management subnet, without a single-client allowlist.

Before publishing, run `tools/validate_public.py`, `tools/check_render.py`,
`tools/check_overlay.py` and `tools/check_automation.py`, and scan both the
working tree and Git history with Gitleaks using redacted output. The custom
validators are an additional guard, not proof that arbitrary archives or
binaries are secret-free.

Do not commit:

- proxy subscription URLs or tokens
- proxy node credentials, node lists or curated-pool node labels
- Wi-Fi or dashboard passwords
- private keys or certificates
- full `/etc/mihomo` archives
- unredacted router backups

Private IPv4 examples in the documentation describe network structure and are
not authentication credentials.

If you find an accidentally published credential, do not open a public issue
containing the value. Revoke the credential first, then contact the repository
owner through a private GitHub channel.
