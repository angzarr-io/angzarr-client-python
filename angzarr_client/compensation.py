"""Compensation flow helpers for saga/PM rejection handling.

This module provides helpers for implementing compensation logic in aggregates
and process managers when saga/PM commands are rejected.

Usage in Aggregate:
    from angzarr_client import Aggregate, handles
    from angzarr_client.compensation import (
        CompensationContext,
        delegate_to_framework,
        emit_compensation_events,
    )

    class OrderAggregate(Aggregate[OrderState]):
        def handle_revocation(self, notification):
            ctx = CompensationContext.from_notification(notification)

            # Option 1: Emit compensation events based on rejected command type
            if ctx.rejected_command_type == "type.googleapis.com/orders.FulfillOrder":
                event = OrderCancelled(
                    order_id=self.order_id,
                    reason=f"Fulfillment failed: {ctx.rejection_reason}",
                )
                self._apply_and_record(event)
                return emit_compensation_events(self.event_book())

            # Option 2: Delegate to framework
            return delegate_to_framework(
                reason=f"No custom compensation for {ctx.rejected_command_type}"
            )

Usage in ProcessManager:
    from angzarr_client.process_manager import ProcessManager, handles
    from angzarr_client.compensation import (
        CompensationContext,
        pm_delegate_to_framework,
        pm_emit_compensation_events,
    )

    class OrderWorkflowPM(ProcessManager[WorkflowState]):
        def handle_revocation(self, notification):
            ctx = CompensationContext.from_notification(notification)

            # Record failure in PM state
            event = WorkflowStepFailed(
                source_domain=ctx.source_domain,
                reason=ctx.rejection_reason,
            )
            self._apply_and_record(event)

            # Return PM events + framework response
            return pm_emit_compensation_events(
                process_events=self.process_events(),
                also_emit_system_event=True,
            )
"""

from dataclasses import dataclass, field

from .helpers import TYPE_URL_PREFIX
from .proto.angzarr import command_handler_pb2 as command_handler
from .proto.angzarr import types_pb2 as types

# Audit finding #58: per `google.protobuf.Any` spec the URL carries the
# fully qualified proto type name verbatim (package prefix included).
# The pre-#58 short form was a Rust-side bug that this also matched —
# fixed in both languages.
_NOTIFICATION_WIRE_NAME = "angzarr_client.proto.angzarr.Notification"


@dataclass
class CompensationContext:
    """Extracted context from a rejection Notification.

    Provides easy access to compensation-relevant fields. Source aggregate info
    is extracted from the rejected command's angzarr_deferred header.
    """

    source_event_sequence: int
    """Sequence of the event that triggered the saga/PM flow."""

    rejection_reason: str
    """Why the command was rejected."""

    rejected_command: types.CommandBook | None
    """The command that was rejected (if available)."""

    source_aggregate: types.Cover | None
    """Cover of the aggregate that triggered the flow."""

    @classmethod
    def from_notification(
        cls, notification: types.Notification
    ) -> "CompensationContext":
        """Extract compensation context from a Notification.

        Source aggregate info is extracted from the rejected command's
        angzarr_deferred header, which is always set by the framework
        for saga/PM-produced commands.

        Args:
            notification: The notification containing RejectionNotification payload.

        Returns:
            CompensationContext with extracted fields.
        """
        rejection = types.RejectionNotification()
        if notification.HasField("payload"):
            notification.payload.Unpack(rejection)

        # Extract source info from angzarr_deferred header
        source_aggregate = None
        source_event_sequence = 0

        if rejection.HasField("rejected_command"):
            cmd = rejection.rejected_command
            if cmd.pages:
                header = cmd.pages[0].header
                if header.HasField("angzarr_deferred"):
                    deferred = header.angzarr_deferred
                    source_event_sequence = deferred.source_seq
                    if deferred.HasField("source"):
                        source_aggregate = deferred.source

        return cls(
            source_event_sequence=source_event_sequence,
            rejection_reason=rejection.rejection_reason,
            rejected_command=(
                rejection.rejected_command
                if rejection.HasField("rejected_command")
                else None
            ),
            source_aggregate=source_aggregate,
        )

    @property
    def rejected_command_type(self) -> str | None:
        """Get the type URL of the rejected command, if available."""
        if self.rejected_command and self.rejected_command.pages:
            page = self.rejected_command.pages[0]
            if page.HasField("command"):
                return page.command.type_url
        return None

    @property
    def source_domain(self) -> str | None:
        """Get the domain of the source aggregate, if available."""
        if self.source_aggregate:
            return self.source_aggregate.domain
        return None

    @property
    def source_root(self) -> bytes | None:
        """Get the root UUID bytes of the source aggregate, if available."""
        if self.source_aggregate and self.source_aggregate.HasField("root"):
            return self.source_aggregate.root.value
        return None

    @property
    def dispatch_key(self) -> str:
        """Build a dispatch key for routing rejection handlers.

        Returns:
            A key in format "domain/command" or empty string.
        """
        if not self.rejected_command or not self.rejected_command.HasField("cover"):
            return ""
        domain = self.rejected_command.cover.domain
        cmd_type = self.rejected_command_type
        if not domain or not cmd_type:
            return ""
        from .helpers import type_name_from_url

        return f"{domain}/{type_name_from_url(cmd_type)}"


# --- Aggregate helpers ---


def delegate_to_framework(
    reason: str,
    emit_system_event: bool = True,
    send_to_dead_letter: bool = False,
    escalate: bool = False,
    abort: bool = False,
) -> command_handler.BusinessResponse:
    """Create a response that delegates compensation to the framework.

    Use when the aggregate doesn't have custom compensation logic for a saga.
    The framework will emit a SagaCompensationFailed event to the fallback domain.

    Args:
        reason: Human-readable explanation for the delegation.
        emit_system_event: Emit SagaCompensationFailed to fallback domain.
        send_to_dead_letter: Move failed event to dead letter queue.
        escalate: Mark for operator intervention.
        abort: Stop the saga entirely without retry.

    Returns:
        BusinessResponse with revocation flags.
    """
    return command_handler.BusinessResponse(
        revocation=command_handler.RevocationResponse(
            emit_system_revocation=emit_system_event,
            send_to_dead_letter_queue=send_to_dead_letter,
            escalate=escalate,
            abort=abort,
            reason=reason,
        )
    )


def emit_compensation_events(
    event_book: types.EventBook,
) -> command_handler.BusinessResponse:
    """Create a response containing compensation events.

    Use when the aggregate emits events to record compensation.
    The framework will persist these events and NOT emit a system event.

    Args:
        event_book: EventBook containing compensation events.

    Returns:
        BusinessResponse with events.
    """
    return command_handler.BusinessResponse(events=event_book)


# --- Process Manager helpers ---


@dataclass
class RejectionHandlerResponse:
    """Response from rejection handlers.

    Can contain events (compensation), notification (upstream propagation), or both.

    Audit finding #56 (Option B — list[EventBook]): aligns with Rust's
    ``Vec<EventBook>``. Multiple books concatenate downstream; first
    non-empty book's cover wins.
    """

    events: list[types.EventBook] = field(default_factory=list)
    """Events to persist to own state (compensation)."""

    notification: types.Notification | None = None
    """Notification to forward upstream (rejection propagation)."""


@dataclass
class PMRevocationResponse:
    """Result from PM compensation helpers.

    Named type matching Go's PMRevocationResponse, replacing raw tuples
    for better discoverability and documentation.
    """

    process_events: types.EventBook | None = None
    """PM events to persist (may be None when delegating)."""

    revocation: command_handler.RevocationResponse | None = None
    """Framework action flags."""


def pm_delegate_to_framework(
    reason: str,
    emit_system_event: bool = True,
) -> PMRevocationResponse:
    """Create a PM response that delegates compensation to the framework.

    Use when the PM doesn't have custom compensation logic.

    Args:
        reason: Human-readable explanation for the delegation.
        emit_system_event: Emit SagaCompensationFailed to fallback domain.

    Returns:
        PMRevocationResponse with no process events, delegate to framework.
    """
    return PMRevocationResponse(
        process_events=None,
        revocation=command_handler.RevocationResponse(
            emit_system_revocation=emit_system_event,
            reason=reason,
        ),
    )


def pm_emit_compensation_events(
    process_events: types.EventBook,
    also_emit_system_event: bool = False,
    reason: str = "",
) -> PMRevocationResponse:
    """Create a PM response containing compensation events.

    Use when the PM emits events to record the compensation in its state.

    Args:
        process_events: EventBook containing PM compensation events.
        also_emit_system_event: Also emit SagaCompensationFailed.
        reason: Reason for system event (if emitting).

    Returns:
        PMRevocationResponse with events and revocation flags.
    """
    return PMRevocationResponse(
        process_events=process_events,
        revocation=command_handler.RevocationResponse(
            emit_system_revocation=also_emit_system_event,
            reason=reason,
        ),
    )


def is_notification(type_url: str) -> bool:
    """Check if a type URL refers to a rejection Notification.

    Cross-language alias for Rust's `is_notification(type_url)`. Useful for
    dispatch code that needs to branch on "is this a notification?" without
    unpacking the Any payload.
    """
    return type_url == f"{TYPE_URL_PREFIX}{_NOTIFICATION_WIRE_NAME}"
