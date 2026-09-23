"""Connection checks: is this player's IP a VPN, proxy, Tor exit, hosting range,
or from a blocked country?  The rules and the lookups, no Qt.

Two providers, both free tiers usable with no card:
  * ip-api.com   - no key, 45 lookups/min, plain HTTP on the free tier.  One
                   ``proxy`` flag covers VPN/proxy/Tor, ``hosting`` is separate.
  * proxycheck.io - optional key (100/day without, 1000/day with), HTTPS,
                   says which kind (VPN / TOR / SOCKS / Hosting ...).
Verdicts are cached for a week so regulars cost nothing.  Every request sets
an explicit User-Agent (Cloudflare bans the default Python one).

Honest limits: a residential VPN or a mate's connection still gets through;
some mobile carriers get flagged as proxies - so KICK is the sensible default
and the whitelist exists for the regular who really plays through a VPN.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

ACTION_NONE, ACTION_KICK, ACTION_BAN = 0, 1, 2
ACTION_LABELS = ["do nothing", "kick", "ban"]
COUNTRY_OFF, COUNTRY_BLOCK, COUNTRY_ALLOW = "off", "block", "allow"
PROVIDER_IPAPI, PROVIDER_PROXYCHECK = "ip-api", "proxycheck"
PROVIDERS = [(PROVIDER_IPAPI, "ip-api.com (no key, 45/min)"),
             (PROVIDER_PROXYCHECK, "proxycheck.io (key optional, better VPN data)")]
CACHE_DAYS = 7
USER_AGENT = "WolfRAT/2 (+https://fmj-squad.com)"
TIMEOUT = 6.0
_PRIVATE_PREFIXES = ("10.", "192.168.", "127.", "169.254.", "0.") + tuple(f"172.{n}." for n in range(16, 32))


@dataclass
class ChecksConfig:
    enabled: bool = False
    provider: str = PROVIDER_IPAPI
    api_key: str = ""
    action_proxy: int = ACTION_KICK       # VPN / proxy / Tor
    action_hosting: int = ACTION_NONE     # datacentre / hosting ranges
    country_mode: str = COUNTRY_OFF
    countries: list = field(default_factory=list)   # ISO codes, upper case
    action_country: int = ACTION_KICK

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data) -> "ChecksConfig":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        cfg.enabled = bool(data.get("enabled", False))
        cfg.provider = data.get("provider") if data.get("provider") in (PROVIDER_IPAPI, PROVIDER_PROXYCHECK) else PROVIDER_IPAPI
        cfg.api_key = str(data.get("api_key") or "")
        cfg.action_proxy = _clamp_action(data.get("action_proxy"), ACTION_KICK)
        cfg.action_hosting = _clamp_action(data.get("action_hosting"), ACTION_NONE)
        cfg.action_country = _clamp_action(data.get("action_country"), ACTION_KICK)
        cfg.country_mode = data.get("country_mode") if data.get("country_mode") in (COUNTRY_OFF, COUNTRY_BLOCK, COUNTRY_ALLOW) else COUNTRY_OFF
        cfg.countries = parse_countries(",".join(data.get("countries") or []) if isinstance(data.get("countries"), list) else str(data.get("countries") or ""))
        return cfg


def _clamp_action(value, default) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v in (ACTION_NONE, ACTION_KICK, ACTION_BAN) else default


def parse_countries(text: str) -> list:
    """'gb, us,DE' -> ['GB', 'US', 'DE'].  Two letters each, junk dropped."""
    out = []
    for part in str(text or "").replace(";", ",").replace(" ", ",").split(","):
        code = part.strip().upper()
        if len(code) == 2 and code.isalpha() and code not in out:
            out.append(code)
    return out


@dataclass
class Verdict:
    ip: str
    checked_at: float
    proxy: bool = False        # VPN / proxy / Tor
    hosting: bool = False
    kind: str = ""             # provider's word for it: VPN, TOR, Hosting, ...
    country: str = ""          # ISO code
    country_name: str = ""
    region: str = ""           # Scotland, Texas, ... (not in caches from before 2.8.3)
    provider: str = ""         # ISP / org
    source: str = ""
    error: str = ""            # lookup failed - never act on an error

    def is_fresh(self, now: float, days: float = CACHE_DAYS) -> bool:
        return now - self.checked_at < days * 86400

    def summary(self) -> str:
        if self.error:
            return f"check failed: {self.error}"
        bits = []
        if self.proxy:
            bits.append(self.kind or "VPN/proxy")
        if self.hosting:
            bits.append("hosting")
        where = self.country or "?"
        return (", ".join(bits) if bits else "clear") + f" ({where}" + (f", {self.provider}" if self.provider else "") + ")"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> Optional["Verdict"]:
        try:
            v = cls(str(data["ip"]), float(data.get("checked_at") or 0))
        except (KeyError, TypeError, ValueError):
            return None
        for name in ("proxy", "hosting"):
            setattr(v, name, bool(data.get(name)))
        for name in ("kind", "country", "country_name", "region", "provider", "source", "error"):
            setattr(v, name, str(data.get(name) or ""))
        return v


def is_private(ip: str) -> bool:
    return ip.startswith(_PRIVATE_PREFIXES)


# ---- the decision -------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    action: int
    reason: str


def decide(verdict: Optional[Verdict], config: ChecksConfig) -> Outcome:
    """What to do about this connection.  Errors and unknowns never act."""
    if not config.enabled or verdict is None or verdict.error:
        return Outcome(ACTION_NONE, "")
    worst = Outcome(ACTION_NONE, "")

    def consider(action: int, reason: str):
        nonlocal worst
        if action > worst.action:
            worst = Outcome(action, reason)

    who = verdict.provider or verdict.source
    suffix = f" ({who})" if who else ""
    if verdict.proxy:
        consider(config.action_proxy, f"{verdict.kind or 'VPN/proxy'} connection{suffix}")
    if verdict.hosting and not verdict.proxy:
        consider(config.action_hosting, f"hosting/datacentre address{suffix}")
    if config.country_mode != COUNTRY_OFF and verdict.country and config.countries:
        listed = verdict.country in config.countries
        if (config.country_mode == COUNTRY_BLOCK and listed) or (config.country_mode == COUNTRY_ALLOW and not listed):
            consider(config.action_country, f"country {verdict.country_name or verdict.country} is "
                     + ("blocked" if config.country_mode == COUNTRY_BLOCK else "not on the allowed list"))
    return worst


# ---- providers -------------------------------------------------------------------

def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def lookup_ipapi(ip: str, api_key: str = "", fetch: Callable[[str], dict] = _get_json) -> Verdict:
    now = time.time()
    url = ("http://ip-api.com/json/" + urllib.parse.quote(ip)
           + "?fields=status,message,country,countryCode,regionName,isp,org,proxy,hosting")
    try:
        data = fetch(url)
    except Exception as exc:
        return Verdict(ip, now, source="ip-api", error=_short(exc))
    if data.get("status") != "success":
        return Verdict(ip, now, source="ip-api", error=str(data.get("message") or "no answer"))
    return Verdict(ip, now, proxy=bool(data.get("proxy")), hosting=bool(data.get("hosting")),
                   kind="VPN/proxy" if data.get("proxy") else "", country=str(data.get("countryCode") or ""),
                   country_name=str(data.get("country") or ""), region=str(data.get("regionName") or ""),
                   provider=str(data.get("isp") or data.get("org") or ""),
                   source="ip-api")


def lookup_proxycheck(ip: str, api_key: str = "", fetch: Callable[[str], dict] = _get_json) -> Verdict:
    now = time.time()
    query = {"vpn": "1", "asn": "1"}
    if api_key:
        query["key"] = api_key
    url = "https://proxycheck.io/v2/" + urllib.parse.quote(ip) + "?" + urllib.parse.urlencode(query)
    try:
        data = fetch(url)
    except Exception as exc:
        return Verdict(ip, now, source="proxycheck", error=_short(exc))
    if data.get("status") not in ("ok", "warning"):
        return Verdict(ip, now, source="proxycheck", error=str(data.get("message") or data.get("status") or "no answer"))
    row = data.get(ip) or {}
    if not isinstance(row, dict):
        return Verdict(ip, now, source="proxycheck", error="no answer for this address")
    kind = str(row.get("type") or "")
    is_proxy = str(row.get("proxy") or "").lower() == "yes"
    hosting = kind.lower() in ("hosting", "business") and not is_proxy and kind.lower() == "hosting"
    return Verdict(ip, now, proxy=is_proxy, hosting=hosting, kind=kind if is_proxy else "",
                   country=str(row.get("isocode") or ""), country_name=str(row.get("country") or ""),
                   region=str(row.get("region") or ""), provider=str(row.get("provider") or ""), source="proxycheck")


LOOKUPS = {PROVIDER_IPAPI: lookup_ipapi, PROVIDER_PROXYCHECK: lookup_proxycheck}


def _short(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}" + (" (rate limit)" if exc.code == 429 else "")
    return f"{type(exc).__name__}: {exc}"[:80]


# ---- cache + background worker -------------------------------------------------------

class VerdictCache:
    def __init__(self):
        self._by_ip: dict[str, Verdict] = {}
        self.dirty = False

    def get(self, ip: str, now: float) -> Optional[Verdict]:
        v = self._by_ip.get(ip)
        if v is None:
            return None
        if v.error and now - v.checked_at > 300:      # retry failures after 5 minutes
            return None
        return v if v.is_fresh(now) else None

    def put(self, verdict: Verdict) -> None:
        self._by_ip[verdict.ip] = verdict
        self.dirty = True

    def forget(self, ip: str) -> None:
        if self._by_ip.pop(ip, None) is not None:
            self.dirty = True

    def to_json(self) -> dict:
        return {"verdicts": [v.to_json() for v in self._by_ip.values()]}

    @classmethod
    def from_json(cls, data) -> "VerdictCache":
        cache = cls()
        if isinstance(data, dict):
            for item in data.get("verdicts") or []:
                v = Verdict.from_json(item) if isinstance(item, dict) else None
                if v is not None:
                    cache._by_ip[v.ip] = v
        return cache


class Checker:
    """Looks IPs up on a background thread, one at a time, and hands each
    Verdict to ``on_result`` (called on the worker thread - the tab re-posts
    it to the Qt thread)."""

    def __init__(self, on_result: Callable[[Verdict], None], lookups=None):
        self._on_result = on_result
        self._lookups = lookups or LOOKUPS
        self._pending: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def submit(self, ip: str, provider: str, api_key: str) -> bool:
        if not ip or is_private(ip):
            return False
        with self._lock:
            if ip in self._pending:
                return False
            self._pending[ip] = (provider, api_key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="wolfrat-ip-checks", daemon=True)
                self._thread.start()
        return True

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _run(self):
        while True:
            with self._lock:
                if not self._pending:
                    return
                ip, (provider, key) = next(iter(self._pending.items()))
            lookup = self._lookups.get(provider, lookup_ipapi)
            try:
                verdict = lookup(ip, key)
            except Exception as exc:                        # pragma: no cover - belt and braces
                verdict = Verdict(ip, time.time(), source=provider, error=_short(exc))
            with self._lock:
                self._pending.pop(ip, None)
            try:
                self._on_result(verdict)
            except Exception:
                pass
            time.sleep(1.4)        # ip-api free tier: 45/min
