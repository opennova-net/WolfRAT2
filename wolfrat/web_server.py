"""
WolfRAT v2.4 — Embedded Web Server
Provides a mobile-friendly web UI for remote server administration.
All state reads from the existing ServerManager instance.
All commands go through the same protocol.py path as the desktop UI.
"""

import asyncio
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
import os
import re
import secrets
import sys
import threading
import time
import logging
from collections.abc import Mapping

from aiohttp import web

from wolfrat.protocol import wire_log

# Suppress aiohttp access logs (noisy GET /api/status every 5s)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

_SECRET_SETTING_NAMES = frozenset({
    'serverpassword',
    'sideapassword',
    'sidebpassword',
    'gamepass',
    'bluepass',
    'redpass',
})


class AmbiguousMissionTarget(ValueError):
    """A filename names more than one authoritative queue entry."""


class StaleMissionTarget(ValueError):
    """A posted mission identity no longer matches the queue snapshot."""


class StalePlayerTarget(ValueError):
    """A posted player identity no longer matches the roster snapshot."""


def _get_template_path():
    """Get path to the web templates directory."""
    if getattr(sys, 'frozen', False):
        # PyInstaller --onefile extracts to sys._MEIPASS
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        return os.path.join(meipass, 'wolfrat', 'web_templates')
    return os.path.join(os.path.dirname(__file__), 'web_templates')


def generate_token():
    """Generate a cryptographically secure access token."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class WebServerStartResult:
    """The observable outcome of binding one web-server lifecycle."""

    ok: bool
    error: str | None = None


class WolfWebServer:
    """Embedded web server for WolfRAT mobile access."""

    def __init__(self, server_manager, host='0.0.0.0', port=8070):
        self.sm = server_manager
        self._running = False
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._lifecycle_id = 0
        self._stop_requested = None
        self._start_future = None
        self._auth_lock = threading.Lock()
        self._web_username = "admin"
        self._web_token = None
        self.host = host
        self.port = port
        self._ws_clients = set()
        self._loop = None
        self._thread = None
        self._runner = None
        self._last_broadcast = 0
        self._broadcast_interval = 1.0

        self._template_path = _get_template_path()
        wire_log(f"WEB SERVER: template path = {self._template_path} (exists={os.path.exists(self._template_path)})")

        # Auth state — separate from JO server credentials
        self._login_ips = {}  # {ip: last_login_timestamp}
        self.on_login = None  # callback(ip, timestamp) for persistence

    def _create_application(self):
        """Build one aiohttp application for one event-loop lifecycle."""
        application = web.Application()

        # Routes (public)
        application.router.add_get('/', self._handle_index)
        application.router.add_post('/api/auth', self._handle_auth)
        application.router.add_get('/static/style.css', self._handle_css)
        application.router.add_get('/static/app.js', self._handle_js)

        # Routes (protected)
        application.router.add_get('/api/status', self._handle_status)
        application.router.add_get('/api/players', self._handle_players)
        application.router.add_get('/api/chat', self._handle_chat)
        application.router.add_get('/api/maps', self._handle_maps)
        application.router.add_get('/api/settings', self._handle_settings)
        application.router.add_get('/api/login-ips', self._handle_login_ips)
        application.router.add_post('/api/action', self._handle_quick_action)
        application.router.add_post('/api/command', self._handle_command)
        application.router.add_post('/api/chat/send', self._handle_send_chat)
        application.router.add_post('/api/player/action', self._handle_player_action)
        application.router.add_post('/api/map/switch', self._handle_map_switch)
        application.router.add_get('/ws', self._handle_websocket)
        return application

    @property
    def is_running(self):
        with self._state_lock:
            return self._running

    @property
    def application(self):
        """Return the aiohttp application for an external HTTP host."""
        return self._create_application()

    def set_auth(self, username, token):
        """Set the web admin credentials (called from WebAdminTab)."""
        with self._auth_lock:
            self._web_username = username or "admin"
            self._web_token = token

    def start(self):
        """Start the web server in a background thread."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._thread is not None and self._thread.is_alive():
                    return self._start_future

                self._lifecycle_id += 1
                lifecycle_id = self._lifecycle_id
                stop_requested = threading.Event()
                start_future = Future()
                host = self.host
                port = self.port
                thread = threading.Thread(
                    target=self._run,
                    args=(
                        lifecycle_id,
                        stop_requested,
                        start_future,
                        host,
                        port,
                    ),
                    daemon=True,
                    name="WolfWeb",
                )
                self._running = False
                self._loop = None
                self._runner = None
                self._stop_requested = stop_requested
                self._start_future = start_future
                self._thread = thread
            thread.start()
            return start_future

    def stop(self):
        """Stop the web server completely. Port is released, thread exits."""
        with self._lifecycle_lock:
            with self._state_lock:
                thread = self._thread
                if thread is None:
                    self._running = False
                    return
                if thread is threading.current_thread():
                    raise RuntimeError("WolfWeb cannot join its own worker thread")
                was_running = self._running
                self._running = False
                loop = self._loop
                stop_requested = self._stop_requested
                start_future = self._start_future
                if stop_requested is not None:
                    stop_requested.set()
            if start_future is not None and not start_future.done():
                self._resolve_start(
                    start_future,
                    WebServerStartResult(
                        ok=False,
                        error="Web server startup was stopped",
                    ),
                )

            if (
                was_running
                and loop
                and not loop.is_closed()
                and loop.is_running()
            ):
                async def _shutdown():
                    for ws in list(self._ws_clients):
                        try:
                            await ws.close()
                        except Exception:
                            pass
                    self._ws_clients.clear()

                shutdown = _shutdown()
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        shutdown,
                        loop,
                    )
                except RuntimeError:
                    shutdown.close()
                else:
                    try:
                        future.result(timeout=3)
                    except Exception:
                        pass
                    try:
                        loop.call_soon_threadsafe(loop.stop)
                    except RuntimeError:
                        pass

            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError("WolfWeb worker did not stop")

            with self._state_lock:
                if self._thread is thread:
                    self._loop = None
                    self._runner = None
                    self._stop_requested = None
                    self._thread = None
            wire_log("WEB SERVER: stopped, port released")

    def restart(self):
        """Restart the web server (e.g. after port change)."""
        with self._lifecycle_lock:
            self.stop()
            return self.start()

    @staticmethod
    def _resolve_start(future, result):
        try:
            future.set_result(result)
        except InvalidStateError:
            pass

    def _run(
        self,
        lifecycle_id,
        stop_requested,
        start_future,
        host,
        port,
    ):
        """Run the aiohttp event loop in a background thread."""
        loop = asyncio.new_event_loop()
        with self._state_lock:
            if lifecycle_id != self._lifecycle_id:
                loop.close()
                return
            self._loop = loop
        asyncio.set_event_loop(loop)
        runner = None
        try:
            if stop_requested.is_set():
                return
            runner = web.AppRunner(self._create_application())
            with self._state_lock:
                if lifecycle_id != self._lifecycle_id:
                    return
                self._runner = runner
            loop.run_until_complete(runner.setup())
            if stop_requested.is_set():
                return
            site = web.TCPSite(runner, host, port)
            loop.run_until_complete(site.start())
            if stop_requested.is_set():
                return
            with self._state_lock:
                if (
                    lifecycle_id != self._lifecycle_id
                    or stop_requested.is_set()
                ):
                    return
                self._running = True
            wire_log(f"WEB SERVER: listening on {host}:{port}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=True),
            )
            if not stop_requested.is_set():
                loop.run_forever()
        except OSError as e:
            error = f"Could not listen on {host}:{port}: {e}"
            wire_log(f"WEB SERVER ERROR: {error}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=False, error=error),
            )
        except Exception as e:
            error = f"Web server startup failed: {e}"
            wire_log(f"WEB SERVER ERROR: {error}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=False, error=error),
            )
        finally:
            if not start_future.done():
                self._resolve_start(
                    start_future,
                    WebServerStartResult(
                        ok=False,
                        error=(
                            "Web server startup was stopped"
                            if stop_requested.is_set()
                            else "Web server exited before becoming ready"
                        ),
                    ),
                )
            try:
                if runner is not None:
                    loop.run_until_complete(runner.cleanup())
            except Exception as error:
                wire_log(f"WEB SERVER CLEANUP ERROR: {error}")
            finally:
                with self._state_lock:
                    if lifecycle_id == self._lifecycle_id:
                        if self._runner is runner:
                            self._runner = None
                        if self._loop is loop:
                            self._loop = None
                        if self._thread is threading.current_thread():
                            self._thread = None
                        if self._stop_requested is stop_requested:
                            self._stop_requested = None
                        self._running = False
                loop.close()

    def get_login_ips(self):
        """Return list of unique IPs with their last login time."""
        with self._auth_lock:
            return [{'ip': ip, 'last_login': ts} for ip, ts in sorted(
                self._login_ips.items(), key=lambda x: x[1], reverse=True)]

    def get_login_ips_dict(self):
        """Return raw dict of {ip: timestamp} for persistence."""
        with self._auth_lock:
            return dict(self._login_ips)

    def load_login_ips(self, ips_dict):
        """Load persisted login IPs from config."""
        with self._auth_lock:
            self._login_ips = {ip: float(ts) for ip, ts in ips_dict.items()}

    def _check_auth(self, request):
        """Check if request has valid auth token. Returns True if authorized."""
        with self._auth_lock:
            if not self._web_token:
                return False  # No token set = no access

        # Check Authorization header
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:].strip()
            with self._auth_lock:
                if self._web_token and secrets.compare_digest(token, self._web_token):
                    return True
        # Check query param (for WebSocket)
        token = request.query.get('token', '')
        if token:
            with self._auth_lock:
                if self._web_token and secrets.compare_digest(token, self._web_token):
                    return True
        return False

    def broadcast_state(self):
        """Called by the desktop app when state changes. Pushes to all WebSocket clients.
        Throttled to avoid flooding — max once per _broadcast_interval seconds."""
        if not self._running or not self._loop or not self._ws_clients:
            return
        now = time.time()
        if now - self._last_broadcast < self._broadcast_interval:
            return
        self._last_broadcast = now
        state = self._build_state()
        asyncio.run_coroutine_threadsafe(self._broadcast(json.dumps(state)), self._loop)

    def broadcast_chat(self):
        """Push chat update immediately (no throttle). Called on new chat messages."""
        if not self._running or not self._loop or not self._ws_clients:
            return
        state = {
            'type': 'chat',
            'chat': self._json_safe((self.sm.chat_messages or [])[-50:]),
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(json.dumps(state)), self._loop)

    async def _broadcast(self, message):
        """Send a message to all connected WebSocket clients."""
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(message)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    def _resolve_missions(self):
        """Render identity-bearing rows from the authoritative queue snapshot."""
        missions = tuple(self.sm.mission_entries)
        avail_raw = self.sm.available_maps_data or ''
        name_map = {}
        for line in avail_raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\d+[.:]\s*', line)
            if m:
                line = line[m.end():]
            ext_match = re.search(r'(?i)\.(bms|npj|npz)\b', line)
            if ext_match:
                filename = line[:ext_match.end()].strip()
                desc = line[ext_match.end():].strip()
                if desc.startswith('-'):
                    desc = desc[1:].strip()
                if desc.startswith('('):
                    desc = desc[1:].strip()
                if desc.endswith(')'):
                    desc = desc[:-1].strip()
                name_map[filename.upper()] = desc if desc else filename
        return [
            {
                'queue_index': mission.queue_index,
                'filename': mission.filename,
                'display_name': (
                    name_map.get(mission.filename.upper())
                    or re.sub(
                        r'(?i)\.(?:bms|npj|npz)$', '', mission.filename
                    )
                ),
                'is_current': mission.is_current,
                'is_next': mission.is_next,
                'double_time': mission.double_time,
                'revision': mission.revision,
            }
            for mission in missions
        ]

    def _resolve_players(self):
        """Render players with the identity required for safe mutations."""
        legacy_by_id = {}
        for raw in self.sm.players or []:
            if not isinstance(raw, Mapping):
                continue
            try:
                legacy_by_id[int(raw.get('id'))] = dict(raw)
            except (TypeError, ValueError):
                continue

        players = []
        for player in self.sm.player_entries:
            if player.server_id == 0:
                continue
            rendered = legacy_by_id.get(player.server_id, {})
            rendered.update({
                'id': player.server_id,
                'name': player.name,
                'team': str(player.team),
                'team_name': {
                    1: 'Joint Ops',
                    2: 'Rebels',
                }.get(player.team, 'Unknown'),
                'class': player.player_class,
                'kills': str(
                    player.kills if player.kills is not None else 0
                ),
                'score': str(
                    player.kills if player.kills is not None else 0
                ),
                'deaths': str(
                    player.deaths if player.deaths is not None else '-'
                ),
                'ping': str(
                    player.ping if player.ping is not None else '-'
                ),
                'revision': player.revision,
            })
            players.append(rendered)
        return players

    def _build_state(self):
        """Build a JSON-safe state snapshot from ServerManager."""
        players = self._json_safe(self._resolve_players())
        chat = self._json_safe((self.sm.chat_messages or [])[-50:])
        game_state = self._json_safe(self.sm.game_state or {})
        return {
            'type': 'state',
            'timestamp': time.time(),
            'connected': self.sm.is_connected,
            'players': players,
            'chat': chat,
            'game_state': game_state,
            'missions': self._resolve_missions(),
            'settings': self._safe_settings(),
            'player_count': len(players),
            'game_mode': self.sm.game_state.get('mode', 'Unknown') if self.sm.game_state else 'Unknown',
        }

    @classmethod
    def _json_safe(cls, value):
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: cls._json_safe(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

    def _safe_settings(self):
        settings = self.sm.game_settings or {}
        if hasattr(settings, 'items'):
            settings = dict(settings.items())
        else:
            settings = dict(settings)
        return self._json_safe({
            key: value
            for key, value in settings.items()
            if str(key).casefold() not in _SECRET_SETTING_NAMES
        })

    @staticmethod
    def _redact_command(command):
        tokens = command.split(maxsplit=2)
        if (
            len(tokens) == 3
            and tokens[0].casefold() == 'set'
            and tokens[1].casefold() in _SECRET_SETTING_NAMES
        ):
            return f'{tokens[0]} {tokens[1]} <redacted>'
        return command

    def _find_mission_entry(
        self, filename=None, *, queue_index=None, revision=None
    ):
        """Resolve an exact queue identity, never a guessed duplicate name."""
        missions = tuple(self.sm.mission_entries)
        target_name = str(filename or '').strip()
        name_matches = [
            mission
            for mission in missions
            if mission.filename.casefold() == target_name.casefold()
        ] if target_name else []

        if queue_index is None or str(queue_index).strip() == '':
            if len(name_matches) > 1:
                raise AmbiguousMissionTarget(
                    f"mission filename {target_name!r} is ambiguous; "
                    "send its queue index and revision"
                )
            raise ValueError(
                "mission identity requires queue index, filename, and revision"
            )
        if not target_name or revision is None or str(revision).strip() == '':
            raise ValueError(
                "mission identity requires queue index, filename, and revision"
            )

        try:
            target_index = int(queue_index)
            target_revision = int(revision)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "mission queue index and revision must be integers"
            ) from error
        if target_index < 0 or target_revision < 0:
            raise ValueError(
                "mission queue index and revision cannot be negative"
            )
        mission = next(
            (
                item
                for item in missions
                if item.queue_index == target_index
            ),
            None,
        )
        if mission is None:
            raise StaleMissionTarget(
                f"stale mission identity: queue index {target_index} "
                "is no longer present"
            )
        if mission.filename.casefold() != target_name.casefold():
            raise StaleMissionTarget(
                f"stale mission identity: queue index {target_index} is "
                f"{mission.filename!r}, not {target_name!r}"
            )
        if mission.revision != target_revision:
            raise StaleMissionTarget(
                f"stale mission identity: revision changed from "
                f"{target_revision} to {mission.revision}"
            )
        return mission

    def _find_player_entry(self, player_id, name, revision):
        """Resolve a complete player identity against the current snapshot."""
        target_name = str(name or '').strip()
        if not target_name or revision is None or str(revision).strip() == '':
            raise ValueError(
                "player identity requires id, name, and revision"
            )
        try:
            target_id = int(player_id)
            target_revision = int(revision)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "player id and revision must be integers"
            ) from error
        if target_revision < 0:
            raise ValueError("player revision cannot be negative")

        player = next(
            (
                item
                for item in self.sm.player_entries
                if item.server_id == target_id
            ),
            None,
        )
        if player is None:
            raise StalePlayerTarget(
                f"stale player identity: id {target_id} is no longer present"
            )
        if player.name != target_name:
            raise StalePlayerTarget(
                f"stale player identity: id {target_id} is now "
                f"{player.name!r}, not {target_name!r}"
            )
        if player.revision != target_revision:
            raise StalePlayerTarget(
                f"stale player identity: revision changed from "
                f"{target_revision} to {player.revision}"
            )
        return player

    @staticmethod
    def _result_payload(result, **context):
        replies = list(getattr(result, 'replies', ()) or ())
        accepted = bool(getattr(result, 'accepted', False))
        has_verification = hasattr(result, 'verified')
        verified = result.verified if has_verification else None
        payload = {
            'ok': (
                accepted and verified is True
                if has_verification
                else accepted
            ),
            'accepted': accepted,
            'replies': replies,
            **context,
        }
        if has_verification:
            payload['verified'] = verified
            payload['verification_error'] = result.verification_error
        if not accepted:
            payload['error'] = replies[-1] if replies else 'Retail command rejected'
        return payload

    async def _await_result(self, future):
        return await asyncio.wrap_future(future)

    async def _ws_result(self, ws, message_type, future, **context):
        try:
            result = await self._await_result(future)
            payload = self._result_payload(
                result, type=f'{message_type}_result', **context
            )
        except Exception as error:
            payload = {
                'type': f'{message_type}_result',
                'ok': False,
                'accepted': False,
                'replies': [],
                'error': str(error),
                **context,
            }
        await ws.send_str(json.dumps(payload))

    # --- Auth ---

    async def _handle_auth(self, request):
        """POST /api/auth — Validate username + token."""
        try:
            body = await request.json()
            if not isinstance(body, Mapping):
                return web.json_response(
                    {'error': 'Request body must be a JSON object'},
                    status=400,
                )
            username = body.get('username', '').strip()
            token = body.get('token', '').strip()
            if not username or not token:
                return web.json_response({'error': 'Missing username or token'}, status=400)

            with self._auth_lock:
                valid_user = self._web_username
                valid_token = self._web_token

            if not valid_token:
                return web.json_response({'error': 'Web admin is not enabled'}, status=403)

            if (secrets.compare_digest(username, valid_user) and
                    secrets.compare_digest(token, valid_token)):
                # Track IP
                ip = request.remote or 'unknown'
                self._login_ips[ip] = time.time()
                wire_log(f"WEB AUTH: {username} logged in from {ip}")
                # Notify for persistence
                if self.on_login:
                    try:
                        self.on_login(ip, time.time())
                    except Exception:
                        pass
                # Return the access token itself — client uses it for all requests
                return web.json_response({'ok': True, 'token': token})

            wire_log(f"WEB AUTH FAILED: {username}")
            return web.json_response({'error': 'Invalid username or token'}, status=401)
        except json.JSONDecodeError:
            return web.json_response(
                {'error': 'Request body must be valid JSON'},
                status=400,
            )
        except Exception as error:
            return web.json_response({'error': str(error)}, status=500)

    # --- HTTP Handlers ---

    async def _handle_index(self, request):
        """Serve the mobile web UI."""
        template_path = os.path.join(self._template_path, 'index.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                html = f.read()
            return web.Response(text=html, content_type='text/html')
        except FileNotFoundError:
            return web.Response(text='<h1>WolfRAT Web UI</h1><p>Template not found.</p>', content_type='text/html')

    async def _handle_css(self, request):
        """Serve the CSS stylesheet."""
        css_path = os.path.join(self._template_path, 'style.css')
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css = f.read()
            return web.Response(text=css, content_type='text/css')
        except FileNotFoundError:
            return web.Response(text='', content_type='text/css')

    async def _handle_js(self, request):
        """Serve the JavaScript app."""
        js_path = os.path.join(self._template_path, 'app.js')
        try:
            with open(js_path, 'r', encoding='utf-8') as f:
                js = f.read()
            return web.Response(text=js, content_type='application/javascript')
        except FileNotFoundError:
            return web.Response(text='// app.js not found', content_type='application/javascript')

    async def _handle_status(self, request):
        """GET /api/status — Server status overview."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response(self._build_state())

    async def _handle_players(self, request):
        """GET /api/players — Current player list."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        players = self._json_safe(self._resolve_players())
        return web.json_response({
            'players': players,
            'count': len(players),
        })

    async def _handle_chat(self, request):
        """GET /api/chat — Recent chat messages."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'messages': self._json_safe((self.sm.chat_messages or [])[-100:]),
        })

    async def _handle_maps(self, request):
        """GET /api/maps — Current mission rotation with resolved display names."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'missions': self._json_safe(self._resolve_missions()),
            'available': self.sm.available_maps_data or '',
        })

    async def _handle_settings(self, request):
        """GET /api/settings — Current server settings."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'settings': self._safe_settings(),
        })

    async def _handle_login_ips(self, request):
        """GET /api/login-ips — List of unique IPs with last login time."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'ips': self.get_login_ips(),
        })

    async def _handle_quick_action(self, request):
        """POST /api/action — Execute a named, typed dashboard operation."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            action = str(body.get('action', '')).strip().lower()
            if not action:
                return web.json_response({'error': 'No action provided'}, status=400)
            if not self.sm.is_connected:
                return web.json_response(
                    {'error': 'Not connected to server'}, status=503
                )

            actions = {
                'next_map': self.sm.cycle_mission,
                'refresh_players': self.sm.refresh_players,
                'refresh_chat': self.sm.refresh_chat,
            }
            operation = actions.get(action)
            if operation is None:
                return web.json_response(
                    {'error': f'Unknown action: {action}'}, status=400
                )

            wire_log(f"WEB QUICK ACTION: {action}")
            result = await self._await_result(operation())
            return web.json_response(
                self._result_payload(result, action=action)
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        except Exception as error:
            return web.json_response({'error': str(error)}, status=502)

    async def _handle_command(self, request):
        """POST /api/command — Send a raw command to the server."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            cmd = body.get('command', '').strip()
            if not cmd:
                return web.json_response({'error': 'No command provided'}, status=400)
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)
            display_command = self._redact_command(cmd)
            wire_log(f"WEB CMD: {display_command}")
            result = await self._await_result(self.sm.execute_raw(cmd))
            return web.json_response(
                self._result_payload(result, command=display_command)
            )
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_send_chat(self, request):
        """POST /api/chat/send — Send a chat message."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            msg = body.get('message', '').strip()
            if not msg:
                return web.json_response({'error': 'No message provided'}, status=400)
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)
            result = await self._await_result(self.sm.send_chat(msg))
            return web.json_response(self._result_payload(result))
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_player_action(self, request):
        """POST /api/player/action — Admin action on a player."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            pid = str(body.get('pid', '')).strip()
            player_name = str(body.get('name', '')).strip()
            revision = body.get('revision')
            action = str(body.get('action', '')).strip().lower()
            if not pid or not action:
                return web.json_response({'error': 'Missing pid or action'}, status=400)
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            # The server parses this with (uint8)atol(), so anything unparseable
            # -- a player name, say -- silently becomes 0, which is the host.
            # KILL, SWAPTEAM and ZEROSCORE are not guarded against id 0 the way
            # PUNT is, so validate before we let it near them.
            try:
                pid_num = int(pid)
            except ValueError:
                return web.json_response({'error': f'Invalid player id: {pid!r}'}, status=400)
            if not 1 <= pid_num <= 250:
                return web.json_response(
                    {'error': f'Player id {pid_num} out of range (1-250)'}, status=400)

            if action not in {'kick', 'ban', 'kill', 'swap', 'zero'}:
                return web.json_response({'error': f'Unknown action: {action}'}, status=400)

            player = self._find_player_entry(
                pid_num, player_name, revision
            )
            actions = {
                'kick': lambda: self.sm.punt_player(player),
                'ban': lambda: self.sm.ban_player(player),
                'kill': lambda: self.sm.kill_player(player),
                'swap': lambda: self.sm.swap_player(player),
                'zero': lambda: self.sm.zero_player(player),
            }

            wire_log(
                f"WEB PLAYER ACTION: {action} on "
                f"{player.server_id}/{player.name}"
            )
            result = await self._await_result(actions[action]())
            return web.json_response(
                self._result_payload(
                    result,
                    action=action,
                    pid=player.server_id,
                    name=player.name,
                    revision=player.revision,
                )
            )
        except StalePlayerTarget as error:
            return web.json_response({'error': str(error)}, status=409)
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_map_switch(self, request):
        """POST /api/map/switch — Switch to a specific map by filename."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            map_name = str(body.get('map', '')).strip()
            queue_index = body.get('index')
            revision = body.get('revision')
            if not map_name and queue_index is None:
                return web.json_response(
                    {'error': 'No mission identity provided'}, status=400
                )
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            mission = self._find_mission_entry(
                map_name,
                queue_index=queue_index,
                revision=revision,
            )
            wire_log(
                f"WEB MAP SWITCH: {mission.filename} at queue index "
                f"{mission.queue_index}"
            )
            result = await self._await_result(self.sm.switch_mission(mission))
            return web.json_response(self._result_payload(
                result,
                map=mission.filename,
                index=mission.queue_index,
                revision=mission.revision,
            ))
        except (AmbiguousMissionTarget, StaleMissionTarget) as error:
            return web.json_response({'error': str(error)}, status=409)
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_websocket(self, request):
        """WebSocket endpoint for live state updates."""
        if not self._check_auth(request):
            return web.Response(status=401, text='Unauthorized')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        wire_log(f"WEB: WebSocket connected ({len(self._ws_clients)} total)")

        # Send initial state
        try:
            await ws.send_str(json.dumps(self._build_state()))
        except Exception:
            pass

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        message_type = data.get('type')
                        if message_type not in {'command', 'chat'}:
                            await ws.send_str(json.dumps({
                                'type': 'error',
                                'error': f'Unknown message type: {message_type}',
                            }))
                            continue
                        if not self.sm.is_connected:
                            await ws.send_str(json.dumps({
                                'type': f'{message_type}_result',
                                'ok': False,
                                'accepted': False,
                                'replies': [],
                                'error': 'Not connected to server',
                            }))
                            continue
                        if message_type == 'command':
                            command = str(data.get('command', '')).strip()
                            if not command:
                                raise ValueError('No command provided')
                            await self._ws_result(
                                ws,
                                message_type,
                                self.sm.execute_raw(command),
                                command=self._redact_command(command),
                            )
                        else:
                            message = str(data.get('message', '')).strip()
                            if not message:
                                raise ValueError('No message provided')
                            await self._ws_result(
                                ws,
                                message_type,
                                self.sm.send_chat(message),
                            )
                    except (json.JSONDecodeError, ValueError) as error:
                        await ws.send_str(json.dumps({
                            'type': 'error',
                            'error': str(error),
                        }))
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except Exception:
            pass
        finally:
            self._ws_clients.discard(ws)
            wire_log(f"WEB: WebSocket disconnected ({len(self._ws_clients)} total)")

        return ws
