"""Compatibility entry point for the packaged WolfRAT application.

The maintained runtime lives in :mod:`wolfrat.app`.  Keeping this module as a
thin import shim prevents the legacy top-level copy from becoming a second
protocol caller.
"""

from wolfrat.app import *  # noqa: F401,F403
from wolfrat.app import main


if __name__ == "__main__":
    main()
