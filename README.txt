WolfRAT 2.4.11 — Joint Operations Server Admin Tool
==================================================

A modern replacement for the original WolfRAT v0.95 (2005).

Features:
  - Server connection manager with saved servers
  - Live player list with admin actions (Warn, Punt, Ban, Kill, Swap, Zero)
  - Mission/map browser with one-click map switching
  - Server settings manager (auto-balance, team switching, votes)
  - Chat bot with bad word filtering and auto-team-swap trigger
  - Modern dark theme UI
  - Packaged as a single Windows .exe

Building from source:
  1. Run: build.bat
  2. Output: dist\WolfRAT2.exe

Running from source:
  1. venv\Scripts\activate
  2. python main.py

Requirements:
  - Python 3.11+
  - PyQt6
  - pyinstaller (for building .exe)

Protocol:
  Connects to the server's configured admin port (commonly TCP 4000).
  Uses 8-byte framed packets and the retail login challenge/response.
  Both username and password fields from admin.cfg are authenticated.
  All active features share one ordered session and typed command facade.
  Mutations require their exact retail acknowledgement and, where the
  server exposes the result, an authoritative readback before WolfRAT
  reports the change as verified.

Based on reverse engineering of WolfRAT v0.95 binary.
