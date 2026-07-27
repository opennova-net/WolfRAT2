"""Pseudonymous startup and heartbeat analytics used by WolfRAT."""

import json
from pathlib import Path
import platform
import threading
import urllib.error
import urllib.request
import uuid


BSTATS_URL = "http://fmj-squad.com/bstats/ping"
HEARTBEAT_INTERVAL = 30 * 60
TIMEOUT = 5

_version = "0.0"
_tool = "unknown"
_os = "unknown"
_client_id = "unknown"
_lifecycle_lock = threading.RLock()
_stop_event: threading.Event | None = None
_threads: tuple[threading.Thread, ...] = ()


def _get_os():
    """Return a compact operating-system label."""
    try:
        system = platform.system()
        if system == "Windows":
            version = platform.version()
            build = int(version.split(".")[-1]) if "." in version else 0
            return "Windows 11" if build >= 22000 else "Windows 10"
        return system
    except Exception:
        return "unknown"


def _get_client_id(data_dir: Path):
    """Load or create the stable telemetry identity in application-owned state."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        identity_path = data_dir / "bstats_id.txt"
        if identity_path.exists():
            return identity_path.read_text(encoding="utf-8").strip()
        identity = str(uuid.uuid4())
        identity_path.write_text(identity, encoding="utf-8")
        return identity
    except Exception:
        return str(uuid.uuid4())


def _ping(ping_type="heartbeat"):
    """Send one telemetry event without affecting the host application."""
    try:
        data = json.dumps(
            {
                "tool": _tool,
                "version": _version,
                "os": _os,
                "client_id": _client_id,
                "type": ping_type,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            BSTATS_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT):
            pass
    except Exception:
        pass


def _heartbeat_loop(stop_event):
    """Wait interruptibly between heartbeat events."""
    while not stop_event.wait(HEARTBEAT_INTERVAL):
        _ping("heartbeat")


def _stop_locked():
    """Stop owned workers while the caller holds ``_lifecycle_lock``."""
    global _stop_event, _threads

    stop_event = _stop_event
    if stop_event is not None:
        stop_event.set()

    current = threading.current_thread()
    survivors = []
    for thread in _threads:
        if thread is not current and thread.is_alive():
            thread.join(timeout=max(float(TIMEOUT) + 1.0, 0.0))
        if thread.is_alive():
            survivors.append(thread)

    _threads = tuple(survivors)
    if survivors:
        names = ", ".join(repr(thread.name) for thread in survivors)
        raise RuntimeError(f"telemetry workers did not stop: {names}")
    _stop_event = None


def bstats_start(tool, version, *, data_dir):
    """Start one startup worker and one interruptible heartbeat worker."""
    global _tool, _version, _os, _client_id
    global _stop_event, _threads

    with _lifecycle_lock:
        _stop_locked()
        stop_event = threading.Event()
        startup = threading.Thread(
            target=_ping,
            args=("startup",),
            daemon=True,
            name="wolfrat-telemetry-startup",
        )
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(stop_event,),
            daemon=True,
            name="wolfrat-telemetry-heartbeat",
        )
        _tool = str(tool).lower()
        _version = str(version)
        _os = _get_os()
        _client_id = _get_client_id(Path(data_dir))
        _stop_event = stop_event
        _threads = (startup, heartbeat)
        startup.start()
        heartbeat.start()


def bstats_stop():
    """Signal and join every telemetry worker; safe to call repeatedly."""
    with _lifecycle_lock:
        _stop_locked()
