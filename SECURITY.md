# Security and privacy

This repository intentionally excludes operational secrets.

The current publication contains fictional, configurable LAN examples and a
render-only tool. Generated candidates contain a fresh controller secret and
must remain private. The tool never installs files or changes a running gateway.
The production adaptive executor and private subscription merger are not included.

The LAN-trusting firewall is intentional for this local deployment. It permits
Mihomo's dynamically allocated redirect listener and local integrations; it is
not an isolation boundary between untrusted LAN clients. SSH password login is
a deployment choice; the renderer does not alter sshd. Upstream SSH is limited
to the configured private management subnet, without a single-client allowlist.

Before publishing, run tools/validate_public.py and scan both the working tree
and Git history with Gitleaks using redacted output. The custom validator is an
additional guard, not proof that arbitrary archives or binaries are secret-free.

Do not commit:

- proxy subscription URLs or tokens
- proxy node credentials
- Wi-Fi or dashboard passwords
- private keys or certificates
- full `/etc/mihomo` archives
- unredacted router backups

Private IPv4 examples in the documentation describe network structure and are
not authentication credentials.

If you find an accidentally published credential, do not open a public issue
containing the value. Revoke the credential first, then contact the repository
owner through a private GitHub channel.
