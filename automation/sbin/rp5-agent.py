#!/usr/bin/env python3
"""rp5-agent - read-only metric aggregator for the T-Display-S3 monitor.

One GET returns everything the display needs:

    GET /api/v1/status   full snapshot (JSON)
    GET /health          liveness only

Sources (all read-only, none of them writes or spawns a shell):
  * node_exporter on 127.0.0.1:9100  -> proves the monitoring agent is alive,
                                          plus CPU / memory / temperature / disk
  * /proc/net/dev                      -> gateway interface rates
  * mihomo external controller         -> proxy mode, active node, delay,
                                          live connections, traffic
  * /run/mihomo-healthcheck/status      -> passive gateway evidence (TUN,
                                          nftables hooks, routing, verdict)
  * quality-budget.json                 -> per-account credits left this hour

Design notes:
  * A background sampler refreshes once a second, so the very first request
    already carries CPU share and rates instead of zero baselines.
  * Never returns 5xx. Each family carries its own ok/age_s so the display can
    tell "agent is up but node_exporter died" apart from "agent is down".
  * The mihomo secret is read from /etc/mihomo/config.yaml and used in the
    Authorization header only. It is never logged and never appears in JSON.
  * /proxies is large (~all nodes), so it is refreshed every META_EVERY seconds
    while /connections runs each tick.
"""

import hmac
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2"

# The controller and node-exporter endpoints are local by construction.  A
# stale default must never send a dashboard secret to another network: see the
# loopback assertion below and F-12 in the remediation ledger.
NODE_EXPORTER = os.environ.get("RP5_NODE_EXPORTER", "http://127.0.0.1:9100/metrics")
MIHOMO_API = os.environ.get("RP5_MIHOMO_API", "")
MIHOMO_CONFIG = os.environ.get("RP5_MIHOMO_CONFIG", "/etc/mihomo/config.yaml")
LEASES = os.environ.get("RP5_LEASES", "/var/lib/misc/dnsmasq.leases")
LISTEN_HOST = os.environ.get("RP5_AGENT_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("RP5_AGENT_PORT", "8799"))
STATUS_TOKEN_FILE = os.environ.get("RP5_STATUS_TOKEN_FILE",
                                  "/etc/mihomo/secrets/rp5-agent.token")

DELAY_PROBE_URL = "https://www.gstatic.com/generate_204"
DELAY_PROBE_TIMEOUT = 3.0
DELAY_CACHE_S = 30.0
CACHE_S = 1.0
META_EVERY = 5.0
SCRAPE_TIMEOUT = 1.5
IFACES = ("wan0", "lan0", "proxy20")
# JSON contract uses short prefixes so the firmware parser stays tiny.
GW_KEYS = (("wan0", "wan"), ("lan0", "lan"), ("proxy20", "px"))
# Gateway evidence produced elsewhere on this host: the passive health probe
# and the measurement broker's rolling ledger. Both are read-only reads.
HEALTH_STATUS = os.environ.get("RP5_HEALTH_STATUS", "/run/mihomo-healthcheck/status")
BUDGET_LEDGER = os.environ.get("RP5_BUDGET_LEDGER",
                               "/var/lib/mihomo-measurements/quality-budget.json")
TUN_IFACE = os.environ.get("RP5_TUN_IFACE", "Mihomo")
START = time.monotonic()

_lock = threading.Lock()
_cache = {"at": -1e9, "body": None}
_secret = None
_prev = {}          # name -> (monotonic, value), for rate deltas
_last_good = {}     # family -> {"at": epoch, "data": snapshot}
_meta = {"at": -1e9, "proxies": {}, "mode": ""}


# ---------------------------------------------------------------- startup ---

def _loopback_url(name, value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "::1"):
        raise SystemExit(
            "rp5-agent: %s must be an explicit loopback URL (127.0.0.1 or ::1); got %r. "
            "Set it in the systemd unit; there is no network default." % (name, value)
        )
    return value


MIHOMO_API = _loopback_url("RP5_MIHOMO_API", MIHOMO_API)
NODE_EXPORTER = _loopback_url("RP5_NODE_EXPORTER", NODE_EXPORTER)


def _load_status_token():
    try:
        token = open(STATUS_TOKEN_FILE, "r", encoding="utf-8").read().strip()
    except OSError as error:
        raise SystemExit(
            "rp5-agent: status token file is missing or unreadable (%s): %s. "
            "Create it with: install -m 0600 /dev/null %s && "
            "python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > %s"
            % (STATUS_TOKEN_FILE, error, STATUS_TOKEN_FILE, STATUS_TOKEN_FILE)
        )
    if len(token) < 16:
        raise SystemExit("rp5-agent: status token in %s is too short (need >=16 chars)" % STATUS_TOKEN_FILE)
    return token


STATUS_TOKEN = _load_status_token()


# ---------------------------------------------------------------- helpers ---

def _delta(name, value):
    """Rate (units/second) since the previous sample of `name`, or 0."""
    t = time.monotonic()
    prev = _prev.get(name)
    _prev[name] = (t, value)
    if prev is None:
        return 0
    dt, pv = t - prev[0], prev[1]
    if dt <= 0 or value < pv:
        return 0
    return int((value - pv) / dt)


LABELS = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _metric(body, name, **labels):
    """Sum every `name{labels...}` sample that matches all given labels."""
    total = 0.0
    found = False
    for line in body.splitlines():
        if not line.startswith(name):
            continue
        pos = len(name)
        if pos < len(line) and line[pos] not in "{ ":
            continue
        rest = line[pos:]
        got = {}
        if rest.startswith("{"):
            end = rest.find("}")
            if end < 0:
                continue
            for key, val in LABELS.findall(rest[1:end]):
                got[key] = val
            rest = rest[end + 1:]
        if any(got.get(k) != v for k, v in labels.items()):
            continue
        try:
            value = float(rest.strip().split()[0])
        except (ValueError, IndexError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        total += value
        found = True
    return total if found else None


def _get(url, headers=None, timeout=SCRAPE_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _read_secret():
    try:
        with open(MIHOMO_CONFIG, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("secret:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _mihomo(path, timeout=SCRAPE_TIMEOUT):
    global _secret
    if _secret is None:
        _secret = _read_secret()
    headers = {"Authorization": "Bearer " + _secret} if _secret else {}
    return json.loads(_get(MIHOMO_API + path, headers, timeout).decode("utf-8"))


# ------------------------------------------------------------------- pi -----

def sample_pi():
    t0 = time.monotonic()
    body = _get(NODE_EXPORTER).decode("utf-8", errors="replace")
    ms = int((time.monotonic() - t0) * 1000)

    # node_cpu_seconds_total{cpu="N",mode="X"} -> per-mode totals over all cores,
    # so the CPU count never has to be hardcoded.
    by_mode = {}
    for line in body.splitlines():
        if not line.startswith("node_cpu_seconds_total{"):
            continue
        end = line.find("}")
        if end < 0:
            continue
        labels = dict(LABELS.findall(line[:end]))
        try:
            value = float(line[end + 1:].strip().split()[0])
        except (ValueError, IndexError):
            continue
        mode = labels.get("mode", "")
        by_mode[mode] = by_mode.get(mode, 0.0) + value
    idle = by_mode.get("idle", 0.0)
    total = sum(by_mode.values())
    cpu_pct = None
    if total > 0:
        d_idle = _delta("cpu_idle", idle)
        d_total = _delta("cpu_total", total)
        if d_total > 0:
            cpu_pct = max(0, min(100, int(round(100 * (1 - d_idle / d_total)))))

    avail = _metric(body, "node_memory_MemAvailable_bytes")
    mem_total = _metric(body, "node_memory_MemTotal_bytes")
    mem_pct = None
    if avail is not None and mem_total:
        mem_pct = max(0, min(100, int(round(100 * (1 - avail / mem_total)))))

    temp = None
    for line in body.splitlines():
        if line.startswith("node_hwmon_temp_celsius{") and "thermal_thermal_zone" in line:
            match = re.search(r"}\s+([0-9.]+)", line)
            if match:
                temp = round(float(match.group(1)), 1)
                break

    freq = None
    for line in body.splitlines():
        if line.startswith("node_cpu_frequency_max_hertz{"):
            match = re.search(r"}\s+([0-9.e+]+)", line)
            if match:
                freq = int(float(match.group(1)) / 1e6)
                break

    fs_avail = _metric(body, "node_filesystem_avail_bytes", mountpoint="/")
    fs_size = _metric(body, "node_filesystem_size_bytes", mountpoint="/")
    disk_pct = None
    if fs_avail is not None and fs_size:
        disk_pct = int(round(100 * (1 - fs_avail / fs_size)))

    boot = _metric(body, "node_boot_time_seconds")

    return {
        "ok": True,
        "age_s": 0,
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "mem_used_kb": int((mem_total - avail) / 1024) if (mem_total and avail is not None) else None,
        "temp_c": temp,
        "load1": _metric(body, "node_load1"),
        "load5": _metric(body, "node_load5"),
        "load15": _metric(body, "node_load15"),
        "uptime_s": int(time.time() - boot) if boot else None,
        "disk_pct": disk_pct,
        "freq_mhz": freq,
        "scrape_ms": ms,
    }


# ------------------------------------------------------------------ gw ------

def sample_gw():
    rates = {}
    with open("/proc/net/dev", "r", encoding="utf-8") as fh:
        for line in fh:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            name = name.strip()
            if name not in IFACES:
                continue
            fields = rest.split()
            rates[name + "_rx"] = _delta(name + "_rx", int(fields[0]))
            rates[name + "_tx"] = _delta(name + "_tx", int(fields[8]))

    leases = 0
    try:
        with open(LEASES, "r", encoding="utf-8", errors="replace") as fh:
            leases = sum(1 for line in fh if line.strip())
    except OSError:
        pass

    out = {"ok": True, "age_s": 0, "leases": leases}
    for iface, key in GW_KEYS:
        out[key + "_rx"] = rates.get(iface + "_rx", 0)
        out[key + "_tx"] = rates.get(iface + "_tx", 0)
    return out


# ---------------------------------------------------------------- proxy -----

def refresh_meta():
    """Refresh /proxies and /configs at a slower cadence than /connections."""
    now = time.monotonic()
    if now - _meta["at"] < META_EVERY:
        return
    _meta["proxies"] = _mihomo("/proxies").get("proxies", {}) or {}
    _meta["mode"] = _mihomo("/configs").get("mode", "") or ""
    _meta["at"] = now


def _active_node(live, proxies):
    """The node actually carrying traffic: most common chain head of live conns.

    `chains` is ordered [node, group, ...] by mihomo; a DIRECT first hop means
    the connection bypassed the proxy, which is not an interesting "node".
    """
    heads = {}
    for conn in live:
        chains = conn.get("chains") or []
        if chains and chains[0] != "DIRECT":
            heads[chains[0]] = heads.get(chains[0], 0) + 1
    if heads:
        return max(heads, key=heads.get)
    for name, info in proxies.items():
        if isinstance(info, dict) and info.get("type") == "URLTest" and info.get("now"):
            return info["now"]
    return ""


_delay_cache = {}  # node name -> (monotonic probe time, delay ms)


def _node_delay(node, selected):
    """Delay in ms for the active node: newest history sample, else a probe.

    URLTest groups only fill `history` for nodes they test, so the node actually
    carrying traffic often has an empty history. One probe per node per
    DELAY_CACHE_S keeps the number real without hammering the API.
    """
    history = (selected or {}).get("history") or []
    if history and isinstance(history[-1], dict):
        value = history[-1].get("delay")
        if isinstance(value, int) and value > 0:
            return value
    if not node:
        return None
    cached = _delay_cache.get(node)
    if cached and time.monotonic() - cached[0] < DELAY_CACHE_S:
        return cached[1]
    try:
        quote = urllib.parse.quote(node, safe="")
        target = urllib.parse.quote(DELAY_PROBE_URL, safe="")
        data = _mihomo("/proxies/%s/delay?url=%s&timeout=2000" % (quote, target),
                       timeout=DELAY_PROBE_TIMEOUT)
        value = data.get("delay")
        if isinstance(value, int) and value > 0:
            _delay_cache[node] = (time.monotonic(), value)
            return value
    except (OSError, ValueError, KeyError):
        pass
    return cached[1] if cached else None


def sample_proxy():
    conns = _mihomo("/connections")
    refresh_meta()
    proxies = _meta["proxies"]

    down_total = int(conns.get("downloadTotal") or 0)
    up_total = int(conns.get("uploadTotal") or 0)
    live = conns.get("connections") or []

    node = _active_node(live, proxies)
    selected = proxies.get(node) if node else None
    delay = _node_delay(node, selected if isinstance(selected, dict) else None)

    return {
        "ok": True,
        "age_s": 0,
        "mode": _meta["mode"],
        "node": node,
        "delay_ms": delay,
        "conns": len(live),
        "down_bps": _delta("proxy_down", down_total),
        "up_bps": _delta("proxy_up", up_total),
        "down_total": down_total,
        "up_total": up_total,
    }


# --------------------------------------------------------------- compose ----

def _keep(family):
    last = _last_good.get(family)
    if not last:
        return {"ok": False, "age_s": None}
    out = dict(last["data"])
    out["ok"] = False
    out["age_s"] = int(time.time() - last["at"])
    return out


def _store(family, data):
    _last_good[family] = {"at": time.time(), "data": data}
    return data


# ------------------------------------------------------------------ gate ----

def _read_status_file(path):
    """Parse the probe's `key=value` status line into a dict, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    out = {}
    for token in text.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def _tun_rates():
    """TUN rx/tx bytes per second; this is the proxied traffic the display shows."""
    try:
        with open("/proc/net/dev", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                if name.strip() != TUN_IFACE:
                    continue
                fields = rest.split()
                return (_delta("tun_rx", int(fields[0])),
                        _delta("tun_tx", int(fields[8])))
    except (OSError, ValueError, IndexError):
        pass
    return 0, 0


def _budget():
    """Per-account credits still inside the broker's rolling one-hour window."""
    try:
        with open(BUDGET_LEDGER, "r", encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False}
    now = time.time()
    accounts = {}
    for name, entries in (ledger.get("accounts") or {}).items():
        if not isinstance(entries, list):
            continue
        live = [e for e in entries
                if isinstance(e, list) and len(e) >= 3 and isinstance(e[2], (int, float))
                and e[2] > now]
        credits = sum(e[1] for e in live if isinstance(e[1], (int, float)))
        accounts[name] = {"credits": int(credits), "probes": int(credits // 2),
                          "window_s": 3600}
    last = ledger.get("last_reservation_at")
    return {"ok": True, "accounts": accounts,
            "last_reservation_age_s": int(now - last) if isinstance(last, (int, float)) else None}


def sample_gate():
    """Gateway health as evidence: probe status, TUN state, budget window."""
    status = _read_status_file(HEALTH_STATUS)
    rx, tx = _tun_rates()
    out = {
        "ok": True,
        "age_s": 0,
        "health": status or {"business": "unknown"},
        "tun": {"operstate": None, "rx_bps": rx, "tx_bps": tx},
        "budget": _budget(),
    }
    try:
        with open("/sys/class/net/%s/operstate" % TUN_IFACE, "r", encoding="utf-8") as fh:
            out["tun"]["operstate"] = fh.read().strip()
    except OSError:
        pass
    # A display should light up when health is anything but good, including the
    # "probe never wrote a status" case.
    business = out["health"].get("business")
    out["degraded"] = business not in ("verified", "idle")
    return out


def snapshot():
    out = {"ts": int(time.time()), "agent": {"up_s": int(time.monotonic() - START),
                                             "version": VERSION}}
    try:
        pi = _store("pi", sample_pi())
        out["agent"]["node_exporter"] = {"ok": True, "ms": pi.pop("scrape_ms")}
    except Exception:
        pi = _keep("pi")
        out["agent"]["node_exporter"] = {"ok": False, "ms": None}
    out["pi"] = pi

    for family, sampler in (("gw", sample_gw), ("proxy", sample_proxy),
                           ("gate", sample_gate)):
        try:
            out[family] = _store(family, sampler())
        except Exception:
            out[family] = _keep(family)
    return out


def cached_snapshot():
    with _lock:
        now = time.monotonic()
        if _cache["body"] is None or now - _cache["at"] >= CACHE_S:
            _cache["body"] = json.dumps(snapshot(), separators=(",", ":")).encode()
            _cache["at"] = now
        return _cache["body"]


# ------------------------------------------------------------------ http ----

class Handler(BaseHTTPRequestHandler):
    server_version = "rp5-agent/" + VERSION
    protocol_version = "HTTP/1.1"

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        scheme, _, provided = header.partition(" ")
        if scheme.lower() != "bearer" or not provided:
            return False
        return hmac.compare_digest(provided.strip(), STATUS_TOKEN)

    def _send_json(self, code, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        # /health is deliberately public but carries no deployment data; every
        # other path, including unknown ones, requires the bearer token so an
        # unauthenticated caller learns nothing about the endpoint layout.
        if self.path == "/health" or self.path.startswith("/health?"):
            body = json.dumps({"ok": True, "up_s": int(time.monotonic() - START),
                               "version": VERSION}).encode()
            self._send_json(200, body)
            return
        if not self._authorized():
            self._send_json(401, b'{"error":"unauthorized"}',
                            {"WWW-Authenticate": 'Bearer realm="rp5-agent"'})
            return
        if self.path.startswith("/api/v1/status"):
            self._send_json(200, cached_snapshot())
            return
        self._send_json(404, b'{"error":"not found"}')

    def log_message(self, fmt, *args):
        pass  # journald gets one summary line per minute instead


def _sampler():
    """Keep baselines warm so the first request is already meaningful."""
    failures = 0
    while True:
        try:
            cached_snapshot()
            failures = 0
        except Exception:
            failures = min(failures + 1, 4)
        time.sleep(CACHE_S if not failures else min(2 ** failures, 15))


def _logger():
    while True:
        time.sleep(60)
        try:
            data = json.loads(cached_snapshot())
            print("agent up=%ss pi=%s%%/%sC mem=%s%% gw=%sB/s proxy=%s %sms" % (
                data["agent"]["up_s"],
                data["pi"].get("cpu_pct"), data["pi"].get("temp_c"), data["pi"].get("mem_pct"),
                data["gw"].get("wan_rx"), (data["proxy"].get("node") or "")[:18],
                data["proxy"].get("delay_ms")), flush=True)
        except Exception:
            pass


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=_sampler, daemon=True).start()
    threading.Thread(target=_logger, daemon=True).start()
    print("rp5-agent %s listening on %s:%d" % (VERSION, LISTEN_HOST, LISTEN_PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
