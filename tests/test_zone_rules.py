"""AAS zone rules (2026-09-22): Doslin Oblast as read live while Dale captured."""
from types import SimpleNamespace as Z

from wolfrat import zone_rules as zr
from wolfrat.zone_rules import ZoneWatch, zones_left

# the five Doslin zones as read at 21:05 (tier, team, pos)
START = [Z(tier=3, team=0, pos=(-16900178, 52104356, 1857437)),
         Z(tier=4, team=2, pos=(-18569420, 56731684, 2361528)),
         Z(tier=5, team=2, pos=(-12567544, 54416764, 2074384)),
         Z(tier=2, team=1, pos=(-18556912, 47465764, 2361528)),
         Z(tier=1, team=1, pos=(-24908850, 52048208, 1671168))]


def with_owner(zones, tier, team):
    return [Z(tier=z.tier, team=(team if z.tier == tier else z.team), pos=z.pos) for z in zones]


def test_names():
    assert [zr.zone_name(t) for t in (1, 3, 8, 20, 21)] == ["Alpha", "Charlie", "Hotel", "Tango", "Zone 21"]
    assert zr.team_name(1) == "Joint Ops" and zr.team_name(2) == "Rebels" and zr.team_name(0) == "team 0"


def test_zones_left_follows_the_live_captures():
    assert zones_left([]) is None
    assert zones_left(START) == (1, 3)                       # 2 each, tie -> team 1, 3 to go
    after_charlie = with_owner(START, 3, 2)
    assert zones_left(after_charlie) == (2, 2)
    after_bravo = with_owner(after_charlie, 2, 2)
    assert zones_left(after_bravo) == (2, 1)                  # one zone left: the vote trigger
    assert zones_left(with_owner(after_bravo, 1, 2)) == (2, 0)


def test_first_capture_is_reported_once_with_the_nearest_player_of_that_team():
    w = ZoneWatch()
    assert w.update(START, {}, {}) == []                     # first sight: nothing
    positions = {"Dale": (-16900178 + 200000, 52104356 - 100000, 0), "Far": (0, 0, 0), "Blue": (-16900178, 52104356, 0)}
    teams = {"Dale": 2, "Far": 2, "Blue": 1}
    ev = w.update(with_owner(START, 3, 2), positions, teams)
    assert len(ev) == 1 and (ev[0].team, ev[0].tier, ev[0].name, ev[0].player, ev[0].first) == (2, 3, "Charlie", "Dale", True)
    ev2 = w.update(with_owner(with_owner(START, 3, 2), 2, 2), {}, {})
    assert ev2[0].first is False and ev2[0].player == "" and ev2[0].name == "Bravo"
    assert w.update(with_owner(with_owner(START, 3, 2), 2, 2), {}, {}) == []       # no change: nothing
    w.reset()
    assert w.update(START, {}, {}) == []


def test_capture_lines_fill_and_fall_back():
    ev = zr.CaptureEvent(2, 3, "Charlie", "Dale", True)
    assert zr.capture_line(ev, "{player} takes {zone} for the {team}!", "{team} take {zone}!") == "Dale takes Charlie for the Rebels!"
    ev2 = zr.CaptureEvent(1, 1, "Alpha", "", True)
    assert zr.capture_line(ev2, "{player} takes {zone} for the {team}!", "{team} take {zone} - first zone!") == "Joint Ops take Alpha - first zone!"
    assert len(zr.capture_line(zr.CaptureEvent(2, 3, "Charlie", "A" * 32, True), "X" * 70, "Y" * 70)) <= 62
