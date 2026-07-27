from pathlib import Path
import json
import os
import runpy
import socket
import subprocess
import sys
import threading
import types

import pytest

from wolfrat.runtime import DesktopRuntime, parse_launch_args


def test_isolated_runtime_routes_state_and_disables_external_startup(tmp_path):
    runtime = DesktopRuntime.isolated(tmp_path)

    assert runtime.data_dir == tmp_path.resolve()
    assert runtime.path("wolfrat_stats.db") == tmp_path.resolve() / "wolfrat_stats.db"
    assert runtime.telemetry_enabled is False
    assert runtime.auto_connect_enabled is False
    assert runtime.web_autostart_enabled is False
    assert runtime.audio_enabled is False


def test_runtime_rejects_paths_outside_its_data_directory(tmp_path):
    runtime = DesktopRuntime.isolated(tmp_path)

    with pytest.raises(ValueError, match="simple filename"):
        runtime.path("../outside.json")


def test_production_runtime_uses_writable_per_user_state_not_launcher_directory(
    tmp_path, monkeypatch
):
    launcher_dir = tmp_path / "venv" / "Scripts"
    launcher_dir.mkdir(parents=True)
    if os.name == "nt":
        state_root = tmp_path / "LocalAppData"
        monkeypatch.setenv("LOCALAPPDATA", str(state_root))
        expected = state_root / "WolfRAT2"
    elif sys.platform == "darwin":
        home = tmp_path / "Home"
        monkeypatch.setenv("HOME", str(home))
        expected = home / "Library" / "Application Support" / "WolfRAT2"
    else:
        state_root = tmp_path / "XdgState"
        monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
        expected = state_root / "wolfrat2"
    monkeypatch.setattr(
        sys,
        "argv",
        [str(launcher_dir / "wolfrat.exe")],
    )

    runtime = DesktopRuntime.production()

    assert runtime.data_dir == expected.resolve()
    assert runtime.data_dir != launcher_dir.resolve()


def test_smoke_launch_uses_isolated_runtime_and_removes_its_cli_flags(tmp_path):
    launch = parse_launch_args(
        ["WolfRAT2.exe", "--smoke-test", "--data-dir", str(tmp_path), "-platform", "offscreen"]
    )

    assert launch.smoke_test is True
    assert launch.runtime == DesktopRuntime.isolated(tmp_path)
    assert launch.qt_argv == ("WolfRAT2.exe", "-platform", "offscreen")


def test_main_module_is_safe_to_import(monkeypatch):
    calls = []
    fake_app = types.ModuleType("wolfrat.app")
    fake_app.main = lambda: calls.append("started")
    monkeypatch.setitem(sys.modules, "wolfrat.app", fake_app)

    runpy.run_path(
        str(Path(__file__).parents[1] / "main.py"),
        run_name="wolfrat_main_import_probe",
    )

    assert calls == []


def test_isolated_desktop_constructs_real_tabs_without_external_startup(
    qapp, qtbot, tmp_path
):
    from wolfrat.app import start_desktop

    runtime = DesktopRuntime.isolated(tmp_path)
    window = start_desktop(qapp, runtime=runtime)
    qtbot.addWidget(window)

    assert window.runtime is runtime
    assert window.tabs.count() == 12

    qtbot.wait(750)

    assert window.web_server.is_running is False
    assert window.server.is_connected is False
    assert not any(thread.name == "wolfrat-connect" for thread in threading.enumerate())

    window.shutdown()
    window.shutdown()


def test_web_admin_reports_bind_failure_instead_of_running(
    qapp,
    qtbot,
    tmp_path,
):
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        reservation.listen()
        port = reservation.getsockname()[1]
        window.web_server.host = "127.0.0.1"
        window.web_admin_tab.port_spin.setValue(port)
        window.web_admin_tab.enable_cb.setChecked(True)

        qtbot.waitUntil(
            lambda: window.web_admin_tab.status_label.text() == "Failed",
            timeout=2_000,
        )

    assert window.web_server.is_running is False
    assert window.web_admin_tab.enable_cb.isChecked() is False
    assert window.web_admin_tab._config["web_enabled"] is False
    assert window.web_admin_tab.url_label.text() == "-"
    assert "Web: Off" in window.web_led_label.text()
    assert str(port) in window.feedback_label.text()
    window.shutdown()


def test_web_admin_reports_running_only_after_the_listener_is_reachable(
    qapp,
    qtbot,
    tmp_path,
):
    from wolfrat.app import start_desktop

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    window.web_server.host = "127.0.0.1"
    window.web_admin_tab.port_spin.setValue(port)
    window.web_admin_tab.enable_cb.setChecked(True)

    qtbot.waitUntil(
        lambda: window.web_admin_tab.status_label.text() == "Running",
        timeout=2_000,
    )

    with socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
    assert window.web_admin_tab.url_label.text() == (
        f"http://localhost:{port}"
    )
    assert "Web: On" in window.web_led_label.text()
    window.shutdown()


def test_desktop_smoke_writes_machine_readable_result(qapp, qtbot, tmp_path):
    from wolfrat.app import schedule_desktop_smoke, start_desktop

    runtime = DesktopRuntime.isolated(tmp_path)
    window = start_desktop(qapp, runtime=runtime)
    qtbot.addWidget(window)

    result_path = schedule_desktop_smoke(qapp, window, delay_ms=0)
    qtbot.waitUntil(result_path.exists, timeout=5_000)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["tab_count"] == 12
    assert result["resources"] == {
        "icon": True,
        "web_templates": True,
        "sounds": True,
        "web_application": True,
    }


def test_smoke_startup_failure_writes_result_and_returns_nonzero(
    tmp_path, monkeypatch
):
    import wolfrat.app as app_module

    monkeypatch.setattr(
        app_module,
        "QApplication",
        lambda _arguments: object(),
    )

    def fail_to_start(_application, _runtime):
        raise RuntimeError("desktop construction failed")

    monkeypatch.setattr(app_module, "start_desktop", fail_to_start)

    exit_code = app_module.main(
        ["WolfRAT2.exe", "--smoke-test", "--data-dir", str(tmp_path)]
    )

    assert exit_code == 1
    result = json.loads(
        (tmp_path / "wolfrat_smoke.json").read_text(encoding="utf-8")
    )
    assert result["ok"] is False
    assert "desktop construction failed" in result["error"]


def test_real_desktop_signal_wiring_updates_visible_state(qapp, qtbot, tmp_path):
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    player = {
        "id": "12",
        "name": "Alice",
        "team": "1",
        "class": "0",
        "kills": "4",
        "deaths": "2",
        "ping": "45",
    }

    window.signals.players_signal.emit([player])
    window.signals.gamestate_signal.emit({"mode": "AAS"})
    window.signals.settings_signal.emit({"servername": "Retail Test"})

    qtbot.waitUntil(lambda: window.players_tab.model.rowCount() == 1)

    assert window.status_players_label.text() == "Players: 1"
    assert window.server_tab.player_count_label.text() == "1"
    assert window.server_tab.game_mode_label.text() == "AAS"
    assert window.windowTitle() == "WolfRAT 2.4.11 — Retail Test"

    window.close()


def test_shutdown_releases_other_resources_when_one_cleanup_fails(
    qapp, qtbot, tmp_path, monkeypatch
):
    from PyQt6.QtCore import QTimer
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    assert window.stats_store.is_open is True

    def broken_web_stop():
        raise RuntimeError("worker stuck")

    monkeypatch.setattr(window.web_server, "stop", broken_web_stop)
    window.shutdown()

    assert window.stats_store.is_open is False
    assert all(not timer.isActive() for timer in window.findChildren(QTimer))


def test_shutdown_invalidates_and_joins_a_pending_connection_worker(
    qapp, qtbot, tmp_path, monkeypatch
):
    from wolfrat.app import start_desktop

    window = start_desktop(qapp, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(window)
    entered = threading.Event()
    release = threading.Event()

    def blocking_connect(*_args):
        entered.set()
        assert release.wait(timeout=2)
        return True, "Connected after shutdown"

    monkeypatch.setattr(window.server, "connect", blocking_connect)
    window.server_tab.host_input.setText("127.0.0.1")
    window.server_tab._do_connect()
    assert entered.wait(timeout=2)
    worker = window.server_tab._connect_thread
    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()

    window.shutdown()
    release_timer.join(timeout=1)

    assert worker is not None
    assert not worker.is_alive()
    assert window.server_tab._pending_connect is None
    assert window.server.is_connected is False


def test_source_entrypoint_completes_smoke_command(tmp_path):
    project_root = Path(__file__).parents[1]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "main.py"),
            "--smoke-test",
            "--data-dir",
            str(tmp_path),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(
        (tmp_path / "wolfrat_smoke.json").read_text(encoding="utf-8")
    )
    assert result["ok"] is True
