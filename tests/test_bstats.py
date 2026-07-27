import threading

import pytest

from wolfrat import bstats


@pytest.fixture(autouse=True)
def stopped_telemetry():
    bstats.bstats_stop()
    yield
    bstats.bstats_stop()


def test_telemetry_identity_lives_in_the_runtime_data_directory(
    tmp_path, monkeypatch
):
    observed = []
    monkeypatch.setattr(bstats, "_ping", observed.append)

    bstats.bstats_start("wolfrat", "2.4.11", data_dir=tmp_path)
    bstats.bstats_stop()

    identity_path = tmp_path / "bstats_id.txt"
    assert identity_path.is_file()
    assert identity_path.read_text(encoding="utf-8").strip()
    assert observed == ["startup"]


def test_stop_interrupts_and_joins_heartbeat_before_restart(
    tmp_path, monkeypatch
):
    startup_seen = threading.Event()

    def observe_ping(ping_type):
        if ping_type == "startup":
            startup_seen.set()

    monkeypatch.setattr(bstats, "_ping", observe_ping)
    monkeypatch.setattr(bstats, "HEARTBEAT_INTERVAL", 60)

    bstats.bstats_start("wolfrat", "2.4.11", data_dir=tmp_path)
    assert startup_seen.wait(timeout=1)
    bstats.bstats_stop()

    assert not any(
        thread.name.startswith("wolfrat-telemetry-") and thread.is_alive()
        for thread in threading.enumerate()
    )

    startup_seen.clear()
    bstats.bstats_start("wolfrat", "2.4.11", data_dir=tmp_path)
    assert startup_seen.wait(timeout=1)
    bstats.bstats_stop()

    assert not any(
        thread.name.startswith("wolfrat-telemetry-") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_overlapping_starts_leave_only_the_newest_workers_owned(
    tmp_path, monkeypatch
):
    real_thread = threading.Thread
    older_paused = threading.Event()
    release_older = threading.Event()
    newer_reached_configuration = threading.Event()
    created_workers = []
    worker_stop_events = []

    def no_ping(_ping_type):
        pass

    def observed_os():
        if threading.current_thread().name == "bstats-new-start":
            newer_reached_configuration.set()
        return "Test OS"

    def gated_thread(*args, **kwargs):
        name = kwargs.get("name", "")
        if (
            threading.current_thread().name == "bstats-old-start"
            and name == "wolfrat-telemetry-heartbeat"
        ):
            older_paused.set()
            release_older.wait(timeout=2)
        worker = real_thread(*args, **kwargs)
        if name.startswith("wolfrat-telemetry-"):
            created_workers.append(worker)
        if name == "wolfrat-telemetry-heartbeat":
            worker_stop_events.append(kwargs["args"][0])
        return worker

    monkeypatch.setattr(bstats, "_ping", no_ping)
    monkeypatch.setattr(bstats, "_get_os", observed_os)
    monkeypatch.setattr(bstats.threading, "Thread", gated_thread)
    monkeypatch.setattr(bstats, "HEARTBEAT_INTERVAL", 60)

    older = real_thread(
        target=lambda: bstats.bstats_start(
            "old", "1", data_dir=tmp_path / "old"
        ),
        name="bstats-old-start",
    )
    newer = real_thread(
        target=lambda: bstats.bstats_start(
            "new", "2", data_dir=tmp_path / "new"
        ),
        name="bstats-new-start",
    )
    try:
        older.start()
        assert older_paused.wait(timeout=1)
        newer.start()
        newer_reached_configuration.wait(timeout=0.1)
        release_older.set()
        older.join(timeout=2)
        newer.join(timeout=2)

        assert not older.is_alive()
        assert not newer.is_alive()
        assert bstats._tool == "new"
        assert len(
            [
                worker
                for worker in threading.enumerate()
                if worker.name == "wolfrat-telemetry-heartbeat"
            ]
        ) == 1
    finally:
        release_older.set()
        for stop_event in worker_stop_events:
            stop_event.set()
        for worker in created_workers:
            if worker is not threading.current_thread():
                worker.join(timeout=1)
        bstats.bstats_stop()


def test_failed_stop_retains_worker_ownership_for_retry(tmp_path, monkeypatch):
    startup_entered = threading.Event()
    release_startup = threading.Event()

    def blocking_ping(ping_type):
        if ping_type == "startup":
            startup_entered.set()
            release_startup.wait(timeout=3)

    monkeypatch.setattr(bstats, "_ping", blocking_ping)
    monkeypatch.setattr(bstats, "TIMEOUT", 0.01)
    bstats.bstats_start("wolfrat", "2.4.11", data_dir=tmp_path)
    assert startup_entered.wait(timeout=1)

    try:
        with pytest.raises(RuntimeError, match="did not stop"):
            bstats.bstats_stop()

        assert any(
            worker.name == "wolfrat-telemetry-startup" and worker.is_alive()
            for worker in bstats._threads
        )
    finally:
        release_startup.set()
        bstats.bstats_stop()

    assert bstats._threads == ()
    assert bstats._stop_event is None
