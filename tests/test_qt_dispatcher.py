from concurrent.futures import Future
from types import SimpleNamespace
import threading

import pytest

from wolfrat.qt_dispatcher import CompletionPolicy, QtAdminDispatcher


def test_verified_completion_reaches_success_on_dispatcher_qt_thread(qtbot):
    errors = []
    observed = []
    qt_thread_id = threading.get_ident()
    future = Future()
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Change server setting",
        policy=CompletionPolicy.VERIFIED,
        on_success=lambda result: observed.append(
            (threading.get_ident(), result)
        ),
    )

    result = SimpleNamespace(accepted=True, verified=True)
    worker = threading.Thread(target=lambda: future.set_result(result))
    worker.start()
    worker.join()

    qtbot.waitUntil(lambda: len(observed) == 1)

    assert observed == [(qt_thread_id, result)]
    assert errors == []


def test_accepted_policy_does_not_require_typed_readback_verification(qtbot):
    errors = []
    observed = []
    future = Future()
    result = SimpleNamespace(accepted=True, verified=None)
    future.set_result(result)
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Run raw console command",
        policy=CompletionPolicy.ACCEPTED,
        on_success=observed.append,
    )

    qtbot.waitUntil(lambda: len(observed) == 1)

    assert observed == [result]
    assert errors == []


def test_verified_policy_reports_unverified_result_on_qt_thread(qtbot):
    errors = []
    failures = []
    successes = []
    qt_thread_id = threading.get_ident()
    future = Future()
    future.set_result(
        SimpleNamespace(
            accepted=True,
            verified=False,
            verification_error="server state did not change",
        )
    )
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Change server setting",
        policy=CompletionPolicy.VERIFIED,
        on_success=successes.append,
        on_failure=lambda message: failures.append(
            (threading.get_ident(), message)
        ),
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    message = (
        "Change server setting was not verified: "
        "server state did not change"
    )
    assert successes == []
    assert failures == [(qt_thread_id, message)]
    assert errors == [message]


def test_rejected_result_reports_retail_reply_for_either_policy(qtbot):
    errors = []
    failures = []
    successes = []
    future = Future()
    future.set_result(
        SimpleNamespace(
            accepted=False,
            verified=False,
            replies=("request received", "NO - player identity changed"),
        )
    )
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Kick player",
        policy=CompletionPolicy.ACCEPTED,
        on_success=successes.append,
        on_failure=failures.append,
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    message = "Kick player rejected: NO - player identity changed"
    assert successes == []
    assert failures == [message]
    assert errors == [message]


def test_synchronous_operation_failure_is_reported_without_escaping(qtbot):
    errors = []
    failures = []
    qt_thread_id = threading.get_ident()
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    def start_operation():
        raise ValueError("not connected")

    dispatcher.submit(
        start_operation,
        context="Change server setting",
        on_failure=lambda message: failures.append(
            (threading.get_ident(), message)
        ),
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    message = "Change server setting failed: not connected"
    assert failures == [(qt_thread_id, message)]
    assert errors == [message]


def test_operation_must_return_a_future(qtbot):
    errors = []
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: None,
        context="Change server setting",
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    assert errors == [
        "Change server setting failed: operation did not return a Future"
    ]


def test_team_workflow_is_started_and_observed_inside_the_dispatcher(qtbot):
    errors = []
    observed = []
    future = Future()
    workflow = SimpleNamespace(
        messages=("Swapping Alice", "Swapping Bob"),
        completion=future,
    )
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit_workflow(
        lambda: workflow,
        context="Mix teams",
        on_success=lambda messages: observed.append(messages),
    )
    future.set_result(SimpleNamespace(accepted=True, verified=True))

    qtbot.waitUntil(lambda: len(observed) == 1)

    assert observed == [workflow.messages]
    assert errors == []


def test_team_workflow_start_failure_uses_the_dispatcher_failure_path(qtbot):
    errors = []
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    def broken_workflow():
        raise ValueError("not enough players")

    dispatcher.submit_workflow(
        broken_workflow,
        context="Mix teams",
        on_success=lambda _messages: None,
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    assert errors == ["Mix teams failed: not enough players"]


def test_future_failure_is_reported_on_dispatcher_qt_thread(qtbot):
    errors = []
    failures = []
    qt_thread_id = threading.get_ident()
    future = Future()
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Change server setting",
        on_failure=lambda message: failures.append(
            (threading.get_ident(), message)
        ),
    )

    worker = threading.Thread(
        target=lambda: future.set_exception(TimeoutError("retail timed out"))
    )
    worker.start()
    worker.join()

    qtbot.waitUntil(lambda: len(errors) == 1)

    message = "Change server setting failed: retail timed out"
    assert failures == [(qt_thread_id, message)]
    assert errors == [message]


def test_success_callback_failure_is_routed_to_error_sink(qtbot):
    errors = []
    future = Future()
    future.set_result(SimpleNamespace(accepted=True, verified=True))
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    def broken_callback(_result):
        raise RuntimeError("render failed")

    dispatcher.submit(
        lambda: future,
        context="Change server setting",
        on_success=broken_callback,
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    assert errors == [
        "Change server setting completion callback failed: render failed"
    ]


def test_failure_callback_failure_is_routed_to_error_sink(qtbot):
    errors = []
    future = Future()
    future.set_result(
        SimpleNamespace(accepted=False, replies=("NO - stale player",))
    )
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    def broken_callback(_message):
        raise RuntimeError("rollback failed")

    dispatcher.submit(
        lambda: future,
        context="Kick player",
        policy=CompletionPolicy.ACCEPTED,
        on_failure=broken_callback,
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    assert errors == [
        "Kick player rejected: NO - stale player; "
        "failure callback failed: rollback failed"
    ]


def test_submit_rejects_unknown_completion_policy_before_starting_operation():
    started = []
    dispatcher = QtAdminDispatcher(error_sink=lambda _message: None)

    with pytest.raises(ValueError, match="CompletionPolicy"):
        dispatcher.submit(
            lambda: started.append(True),
            context="Unsafe operation",
            policy="accepted",
        )

    assert started == []


def test_verified_policy_fails_closed_when_result_has_no_verified_evidence(
    qtbot,
):
    errors = []
    future = Future()
    future.set_result(SimpleNamespace(accepted=True))
    dispatcher = QtAdminDispatcher(error_sink=errors.append)

    dispatcher.submit(
        lambda: future,
        context="Change server setting",
        policy=CompletionPolicy.VERIFIED,
    )

    qtbot.waitUntil(lambda: len(errors) == 1)

    assert errors == [
        "Change server setting was accepted but not verified by retail"
    ]
