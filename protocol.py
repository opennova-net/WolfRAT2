"""Compatibility imports for the packaged WolfRAT protocol facade.

All maintained protocol behavior lives under :mod:`wolfrat`; this file exists
only for older launchers and extensions which imported ``protocol`` directly.
"""

from wolfrat.protocol import *  # noqa: F401,F403
