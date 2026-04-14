"""Handler Protocol classes for component domain logic.

These Protocols define the interface that handler objects must implement.
Similar to Rust traits, they provide type-safe contracts for component handlers.

Usage:
    from angzarr_client import CommandHandlerDomainHandler, CommandHandlerRouter

    class PlayerHandler(CommandHandlerDomainHandler[PlayerState]):
        def command_types(self) -> list[str]:
            return ["RegisterPlayer", "DepositFunds"]

        def state_router(self) -> StateRouter[PlayerState]:
            return self._state_router

        def handle(self, cmd_book, payload, state, seq) -> EventBook:
            if payload.type_url.endswith("RegisterPlayer"):
                # Handle command...
                pass
            raise ValueError(f"Unknown command: {payload.type_url}")

        def on_rejected(self, notification, state, target_domain, target_command):
            return RejectionHandlerResponse()

    router = CommandHandlerRouter("player", "player", PlayerHandler())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, runtime_checkable

from google.protobuf.any_pb2 import Any

from .proto.angzarr import types_pb2 as types

if TYPE_CHECKING:
    from .compensation import RejectionHandlerResponse
    from .destinations import Destinations
    from .state_builder import StateRouter

S = TypeVar("S")
S_co = TypeVar("S_co", covariant=True)


# ============================================================================
# Handler Protocols (like Rust traits)
# ============================================================================


@runtime_checkable
class CommandHandlerDomainHandler(Protocol[S_co]):
    """Protocol for command handler domain handlers.

    Implementations provide command handling logic for a single domain.
    The handler is responsible for:
    - Declaring which command types it handles
    - Providing state reconstruction via StateRouter
    - Processing commands and returning events
    - Handling rejection notifications

    Example:
        class PlayerHandler(CommandHandlerDomainHandler[PlayerState]):
            def command_types(self) -> list[str]:
                return ["RegisterPlayer", "DepositFunds"]

            def state_router(self) -> StateRouter[PlayerState]:
                return StateRouter(PlayerState).on(PlayerRegistered, apply_registered)

            def handle(self, cmd_book, payload, state, seq) -> EventBook:
                # Dispatch to handlers based on type_url
                ...

            def on_rejected(self, notification, state, target_domain, target_command):
                return RejectionHandlerResponse()
    """

    def command_types(self) -> list[str]:
        """Return list of command type suffixes this handler processes."""
        ...

    def state_router(self) -> StateRouter[S_co]:
        """Return StateRouter for state reconstruction."""
        ...

    def handle(
        self,
        cmd_book: types.CommandBook,
        payload: Any,
        state: S_co,
        seq: int,
    ) -> types.EventBook:
        """Handle a command and return resulting events.

        Args:
            cmd_book: Full command book with cover and pages
            payload: Unpacked command Any from first page
            state: Current aggregate state (rebuilt from events)
            seq: Next sequence number for emitted events

        Returns:
            EventBook containing emitted events

        Raises:
            CommandRejectedError: If command should be rejected
            ValueError: If command type is unknown
        """
        ...

    def handle_fact(
        self,
        facts: types.EventBook,
        state: S_co,
    ) -> types.EventBook:
        """Handle injected facts and return the resulting EventBook.

        Facts are always accepted (cannot be rejected). Override to emit
        additional events or augment facts. Default: pass-through.

        Args:
            facts: The injected facts EventBook
            state: Current aggregate state

        Returns:
            EventBook with original facts plus any additional events
        """
        return facts

    def on_rejected(
        self,
        notification: types.Notification,
        state: S_co,
        target_domain: str,
        target_command: str,
    ) -> RejectionHandlerResponse:
        """Handle rejection notification for compensation.

        Args:
            notification: Notification containing RejectionNotification payload
            state: Current aggregate state
            target_domain: Domain of rejected command
            target_command: Type of rejected command

        Returns:
            RejectionHandlerResponse with compensation events or delegation flags
        """
        ...


@runtime_checkable
class ProcessManagerDomainHandler(Protocol, Generic[S]):
    """Protocol for process manager domain handlers.

    Implementations handle events from a specific source domain within a PM.
    PMs maintain state correlated by correlation_id across domains.

    Example:
        class OrderDomainHandler(ProcessManagerDomainHandler[WorkflowState]):
            def event_types(self) -> list[str]:
                return ["OrderCreated", "OrderCompleted"]

            def prepare(self, trigger, state, event) -> list[Cover]:
                return []

            def handle(self, trigger, state, event, destinations):
                return ProcessManagerResponse(
                    commands=[new_command_book("inventory", ReserveStock(...))]
                )
    """

    def event_types(self) -> list[str]:
        """Return list of event type suffixes this handler processes."""
        ...

    def prepare(
        self,
        trigger: types.EventBook,
        state: S,
        event: Any,
    ) -> list[types.Cover]:
        """Declare destination aggregates needed for handling.

        Args:
            trigger: Source EventBook with triggering events
            state: Current PM state (rebuilt from PM's own events)
            event: The specific event being processed

        Returns:
            List of Covers identifying destination aggregates to fetch
        """
        ...

    def handle(
        self,
        trigger: types.EventBook,
        state: S,
        event: Any,
        destinations: Destinations,
    ) -> ProcessManagerResponse:
        """Handle event and produce commands/events.

        Args:
            trigger: Source EventBook with triggering events
            state: Current PM state
            event: The specific event being processed
            destinations: Destinations context for command stamping

        Returns:
            ProcessManagerResponse with commands and/or PM events
        """
        ...

    def on_rejected(
        self,
        notification: types.Notification,
        state: S,
        target_domain: str,
        target_command: str,
    ) -> RejectionHandlerResponse:
        """Handle rejection notification for compensation.

        Called when a PM-issued command was rejected. Override to provide
        custom compensation logic.

        Default implementations should return empty RejectionHandlerResponse
        to delegate to framework.

        Args:
            notification: Notification containing RejectionNotification payload
            state: Current PM state
            target_domain: Domain of rejected command
            target_command: Type of rejected command

        Returns:
            RejectionHandlerResponse with compensation events or delegation flags
        """
        ...


@runtime_checkable
class SagaDomainHandler(Protocol):
    """Protocol for saga domain handlers.

    Implementations translate events from a source domain into commands
    for other domains. Sagas are stateless - each event is processed independently.

    Design Philosophy:
        Sagas are translators, NOT decision makers. They should NOT rebuild
        destination state to make decisions. They receive only destination
        sequences for command stamping. Business logic belongs in aggregates.

    Example:
        class OrderSagaHandler(SagaDomainHandler):
            def event_types(self) -> list[str]:
                return ["OrderCreated", "OrderCompleted"]

            def handle(self, source, event, destinations) -> SagaHandlerResponse:
                if type_matches(event, OrderCreated):
                    cmd = make_command_book("inventory", ReserveInventory(...))
                    destinations.stamp_command(cmd, "inventory")
                    return SagaHandlerResponse(commands=[cmd])
                return SagaHandlerResponse()

            def on_rejected(self, notification, target_domain, target_command):
                return RejectionHandlerResponse()
    """

    def event_types(self) -> list[str]:
        """Return list of event type suffixes this handler processes."""
        ...

    def handle(
        self,
        source: types.EventBook,
        event: Any,
        destinations: Destinations,
    ) -> SagaHandlerResponse:
        """Handle an event and produce commands.

        Args:
            source: EventBook containing the triggering event
            event: The specific event being processed
            destinations: Destinations context for command stamping

        Returns:
            SagaHandlerResponse with commands to send and/or facts to inject
        """
        ...

    def on_rejected(
        self,
        notification: types.Notification,
        target_domain: str,
        target_command: str,
    ) -> RejectionHandlerResponse:
        """Handle rejection notification for compensation.

        Called when a saga-issued command was rejected. Override to provide
        custom compensation logic.

        Args:
            notification: Notification containing RejectionNotification payload
            target_domain: Domain of rejected command
            target_command: Type of rejected command

        Returns:
            RejectionHandlerResponse with compensation events or delegation flags
        """
        ...


@runtime_checkable
class ProjectorDomainHandler(Protocol):
    """Protocol for projector domain handlers.

    Implementations project events from a source domain to external output.

    Example:
        class PlayerDomainHandler(ProjectorDomainHandler):
            def event_types(self) -> list[str]:
                return ["PlayerRegistered", "FundsDeposited"]

            def project(self, events) -> Projection:
                # Process events and update external state
                return Projection()
    """

    def event_types(self) -> list[str]:
        """Return list of event type suffixes this handler processes."""
        ...

    def project(
        self,
        events: types.EventBook,
    ) -> types.Projection:
        """Project events to external output.

        Args:
            events: EventBook containing events to project

        Returns:
            Projection result
        """
        ...


# ============================================================================
# Response types
# ============================================================================


class SagaHandlerResponse:
    """Response from a saga handler.

    Contains commands to send and/or facts (events) to inject.
    """

    def __init__(
        self,
        commands: list[types.CommandBook] | None = None,
        events: list[types.EventBook] | None = None,
    ):
        self.commands = commands or []
        self.events = events or []


class ProcessManagerResponse:
    """Response from a process manager handler.

    Contains commands to send and/or events to record in PM's own event stream.
    """

    def __init__(
        self,
        commands: list[types.CommandBook] | None = None,
        events: types.EventBook | None = None,
    ):
        self.commands = commands or []
        self.events = events
