"""Live conformance runner for a retail Joint Operations admin server.

The default run is read-only.  Reversible non-player mutations require
``--full``; player mutations additionally require a unique non-host dummy
player name.  This module intentionally uses
``RetailAdminSession`` and ``AdminCommands``; it is not a second protocol
client.

Example:

    python -m tests.live_retail --host 127.0.0.1 --port 4000
    python -m tests.live_retail --host 127.0.0.1 --port 4000 \
        --full --dummy-name biggy
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from getpass import getpass
import os
import re
import sys
import time
from typing import Any, Callable

from wolfrat.admin_commands import (
    AdminCommands,
    AdminOperation,
    CANONICAL_SETTING_KEYS,
    SETTING_SCHEMA,
    WeaponMode,
)
from wolfrat.admin_session import (
    AdminSessionError,
    RetailAdminSession,
    RetailProtocolError,
)


class LiveConformanceError(RuntimeError):
    """A retail reply or independent readback did not meet the contract."""


@dataclass(frozen=True)
class LiveConfig:
    host: str
    port: int
    username: str
    password: str
    timeout: float
    full: bool
    dummy_name: str | None
    player_disconnects: bool
    rejoin_timeout: float


class LiveRetailSuite:
    def __init__(
        self,
        config: LiveConfig,
        *,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self._session_factory = session_factory or (
            lambda: RetailAdminSession(
                config.host,
                config.port,
                config.username,
                config.password,
                timeout=config.timeout,
            )
        )
        self.session = self._session_factory()
        self.passed = 0
        self.skipped = 0

    def close(self) -> None:
        self.session.close()

    def _reconnect(self) -> None:
        """Discard the current generation before an authoritative audit."""

        try:
            self.session.close()
        finally:
            self.session = self._session_factory()

    def _reconnect_execute(self, label: str, operation: Any) -> Any:
        self._reconnect()
        return self.execute(label, operation)

    def _mutation_audit(
        self,
        label: str,
        operation: Any,
        audit_label: str,
        audit_operation: Any,
    ) -> tuple[Any | None, list[Exception]]:
        """Audit a mutation, reconnecting only when its outcome is ambiguous."""

        errors: list[Exception] = []
        ambiguous = False
        try:
            self.execute(label, operation)
        except Exception as error:
            errors.append(error)
            ambiguous = True

        observed = None
        if ambiguous:
            try:
                observed = self._reconnect_execute(
                    audit_label, audit_operation
                )
            except Exception as error:
                errors.append(error)
        else:
            try:
                observed = self.execute(audit_label, audit_operation)
            except Exception as error:
                errors.append(error)
                try:
                    observed = self._reconnect_execute(
                        f"{audit_label} after reconnect",
                        audit_operation,
                    )
                except Exception as reconnect_error:
                    errors.append(reconnect_error)
        return observed, errors

    @staticmethod
    def _raise_collected(label: str, errors: list[Exception]) -> None:
        if not errors:
            return
        if len(errors) == 1:
            raise errors[0]
        raise LiveConformanceError(
            f"{label}: {len(errors)} failures occurred across mutation, "
            "audit, and cleanup"
        ) from errors[0]

    def execute(self, label: str, operation: Any) -> Any:
        """Execute one typed command and require an accepting retail reply."""

        result = self.session.execute(operation).result(
            timeout=self.config.timeout + 5.0
        )
        if not result.accepted:
            raise LiveConformanceError(
                f"{label}: retail server rejected the operation"
            )
        if operation.mutating and not _mutation_acknowledged(
            operation, result.replies
        ):
            raise LiveConformanceError(
                f"{label}: retail did not return the command-specific "
                "acknowledgement"
            )
        self.passed += 1
        suffix = "" if len(result.replies) == 1 else "ies"
        print(f"PASS  {label} ({len(result.replies)} repl{suffix or 'y'})")
        return result.value

    def skip(self, label: str, reason: str) -> None:
        self.skipped += 1
        print(f"SKIP  {label}: {reason}")

    def read_matrix(self) -> dict[str, Any]:
        """Run every supported safe read sequentially on the shared session."""

        reads: tuple[tuple[str, Callable[[], Any]], ...] = (
            ("GET GAMESTATE", AdminCommands.game_state),
            ("GET GAMESETTINGS", AdminCommands.game_settings),
            ("PLAYER LIST", AdminCommands.players),
            ("MISSION LIST", AdminCommands.missions),
            ("MISSION AVAILABLE", AdminCommands.available_missions),
            ("WEAPON LIST", AdminCommands.weapons),
            ("CHAT GET", AdminCommands.chat),
        )
        values: dict[str, Any] = {}
        for label, factory in reads:
            values[label] = self.execute(label, factory())
        return values

    def run_full(self, baseline: dict[str, Any]) -> None:
        if self.config.dummy_name:
            self._require_unique_dummy(baseline["PLAYER LIST"])
        self._settings_roundtrip(baseline["GET GAMESETTINGS"])
        self._mission_add_remove_roundtrip(
            baseline["MISSION LIST"], baseline["MISSION AVAILABLE"]
        )
        self._weapons_roundtrip(baseline["WEAPON LIST"])
        self._chat_roundtrip()
        if self.config.dummy_name:
            self._player_roundtrip()
        else:
            self.skip(
                "PLAYER mutations",
                "pass --dummy-name with the exact unique non-host player name",
            )

    def _settings_roundtrip(self, original_settings: Any) -> None:
        missing = [
            key for key in CANONICAL_SETTING_KEYS
            if original_settings.get(key) is None
        ]
        if missing:
            raise LiveConformanceError(
                "GET GAMESETTINGS omitted canonical keys: " + ", ".join(missing)
            )

        for key in CANONICAL_SETTING_KEYS:
            original = str(original_settings[key])
            original_write = _setting_write_value(key, original)
            alternate = _alternate_setting(key, original)
            if alternate is None:
                self.skip(f"SET {key}", "server returned a masked secret")
                continue
            errors: list[Exception] = []
            observed, mutation_errors = self._mutation_audit(
                f"SET {key} mutation",
                AdminCommands.set_setting(key, alternate),
                f"GET GAMESETTINGS audit {key}",
                AdminCommands.game_settings(),
            )
            errors.extend(mutation_errors)

            if observed is None:
                try:
                    observed = self._reconnect_execute(
                        f"GET GAMESETTINGS retry cleanup {key}",
                        AdminCommands.game_settings(),
                    )
                except Exception as error:
                    errors.append(error)
            if observed is not None and not _settings_equal(
                key, observed.get(key), alternate
            ):
                errors.append(
                    LiveConformanceError(
                        f"SET {key}: independent readback did not confirm "
                        "mutation"
                    )
                )

            # Restoration is based only on newly observed retail state.  It
            # therefore still runs when the server applied SET but its ACK
            # was lost.
            if observed is None or not _settings_equal(
                key, observed.get(key), original_write
            ):
                restored, restore_errors = self._mutation_audit(
                    f"SET {key} restore",
                    AdminCommands.set_setting(key, original_write),
                    f"GET GAMESETTINGS audit restore {key}",
                    AdminCommands.game_settings(),
                )
                errors.extend(restore_errors)
            else:
                restored = observed

            if restored is None or not _settings_equal(
                key, restored.get(key), original_write
            ):
                errors.append(
                    LiveConformanceError(
                        f"SET {key}: restoration was not confirmed"
                    )
                )
            self._raise_collected(f"SET {key}", errors)

    def _mission_add_remove_roundtrip(
        self, original_queue: tuple[Any, ...], available: tuple[Any, ...]
    ) -> None:
        if not available:
            self.skip("MISSION ADD/REMOVE", "server reported no available missions")
            return

        original_counts = Counter(
            mission.filename.casefold() for mission in original_queue
        )
        candidate = min(
            available,
            key=lambda mission: original_counts[mission.filename.casefold()],
        )
        candidate_name = candidate.filename.casefold()
        # Refresh the identity-bearing catalog immediately before ADD. This
        # keeps the record authoritative whether recovery replaced the
        # session or the healthy persistent session is still in use.
        fresh_available = self.execute(
            "MISSION AVAILABLE refresh ADD",
            AdminCommands.available_missions(),
        )
        fresh_candidates = [
            mission for mission in fresh_available
            if mission.filename.casefold() == candidate_name
        ]
        if len(fresh_candidates) != 1:
            raise LiveConformanceError(
                "MISSION ADD: candidate was not unique in a fresh "
                "MISSION AVAILABLE snapshot"
            )
        candidate = fresh_candidates[0]
        errors: list[Exception] = []
        current, add_errors = self._mutation_audit(
            "MISSION ADD",
            AdminCommands.add_mission(candidate),
            "MISSION LIST audit ADD",
            AdminCommands.missions(),
        )
        errors.extend(add_errors)
        if current is None:
            try:
                current = self._reconnect_execute(
                    "MISSION LIST retry cleanup ADD",
                    AdminCommands.missions(),
                )
            except Exception as error:
                errors.append(error)

        if current is not None:
            after_counts = Counter(
                mission.filename.casefold() for mission in current
            )
            if (
                after_counts[candidate_name]
                != original_counts[candidate_name] + 1
            ):
                errors.append(
                    LiveConformanceError(
                        "MISSION ADD: independent list did not show exactly "
                        "one new entry"
                    )
                )

        # Remove every observed addition, including one applied before an
        # acknowledgement was lost. Each cleanup mutation gets an
        # authoritative list audit and reconnects only if its outcome is
        # ambiguous.
        while current is not None:
            candidates = [
                mission
                for mission in current
                if mission.filename.casefold() == candidate_name
            ]
            if len(candidates) <= original_counts[candidate_name]:
                break
            added = max(
                candidates, key=lambda item: item.queue_index
            )
            before_count = len(candidates)
            current_after_remove, remove_errors = self._mutation_audit(
                "MISSION REMOVE",
                AdminCommands.remove_mission(added),
                "MISSION LIST audit REMOVE",
                AdminCommands.missions(),
            )
            errors.extend(remove_errors)
            if current_after_remove is None:
                try:
                    current_after_remove = self._reconnect_execute(
                        "MISSION LIST retry cleanup REMOVE",
                        AdminCommands.missions(),
                    )
                except Exception as error:
                    errors.append(error)
                    break
            after_count = sum(
                mission.filename.casefold() == candidate_name
                for mission in current_after_remove
            )
            current = current_after_remove
            if after_count >= before_count:
                errors.append(
                    LiveConformanceError(
                        "MISSION REMOVE cleanup made no observable progress"
                    )
                )
                break

        restored = current
        if restored is None:
            try:
                restored = self._reconnect_execute(
                    "MISSION LIST final cleanup audit",
                    AdminCommands.missions(),
                )
            except Exception as error:
                errors.append(error)
        if (
            restored is None
            or _mission_fingerprint(restored)
            != _mission_fingerprint(original_queue)
        ):
            errors.append(
                LiveConformanceError(
                    "MISSION REMOVE: queue did not return to its original "
                    "state"
                )
            )
        self._raise_collected("MISSION ADD/REMOVE", errors)

        original_next = next(
            (mission for mission in restored if mission.is_next),
            None,
        )
        if original_next is not None:
            # Selecting the already-designated next item verifies SETNEXT while
            # avoiding an unnecessary map cycle in the reversible phase.
            verified, setnext_errors = self._mutation_audit(
                "MISSION SETNEXT",
                AdminCommands.set_next_mission(original_next),
                "MISSION LIST audit SETNEXT",
                AdminCommands.missions(),
            )
            errors = list(setnext_errors)
            selected = (
                next(
                    (
                        mission for mission in verified
                        if mission.queue_index
                        == original_next.queue_index
                    ),
                    None,
                )
                if verified is not None
                else None
            )
            if selected is None or not selected.is_next:
                errors.append(
                    LiveConformanceError(
                        "MISSION SETNEXT: independent list did not confirm "
                        "selection"
                    )
                )
            self._raise_collected("MISSION SETNEXT", errors)
        else:
            self.skip(
                "MISSION SETNEXT",
                "queue has no existing next mission to select reversibly",
            )

    def _weapons_roundtrip(self, original_weapons: tuple[Any, ...]) -> None:
        if not original_weapons:
            self.skip("WEAPON SET", "server reported no weapons")
            return

        for original in original_weapons:
            errors: list[Exception] = []
            fresh = self.execute(
                f"WEAPON LIST refresh {original.admdef_id}",
                AdminCommands.weapons(),
            )
            target = _weapon_by_id(fresh, original.admdef_id)
            alternate = next(mode for mode in WeaponMode if mode is not target.mode)
            observed, mutation_errors = self._mutation_audit(
                f"WEAPON SET {target.admdef_id}",
                AdminCommands.set_weapon(target, alternate),
                f"WEAPON LIST audit {target.admdef_id}",
                AdminCommands.weapons(),
            )
            errors.extend(mutation_errors)
            if observed is None:
                try:
                    observed = self._reconnect_execute(
                        f"WEAPON LIST retry cleanup {target.admdef_id}",
                        AdminCommands.weapons(),
                    )
                except Exception as error:
                    errors.append(error)

            observed_target = None
            if observed is not None:
                try:
                    observed_target = _weapon_by_id(
                        observed, target.admdef_id
                    )
                except Exception as error:
                    errors.append(error)
            if (
                observed_target is None
                or observed_target.mode is not alternate
            ):
                errors.append(
                    LiveConformanceError(
                        f"WEAPON SET {target.admdef_id}: readback mismatch"
                    )
                )

            # A failed/lost SET acknowledgement is not evidence that the
            # mutation did not happen. Restore only from the new list.
            if (
                observed_target is not None
                and observed_target.mode is not original.mode
            ):
                restored, restore_errors = self._mutation_audit(
                    f"WEAPON SET restore {target.admdef_id}",
                    AdminCommands.set_weapon(
                        observed_target, original.mode
                    ),
                    f"WEAPON LIST audit restore {target.admdef_id}",
                    AdminCommands.weapons(),
                )
                errors.extend(restore_errors)
            else:
                restored = observed

            restored_target = None
            if restored is not None:
                try:
                    restored_target = _weapon_by_id(
                        restored, target.admdef_id
                    )
                except Exception as error:
                    errors.append(error)
            if (
                restored_target is None
                or restored_target.mode is not original.mode
            ):
                errors.append(
                    LiveConformanceError(
                        f"WEAPON SET {target.admdef_id}: restore mismatch"
                    )
                )
            self._raise_collected(
                f"WEAPON SET {target.admdef_id}", errors
            )

        originals = {weapon.admdef_id: weapon.mode for weapon in original_weapons}
        all_mode = WeaponMode.NEVER
        errors: list[Exception] = []
        all_observed, all_errors = self._mutation_audit(
            "WEAPON SET ALL",
            AdminCommands.set_all_weapons(all_mode),
            "WEAPON LIST audit ALL",
            AdminCommands.weapons(),
        )
        errors.extend(all_errors)
        if not all_observed or any(
            weapon.mode is not all_mode for weapon in all_observed
        ):
            errors.append(
                LiveConformanceError(
                    "WEAPON SET ALL: independent list did not confirm every "
                    "weapon"
                )
            )

        # Restore every row independently. A failed refresh or SET is
        # recorded, but never prevents later rows or the final audit.
        for admdef_id, mode in originals.items():
            try:
                current = self.execute(
                    f"WEAPON LIST refresh final restore {admdef_id}",
                    AdminCommands.weapons(),
                )
            except Exception as error:
                errors.append(error)
                try:
                    current = self._reconnect_execute(
                        f"WEAPON LIST retry final restore {admdef_id}",
                        AdminCommands.weapons(),
                    )
                except Exception as reconnect_error:
                    errors.append(reconnect_error)
                    continue
            try:
                target = _weapon_by_id(current, admdef_id)
            except Exception as error:
                errors.append(error)
                continue
            _restored, restore_errors = self._mutation_audit(
                f"WEAPON SET final restore {admdef_id}",
                AdminCommands.set_weapon(target, mode),
                f"WEAPON LIST audit final restore {admdef_id}",
                AdminCommands.weapons(),
            )
            errors.extend(restore_errors)

        final = None
        try:
            final = self.execute(
                "WEAPON LIST final restoration audit",
                AdminCommands.weapons(),
            )
        except Exception as error:
            errors.append(error)
            try:
                final = self._reconnect_execute(
                    "WEAPON LIST retry final restoration audit",
                    AdminCommands.weapons(),
                )
            except Exception as reconnect_error:
                errors.append(reconnect_error)
        if (
            final is None
            or {
                weapon.admdef_id: weapon.mode for weapon in final
            }
            != originals
        ):
            errors.append(
                LiveConformanceError(
                    "WEAPON SET ALL: original mixed weapon state was not "
                    "restored"
                )
            )
        self._raise_collected("WEAPON SET ALL", errors)

    def _chat_roundtrip(self) -> None:
        tag = f"[wolfrat-live-{int(time.time())}]"
        _observed, errors = self._mutation_audit(
            "CHAT SEND",
            AdminCommands.send_chat(tag),
            "CHAT GET after SEND",
            AdminCommands.chat(),
        )
        # Server-originated admin chat is not inserted into the inbound player
        # chat buffer, so non-echo is expected. The audit proves framing and
        # request correlation; a lost ACK takes the recovery/reconnect path.
        self._raise_collected("CHAT SEND", errors)

    def _player_roundtrip(self) -> None:
        players = self.execute("PLAYER LIST refresh dummy", AdminCommands.players())
        target = self._require_unique_dummy(players)
        original_team = target.team
        errors: list[Exception] = []
        observed, mutation_errors = self._mutation_audit(
            "PLAYER SWAPTEAM",
            AdminCommands.swap_team(target),
            "PLAYER LIST audit SWAPTEAM",
            AdminCommands.players(),
        )
        errors.extend(mutation_errors)
        if observed is None:
            try:
                observed = self._reconnect_execute(
                    "PLAYER LIST retry cleanup SWAPTEAM",
                    AdminCommands.players(),
                )
            except Exception as error:
                errors.append(error)

        current = None
        if observed is not None:
            try:
                current = self._require_unique_dummy(observed)
            except Exception as error:
                errors.append(error)
        if current is None or current.team == original_team:
            errors.append(
                LiveConformanceError(
                    "PLAYER SWAPTEAM: independent list did not show a team "
                    "change"
                )
            )

        if current is not None and current.team != original_team:
            restored, restore_errors = self._mutation_audit(
                "PLAYER SWAPTEAM restore",
                AdminCommands.swap_team(current),
                "PLAYER LIST audit team restore",
                AdminCommands.players(),
            )
            errors.extend(restore_errors)
        else:
            restored = observed
        restored_player = None
        if restored is not None:
            try:
                restored_player = self._require_unique_dummy(restored)
            except Exception as error:
                errors.append(error)
        if (
            restored_player is None
            or restored_player.team != original_team
        ):
            errors.append(
                LiveConformanceError(
                    "PLAYER SWAPTEAM: original team was not restored"
                )
            )
        self._raise_collected("PLAYER SWAPTEAM", errors)

        if not self.config.player_disconnects:
            self.skip(
                "PLAYER KILL/ZEROSCORE/PUNT/BAN",
                "pass --player-disconnects to enable disruptive dummy actions",
            )
            return

        current = self._require_unique_dummy(
            self.execute("PLAYER LIST refresh KILL", AdminCommands.players())
        )
        killed_players, errors = self._mutation_audit(
            "PLAYER KILL",
            AdminCommands.kill(current),
            "PLAYER LIST audit KILL",
            AdminCommands.players(),
        )
        killed = (
            self._require_unique_dummy(killed_players)
            if killed_players is not None
            else None
        )
        if (
            killed is None
            or (
                current.deaths is not None
                and killed.deaths is not None
                and killed.deaths <= current.deaths
            )
        ):
            errors.append(
                LiveConformanceError(
                    "PLAYER KILL: death counter did not advance"
                )
            )
        self._raise_collected("PLAYER KILL", errors)

        if (killed.kills or 0) or (killed.deaths or 0):
            zeroed_players, errors = self._mutation_audit(
                "PLAYER ZEROSCORE",
                AdminCommands.zero_score(killed),
                "PLAYER LIST audit ZEROSCORE",
                AdminCommands.players(),
            )
            zeroed = (
                self._require_unique_dummy(zeroed_players)
                if zeroed_players is not None
                else None
            )
            if zeroed is None or (zeroed.kills not in (None, 0)) or (
                zeroed.deaths not in (None, 0)
            ):
                errors.append(
                    LiveConformanceError(
                        "PLAYER ZEROSCORE: score columns were not cleared"
                    )
                )
            self._raise_collected("PLAYER ZEROSCORE", errors)
            disconnect_target = zeroed
        else:
            self.skip("PLAYER ZEROSCORE", "dummy score is already zero")
            disconnect_target = killed

        after_punt, errors = self._mutation_audit(
            "PLAYER PUNT",
            AdminCommands.punt(disconnect_target),
            "PLAYER LIST audit PUNT",
            AdminCommands.players(),
        )
        punted_matches = (
            [
                player for player in after_punt
                if player.server_id != 0
                and player.name.casefold()
                == str(self.config.dummy_name).casefold()
            ]
            if after_punt is not None
            else []
        )
        if after_punt is None or punted_matches:
            errors.append(
                LiveConformanceError(
                    "PLAYER PUNT: independent list still contains the dummy"
                )
            )
            self._raise_collected("PLAYER PUNT", errors)
        self._wait_for_dummy(present=True, label="PUNT rejoin")
        self._raise_collected("PLAYER PUNT", errors)

        # BAN is intentionally last.  Automating it without a verified
        # banlist snapshot and listener-only restart would leave persistent
        # server state behind, so this harness refuses to pretend an ACK is
        # conformance.
        self.skip(
            "PLAYER BAN",
            "requires banlist snapshot/restore and listener restart support",
        )

    def _require_unique_dummy(self, players: tuple[Any, ...]) -> Any:
        wanted = self.config.dummy_name
        matches = [
            player for player in players
            if player.server_id != 0
            and player.name.casefold() == str(wanted).casefold()
        ]
        if len(matches) != 1:
            raise LiveConformanceError(
                f"dummy {wanted!r} must resolve to exactly one non-host player; "
                f"found {len(matches)}"
            )
        if matches[0].server_id == 0:
            raise LiveConformanceError("player id 0 is never a mutation target")
        return matches[0]

    def _wait_for_dummy(self, *, present: bool, label: str) -> Any | None:
        deadline = time.monotonic() + self.config.rejoin_timeout
        while time.monotonic() < deadline:
            players = self.execute(
                f"PLAYER LIST poll {label}", AdminCommands.players()
            )
            matches = [
                player for player in players
                if player.server_id != 0
                and player.name.casefold()
                == str(self.config.dummy_name).casefold()
            ]
            if bool(matches) is present:
                if len(matches) > 1:
                    raise LiveConformanceError(
                        f"{label}: dummy name became ambiguous"
                    )
                return matches[0] if matches else None
            time.sleep(1.0)
        state = "rejoin" if present else "leave"
        raise LiveConformanceError(
            f"{label}: dummy did not {state} within "
            f"{self.config.rejoin_timeout:g} seconds"
        )


def _alternate_setting(key: str, original: str) -> Any | None:
    schema = SETTING_SCHEMA[key]
    if schema == "boolean":
        return "0" if original.strip().casefold() in {"1", "true", "yes", "on"} else "1"
    if schema == "number":
        try:
            number = float(original)
        except ValueError as error:
            raise LiveConformanceError(
                f"{key}: server returned a non-numeric value"
            ) from error
        return "0.67" if number != 0.67 else "0.66"
    if schema == "integer":
        try:
            number = int(_setting_write_value(key, original))
        except ValueError as error:
            raise LiveConformanceError(
                f"{key}: server returned a non-integer value"
            ) from error
        return 1 if number != 1 else 2
    if schema == "secret":
        if original and set(original) == {"*"}:
            return None
        return "wr-live" if original != "wr-live" else "wr-live-2"
    if schema == "text":
        suffix = "-live"
        return (
            (original[: 27 - len(suffix)] + suffix)
            if not original.endswith(suffix)
            else original[: -len(suffix)] + "-test"
        )
    raise LiveConformanceError(f"{key}: unknown setting schema {schema!r}")


def _setting_write_value(key: str, observed: str) -> Any:
    """Convert a formatted query value into the value accepted by SET."""

    if key == "GameTime":
        total = str(observed).rsplit("/", 1)[-1]
        try:
            return int(float(total))
        except ValueError as error:
            raise LiveConformanceError(
                "GameTime: server returned a malformed remaining/total value"
            ) from error
    return observed


def _settings_equal(key: str, actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if SETTING_SCHEMA[key] == "boolean":
        truthy = {"1", "true", "yes", "on"}
        return (
            str(actual).strip().casefold() in truthy
        ) == (
            str(expected).strip().casefold() in truthy
        )
    if SETTING_SCHEMA[key] == "number":
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if key == "GameTime":
        try:
            return int(_setting_write_value(key, str(actual))) == int(expected)
        except (LiveConformanceError, TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def _mission_fingerprint(missions: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            mission.queue_index,
            mission.filename.casefold(),
            mission.one_shot,
            mission.is_flipped,
            mission.double_time,
            mission.is_current,
            mission.is_next,
        )
        for mission in missions
    )


def _weapon_by_id(weapons: tuple[Any, ...], admdef_id: int) -> Any:
    matches = [weapon for weapon in weapons if weapon.admdef_id == admdef_id]
    if len(matches) != 1:
        raise LiveConformanceError(
            f"weapon id {admdef_id} was not unique in WEAPON LIST"
        )
    return matches[0]


def _chat_acknowledged(replies: tuple[str, ...]) -> bool:
    return tuple(reply.strip() for reply in replies) == (
        "OK - Chat sent.",
    )


def _mutation_acknowledged(operation: Any, replies: tuple[str, ...]) -> bool:
    """Return whether *replies* are the exact retail ACK for a typed mutation."""

    expected_by_operation = {
        AdminOperation.SET_SETTING: "OK - Setting Changed.",
        AdminOperation.MISSION_ADD: "OK - Entry Added",
        AdminOperation.MISSION_REMOVE: "OK - Mission Removed.",
        AdminOperation.MISSION_CLEAR: "OK - Mission list reset.",
        AdminOperation.MISSION_SETNEXT: "OK - Next Mission Set.",
        AdminOperation.MISSION_CYCLE: "OK - Server is cycling...",
        AdminOperation.PLAYER_PUNT: "OK - Player punted.",
        AdminOperation.PLAYER_SWAPTEAM: "OK - Player Swapped.",
        AdminOperation.PLAYER_KILL: "OK - Player Killed.",
        AdminOperation.PLAYER_ZEROSCORE: "OK - Player Zeroed.",
        AdminOperation.CHAT_SEND: "OK - Chat sent.",
    }
    if operation.operation is AdminOperation.PLAYER_BAN:
        if len(replies) != 2 or replies[-1].strip() != "OK - Player Banned.":
            return False
        player_id = operation.identity.key
        return re.fullmatch(
            rf'OK - Banned Player #{player_id} "[^"\r\n]*"\.',
            replies[0].strip(),
        ) is not None
    if operation.operation is AdminOperation.WEAPON_SET:
        expected = (
            "OK - All weapons availbility changed."
            if operation.text.startswith("WEAPON SET ALL ")
            else "OK - Weapon availbility changed."
        )
    else:
        expected = expected_by_operation.get(operation.operation)
    if expected is None:
        return False
    return tuple(reply.strip() for reply in replies) == (expected,)


def _environment(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the canonical RetailAdminSession against a live retail "
            "Joint Operations server. Default mode is read-only."
        )
    )
    parser.add_argument(
        "--host", default=_environment("WOLFRAT_HOST") or "127.0.0.1"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_environment("WOLFRAT_PORT") or "4000"),
    )
    parser.add_argument(
        "--username",
        default=_environment("WOLFRAT_USERNAME", "WOLFRAT_ADMIN_USER"),
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--full",
        action="store_true",
        help="enable independently verified, reversible mutation checks",
    )
    parser.add_argument(
        "--dummy-name",
        help="unique non-host player used by --full player checks",
    )
    parser.add_argument(
        "--player-disconnects",
        action="store_true",
        help="with --full, enable KILL/ZEROSCORE/PUNT on the dummy",
    )
    parser.add_argument(
        "--rejoin-timeout",
        type=float,
        default=180.0,
        help="seconds to wait for the punted dummy to reconnect",
    )
    return parser


def _config(arguments: argparse.Namespace) -> LiveConfig:
    username = arguments.username
    if not username:
        username = input("Retail admin username: ").strip()
    password = _environment("WOLFRAT_PASSWORD", "WOLFRAT_ADMIN_PASSWORD")
    if password is None:
        password = getpass("Retail admin password: ")
    if arguments.player_disconnects and not arguments.full:
        raise LiveConformanceError("--player-disconnects requires --full")
    if arguments.player_disconnects and not arguments.dummy_name:
        raise LiveConformanceError(
            "--player-disconnects requires --dummy-name"
        )
    if not (1 <= arguments.port <= 65535):
        raise LiveConformanceError("port must be between 1 and 65535")
    if arguments.timeout <= 0 or arguments.rejoin_timeout <= 0:
        raise LiveConformanceError("timeouts must be positive")
    return LiveConfig(
        host=arguments.host,
        port=arguments.port,
        username=username,
        password=password,
        timeout=arguments.timeout,
        full=arguments.full,
        dummy_name=arguments.dummy_name,
        player_disconnects=arguments.player_disconnects,
        rejoin_timeout=arguments.rejoin_timeout,
    )


def _redact(message: str, config: LiveConfig | None) -> str:
    if config is None:
        return message
    redacted = message
    for secret in (config.password, config.username):
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def main(argv: list[str] | None = None) -> int:
    config: LiveConfig | None = None
    suite: LiveRetailSuite | None = None
    try:
        arguments = _parser().parse_args(argv)
        config = _config(arguments)
        suite = LiveRetailSuite(config)
        baseline = suite.read_matrix()
        if config.full:
            suite.run_full(baseline)
        print(
            f"Live retail conformance complete: "
            f"{suite.passed} passed, {suite.skipped} skipped."
        )
        return 0
    except (
        LiveConformanceError,
        AdminSessionError,
        RetailProtocolError,
        OSError,
        TimeoutError,
        ValueError,
    ) as error:
        print(f"FAIL  {_redact(str(error), config)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; the session has been closed.", file=sys.stderr)
        return 130
    finally:
        if suite is not None:
            suite.close()


if __name__ == "__main__":
    raise SystemExit(main())
