"""Shared publication guards; callers retain their original scanning scopes.

Three content scopes:

* ``PUBLIC_PATTERNS`` -- no legitimate occurrence anywhere in the repository.
* ``DOC_PATTERNS`` -- device- and account-shaped data that executable fixtures
  may legitimately contain (tests need sample URLs, proxy URIs and hostnames)
  but that must never reach the public documentation surface.
* ``ARCHIVE_PATTERNS`` -- hand-picked checks for the documentation archive.

Private IPv4 addresses are not a regex rule: public docs may describe the
deployment network, so an address is accepted only when it falls inside a
network the repository itself documents (``documented_networks``); RFC 5737
test ranges and loopback are exempt.  A private address outside the documented
plan is reported without its value.

Values are never returned to callers: only rule names leave this module.
"""
import base64
import ipaddress
import re

PUBLIC_PATTERNS = {
        "private user path": r"/(?:Users|home)/[A-Za-z0-9._-]+/",
        "subscription endpoint": r"https?://[^\s\"'<>]*(?:glados-config\.com|bwbwbw\.cc|infraapi\.cloud)[^\s\"'<>]*",
        "generic subscription endpoint": r"https?://[^\s\"'<>]*(?:/subscribe\b|/api/v\d+/client/subscribe\b|[?&](?:token|access_token|auth|sub)=[A-Za-z0-9._~+/-]{8,})",
        "UUID/node identity": r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b",
        "MAC identity": r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b",
        "platform token": r"\b(?:xoxb-|sk-)[A-Za-z0-9_-]{16,}\b",
        "public IPv6 identity": r"\b(?:2[0-9a-f]{3}:)(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}\b",
        # Escaped on purpose: the guard must not ship the separator it bans.
        "subscription node label": r"\u4e28",
        "split secret assignment": r"(?m)^[^\n]*(?:token|secret|password|passwd)\s*=\s*$\n[ \t]*[A-Za-z0-9._~+/-]{24,}[ \t]*$",
        "secret assignment": r"(?mi)^[ \t]*(?:password|passwd|secret|api[_-]?key|token)[ \t]*:[ \t]*(?!$|[<${*]|xxx|redacted|change|replace|example|placeholder|\.\.\.)[^\s#\"']{6,}[ \t]*$",
        "environment secret": r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)\s*=\s*['\"]?(?!$|\$|<|xxx|redacted|change|replace|example|placeholder|\.\.\.)[A-Za-z0-9._~+/-]{12,}['\"]?[ \t]*$",
    }

# Public documentation only; executable fixtures are allowed to carry samples.
DOC_PATTERNS = {
        "provider endpoint": r"\b(?:glados-config\.com|bwbwbw\.cc|infraapi\.cloud)/[A-Za-z0-9._~+/-]{8,}",
        "internal hostname": r"\b[a-z0-9][a-z0-9-]*\.(?:local|lan|home\.arpa)\b(?![\w.])",
        "email address": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "provider node label": r"\b(?:STT|ByWave|BWB|InfraAPI|GLaDOS)\s*\|\s*(?=[A-Za-z0-9 ._-]*\d)[A-Za-z0-9][A-Za-z0-9 ._-]{0,24}",
        "device/SSID name": r"(?-i:\bRP5-[A-Z0-9][A-Z0-9-]{1,}\b)",
        "proxy URI": r"\b(?:vmess|vless|ss|ssr|trojan|hysteria2?|tuic)://[A-Za-z0-9+/=_%.-]{8,}",
        "URL with credential": r"https?://[^\s/\"'<>:@]{1,64}:[^\s/\"'<>@]{2,}@",
    }

AUTOMATION_PATTERNS = {
    **PUBLIC_PATTERNS,
    "deployment LAN address": r"\b192\.168\.[0-9]+\.[0-9]+\b",
}
NODE_PATTERNS = {
    "node label separator": PUBLIC_PATTERNS["subscription node label"],
    "provider label with index in code": '"(?:STT|ByWave|BWB|InfraAPI) \\| [^"]*[0-9]"',
}
ARCHIVE_PATHS = (
    "README.md", "SECURITY.md", "docs/deployment-debugging-summary.zh-CN.md",
    "assets/network-topology.svg",
)
# These historical archive guards also catch endpoints without a URL scheme.
ARCHIVE_PATTERNS = {
    "private user path": PUBLIC_PATTERNS["private user path"],
    "GLaDOS subscription token": r"glados-config\.com/.{12,}",
    "InfraAPI subscription token": r"infraapi\.cloud/link/.+",
    "BWB subscription token": r"bwbwbw\.cc/(?:subscribe/|[0-9]+/).+",
}

# RFC 5737 documentation ranges and loopback never describe deployment data.
_EXEMPT_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("127.0.0.0/8"),
)
# Python 3.9 does not mark these as private, but they are not routable
# deployment addresses either.
_PRIVATE_LIKE = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_CIDR_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}(?![\d.])")
_RANGE_RE = re.compile(
    r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})\s*[\u2013\u2014-]\s*((?:\d{1,3}\.){3}\d{1,3})(?![\d.])"
)
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")


def matching_labels(content, patterns, flags=re.IGNORECASE):
    """Return rule names only; never expose matched deployment data."""
    return [label for label, pattern in patterns.items() if re.search(pattern, content, flags)]


def base64_labels(content):
    """Rule names found inside printable base64 payloads (one decode level)."""
    labels = set()
    for match in _BASE64_RE.finditer(content):
        chunk = match.group(0)
        try:
            raw = base64.b64decode(chunk + "=" * (-len(chunk) % 4), validate=False)
        except Exception:
            continue
        if not raw:
            continue
        printable = sum(32 <= byte < 127 or byte in (9, 10, 13) for byte in raw) / len(raw)
        if printable < 0.9:
            continue
        decoded = raw.decode("ascii", "ignore")
        for label in matching_labels(decoded, PUBLIC_PATTERNS):
            labels.add("base64 " + label)
    return sorted(labels)


def documented_networks(texts):
    """Networks the repository itself documents (CIDR literals and ranges)."""
    networks = set()
    for text in texts:
        for match in _CIDR_RE.finditer(text):
            try:
                networks.add(ipaddress.ip_network(match.group(0), strict=False))
            except ValueError:
                continue
        for match in _RANGE_RE.finditer(text):
            try:
                first = ipaddress.ip_address(match.group(1))
                last = ipaddress.ip_address(match.group(2))
            except ValueError:
                continue
            if first <= last:
                networks.update(ipaddress.summarize_address_range(first, last))
    return tuple(
        network
        for network in networks
        if network.prefixlen >= 8
        and (
            network.is_private
            or network.is_loopback
            or any(network.overlaps(private) for private in _PRIVATE_LIKE)
        )
    )


def undocumented_private_addresses(content, allowed):
    """Private IPv4 addresses outside the documented plan (values stay inside)."""
    found = set()
    for match in _IPV4_RE.finditer(content):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if (
            address.is_loopback
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            continue
        if not (address.is_private or any(address in network for network in _PRIVATE_LIKE)):
            continue
        if any(address in network for network in _EXEMPT_NETWORKS):
            continue
        if any(address in network for network in allowed):
            continue
        found.add(str(address))
    return sorted(found)
