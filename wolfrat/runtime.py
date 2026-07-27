"""Desktop lifecycle configuration and mutable-state path ownership."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys


@dataclass(frozen=True, slots=True)
class DesktopRuntime:
    """Own the desktop application's state directory and startup permissions."""

    data_dir: Path
    telemetry_enabled: bool
    auto_connect_enabled: bool
    web_autostart_enabled: bool
    audio_enabled: bool

    def __post_init__(self) -> None:
        data_dir = self.data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "data_dir", data_dir)

    @classmethod
    def production(cls, data_dir: str | Path | None = None) -> "DesktopRuntime":
        """Use writable per-user application state and normal startup behavior."""

        if data_dir is None:
            if os.name == "nt":
                state_root = (
                    os.environ.get("LOCALAPPDATA")
                    or os.environ.get("APPDATA")
                )
                data_dir = (
                    Path(state_root) / "WolfRAT2"
                    if state_root
                    else Path.home() / "AppData" / "Local" / "WolfRAT2"
                )
            elif sys.platform == "darwin":
                data_dir = (
                    Path.home()
                    / "Library"
                    / "Application Support"
                    / "WolfRAT2"
                )
            else:
                data_dir = (
                    Path(
                        os.environ.get(
                            "XDG_STATE_HOME",
                            Path.home() / ".local" / "state",
                        )
                    )
                    / "wolfrat2"
                )
        return cls(
            data_dir=Path(data_dir),
            telemetry_enabled=True,
            auto_connect_enabled=True,
            web_autostart_enabled=True,
            audio_enabled=True,
        )

    @classmethod
    def isolated(cls, data_dir: str | Path) -> "DesktopRuntime":
        """Create a deterministic runtime with every external startup disabled."""

        return cls(
            data_dir=Path(data_dir),
            telemetry_enabled=False,
            auto_connect_enabled=False,
            web_autostart_enabled=False,
            audio_enabled=False,
        )

    def path(self, filename: str) -> Path:
        """Resolve one application-owned state file inside ``data_dir``."""

        candidate = Path(filename)
        if (
            not filename
            or candidate.is_absolute()
            or candidate.name != filename
            or filename in {".", ".."}
        ):
            raise ValueError("runtime paths require a simple filename")
        return self.data_dir / filename


@dataclass(frozen=True, slots=True)
class DesktopLaunch:
    """Parsed WolfRAT arguments, separated from arguments owned by Qt."""

    runtime: DesktopRuntime
    smoke_test: bool
    qt_argv: tuple[str, ...]


def parse_launch_args(argv: list[str] | tuple[str, ...]) -> DesktopLaunch:
    """Parse WolfRAT flags while preserving unrecognized Qt flags."""

    if not argv:
        argv = ("wolfrat",)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--data-dir")
    options, qt_arguments = parser.parse_known_args(list(argv[1:]))

    if options.smoke_test:
        if not options.data_dir:
            raise ValueError("--smoke-test requires --data-dir")
        runtime = DesktopRuntime.isolated(options.data_dir)
    else:
        runtime = DesktopRuntime.production(options.data_dir)

    return DesktopLaunch(
        runtime=runtime,
        smoke_test=options.smoke_test,
        qt_argv=(str(argv[0]), *qt_arguments),
    )
