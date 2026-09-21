"""Live weather control for a Joint Operations dedicated server.

How it works (traced in Jointops.exe.kong.c, 2026-09-21)
--------------------------------------------------------
The server simulates the environment every game tick
(``Environment_UpdateWeatherTick`` @ 0x57E9B0) from a handful of globals, and
``NetPacket_WritePlayerState`` @ 0x4FF6B0 sends them to every client in the
"phase 2" block of the per-player update: fog distance, time of day, quake,
cloud speed, rain/snow amount, overcast and the rain-or-snow switch.  Clients
copy those straight into their own globals (``NapiNPClientMsg_0x00A``).

So changing the numbers *on the server* changes the weather for everybody,
with stock clients and no client-side anything.  The mission script engine
already owns setters for them (WAC actions ``rain``, ``snow``, ``overcast``,
``movefog``, ``skyspeed``, ``quake`` - table @ 0x82D290); this module writes
exactly what those setters write, from outside the process.

That needs WolfRAT to run on the same PC as the server.  The admin port has no
weather command (``CMD`` reaches the key-binding parser, never the script
engine), so there is no remote route without patching the server exe.

Not reachable this way: lightning.  It is not in the synced block - the engine
broadcasts it as a net text command (``SETFLASH1``), which needs code running
inside the server.  See the vault page "Weather Control".

This module has no Qt in it, and everything except ``ProcessMemory`` runs
against the ``Memory`` protocol so tests use a fake.
"""

from __future__ import annotations

import ctypes
import struct
import sys
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional, Protocol

SERVER_IMAGE_NAME = "jointops.exe"

# The game simulates at 62 ticks a second as far as the script setters are
# concerned ("seconds * 62" is how the engine turns a fade time into a step).
TICKS_PER_SECOND = 62

FIXED_ONE = 0x10000            # 100 % for rain / snow / overcast
FOG_MIN = 0x20000              # the engine refuses fog closer than 2 m
QUAKE_MAX_SECONDS = 40         # same cap the engine's own host command uses
QUAKE_TICKS_PER_SECOND = 6     # WAC 'quake n' stores 6 * n


class Addr:
    """Globals in the retail 1.7.5.7 image (no ASLR; image base 0x400000)."""

    IS_AUTHORITY = 0x00B5CC28      # g_napi_np_ctx.is_authority: this process hosts the game
    TIME_OF_DAY = 0x026C6448       # hours << 24, keeps advancing - liveness check
    FOG_CURRENT = 0x026C681C       # metres << 16
    FOG_TARGET = 0x026C6820
    FOG_STEP = 0x026C6828
    CLOUD_SPEED = 0x026C686C
    CLOUD_SPEED_TARGET = 0x026C6870    # skyspeed << 10
    PRECIP_CURRENT = 0x026C6880        # 0 .. 0x10000, this is what clients are sent
    PRECIP_TARGET = 0x026C6884
    PRECIP_STEP = 0x026C688C
    OVERCAST_CURRENT = 0x026C6894
    OVERCAST_TARGET = 0x026C6898
    OVERCAST_STEP = 0x026C68A0
    FOG_REFERENCE = 0x026C68A8         # the furthest the map lets anyone see
    # What the map itself authored.  The engine's map-load "snap" (0x57D2B1..)
    # copies exactly these three into the targets, so "clear" means these.
    FOG_MAP = 0x026C6824               # Env_FogLevelFixed
    PRECIP_MAP = 0x026C6888
    OVERCAST_MAP = 0x026C689C
    QUAKE_TICKS = 0x026C68AC
    PRECIP_IS_SNOW = 0x02C059D0        # 0 rain, 1 snow

    # Lightning needs code inside the server (the flash is a broadcast net
    # message, not a synced number).  The contract with that future patch:
    # its per-tick cave stamps MARKER with LIGHTNING_MAGIC every tick - proof
    # that it is present AND running - and, when MAILBOX is non-zero, clears
    # it and broadcasts "SETFLASH1 16".  These twelve bytes are the unused
    # tail of the .data section's last page: zero in the file, zero in the
    # live server, referenced by no code (checked 2026-09-21).
    # The loaded map's 616-byte BMS header (layout: OpenNova libs/mission bms.h,
    # matched field by field against the live server 2026-09-21).
    BMS_HEADER = 0x00A761D0

    LIGHTNING_MARKER = 0x0334BF00
    LIGHTNING_VERSION = 0x0334BF04
    LIGHTNING_MAILBOX = 0x0334BF08


LIGHTNING_MAGIC = 0x544C5257           # "WRLT"


# Proof that the process is the build this address map was traced from: the
# first bytes of the engine's own `rain` setter, which end in the very write
# we imitate, plus its name in the script action table.  Every community
# patched exe (tick rate, LAA, spawn protection...) leaves these alone.
FINGERPRINT = (
    (0x004EDF60, bytes.fromhex("8b4c2404c1e110b81f85eb51f7e9")),
    (0x004EDFB0, bytes.fromhex("893584686c02")),    # mov [PRECIP_TARGET], esi
    (0x0082E2E5, b"rain\x00"),
)


class WeatherError(Exception):
    """Something the admin should be told about in plain words."""


class Memory(Protocol):
    def read(self, address: int, size: int) -> bytes: ...
    def write(self, address: int, data: bytes) -> None: ...


# ---------------------------------------------------------------------------
# Pure arithmetic - identical to the engine's setters
# ---------------------------------------------------------------------------

def percent_to_fixed(percent: int) -> int:
    """WAC ``rain``/``snow``/``overcast``: pct * 65536 / 100, capped at 100 %."""
    percent = max(0, int(percent))
    return min((percent << 16) // 100, FIXED_ONE)


def fixed_to_percent(fixed: int) -> int:
    return max(0, min(100, round(fixed * 100 / FIXED_ONE)))


def fade_step(current: int, target: int, seconds: float) -> int:
    """Per-tick step so `current` reaches `target` in about `seconds`.

    The engine computes ``abs(ticks/2 + target - current) / ticks`` with
    ``ticks = seconds * 62`` (1 when zero).  Never 0: a zero step would freeze
    the ramp for good.
    """
    ticks = int(seconds * TICKS_PER_SECOND) or 1
    return max(1, abs(ticks // 2 + target - current) // ticks)


def metres_to_fixed(metres: float) -> int:
    return int(metres * FIXED_ONE)


def fixed_to_metres(fixed: int) -> int:
    return fixed >> 16


# ---------------------------------------------------------------------------
# What the admin asks for
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Weather:
    """A target sky.  ``None`` means "leave that dial alone"."""

    precip_percent: int = 0
    snow: bool = False
    overcast_percent: int = 0
    fog_metres: Optional[int] = None     # None = the map's own distance
    cloud_speed: Optional[int] = None    # 0-255, WAC 'skyspeed'
    fade_seconds: int = 20

    def describe(self) -> str:
        if self.precip_percent <= 0 and self.overcast_percent <= 0 and self.fog_metres is None:
            return "clear"
        parts = []
        if self.precip_percent > 0:
            parts.append(f"{'snow' if self.snow else 'rain'} {self.precip_percent}%")
        if self.overcast_percent > 0:
            parts.append(f"overcast {self.overcast_percent}%")
        if self.fog_metres is not None:
            parts.append(f"fog {self.fog_metres} m")
        return ", ".join(parts)


CLEAR = Weather()

PRESETS: dict[str, Weather] = {
    "clear": CLEAR,
    "drizzle": Weather(precip_percent=25, overcast_percent=50, fade_seconds=30),
    "rain": Weather(precip_percent=60, overcast_percent=80, fade_seconds=30),
    "storm": Weather(precip_percent=100, overcast_percent=100, fog_metres=350,
                     cloud_speed=60, fade_seconds=25),
    "snow": Weather(precip_percent=50, snow=True, overcast_percent=70, fade_seconds=30),
    "blizzard": Weather(precip_percent=100, snow=True, overcast_percent=100,
                        fog_metres=200, cloud_speed=60, fade_seconds=25),
    "fog": Weather(overcast_percent=40, fog_metres=150, fade_seconds=40),
    "overcast": Weather(overcast_percent=100, fade_seconds=40),
}


CLIMATES = {0: "desert", 1: "jungle", 2: "snow"}
WEATHER_TYPES = {0: "nice day", 1: "rainy", 2: "snow"}


@dataclass(frozen=True)
class MapInfo:
    """What the loaded map says about itself."""

    title: str = ""
    terrain: str = ""          # lower case, no extension: "dvxg1"
    environment: str = ""
    climate: int = -1          # 0 desert, 1 jungle, 2 snow
    weather_type: int = -1     # 0 nice day, 1 rainy, 2 snow

    @property
    def says_snow(self) -> bool:
        return self.climate == 2 or self.weather_type == 2

    def describe(self) -> str:
        if not self.terrain:
            return "no map loaded"
        return (f"terrain {self.terrain}, climate {CLIMATES.get(self.climate, '?')}, "
                f"weather {WEATHER_TYPES.get(self.weather_type, '?')}")


@dataclass(frozen=True)
class Reading:
    """What the server's sky is doing right now."""

    precip_percent: int
    snow: bool
    overcast_percent: int
    fog_metres: int
    map_fog_metres: int
    cloud_speed: int
    quake_ticks: int
    time_of_day_raw: int

    @property
    def clock(self) -> str:
        minutes = (self.time_of_day_raw * 60) >> 24
        return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

class WeatherController:
    """Reads and sets the sky through a ``Memory``.  Verifies before any write."""

    def __init__(self, memory: Memory):
        self._mem = memory
        self._verified = False

    # -- plumbing ----------------------------------------------------------
    def _get(self, address: int) -> int:
        return struct.unpack("<i", self._mem.read(address, 4))[0]

    def _put(self, address: int, value: int) -> None:
        self._mem.write(address, struct.pack("<i", int(value)))

    def verify(self) -> None:
        """Refuse anything that is not the traced build hosting a game."""
        for address, expected in FINGERPRINT:
            if self._mem.read(address, len(expected)) != expected:
                raise WeatherError(
                    "This jointops.exe is not the build WolfRAT's weather map was "
                    "made for, so nothing was changed."
                )
        if self._get(Addr.IS_AUTHORITY) == 0:
            raise WeatherError("That jointops.exe is a game client, not a server hosting a game.")
        self._verified = True

    # -- reading -----------------------------------------------------------
    def read(self) -> Reading:
        if not self._verified:
            self.verify()
        return Reading(
            precip_percent=fixed_to_percent(self._get(Addr.PRECIP_CURRENT)),
            snow=self._get(Addr.PRECIP_IS_SNOW) == 1,
            overcast_percent=fixed_to_percent(self._get(Addr.OVERCAST_CURRENT)),
            fog_metres=fixed_to_metres(self._get(Addr.FOG_CURRENT)),
            map_fog_metres=fixed_to_metres(self._get(Addr.FOG_REFERENCE)),
            cloud_speed=self._get(Addr.CLOUD_SPEED_TARGET) >> 10,
            quake_ticks=self._get(Addr.QUAKE_TICKS),
            time_of_day_raw=self._get(Addr.TIME_OF_DAY),
        )

    # -- writing -----------------------------------------------------------
    def _ramp(self, current_addr: int, target_addr: int, step_addr: int,
              target: int, seconds: float) -> None:
        if self._get(target_addr) == target:
            # Already heading there.  Re-writing the step from where the ramp
            # has got to would shrink it every time and the fade would never
            # finish on schedule; after a map change the target has been
            # snapped back, so this is skipped and the fade starts afresh.
            return
        current = self._get(current_addr)
        # Step first: with the old step and the new target the engine could
        # take one oversized stride in the tick between the two writes.
        self._put(step_addr, fade_step(current, target, seconds))
        self._put(target_addr, target)

    def apply(self, weather: Weather) -> None:
        if not self._verified:
            self.verify()
        seconds = max(0, weather.fade_seconds)
        clear = weather.precip_percent <= 0 and weather.overcast_percent <= 0 \
            and weather.fog_metres is None
        precip_target = percent_to_fixed(weather.precip_percent)
        overcast_target = percent_to_fixed(weather.overcast_percent)
        if clear:
            # Back to the sky the map was built with, not to a bare blue one.
            precip_target = max(0, min(FIXED_ONE, self._get(Addr.PRECIP_MAP)))
            overcast_target = max(0, min(FIXED_ONE, self._get(Addr.OVERCAST_MAP)))

        if weather.precip_percent > 0:
            # Rain and snow share one amount; only flip the kind while it is
            # (nearly) dry, or falling rain would turn to snow mid-air.
            if self._get(Addr.PRECIP_CURRENT) < FIXED_ONE // 50:
                self._put(Addr.PRECIP_IS_SNOW, 1 if weather.snow else 0)
        self._ramp(Addr.PRECIP_CURRENT, Addr.PRECIP_TARGET, Addr.PRECIP_STEP,
                   precip_target, seconds)
        self._ramp(Addr.OVERCAST_CURRENT, Addr.OVERCAST_TARGET, Addr.OVERCAST_STEP,
                   overcast_target, seconds)

        reference = self._get(Addr.FOG_REFERENCE)
        if reference >= FOG_MIN:            # 0 before the first map has loaded
            if weather.fog_metres is None:
                fog = self._get(Addr.FOG_MAP)          # the map's own distance
                if fog < FOG_MIN:
                    fog = reference
            else:
                fog = metres_to_fixed(weather.fog_metres)
            fog = max(FOG_MIN, min(fog, reference))   # never further than the map allows
            self._ramp(Addr.FOG_CURRENT, Addr.FOG_TARGET, Addr.FOG_STEP, fog, seconds)

        if weather.cloud_speed is not None:
            self._put(Addr.CLOUD_SPEED_TARGET, max(0, min(255, weather.cloud_speed)) << 10)

    def map_info(self) -> MapInfo:
        if not self._verified:
            self.verify()
        header = self._mem.read(Addr.BMS_HEADER, 0xF8)

        def text(offset: int, size: int) -> str:
            return header[offset:offset + size].split(b"\x00")[0].decode("latin-1").strip()

        terrain = text(0x44, 48).lower()
        if terrain.endswith(".trn"):
            terrain = terrain[:-4]
        return MapInfo(
            title=text(0x04, 32),
            terrain=terrain,
            environment=text(0xDC, 26).lower(),
            climate=struct.unpack_from("<i", header, 0x84)[0],
            weather_type=struct.unpack_from("<i", header, 0xB8)[0],
        )

    def lightning_available(self) -> bool:
        """True only when the patched server is running its lightning cave."""
        if not self._verified:
            self.verify()
        try:
            if self._get(Addr.LIGHTNING_MARKER) != LIGHTNING_MAGIC:
                return False
            return True
        except WeatherError:
            return False

    def flash(self) -> bool:
        """One lightning flash + thunder for everyone.  False without the patch."""
        if not self.lightning_available():
            return False
        self._put(Addr.LIGHTNING_MAILBOX, 1)
        return True

    def quake(self, seconds: int) -> int:
        """Shake the ground.  Returns the seconds actually used."""
        if not self._verified:
            self.verify()
        seconds = max(1, min(QUAKE_MAX_SECONDS, int(seconds)))
        self._put(Addr.QUAKE_TICKS, seconds * QUAKE_TICKS_PER_SECOND)
        return seconds


# ---------------------------------------------------------------------------
# Timed weather: "!storm 10" = hold for ten minutes, then clear
# ---------------------------------------------------------------------------

class WeatherSchedule:
    """Holds one active weather, re-asserts it, and clears it when time is up.

    A map change resets the sky to whatever the map authored, and a mission
    script can move it too, so the chosen weather is written again on every
    ``tick`` rather than once.  Call ``tick`` every few seconds.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._active: Optional[Weather] = None
        self._until: Optional[float] = None

    @property
    def active(self) -> Optional[Weather]:
        return self._active

    def seconds_left(self) -> Optional[int]:
        if self._active is None or self._until is None:
            return None
        return max(0, int(self._until - self._clock()))

    def start(self, controller: WeatherController, weather: Weather,
              minutes: Optional[float]) -> None:
        controller.apply(weather)
        self._active = weather
        self._until = None if not minutes else self._clock() + minutes * 60

    def clear(self, controller: WeatherController, fade_seconds: int = 20) -> None:
        self._active = None
        self._until = None
        controller.apply(replace(CLEAR, fade_seconds=fade_seconds))

    def forget(self) -> None:
        """Drop the active weather without touching the server (it went away)."""
        self._active = None
        self._until = None

    def tick(self, controller: WeatherController) -> Optional[str]:
        """Returns "ended" when a timed weather just ran out, else None."""
        if self._active is None:
            return None
        if self._until is not None and self._clock() >= self._until:
            self.clear(controller)
            return "ended"
        controller.apply(self._active)
        return None


# ---------------------------------------------------------------------------
# Chat commands
# ---------------------------------------------------------------------------

CHAT_COMMANDS = ("!weather", "!storm", "!rain", "!snow", "!blizzard", "!fog",
                 "!drizzle", "!overcast", "!clear", "!quake")

MAX_MINUTES = 240


@dataclass(frozen=True)
class ChatRequest:
    kind: str                      # 'weather' | 'quake' | 'usage'
    weather: Optional[Weather] = None
    name: str = ""
    minutes: Optional[int] = None
    seconds: int = 0
    message: str = ""


def parse_chat_command(cmd: str, args: list[str]) -> ChatRequest:
    """``!storm 10`` / ``!weather storm 10`` / ``!clear`` / ``!quake 5``."""
    cmd = cmd.lower()
    args = list(args)
    if cmd == "!weather":
        if not args:
            return ChatRequest("usage", message="Usage: !weather <" + "|".join(PRESETS) + "> [minutes]")
        cmd = "!" + args.pop(0).lower()
    name = cmd[1:]
    if name == "quake":
        seconds = 5
        if args:
            if not args[0].isdigit():
                return ChatRequest("usage", message=f"Usage: !quake [1-{QUAKE_MAX_SECONDS} seconds]")
            seconds = int(args[0])
        return ChatRequest("quake", seconds=max(1, min(QUAKE_MAX_SECONDS, seconds)))
    if name not in PRESETS:
        return ChatRequest("usage", message="Weather: " + ", ".join(PRESETS))
    minutes = None
    if args:
        if not args[0].isdigit() or not 1 <= int(args[0]) <= MAX_MINUTES:
            return ChatRequest("usage", message=f"Usage: !{name} [1-{MAX_MINUTES} minutes]")
        minutes = int(args[0])
    return ChatRequest("weather", weather=PRESETS[name], name=name, minutes=minutes)


# ---------------------------------------------------------------------------
# Windows process access
# ---------------------------------------------------------------------------

_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_char * 260),
    ]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
    k.Process32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32))
    k.Process32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32))
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    k.ReadProcessMemory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    k.WriteProcessMemory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    return k


def find_process_ids(image_name: str = SERVER_IMAGE_NAME) -> list[int]:
    if sys.platform != "win32":
        return []
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    found = []
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        ok = k.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode("mbcs", "replace").lower() == image_name.lower():
                found.append(int(entry.th32ProcessID))
            ok = k.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return found


class ProcessMemory:
    """``Memory`` over a live process.  ``writable=False`` cannot write at all."""

    def __init__(self, pid: int, writable: bool = True):
        self.pid = pid
        self._k = _kernel32()
        access = _PROCESS_VM_READ | _PROCESS_QUERY_LIMITED_INFORMATION
        if writable:
            access |= _PROCESS_VM_WRITE | _PROCESS_VM_OPERATION
        self._handle = self._k.OpenProcess(access, False, pid)
        if not self._handle:
            raise WeatherError(
                "Windows would not let WolfRAT open the server process. If the "
                "server runs as administrator, run WolfRAT as administrator too."
            )

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not self._k.ReadProcessMemory(self._handle, ctypes.c_void_p(address), buffer,
                                         size, ctypes.byref(done)) or done.value != size:
            raise WeatherError("Could not read the server's memory (has it closed?).")
        return buffer.raw

    def write(self, address: int, data: bytes) -> None:
        done = ctypes.c_size_t()
        if not self._k.WriteProcessMemory(self._handle, ctypes.c_void_p(address), data,
                                          len(data), ctypes.byref(done)) or done.value != len(data):
            raise WeatherError("Could not write to the server's memory (has it closed?).")

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None


def attach_local_server(writable: bool = True) -> tuple[WeatherController, ProcessMemory]:
    """Find the dedicated server on this PC.  Raises WeatherError with the reason."""
    if sys.platform != "win32":
        raise WeatherError("Weather control needs Windows.")
    pids = find_process_ids()
    if not pids:
        raise WeatherError(
            "No jointops.exe is running on this PC. Weather works when WolfRAT "
            "runs on the same machine as the game server."
        )
    last: Optional[WeatherError] = None
    for pid in pids:
        try:
            memory = ProcessMemory(pid, writable=writable)
        except WeatherError as exc:
            last = exc
            continue
        controller = WeatherController(memory)
        try:
            controller.verify()
            return controller, memory
        except WeatherError as exc:
            last = exc
            memory.close()
    raise last or WeatherError("No usable server process found.")
