"""Connection checks (2026-09-22): provider parsing, the decision, the cache, the worker."""
import time

import pytest

from wolfrat import ip_checks as ic
from wolfrat.ip_checks import (ACTION_BAN, ACTION_KICK, ACTION_NONE, COUNTRY_ALLOW, COUNTRY_BLOCK,
                               ChecksConfig, Verdict, VerdictCache, decide)

NOW = 1_800_000_000.0


def test_ipapi_parses_success_and_failure():
    good = ic.lookup_ipapi("1.2.3.4", fetch=lambda url: {"status": "success", "country": "United Kingdom",
                                                        "countryCode": "GB", "isp": "BT", "proxy": True, "hosting": False})
    assert (good.proxy, good.hosting, good.country, good.provider, good.kind) == (True, False, "GB", "BT", "VPN/proxy")
    assert good.summary() == "VPN/proxy (GB, BT)"
    bad = ic.lookup_ipapi("1.2.3.4", fetch=lambda url: {"status": "fail", "message": "private range"})
    assert bad.error == "private range" and bad.summary() == "check failed: private range"
    down = ic.lookup_ipapi("1.2.3.4", fetch=lambda url: (_ for _ in ()).throw(TimeoutError("timed out")))
    assert down.error.startswith("TimeoutError")


def test_ipapi_url_asks_for_exactly_the_fields_we_read():
    seen = {}
    ic.lookup_ipapi("8.8.8.8", fetch=lambda url: seen.setdefault("url", url) and {"status": "success"})
    assert seen["url"] == "http://ip-api.com/json/8.8.8.8?fields=status,message,country,countryCode,isp,org,proxy,hosting"


def test_proxycheck_parses_type_and_key_goes_in_the_query():
    seen = {}

    def fetch(url):
        seen["url"] = url
        return {"status": "ok", "5.6.7.8": {"asn": "AS1", "provider": "NordVPN", "country": "Germany",
                                            "isocode": "DE", "proxy": "yes", "type": "VPN"}}
    v = ic.lookup_proxycheck("5.6.7.8", api_key="abc", fetch=fetch)
    assert "key=abc" in seen["url"] and seen["url"].startswith("https://proxycheck.io/v2/5.6.7.8?")
    assert (v.proxy, v.kind, v.country, v.provider) == (True, "VPN", "DE", "NordVPN")
    clean = ic.lookup_proxycheck("9.9.9.9", fetch=lambda url: {"status": "ok", "9.9.9.9": {"proxy": "no", "type": "Residential", "isocode": "GB"}})
    assert not clean.proxy and not clean.hosting and clean.summary() == "clear (GB)"
    host = ic.lookup_proxycheck("9.9.9.9", fetch=lambda url: {"status": "ok", "9.9.9.9": {"proxy": "no", "type": "Hosting", "isocode": "US"}})
    assert host.hosting and not host.proxy
    denied = ic.lookup_proxycheck("9.9.9.9", fetch=lambda url: {"status": "denied", "message": "bad key"})
    assert denied.error == "bad key"


def test_every_request_carries_our_user_agent(monkeypatch):
    captured = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"status":"success"}'

    def fake_open(request, timeout):
        captured["ua"] = request.get_header("User-agent"); captured["timeout"] = timeout
        return Resp()
    monkeypatch.setattr(ic.urllib.request, "urlopen", fake_open)
    ic.lookup_ipapi("8.8.8.8")
    assert captured["ua"] == ic.USER_AGENT and captured["timeout"] == ic.TIMEOUT


# ---- the decision ----------------------------------------------------------------

def v(**kw):
    return Verdict("5.6.7.8", NOW, **kw)


def test_disabled_error_or_unknown_never_acts():
    cfg = ChecksConfig(enabled=False, action_proxy=ACTION_BAN)
    assert decide(v(proxy=True), cfg).action == ACTION_NONE
    cfg.enabled = True
    assert decide(None, cfg).action == ACTION_NONE
    assert decide(v(proxy=True, error="HTTP 429 (rate limit)"), cfg).action == ACTION_NONE


def test_proxy_hosting_and_country_pick_the_strongest_action():
    cfg = ChecksConfig(enabled=True, action_proxy=ACTION_KICK, action_hosting=ACTION_NONE)
    assert decide(v(proxy=True, kind="VPN", provider="NordVPN"), cfg) == ic.Outcome(ACTION_KICK, "VPN connection (NordVPN)")
    assert decide(v(hosting=True), cfg).action == ACTION_NONE
    cfg.action_hosting = ACTION_BAN
    assert decide(v(hosting=True, provider="OVH"), cfg) == ic.Outcome(ACTION_BAN, "hosting/datacentre address (OVH)")
    cfg.country_mode, cfg.countries, cfg.action_country = COUNTRY_BLOCK, ["RU", "CN"], ACTION_KICK
    assert decide(v(country="RU", country_name="Russia"), cfg) == ic.Outcome(ACTION_KICK, "country Russia is blocked")
    assert decide(v(country="GB"), cfg).action == ACTION_NONE
    cfg.country_mode, cfg.countries = COUNTRY_ALLOW, ["GB", "IE"]
    assert decide(v(country="GB"), cfg).action == ACTION_NONE
    assert decide(v(country="FR", country_name="France"), cfg).reason == "country France is not on the allowed list"
    assert decide(v(country=""), cfg).action == ACTION_NONE           # unknown country never acts
    # proxy at kick + country at ban -> ban wins
    cfg.country_mode, cfg.countries, cfg.action_country = COUNTRY_BLOCK, ["DE"], ACTION_BAN
    assert decide(v(proxy=True, country="DE"), cfg).action == ACTION_BAN


def test_config_round_trip_and_junk():
    cfg = ChecksConfig(enabled=True, provider="proxycheck", api_key="k", action_proxy=ACTION_BAN,
                       country_mode=COUNTRY_BLOCK, countries=["GB"])
    again = ChecksConfig.from_json(cfg.to_json())
    assert again == cfg
    junk = ChecksConfig.from_json({"provider": "nope", "action_proxy": 9, "country_mode": "x", "countries": "gb, us,zz1"})
    assert junk.provider == "ip-api" and junk.action_proxy == ACTION_KICK and junk.country_mode == "off" and junk.countries == ["GB", "US"]
    assert ic.parse_countries("de;fr GB") == ["DE", "FR", "GB"]


def test_cache_keeps_verdicts_a_week_and_retries_errors_after_five_minutes():
    cache = VerdictCache()
    cache.put(v(proxy=True))
    assert cache.get("5.6.7.8", NOW + 6 * 86400).proxy
    assert cache.get("5.6.7.8", NOW + 8 * 86400) is None
    cache.put(Verdict("9.9.9.9", NOW, error="HTTP 429"))
    assert cache.get("9.9.9.9", NOW + 60).error == "HTTP 429"
    assert cache.get("9.9.9.9", NOW + 400) is None
    again = VerdictCache.from_json(cache.to_json())
    assert again.get("5.6.7.8", NOW).kind == "" and again.get("5.6.7.8", NOW).proxy is True
    assert VerdictCache.from_json("junk").get("5.6.7.8", NOW) is None


def test_worker_looks_up_each_ip_once_skips_private_and_reports_back(monkeypatch):
    monkeypatch.setattr(ic.time, "sleep", lambda s: None)
    results = []
    calls = []

    def fake_lookup(ip, key):
        calls.append((ip, key)); return Verdict(ip, NOW, proxy=ip.endswith("8"))
    checker = ic.Checker(results.append, lookups={"ip-api": fake_lookup})
    assert checker.submit("5.6.7.8", "ip-api", "") is True
    assert checker.submit("5.6.7.8", "ip-api", "") is False        # already queued
    assert checker.submit("192.168.1.9", "ip-api", "") is False    # private
    checker.submit("9.9.9.9", "ip-api", "k")
    deadline = time.time() + 5
    while len(results) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert sorted(r.ip for r in results) == ["5.6.7.8", "9.9.9.9"]
    assert ("9.9.9.9", "k") in calls and checker.pending() == 0
