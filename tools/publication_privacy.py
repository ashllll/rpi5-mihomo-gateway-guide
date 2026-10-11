"""Shared publication guards; callers retain their original scanning scopes."""
import re

PUBLIC_PATTERNS = {
        "private user path": r"/(?:Users|home)/[A-Za-z0-9._-]+/",
        "subscription endpoint": r"https?://[^\s\"'<>]*(?:glados-config\.com|bwbwbw\.cc|infraapi\.cloud)[^\s\"'<>]*",
        "UUID/node identity": r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b",
        "MAC identity": r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b",
        "public IPv6 identity": r"\b(?:2[0-9a-f]{3}:)(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}\b",
        # Escaped on purpose: the guard must not ship the separator it bans.
        "subscription node label": r"\u4e28",
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
    "assets/network-topology.svg", "web/index.html",
)
# These historical archive guards also catch endpoints without a URL scheme.
ARCHIVE_PATTERNS = {
    "private user path": PUBLIC_PATTERNS["private user path"],
    "GLaDOS subscription token": r"glados-config\.com/.{12,}",
    "InfraAPI subscription token": r"infraapi\.cloud/link/.+",
    "BWB subscription token": r"bwbwbw\.cc/(?:subscribe/|[0-9]+/).+",
}


def matching_labels(content, patterns, flags=re.IGNORECASE):
    """Return rule names only; never expose matched deployment data."""
    return [label for label, pattern in patterns.items() if re.search(pattern, content, flags)]
