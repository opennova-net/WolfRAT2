import unittest
from concurrent.futures import Future
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

from wolfrat.admin_commands import (
    AdminCommands,
    AdminOperation,
    AvailableMission,
    GameSettings,
    MissionEntry,
    PlayerEntry,
    WeaponEntry,
    WeaponMode,
)
from wolfrat.admin_session import (
    AdminSessionError,
    CommandResult,
    RetailProtocolError,
    StaleIdentityError,
)

from tests.live_retail import (
    LiveConfig,
    LiveConformanceError,
    LiveRetailSuite,
    _alternate_setting,
    _chat_acknowledged,
    _config,
    _mutation_acknowledged,
    _parser,
    _setting_write_value,
    _settings_equal,
    main,
)


def _completed(value=None, *, error=None):
    future = Future()
    if error is None:
        future.set_result(value)
    else:
        future.set_exception(error)
    return future


class _StaticSession:
    def __init__(self, result):
        self.result = result
        self.closed = False

    def execute(self, operation):
        return _completed(self.result)

    def close(self):
        self.closed = True


class _SettingsServer:
    def __init__(self, *, fail_after_apply=True):
        self.settings = {"FriendlyFire": "0"}
        self.fail_after_apply = fail_after_apply
        self.sessions = []

    def new_session(self):
        session = _SettingsSession(self)
        self.sessions.append(session)
        return session


class _SettingsSession:
    def __init__(self, server):
        self.server = server
        self.closed = False

    def execute(self, operation):
        if operation.operation is AdminOperation.GET_GAMESETTINGS:
            value = GameSettings(self.server.settings, revision=1)
            return _completed(
                CommandResult(
                    operation.operation,
                    ("FriendlyFire = " + self.server.settings["FriendlyFire"],),
                    True,
                    value,
                )
            )
        if operation.operation is AdminOperation.SET_SETTING:
            _set, key, value = operation.text.split(" ", 2)
            self.server.settings[key] = value
            if self.server.fail_after_apply:
                self.server.fail_after_apply = False
                return _completed(error=AdminSessionError("reply lost"))
            return _completed(
                CommandResult(
                    operation.operation,
                    ("OK - Setting Changed.",),
                    True,
                )
            )
        raise AssertionError(operation.text)

    def close(self):
        self.closed = True


class _MissionServer:
    def __init__(
        self, *, fail_after_add=True, fail_after_remove=False
    ):
        self.missions = [
            MissionEntry(
                0,
                "ORIG.BMS",
                is_current=True,
                revision=1,
            )
        ]
        self.fail_after_add = fail_after_add
        self.fail_after_remove = fail_after_remove
        self.sessions = []

    def new_session(self):
        session = _MissionSession(self)
        self.sessions.append(session)
        return session


class _MissionSession:
    def __init__(self, server):
        self.server = server
        self.available_loaded = False

    def execute(self, operation):
        if operation.operation is AdminOperation.MISSION_AVAILABLE:
            self.available_loaded = True
            return _completed(
                CommandResult(
                    operation.operation,
                    ("mission available",),
                    True,
                    (
                        AvailableMission(
                            4, "CP08.BMS", revision=1
                        ),
                    ),
                )
            )
        if operation.operation is AdminOperation.MISSION_LIST:
            return _completed(
                CommandResult(
                    operation.operation,
                    ("mission list",),
                    True,
                    tuple(self.server.missions),
                )
            )
        if operation.operation is AdminOperation.MISSION_ADD:
            if not self.available_loaded:
                return _completed(
                    error=StaleIdentityError(
                        "available mission is stale after reconnect"
                    )
                )
            next_index = max(
                (mission.queue_index for mission in self.server.missions),
                default=-1,
            ) + 1
            self.server.missions.append(
                MissionEntry(next_index, "CP08.BMS", revision=1)
            )
            if self.server.fail_after_add:
                self.server.fail_after_add = False
                return _completed(error=AdminSessionError("add reply lost"))
            return _completed(
                CommandResult(
                    operation.operation,
                    ("OK - Entry Added",),
                    True,
                )
            )
        if operation.operation is AdminOperation.MISSION_REMOVE:
            target = operation.identity.key
            self.server.missions = [
                mission
                for mission in self.server.missions
                if mission.queue_index != target
            ]
            if self.server.fail_after_remove:
                self.server.fail_after_remove = False
                return _completed(
                    error=AdminSessionError("remove reply lost")
                )
            return _completed(
                CommandResult(
                    operation.operation,
                    ("OK - Mission Removed.",),
                    True,
                )
            )
        raise AssertionError(operation.text)

    def close(self):
        pass


class _WeaponServer:
    def __init__(
        self,
        *,
        lose_first_single_ack=False,
        lose_all_ack=False,
        fail_first_restore_refresh_after_all=False,
        fail_first_restore_after_all=False,
    ):
        self.weapons = {
            1: ("M4", WeaponMode.ALWAYS),
            2: ("M9", WeaponMode.ARMORY),
        }
        self.lose_first_single_ack = lose_first_single_ack
        self.lose_all_ack = lose_all_ack
        self.fail_first_restore_refresh_after_all = (
            fail_first_restore_refresh_after_all
        )
        self.fail_first_restore_after_all = fail_first_restore_after_all
        self.after_all = False
        self.after_all_queries = 0
        self.set_targets = []
        self.query_count = 0
        self.sessions = []

    def new_session(self):
        session = _WeaponSession(self)
        self.sessions.append(session)
        return session

    def entries(self):
        return tuple(
            WeaponEntry(admdef_id, name, mode, revision=1)
            for admdef_id, (name, mode) in sorted(self.weapons.items())
        )


class _WeaponSession:
    def __init__(self, server):
        self.server = server

    def execute(self, operation):
        if operation.operation is AdminOperation.WEAPON_LIST:
            self.server.query_count += 1
            if self.server.after_all:
                self.server.after_all_queries += 1
                if (
                    self.server.fail_first_restore_refresh_after_all
                    and self.server.after_all_queries == 2
                ):
                    return _completed(
                        error=AdminSessionError(
                            "restore refresh failed"
                        )
                    )
            return _completed(
                CommandResult(
                    operation.operation,
                    ("weapon list",),
                    True,
                    self.server.entries(),
                )
            )
        if operation.operation is AdminOperation.WEAPON_SET:
            _weapon, _set, target, mode_name = operation.text.split()
            self.server.set_targets.append(target)
            if target == "ALL":
                for admdef_id, (name, _mode) in self.server.weapons.items():
                    self.server.weapons[admdef_id] = (
                        name,
                        WeaponMode[mode_name],
                    )
                self.server.after_all = True
                if self.server.lose_all_ack:
                    self.server.lose_all_ack = False
                    return _completed(
                        error=AdminSessionError("all reply lost")
                    )
                return _completed(
                    CommandResult(
                        operation.operation,
                        ("OK - All weapons availbility changed.",),
                        True,
                    )
                )

            admdef_id = int(target)
            if (
                self.server.after_all
                and self.server.fail_first_restore_after_all
                and admdef_id == 1
            ):
                self.server.fail_first_restore_after_all = False
                return _completed(
                    error=AdminSessionError("restore set failed")
                )
            name, _mode = self.server.weapons[admdef_id]
            self.server.weapons[admdef_id] = (
                name,
                WeaponMode[mode_name],
            )
            if self.server.lose_first_single_ack:
                self.server.lose_first_single_ack = False
                return _completed(
                    error=AdminSessionError("weapon reply lost")
                )
            return _completed(
                CommandResult(
                    operation.operation,
                    ("OK - Weapon availbility changed.",),
                    True,
                )
            )
        raise AssertionError(operation.text)

    def close(self):
        pass


class _PlayerServer:
    def __init__(self, *, lose_swap_ack=True):
        self.players = {
            0: PlayerEntry(0, "host", 0, revision=1),
            7: PlayerEntry(
                7,
                "biggy",
                1,
                kills=2,
                deaths=3,
                revision=1,
            ),
        }
        self.lose_swap_ack = lose_swap_ack
        self.sessions = []

    def new_session(self):
        session = _PlayerSession(self)
        self.sessions.append(session)
        return session


class _PlayerSession:
    def __init__(self, server):
        self.server = server

    def execute(self, operation):
        if operation.operation is AdminOperation.PLAYER_LIST:
            return _completed(
                CommandResult(
                    operation.operation,
                    ("player list",),
                    True,
                    tuple(self.server.players.values()),
                )
            )
        if operation.operation is AdminOperation.PLAYER_SWAPTEAM:
            player_id = operation.identity.key
            current = self.server.players[player_id]
            self.server.players[player_id] = PlayerEntry(
                current.server_id,
                current.name,
                2 if current.team == 1 else 1,
                current.player_class,
                current.kills,
                current.deaths,
                current.ping,
                revision=1,
            )
            if self.server.lose_swap_ack:
                self.server.lose_swap_ack = False
                return _completed(
                    error=AdminSessionError("swap reply lost")
                )
            return _completed(
                CommandResult(
                    operation.operation,
                    ("OK - Player Swapped.",),
                    True,
                )
            )
        raise AssertionError(operation.text)

    def close(self):
        pass


def _live_config(**changes):
    values = {
        "host": "127.0.0.1",
        "port": 4000,
        "username": "admin",
        "password": "secret",
        "timeout": 0.1,
        "full": True,
        "dummy_name": None,
        "player_disconnects": False,
        "rejoin_timeout": 0.1,
    }
    values.update(changes)
    return LiveConfig(**values)


class LiveRetailConfigurationTests(unittest.TestCase):
    def test_full_non_player_matrix_does_not_require_a_dummy(self):
        with patch.dict(
            "os.environ",
            {
                "WOLFRAT_USERNAME": "admin",
                "WOLFRAT_PASSWORD": "secret",
            },
            clear=False,
        ):
            arguments = _parser().parse_args(["--full"])
            config = _config(arguments)

        self.assertTrue(config.full)
        self.assertIsNone(config.dummy_name)

    def test_game_time_uses_the_total_component_for_write_and_readback(self):
        self.assertEqual(30, _setting_write_value("GameTime", "0/30"))
        self.assertEqual(1, _alternate_setting("GameTime", "0/30"))
        self.assertTrue(_settings_equal("GameTime", "0/30", 30))
        self.assertFalse(_settings_equal("GameTime", "0/29", 30))

    def test_chat_requires_the_command_specific_retail_ack(self):
        self.assertTrue(_chat_acknowledged(("OK - Chat sent.",)))
        self.assertFalse(_chat_acknowledged(("ok - chat sent.",)))
        self.assertFalse(_chat_acknowledged(("OK - Command executed.",)))
        self.assertFalse(_chat_acknowledged(("ERROR - No chat",)))

    def test_setting_requires_the_command_specific_retail_ack(self):
        operation = AdminCommands.set_setting("FriendlyFire", 1)

        self.assertTrue(
            _mutation_acknowledged(
                operation, ("OK - Setting Changed.",)
            )
        )
        self.assertFalse(
            _mutation_acknowledged(operation, ("OK",))
        )

    def test_every_live_mutation_has_an_exact_retail_ack_oracle(self):
        available = AvailableMission(2, "CP08.BMS", revision=1)
        mission = MissionEntry(3, "CP08.BMS", revision=1)
        player = PlayerEntry(7, "biggy", 1, revision=1)
        weapon = WeaponEntry(9, "M4", WeaponMode.ARMORY, revision=1)
        cases = (
            (
                AdminCommands.add_mission(available),
                ("OK - Entry Added",),
            ),
            (
                AdminCommands.remove_mission(mission),
                ("OK - Mission Removed.",),
            ),
            (
                AdminCommands.clear_missions(),
                ("OK - Mission list reset.",),
            ),
            (
                AdminCommands.set_next_mission(mission),
                ("OK - Next Mission Set.",),
            ),
            (
                AdminCommands.cycle_mission(),
                ("OK - Server is cycling...",),
            ),
            (
                AdminCommands.punt(player),
                ("OK - Player punted.",),
            ),
            (
                AdminCommands.swap_team(player),
                ("OK - Player Swapped.",),
            ),
            (
                AdminCommands.kill(player),
                ("OK - Player Killed.",),
            ),
            (
                AdminCommands.zero_score(player),
                ("OK - Player Zeroed.",),
            ),
            (
                AdminCommands.set_weapon(weapon, WeaponMode.NEVER),
                ("OK - Weapon availbility changed.",),
            ),
            (
                AdminCommands.set_all_weapons(WeaponMode.NEVER),
                ("OK - All weapons availbility changed.",),
            ),
            (
                AdminCommands.send_chat("hello"),
                ("OK - Chat sent.",),
            ),
        )

        for operation, replies in cases:
            with self.subTest(command=operation.text):
                self.assertTrue(
                    _mutation_acknowledged(operation, replies)
                )
                self.assertFalse(
                    _mutation_acknowledged(
                        operation, ("OK - Command executed.",)
                    )
                )

        ban = AdminCommands.ban(player)
        self.assertTrue(
            _mutation_acknowledged(
                ban,
                (
                    'OK - Banned Player #7 "biggy".',
                    "OK - Player Banned.",
                ),
            )
        )
        self.assertFalse(
            _mutation_acknowledged(
                ban,
                (
                    'OK - Banned Player #7 "biggy".',
                    "OK - Command executed.",
                ),
            )
        )

    def test_execute_rejects_a_generic_ok_for_a_typed_mutation(self):
        operation = AdminCommands.set_setting("FriendlyFire", 1)
        session = _StaticSession(
            CommandResult(
                operation.operation,
                ("OK",),
                True,
            )
        )
        suite = LiveRetailSuite(
            _live_config(), session_factory=lambda: session
        )
        self.addCleanup(suite.close)

        with self.assertRaisesRegex(
            LiveConformanceError, "command-specific acknowledgement"
        ):
            suite.execute("SET FriendlyFire", operation)

    def test_setting_is_restored_when_mutation_applies_but_ack_is_lost(self):
        server = _SettingsServer()
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)

        with patch(
            "tests.live_retail.CANONICAL_SETTING_KEYS",
            ("FriendlyFire",),
        ):
            with self.assertRaisesRegex(AdminSessionError, "reply lost"):
                suite._settings_roundtrip(
                    GameSettings({"FriendlyFire": "0"}, revision=1)
                )

        self.assertEqual({"FriendlyFire": "0"}, server.settings)
        self.assertEqual(2, len(server.sessions))

    def test_added_mission_is_removed_when_add_ack_is_lost(self):
        server = _MissionServer()
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)
        original = tuple(server.missions)
        available = (
            AvailableMission(4, "CP08.BMS", revision=1),
        )

        with self.assertRaisesRegex(AdminSessionError, "add reply lost"):
            suite._mission_add_remove_roundtrip(original, available)

        self.assertEqual(original, tuple(server.missions))
        self.assertEqual(2, len(server.sessions))

    def test_removed_mission_cleanup_survives_a_lost_remove_ack(self):
        server = _MissionServer(
            fail_after_add=False,
            fail_after_remove=True,
        )
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)
        original = tuple(server.missions)

        with self.assertRaisesRegex(
            AdminSessionError, "remove reply lost"
        ):
            suite._mission_add_remove_roundtrip(
                original,
                (AvailableMission(4, "CP08.BMS", revision=1),),
            )

        self.assertEqual(original, tuple(server.missions))

    def test_single_weapon_is_restored_when_mutation_ack_is_lost(self):
        server = _WeaponServer(lose_first_single_ack=True)
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)
        original = server.entries()

        with self.assertRaisesRegex(
            AdminSessionError, "weapon reply lost"
        ):
            suite._weapons_roundtrip(original)

        self.assertEqual(
            {1: WeaponMode.ALWAYS, 2: WeaponMode.ARMORY},
            {
                admdef_id: mode
                for admdef_id, (_name, mode) in server.weapons.items()
            },
        )
        self.assertEqual(2, len(server.sessions))

    def test_set_all_cleanup_attempts_every_weapon_and_final_audit(self):
        server = _WeaponServer(fail_first_restore_after_all=True)
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)

        with self.assertRaises(LiveConformanceError):
            suite._weapons_roundtrip(server.entries())

        all_position = server.set_targets.index("ALL")
        self.assertIn("1", server.set_targets[all_position + 1 :])
        self.assertIn("2", server.set_targets[all_position + 1 :])
        self.assertEqual(WeaponMode.ARMORY, server.weapons[2][1])
        self.assertEqual(2, len(server.sessions))
        self.assertGreaterEqual(server.query_count, 1)

    def test_set_all_is_restored_when_mutation_ack_is_lost(self):
        server = _WeaponServer(lose_all_ack=True)
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)

        with self.assertRaisesRegex(AdminSessionError, "all reply lost"):
            suite._weapons_roundtrip(server.entries())

        self.assertEqual(
            {1: WeaponMode.ALWAYS, 2: WeaponMode.ARMORY},
            {
                admdef_id: mode
                for admdef_id, (_name, mode) in server.weapons.items()
            },
        )
        self.assertEqual(2, len(server.sessions))

    def test_set_all_cleanup_continues_after_a_refresh_failure(self):
        server = _WeaponServer(
            fail_first_restore_refresh_after_all=True
        )
        suite = LiveRetailSuite(
            _live_config(), session_factory=server.new_session
        )
        self.addCleanup(suite.close)

        with self.assertRaisesRegex(
            AdminSessionError, "restore refresh failed"
        ):
            suite._weapons_roundtrip(server.entries())

        all_position = server.set_targets.index("ALL")
        self.assertIn("2", server.set_targets[all_position + 1 :])
        self.assertEqual(WeaponMode.ARMORY, server.weapons[2][1])
        self.assertEqual(2, len(server.sessions))

    def test_player_team_is_restored_when_swap_ack_is_lost(self):
        server = _PlayerServer()
        suite = LiveRetailSuite(
            _live_config(dummy_name="biggy"),
            session_factory=server.new_session,
        )
        self.addCleanup(suite.close)

        with self.assertRaisesRegex(
            AdminSessionError, "swap reply lost"
        ):
            suite._player_roundtrip()

        self.assertEqual(1, server.players[7].team)
        self.assertEqual(2, len(server.sessions))

    def test_cli_redacts_session_and_protocol_failures(self):
        for error_type in (AdminSessionError, RetailProtocolError):
            with self.subTest(error=error_type.__name__):
                stderr = StringIO()
                error = error_type(
                    "admin could not authenticate with secret"
                )
                with (
                    patch.dict(
                        "os.environ",
                        {"WOLFRAT_PASSWORD": "secret"},
                        clear=False,
                    ),
                    patch(
                        "tests.live_retail.RetailAdminSession",
                        side_effect=error,
                    ),
                    redirect_stderr(stderr),
                ):
                    exit_code = main(["--username", "admin"])

                self.assertEqual(1, exit_code)
                self.assertNotIn("admin", stderr.getvalue())
                self.assertNotIn("secret", stderr.getvalue())
                self.assertIn("<redacted>", stderr.getvalue())

    def test_normal_roundtrips_preserve_one_persistent_session(self):
        settings_server = _SettingsServer(fail_after_apply=False)
        settings_suite = LiveRetailSuite(
            _live_config(),
            session_factory=settings_server.new_session,
        )
        self.addCleanup(settings_suite.close)
        with patch(
            "tests.live_retail.CANONICAL_SETTING_KEYS",
            ("FriendlyFire",),
        ):
            settings_suite._settings_roundtrip(
                GameSettings({"FriendlyFire": "0"}, revision=1)
            )
        self.assertEqual(1, len(settings_server.sessions))

        mission_server = _MissionServer(fail_after_add=False)
        mission_suite = LiveRetailSuite(
            _live_config(),
            session_factory=mission_server.new_session,
        )
        self.addCleanup(mission_suite.close)
        mission_suite._mission_add_remove_roundtrip(
            tuple(mission_server.missions),
            (AvailableMission(4, "CP08.BMS", revision=1),),
        )
        self.assertEqual(1, len(mission_server.sessions))

        weapon_server = _WeaponServer()
        weapon_suite = LiveRetailSuite(
            _live_config(),
            session_factory=weapon_server.new_session,
        )
        self.addCleanup(weapon_suite.close)
        weapon_suite._weapons_roundtrip(weapon_server.entries())
        self.assertEqual(1, len(weapon_server.sessions))

        player_server = _PlayerServer(lose_swap_ack=False)
        player_suite = LiveRetailSuite(
            _live_config(dummy_name="biggy"),
            session_factory=player_server.new_session,
        )
        self.addCleanup(player_suite.close)
        player_suite._player_roundtrip()
        self.assertEqual(1, len(player_server.sessions))


if __name__ == "__main__":
    unittest.main()
