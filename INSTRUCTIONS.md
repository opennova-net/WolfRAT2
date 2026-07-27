# WolfRAT 2.4.11 — Instructions

## What Is This?

WolfRAT 2.4.11 is a remote admin tool for **Joint Operations: Typhoon Rising** dedicated servers. It connects to the server's configured admin port (commonly 4000) and lets you manage players, maps, settings, and chat from a Windows GUI.

It replaces the original WolfRAT v0.95 from 2005.

---

## Changelog

### 2.4.11 — protocol correctness pass

Cross-checked the client against the retail server's `CAdminServer` implementation
and fixed everywhere the two disagreed.

- **Chat limit lowered from 69 to 62 characters.** The real ceiling is a 64-byte
  stack buffer that the server copies into without a length check, so the old
  limit overran it by up to six bytes on every long message. See "Chat Message
  Limit" below.
- **Replaced heuristic packet resynchronisation with exact framing.** The old
  parser searched for a bare `0D 0A`, which can also occur inside payloads.
  The session now reads the full 8-byte header and declared body, supports
  fragmented and coalesced TCP reads, and closes the connection on malformed
  magic or lengths instead of guessing where the next response begins.
- **Login now fails closed.** A short or malformed challenge used to skip the
  login exchange entirely and still report a successful connection.
- **Fixed a socket leak** on every failed connection attempt, which accumulated
  once per retry while auto-reconnect was running.
- **`.npz` maps are now recognised.** The client filtered for a `.npaj`
  extension that the server never produces, and ignored `.npz`, which it does.
- **Length caps on values the server stores in fixed buffers**: passwords (16),
  server name (27), and `CMD` argument strings (98).
- **Removed the bare `mission <filename>` map switch**, which was never a valid
  command — the server only echoed its usage string in response.
- **The Console tab and the web dashboard's `/api/command` no longer bypass the
  length limits.** They passed raw text straight to the server, so a long
  `chat send` or `cmd` typed by hand could overrun a buffer that the checked
  helpers were carefully avoiding. The limits now apply on every path.
- **Ping restriction settings work.** They were sent as `pingMinCheck`/`pingMin`
  rather than `DoMinPingCheck`/`MinPing`, so every change was rejected and the
  four controls never showed the server's values either.
- **Settings keys are canonicalised.** Sliders sent camelCase guesses; `KOTHLimit`
  in particular is not reachable from any capitalisation rule.
- **`MISSION SETNEXT` index fixes.** Two map-vote paths tested the wrong
  sentinel (`_get_server_index` returns `-1`, not `None`) and sent
  `MISSION SETNEXT -1`; the "Queue Next" button and the `!remove` chat command
  used a list position instead of the index the server actually printed.
- **The web dashboard validates player ids.** A non-numeric id parses to 0
  server-side, which is the host — and `KILL`/`SWAPTEAM`/`ZEROSCORE` have no
  host guard.
- **Anti-spam cooldown no longer drops the rest of the chat batch** — it
  returned out of the per-message loop instead of skipping the one player.

### 2.1.1 (June 2026)
- **FIXED: Chat duplicate filter blocking repeated commands.** The old filter used raw text matching, so `!switch` would only work once per session. Replaced with sequence overlap detection — commands now work every time they're typed.
- `protocol.py`: Changed from set-based dedup to chunk overlap detection.
- `app.py`: Tracks messages by unique ID instead of raw text.

### 2.1 (June 2026)
- Killing spree announcer (3/5/7/10 kill streaks)
- Recurring message timers (30/45/60 min intervals)
- Auto-reconnect on server drop (30s retry delay)
- Ghost connection timeout (60s)
- Custom WolfRAT icon

---

## Building the Executable

### Requirements
- Windows 10/11
- Python 3.11–3.14 installed
- Internet connection (for pip)
- Node.js 24 with npm 11 only when running browser-client validation

### Build Steps
1. Open PowerShell
2. Navigate to the WolfRAT2 repository:
   ```
   cd "C:\path\to\WolfRAT2"
   ```
3. Run the build script:
   ```
   .\build.bat
   ```
4. The executable will be at `dist\WolfRAT2.exe`

### First-Time Setup
`build.bat` installs the canonical development dependency group from
`pyproject.toml` before invoking PyInstaller. To prepare the same environment
without building, run:

```
python -m pip install --editable ".[dev]"
```

Do not install PyQt6, aiohttp, PyInstaller, or test tools individually.

---

## Earlier 2.1 Changes (June 7, 2026)
- **Auto-Reconnect**: Added a 30s reconnect delay to keep the tool hooked to the server automatically.
- **Killing Spree Announcer**: Automatically tracks player streaks (3, 5, 7, 10 kills without dying) and broadcasts custom rampage messages to the server chat.
- **Recurring Timers**: Added 30, 45, and 60-minute interval options for recurring chat messages.
- **Custom Icon**: Added a sleek black-and-gold rat/wolf silhouette icon to the executable.

---

## Running WolfRAT 2.4.11

### From the Executable
Double-click `dist\WolfRAT2.exe`

### From Source
```
cd WolfRAT2
python -m pip install --editable .
python main.py
```

### Developer Validation

Python runtime, test, lint, package-build, and executable-build dependencies are
declared in `pyproject.toml`. The Node toolchain and browser-client commands are
declared in `package.json`; there are currently no third-party npm packages.

```
python -m pip install --editable ".[dev]"
python -m ruff check .
python -m pytest -q
npm run validate
python -m build
```

CI builds the wheel and source distribution on Linux and Windows, installs the
wheel into a clean environment, and builds and smoke-tests `WolfRAT2.exe`. The
Windows artifact includes the executable, SHA-256 checksum, MIT license, and
machine-readable smoke result.

### Protected Retail Conformance

The live retail workflow must use a GitHub environment named
`retail-conformance`. Configure required reviewers, prevent self-review, and
restrict deployments to the default branch. Store only these environment
secrets there:

- `WOLFRAT_HOST`
- `WOLFRAT_PORT`
- `WOLFRAT_USERNAME`
- `WOLFRAT_PASSWORD`

The dedicated runner must carry the `self-hosted`, `windows`, `x64`, and
`jotac-retail` labels and use a current GitHub Actions runner. The workflow runs
the standard-library-only conformance module directly, so checkout and tool
setup never receive retail credentials and no packages are installed on the
live host.

---

## Server Connection

### Prerequisites
- JO dedicated server running (`Jointops.exe`)
- Admin configured in `admin.cfg` (three whitespace-separated fields:
  `username password access_level`)
- Server listening on the `remote_admin_port` configured in `game.cfg`
- Server needs ~115 seconds to fully initialize after starting

### Connecting
1. Enter the server IP address
2. Port: the configured admin port (commonly 4000)
3. Username: from your `admin.cfg`
4. Password: from your `admin.cfg`
5. Click **Connect**

### Status Bar
- **Green bar** = connected, pulsing
- **Red bar** = disconnected
- Shows current map and game mode
- Shows feedback when settings are changed

---

## Tabs

### 🖥 Server
- Connection settings (IP, port, username, password)
- Server status (name, game mode, player count)
- Save/load server profiles
- Connection log

### 👥 Players
- Player list (excludes host ID 0)
- Shows: Name, Team (Joint Ops / Rebels), Class, Kills, Deaths, Ping
- Admin actions: Warn, Punt (Kick), Ban, Kill, Swap Team, Zero Score
- Custom message field for warns/kicks
- Team balance display

### 🗺 Missions
- **Map Rotation** (left side): Maps currently on the server
  - Drag-and-drop to reorder
  - Right-click for context menu:
    - Switch to map
    - Play Once / Play Twice
    - Remove from rotation
  - Remove, Move Up, Move Down buttons
- **Available Maps** (right side): All maps on the server
  - Double-click to add to rotation
  - Search and filter by game mode
- **Set Next Map**: Skip to next map in rotation
- **Presets**: Save/load rotation presets

### ⚙ Settings
- **Game Settings**: Checkboxes for Friendly Fire, Auto Rebalance, Tracers, Fat Bullets, One Shot Kills, Tags, Friendly Tags, Allow Team Switching
- **Limits & Timers**: Sliders for Kill Limit, Time Limit, King of the Hill, Armoury Timer, Max Kills, Vote Kick Percent, Team Switch Interval
- **Time of Day**: Set hour (0100–2400) and game pass duration (5–120 min)
- **Passwords & Title**: Dropdown to select Server Password / Side A Password / Side B Password / Server Title, with Set and Clear buttons
- **Custom Command**: Send raw commands to the server
- All settings auto-apply on change (no Apply button needed)
- Feedback shown in status bar when settings change

### 💬 Chat Bot
- Live chat display
- Send messages to all players
- Recurring message system (auto-broadcasts messages on a timer)
- Welcome message for new players
- Auto team swap on chat trigger word

### 📢 Messages
- Send server-wide messages
- Configure recurring messages

---

## Chat Message Limit

JO's chat limit is **62 characters**, and it is not enforced by the server.

`CHAT SEND` rejoins the command's tokens with a space after every token — including
the last — and hands the result to a 64-byte stack buffer that is copied into
without any length check. A 63-character message therefore overruns that buffer
rather than being truncated, which can corrupt or crash the game server.

WolfRAT caps chat at 62 characters in both the GUI and the command catalog.
Direct messages beyond the limit are rejected; multi-part announcements are
split into safe messages. An earlier build used 69 and was over the line.

---

## Map Rotation

### How It Works
- Maps are stored on the JO server in `game.cfg`
- WolfRAT reads the server's current rotation via `mission list`
- You can add/remove/reorder maps through the GUI
- Changes are sent to the server immediately via `mission add` / `mission remove` / `mission clear`

### Commands Used
- `mission list` — Get current rotation from server
- `mission available` — Get all available maps
- `mission add <filename>` — Add map to rotation
- `mission remove <index>` — Remove map from rotation
- `mission clear` — Clear entire rotation
- `mission setnext <index>` — Choose which map plays next
- `mission cycle` — End the round and advance the rotation

There is no "switch straight to this filename" command. `MISSION` accepts only
`LIST`, `AVAILABLE`, `ADD`, `REMOVE`, `CLEAR`, `CYCLE` and `SETNEXT`; anything
else just makes the server echo its usage string. Switching maps means
`MISSION SETNEXT <index>` followed by a cycle.

Maps are `.bms`, `.npj` or `.npz` — those are the only extensions the server's
mission scanner picks up.

### Presets
- Rotation is auto-saved when you add/remove maps
- Load saved presets from the dropdown
- Presets are stored in `%LOCALAPPDATA%\WolfRAT2\wolfrat_rotations.json`
  on Windows.

---

## Protocol

WolfRAT talks to JO's admin server over TCP. Port 4000 is the community
convention, not a default — the server reads `remote_admin_port` from `game.cfg`
and does not listen at all when it is unset.

Only the login exchange is encrypted. Every command and response after it is
plaintext ASCII.

### Packet Format
- 8-byte header: `[magic(4) = 00 00 0D 0A] [total length(4), little-endian]`
- Payload: ASCII command string, null-terminated

The length counts the whole packet, including the 8-byte header and the trailing
NUL. The `0D 0A` is part of the magic value, **not** a line terminator — CR/LF
also appears inside payloads, so it cannot be used to find packet boundaries.

The server's receive buffer is a fixed 1024 bytes and it processes exactly one
packet per read, discarding whatever else arrived with it. Commands must not be
pipelined, and no single packet may exceed 1024 bytes.

All active features share one `RetailAdminSession`. It owns the authenticated
socket, permits one outstanding command, correlates replies using the command's
known completion policy, prioritises interactive work over coalesced polling,
and invalidates stale player, map, and weapon identities before mutation.
Desktop, web, bots, votes, workflows, and polling all use the typed
`ServerManager` facade. Only the explicitly labelled raw console accepts
untyped input, and it still applies the retail grammar and buffer guards.

Semantic mutation workflows stay serialized until their authoritative
confirmation finishes; serialization is bound to the connection that accepted
the work, so queued work cannot cross a disconnect/reconnect boundary. An
accepted raw mutation clears authoritative identities before the next queued
typed operation validates its target.

The session deliberately uses an abortive TCP close rather than an orderly FIN.
Retail treats `recv() == 0` as a successful admin read and retains that client;
after enough orderly reconnects its client-table growth path corrupts the
retained slots. A reset reaches retail's working socket-error cleanup path.

For mutations, an `OK` packet is not treated as generic proof. WolfRAT checks
the command-specific retail acknowledgement and, where the server exposes the
state, performs an authoritative readback. The UI distinguishes an accepted
command from a verified state change.

### Login Flow
1. Connect TCP to the configured admin port
2. Server sends a 41-byte frame (8-byte header, 32-byte challenge, NUL)
3. Client sends a 73-byte frame (8-byte header, 65-byte encrypted response)
4. Server responds with "OK - User: <username> successfully logged in."

### Authentication transform
The retail-compatible challenge transform is implemented in
`wolfrat/admin_session.py` and is covered by byte-for-byte test vectors. It
encodes the configured username and password into the exact 65-byte response;
neither credential field is optional.

---

## Troubleshooting

### "Login failed"
- Check username/password in `admin.cfg`
- Server must be fully initialized (~115 seconds after start)
- Check `admin_log.txt` on the server

### No players showing
- Make sure you're connected (green bar at bottom)
- Click Refresh on the Server tab
- Check if the server has players connected

### Map switching doesn't work
- Refresh both the available-map catalog and current rotation.
- Add the map to the rotation if it is not already present.
- Use **Queue Next** to select it, or **Run Selected** to perform the verified
  `SETNEXT` then `CYCLE` workflow.
- Check the operation result: a retail rejection or failed readback is shown
  instead of an optimistic success message.

### Chat not showing
- WolfRAT polls for chat every 5 seconds
- Chat messages are fetched via `chat get`
- Check the connection log for errors

### Build fails
- Make sure Python 3.11–3.14 is installed
- Run `python -m pip install --editable ".[dev]"` from the repository root
- Use `python -m PyInstaller` instead of `pyinstaller` if PATH issues

### Browser-client validation is skipped or unavailable
- Use Node.js 24 and npm 11
- Run `npm run validate` from the repository root
- Do not install ad-hoc npm packages; the validation scripts use Node built-ins

---

## File Locations

### On the Build Machine
- `main.py` — Entry point
- `wolfrat/app.py` — Main GUI application
- `wolfrat/admin_commands.py` — Typed retail command catalog
- `wolfrat/admin_session.py` — Framing, login, scheduling, and snapshots
- `wolfrat/protocol.py` — Semantic application facade
- `pyproject.toml` — Canonical Python dependencies and tool configuration
- `package.json` — Canonical Node toolchain and browser-client commands
- `WolfRAT2.spec` — Reproducible PyInstaller build definition
- `build.bat` — Build script
- `dist/WolfRAT2.exe` — Built executable

### Runtime Files

Mutable state is stored in `%LOCALAPPDATA%\WolfRAT2` on Windows, not beside the
executable. This includes saved server profiles, map rotations, chat, stats,
moderation data, web settings, telemetry identity, and crash diagnostics.
`--data-dir <directory>` provides an explicit override.

### JO Server Files
- `C:\GAMES\JOTAC\Game\JO\admin.cfg` — Admin credentials
- `C:\GAMES\JOTAC\Game\JO\game.cfg` — Server configuration
- `C:\GAMES\JOTAC\Game\JO\matchlogs\` — Match history

---

## Known Limitations

1. **Play count (x2)** — Display only. JO server doesn't have a `setrepeat` command. The actual repeat behavior is controlled by the server's rotation config.
2. **No real-time chat push** — Chat is polled every 5 seconds, not streamed. There may be a delay.
3. **Side passwords** — May not be supported by all JO server versions.
4. **No admin-user or ban-list management** — `ADMINUSER` and `BANLIST` exist as
   verbs but every subcommand returns "not yet implemented". Bans are added with
   `PLAYER BAN` and edited on the server in `banlist.txt`.
5. **`CMD` results are invisible** — the server replies "OK - Command executed."
   whether or not the console command existed, and never returns its output. A
   custom command that silently did nothing is indistinguishable from one that
   worked.
6. **The server title cannot be cleared** — `SET` with no value only clears
   the three password fields; for any other key the server returns its usage
   string. WolfRAT therefore does not offer a title-clear action.
7. **Value length caps** — passwords are capped at 16 characters and the server
    title at 27, because the server stores them in fixed buffers it does not
    bounds-check when printing them back.

---

## Theme

WolfRAT 2.4.11 uses an OLED Black + Yellow theme:
- Background: pure black (#000000)
- Primary text: gold (#e8c840)
- Accents: dark yellow (#6a6a20, #8a7a20)
- Danger: red (#ff6040)
- Success: green (#50ff50)
- Buttons: 3D raised/depressed effect with border inversion on press

---

*WolfRAT 2.4.11 — Built for the Joint Operations community.*
*Original WolfRAT v0.95 (2005) by the Archon team.*
