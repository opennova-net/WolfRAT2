"""Qt-thread adapter for asynchronous retail admin operations."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from PyQt6.QtCore import QObject, Qt, pyqtSignal


class CompletionPolicy(Enum):
    """The evidence a result must carry before a success callback may run."""

    VERIFIED = "verified"
    ACCEPTED = "accepted"


class AdminWorkflow(Protocol):
    """A planned operation with one observable completion."""

    messages: tuple[str, ...]
    completion: Future[Any]


@dataclass(frozen=True)
class _Pending:
    context: str
    policy: CompletionPolicy
    on_success: Callable[[Any], None] | None
    on_failure: Callable[[str], None] | None


class QtAdminDispatcher(QObject):
    """Observe admin Futures and deliver their outcomes on this object's thread."""

    _completed = pyqtSignal(object, object, object)

    def __init__(
        self,
        *,
        error_sink: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._error_sink = error_sink
        self._completed.connect(
            self._deliver,
            Qt.ConnectionType.QueuedConnection,
        )

    def submit(
        self,
        operation: Callable[[], Future[Any]],
        *,
        context: str,
        policy: CompletionPolicy = CompletionPolicy.VERIFIED,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        """Start an operation; completion is observed only through callbacks."""

        if not isinstance(policy, CompletionPolicy):
            raise ValueError("policy must be a CompletionPolicy")
        pending = _Pending(context, policy, on_success, on_failure)
        try:
            future = operation()
        except BaseException as error:
            self._emit_completion(pending, None, error)
            return
        if not isinstance(future, Future):
            self._emit_completion(
                pending,
                None,
                TypeError("operation did not return a Future"),
            )
            return

        def finished(done: Future[Any]) -> None:
            try:
                result = done.result()
                error = None
            except BaseException as caught:
                result = None
                error = caught
            self._emit_completion(pending, result, error)

        future.add_done_callback(finished)

    def submit_workflow(
        self,
        operation: Callable[[], AdminWorkflow],
        *,
        context: str,
        on_success: Callable[[tuple[str, ...]], None],
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        """Start and observe a multi-step admin workflow through this seam."""

        workflow: AdminWorkflow | None = None

        def start() -> Future[Any]:
            nonlocal workflow
            workflow = operation()
            return workflow.completion

        def completed(_result: Any) -> None:
            if workflow is None:
                raise RuntimeError("workflow completed before it was started")
            on_success(tuple(workflow.messages))

        self.submit(
            start,
            context=context,
            policy=CompletionPolicy.VERIFIED,
            on_success=completed,
            on_failure=on_failure,
        )

    def _emit_completion(
        self,
        pending: _Pending,
        result: Any,
        error: BaseException | None,
    ) -> None:
        try:
            self._completed.emit(pending, result, error)
        except RuntimeError:
            # The owning widget may have been deleted while retail completed.
            return

    def _deliver(
        self,
        pending: _Pending,
        result: Any,
        error: BaseException | None,
    ) -> None:
        if error is not None:
            self._report_failure(
                pending,
                f"{pending.context} failed: {error}",
            )
            return
        if getattr(result, "accepted", False) is not True:
            replies = getattr(result, "replies", ())
            detail = (
                replies[-1]
                if replies
                else "retail server rejected the operation"
            )
            self._report_failure(
                pending,
                f"{pending.context} rejected: {detail}",
            )
            return
        if (
            pending.policy is CompletionPolicy.VERIFIED
            and getattr(result, "verified", None) is not True
        ):
            detail = getattr(result, "verification_error", None)
            if detail:
                message = f"{pending.context} was not verified: {detail}"
            else:
                message = (
                    f"{pending.context} was accepted but not verified "
                    "by retail"
                )
            self._report_failure(pending, message)
            return
        if pending.on_success is not None:
            try:
                pending.on_success(result)
            except Exception as callback_error:
                self._error_sink(
                    f"{pending.context} completion callback failed: "
                    f"{callback_error}"
                )

    def _report_failure(self, pending: _Pending, message: str) -> None:
        if pending.on_failure is not None:
            try:
                pending.on_failure(message)
            except Exception as callback_error:
                message = (
                    f"{message}; failure callback failed: "
                    f"{callback_error}"
                )
        self._error_sink(message)
