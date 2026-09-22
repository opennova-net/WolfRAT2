"""Player history (2026-09-22): names, IPs, visits, chat, and the rejoin lookup."""
from wolfrat.player_history import PlayerHistory, describe_when

NOW = 1_800_000_000.0


def test_observe_builds_records_visits_and_ips():
    h = PlayerHistory()
    h.observe([{"name": "Host"}, {"name": "Troll"}], {"Troll": "5.6.7.8"}, NOW)
    h.observe([{"name": "Troll"}], {"Troll": "5.6.7.8"}, NOW + 5)
    h.observe([{"name": "Troll"}], {"Troll": "9.9.9.9"}, NOW + 1000)      # came back, new IP
    rec = h.get("troll")
    assert rec and rec.visits == 2 and rec.first_seen == NOW and rec.last_seen == NOW + 1000
    assert set(rec.ips) == {"5.6.7.8", "9.9.9.9"} and rec.last_ip() == "9.9.9.9"
    assert h.get("host") is None


def test_by_ip_finds_other_names_on_the_same_address_and_chat_sticks():
    h = PlayerHistory()
    h.observe([{"name": "Troll"}], {"Troll": "5.6.7.8"}, NOW)
    h.observe([{"name": "TotallyNew"}], {"TotallyNew": "5.6.7.8"}, NOW + 500)
    assert [r.name for r in h.by_ip("5.6.7.8")] == ["TotallyNew", "Troll"]
    h.chat("Troll", "hello", NOW + 1)
    h.chat("Nobody", "lost", NOW + 1)
    assert list(h.get("Troll").chat) == [[NOW + 1, "hello"]]
    assert [r.name for r in h.search("5.6.7")] == ["TotallyNew", "Troll"]
    assert [r.name for r in h.search("tro")] == ["Troll"]


def test_json_round_trip():
    h = PlayerHistory()
    h.observe([{"name": "Troll"}], {"Troll": "5.6.7.8"}, NOW)
    h.chat("Troll", "hi", NOW + 1)
    again = PlayerHistory.from_json(h.to_json())
    rec = again.get("Troll")
    assert rec.ips == {"5.6.7.8": [1, NOW]} and list(rec.chat) == [[NOW + 1, "hi"]] and rec.visits == 1
    assert PlayerHistory.from_json("junk").all() == []


def test_describe_when():
    assert describe_when(0) == "-"
    assert describe_when(NOW - 30, NOW) == "just now"
    assert describe_when(NOW - 600, NOW) == "10 min ago"
    assert describe_when(NOW - 7200, NOW) == "2 h ago"
