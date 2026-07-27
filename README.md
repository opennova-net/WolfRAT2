# WolfRAT2

**Modern Remote Admin Tool for Joint Operations: Typhoon Rising**

A complete rewrite of the original WolfRAT v0.95 (2005) — rebuilt from scratch in Python 3 + PyQt6 with a dark theme UI, web dashboard, and features the original never had.

![Version](https://img.shields.io/badge/version-2.4.11-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## What Is This?

WolfRAT2 connects to a Joint Operations server's configured admin port
(commonly TCP 4000) and gives server admins a full GUI for managing players,
maps, chat, and server settings. No more typing commands into a console —
point, click, done.

"RAT" = **R**emote **A**dmin **T**ool. Not a backdoor. Just a better way to run your server.

## Features

### 🖥️ Desktop GUI (11 Tabs)

| Tab | What It Does |
|-----|-------------|
| **Server** | Connection manager, saved servers, server status |
| **Console** | Raw command console with send/receive log |
| **Players** | Live player list with admin actions (Warn, Punt, Ban, Kill, Swap, Zero Score) |
| **Missions** | Map browser, one-click map switching, next map, mission presets |
| **Settings** | Server config — auto-balance, team switching, vote percent, respawn delay |
| **Chat Bot** | Live chat monitor, send chat, bad word filter, auto-team-swap trigger |
| **Messages** | First blood & killing spree announcement templates with color-coded text |
| **Spree** | Kill streak tracker — auto-announces 3/5/7/10 kill streaks with gold heat gradient |
| **Mods** | TAC mod management and configuration |
| **Map Voting** | In-game map voting system |
| **Weapons** | Weapon loadout editor and configuration |

### 🌐 Web Dashboard

Mobile-friendly web UI for remote administration from your phone or tablet. Runs on a configurable LAN port, token-protected. Same commands as the desktop UI.

### 🎨 Dark Theme

Catppuccin Mocha-inspired dark theme with gold accents. Easy on the eyes during long sessions.

### 📦 Single Executable

Packages into a single `.exe` via PyInstaller — no Python install needed on the target machine.

## Getting the Executable

Download the latest `WolfRAT2.exe` from [Releases](https://github.com/BadgerLove/WolfRAT2/releases).

## Building From Source

```bash
# Clone the repo
git clone https://github.com/BadgerLove/WolfRAT2.git
cd WolfRAT2

# Install dependencies
pip install PyQt6 pyinstaller aiohttp

# Build
build.bat
```

Output: `dist\WolfRAT2.exe`

## Running From Source

```bash
pip install PyQt6 aiohttp
python main.py
```

## Requirements

- **Python 3.11+** (for building/running from source)
- **PyQt6** — GUI framework
- **aiohttp** — web dashboard server
- **PyInstaller** — for building the executable

## How It Works

WolfRAT2 connects to the Joint Operations admin interface over TCP (port 4000 by
convention; the server reads `remote_admin_port` from `game.cfg`) using the same
protocol as the original WolfRAT v0.95 (2005).

Login is a challenge/response exchange: the server sends a framed 32-byte
challenge plus NUL, and the client returns an encrypted 65-byte response holding
the username and password fields from `admin.cfg`. Both are checked. Everything
after login is plaintext ASCII.

One mandatory session owns framing, authentication, request ordering, and the
authoritative snapshots. Every desktop and web feature uses typed semantic
operations through that session; background reads are coalesced and cannot race
interactive commands. Semantic mutations retain the workflow gate through their
confirmation readback, so a later mutation cannot act on an identity that the
first operation shifted. Mutations require their exact retail acknowledgement
and, where available, an independent state readback before WolfRAT reports them
as verified.

The connection is kept persistent. Teardown uses an abortive TCP close because
the retail server fails to retire orderly-FIN admin clients correctly; repeated
orderly reconnects can corrupt its retained client table. Accepted raw mutations
conservatively invalidate typed snapshots before any queued command can reuse an
old player, mission, or weapon identity.

The wire contract and command catalog were cross-checked against retail
`CAdminServer` behavior in IDA and the OpenNova implementation.

## History

The original **WolfRAT** was built in 2005 by WolfGaming using MFC70 (Visual C++ .NET). It served the JO community well but is long dead — won't run on modern Windows, source is gone.

**WolfRAT2** is a from-scratch rebuild. Same protocol, same purpose, modern everything.

## License

MIT — do what you want with it.

## Credits

- **WolfGaming** — original WolfRAT v0.95 (2005)
- **BadgerLove** — WolfRAT2 rewrite
- **NovaLogic** — Joint Operations: Typhoon Rising (2004)
