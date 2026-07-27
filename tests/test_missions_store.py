import json

from wolfrat.app import MissionsStore
from wolfrat.runtime import DesktopRuntime


def test_authoritative_empty_rotation_clears_memory_and_cache(tmp_path):
    runtime = DesktopRuntime.isolated(tmp_path)
    store = MissionsStore(runtime)
    store.update_rotation(["0: AS-OldMap.bms - (1x)"])

    store.update_rotation([])

    assert store.rotation_count == 0
    cached = json.loads(
        runtime.path("wolfrat_missions.json").read_text(encoding="utf-8")
    )
    assert cached["rotation"] == []


def test_authoritative_empty_available_list_clears_memory_and_cache(tmp_path):
    runtime = DesktopRuntime.isolated(tmp_path)
    store = MissionsStore(runtime)
    store.update_available("0: AS-OldMap.bms (Old Map)")

    store.update_available("")

    assert store.available_count == 0
    cached = json.loads(
        runtime.path("wolfrat_missions.json").read_text(encoding="utf-8")
    )
    assert cached["available"] == []
