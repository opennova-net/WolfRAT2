"""Supplementary static contracts for Qt-facing admin call sites."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "wolfrat" / "app.py"
TREE = ast.parse(APP_PATH.read_text(encoding="utf-8-sig"), filename=str(APP_PATH))


def _class(name: str) -> ast.ClassDef:
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    return next(
        node
        for node in _class(class_name).body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _attribute_name(node: ast.AST) -> str | None:
    return node.attr if isinstance(node, ast.Attribute) else None


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and _attribute_name(candidate.func) == name
    ]


def _is_server_owner(node: ast.AST) -> bool:
    """Recognize both direct and cross-widget references to the active facade."""

    if isinstance(node, ast.Name):
        return node.id == "server"
    if not isinstance(node, ast.Attribute) or node.attr != "server":
        return False

    root = node.value
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id == "self"


class DesktopAsyncContractTests(unittest.TestCase):
    def test_every_desktop_player_action_carries_displayed_identity(self):
        player_mutations = {
            "warn_player",
            "punt_player",
            "ban_player",
            "kill_player",
            "swap_player",
            "zero_player",
            "swap_and_kill",
        }
        identity_names = {"player_target", "target_entry"}
        violations = []
        checked = 0

        for call in (
            node
            for node in ast.walk(TREE)
            if isinstance(node, ast.Call)
            and _attribute_name(node.func) in player_mutations
            and _is_server_owner(node.func.value)
        ):
            checked += 1
            first = call.args[0] if call.args else None
            if (
                not isinstance(first, ast.Name)
                or first.id not in identity_names
            ):
                violations.append(
                    f"line {call.lineno}: "
                    f"{ast.unparse(call)} does not carry a displayed "
                    "PlayerEntry"
                )

        self.assertGreaterEqual(checked, 15)
        self.assertEqual([], violations, "\n".join(violations))
        self.assertGreaterEqual(
            sum(
                1
                for node in ast.walk(TREE)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "player_entry_from_legacy"
            ),
            4,
            "desktop adapters must capture id, name, and revision",
        )

    def test_server_connect_is_invoked_only_by_background_entrypoint(self):
        do_connect = _method("ServerTab", "_do_connect")
        background = _method("ServerTab", "_connect_in_background")
        initializer = _method("ServerTab", "__init__")

        self.assertEqual([], _calls(do_connect, "connect"))
        self.assertEqual(1, len(_calls(background, "connect")))
        self.assertTrue(
            _calls(do_connect, "start"),
            "the Connect button path must start a worker and return",
        )
        self.assertTrue(
            any(
                _attribute_name(call.func) == "connect"
                and any(
                    _attribute_name(argument) == "_finish_connect"
                    for argument in call.args
                )
                for call in ast.walk(initializer)
                if isinstance(call, ast.Call)
            ),
            "connection completion must be delivered to the Qt-thread slot",
        )
        self.assertEqual(
            [],
            _calls(do_connect, "result"),
            "the Qt button handler must never wait on a session Future",
        )

    def test_raw_chat_callback_crosses_an_explicit_queued_qt_signal(self):
        voting = _class("MapVotingTab")
        initializer = _method("MapVotingTab", "__init__")

        signal_assignment = next(
            (
                node
                for node in voting.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id == "raw_chat_signal"
                    for target in node.targets
                )
            ),
            None,
        )
        self.assertIsNotNone(signal_assignment)

        registrations = _calls(initializer, "set_raw_chat_callback")
        self.assertEqual(1, len(registrations))
        callback = registrations[0].args[0]
        self.assertEqual("emit", _attribute_name(callback))
        self.assertNotEqual("_on_raw_chat", _attribute_name(callback))

        signal_connections = [
            call
            for call in _calls(initializer, "connect")
            if isinstance(call.func, ast.Attribute)
            and _attribute_name(call.func.value) == "raw_chat_signal"
        ]
        self.assertEqual(1, len(signal_connections))
        self.assertTrue(
            any(
                isinstance(argument, ast.Attribute)
                and argument.attr == "QueuedConnection"
                for argument in signal_connections[0].args
            ),
            "raw chat delivery must be explicitly queued onto the GUI thread",
        )

    def test_raw_console_uses_explicit_ack_only_completion_mode(self):
        send_command = _method("ConsoleTab", "_send_command")
        accepted = _method("ConsoleTab", "_raw_command_accepted")

        raw_calls = _calls(send_command, "execute_raw")
        self.assertEqual(1, len(raw_calls))
        submit_calls = [
            call
            for call in ast.walk(send_command)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "submit_admin"
        ]
        self.assertEqual(1, len(submit_calls))
        policy = next(
            (
                keyword.value
                for keyword in submit_calls[0].keywords
                if keyword.arg == "policy"
            ),
            None,
        )
        self.assertIsInstance(policy, ast.Attribute)
        self.assertEqual("ACCEPTED", policy.attr)
        self.assertEqual([], _calls(send_command, "clear"))
        self.assertTrue(_calls(accepted, "clear"))

        submit_helper = next(
            node
            for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "submit_admin"
        )
        helper_source = ast.unparse(submit_helper)
        self.assertIn(
            "policy=CompletionPolicy.VERIFIED",
            helper_source,
        )

    def test_unreadable_console_time_effects_are_only_reported_as_accepted(self):
        settings_methods = (
            _method("SettingsTab", "_on_time_of_day"),
            _method("SettingsTab", "_on_time_rate"),
        )
        for method in settings_methods:
            submit_call = next(
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "submit_admin"
            )
            policy = next(
                (
                    keyword.value
                    for keyword in submit_call.keywords
                    if keyword.arg == "policy"
                ),
                None,
            )
            self.assertIsInstance(policy, ast.Attribute)
            self.assertEqual("ACCEPTED", policy.attr)
            self.assertIn("readback", ast.unparse(method).casefold())

        moderator = _method("ModsTab", "_check_mod_command")
        time_call = next(
            call
            for call in _calls(moderator, "_submit_mod_action")
            if any(
                _attribute_name(candidate.func) == "set_time_of_day"
                for candidate in ast.walk(call)
                if isinstance(candidate, ast.Call)
            )
        )
        policy = next(
            (
                keyword.value
                for keyword in time_call.keywords
                if keyword.arg == "policy"
            ),
            None,
        )
        self.assertIsInstance(policy, ast.Attribute)
        self.assertEqual("ACCEPTED", policy.attr)

    def test_every_active_retail_mutation_is_future_observed(self):
        mutation_names = {
            "execute_raw",
            "set_setting",
            "send_chat",
            "announce",
            "warn_player",
            "punt_player",
            "ban_player",
            "kill_player",
            "swap_player",
            "zero_player",
            "swap_and_kill",
            "add_mission",
            "remove_mission",
            "clear_missions",
            "set_next_mission",
            "cycle_mission",
            "switch_mission",
            "set_weapon",
            "set_all_weapons",
            "set_time_of_day",
            "set_time_rate",
        }
        gate_names = {"submit", "submit_admin", "_submit_mod_action"}
        factory_methods = {
            ("MissionsTab", "_send_mission_add"),
            ("MissionsTab", "_send_mission_add_to_server"),
        }
        violations = []
        checked = 0

        for class_node in (
            node for node in TREE.body if isinstance(node, ast.ClassDef)
        ):
            for method in (
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef)
            ):
                parents: dict[ast.AST, ast.AST] = {}
                for parent in ast.walk(method):
                    for child in ast.iter_child_nodes(parent):
                        parents[child] = parent

                for call in (
                    node
                    for node in ast.walk(method)
                    if isinstance(node, ast.Call)
                    and _attribute_name(node.func) in mutation_names
                ):
                    owner = call.func.value
                    if not _is_server_owner(owner):
                        continue
                    checked += 1
                    if (class_node.name, method.name) in factory_methods:
                        continue

                    ancestor = parents.get(call)
                    while ancestor is not None and not (
                        isinstance(ancestor, ast.Call)
                        and (
                            _attribute_name(ancestor.func) in gate_names
                            or (
                                isinstance(ancestor.func, ast.Name)
                                and ancestor.func.id in gate_names
                            )
                        )
                    ):
                        ancestor = parents.get(ancestor)
                    if ancestor is None:
                        violations.append(
                            f"{class_node.name}.{method.name}:{call.lineno} "
                            f"discards {call.func.attr}()"
                        )

        self.assertGreater(checked, 50)
        self.assertEqual([], violations, "\n".join(violations))

        app_source = APP_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("submit_team_workflow(", app_source)

    def test_warn_player_is_the_only_public_warning_message(self):
        """A semantic warning already sends CHAT SEND; do not announce twice."""
        warning_submissions = []
        for class_node in (
            node for node in TREE.body if isinstance(node, ast.ClassDef)
        ):
            for method in (
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef)
            ):
                parents: dict[ast.AST, ast.AST] = {}
                for parent in ast.walk(method):
                    for child in ast.iter_child_nodes(parent):
                        parents[child] = parent

                for warning_call in _calls(method, "warn_player"):
                    ancestor = parents.get(warning_call)
                    while ancestor is not None:
                        if isinstance(ancestor, ast.Call) and (
                            _attribute_name(ancestor.func)
                            in {"submit", "_submit_mod_action"}
                            or (
                                isinstance(ancestor.func, ast.Name)
                                and ancestor.func.id == "submit_admin"
                            )
                        ):
                            warning_submissions.append(ancestor)
                            break
                        ancestor = parents.get(ancestor)

        self.assertEqual(3, len(warning_submissions))
        for submission in warning_submissions:
            source = ast.unparse(submission)
            self.assertNotIn("_announce_admin_action", source)
            self.assertNotIn("_send_automod_announcement", source)
            self.assertFalse(
                any(
                    keyword.arg == "announcement"
                    for keyword in submission.keywords
                ),
                source,
            )

    def test_team_dialog_does_not_promise_chat_announcements(self):
        source = ast.unparse(_method("PlayersTab", "_mix_teams"))
        self.assertNotIn("Announce each swap", source)


if __name__ == "__main__":
    unittest.main()
