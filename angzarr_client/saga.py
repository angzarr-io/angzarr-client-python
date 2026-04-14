"""Base Saga class for event-driven command production.

Sagas translate events from one domain into commands for another domain.
They are stateless - each event is processed independently.

Design Philosophy:
    Sagas are translators, NOT decision makers. They should NOT rebuild destination
    state to make business decisions. The framework provides only destination
    sequences for command stamping. Business logic belongs in aggregates.

Router Pattern: Saga follows the SINGLE-DOMAIN OO pattern.
- One input domain: use @domain class decorator
- One output_domain: use @output_domain class decorator
- Uses @handles decorator for handler registration

Example usage (simple saga):
    from angzarr_client.saga import Saga, domain, output_domain, handles

    @domain("order")
    @output_domain("fulfillment")
    class OrderFulfillmentSaga(Saga):
        name = "saga-order-fulfillment"

        @handles(OrderCompleted)
        def handle_completed(self, event: OrderCompleted) -> CreateShipment:
            return CreateShipment(order_id=event.order_id)

Example usage (saga with destination sequences):
    from angzarr_client.saga import Saga, domain, output_domain, handles
    from angzarr_client.destinations import Destinations

    @domain("table")
    @output_domain("hand")
    class TableHandSaga(Saga):
        name = "saga-table-hand"

        @handles(HandStarted)
        def handle_hand_started(
            self, event: HandStarted, destinations: Destinations
        ) -> DealCards:
            cmd = DealCards(table_root=event.hand_root, ...)
            destinations.stamp_command(cmd, "hand")
            return cmd

"""

from __future__ import annotations

import inspect
from abc import ABC

from google.protobuf.any_pb2 import Any

from .destinations import Destinations
from .helpers import TYPE_URL_PREFIX
from .proto.angzarr import saga_pb2
from .proto.angzarr import types_pb2 as types
from .router import (
    _pack_any,
    domain,
    handles,
    output_domain,
)

# Re-export decorators
__all__ = ["Saga", "domain", "output_domain", "handles"]


class Saga(ABC):
    """Base class for stateless event-to-command sagas.

    Router Pattern: Follows the SINGLE-DOMAIN OO pattern.

    Saga-specific additions:
    - @output_domain: target domain for emitted commands
    - Auto-packing: returned commands are automatically packed into CommandBooks

    Provides:
    - Event dispatch via @handles decorated methods
    - Command packing into CommandBook
    - Descriptor generation for topology discovery

    Subclasses must:
    - Use @domain class decorator for input domain
    - Use @output_domain class decorator for output domain
    - Set `name` class attribute (e.g., "saga-order-fulfillment")
    - Decorate event handlers with `@handles(EventType)`

    Usage (simple):
        @domain("order")
        @output_domain("fulfillment")
        class OrderFulfillmentSaga(Saga):
            name = "saga-order-fulfillment"

            @handles(OrderCompleted)
            def handle_completed(self, event: OrderCompleted) -> CreateShipment:
                return CreateShipment(order_id=event.order_id)

    Usage (with destination sequences):
        @domain("table")
        @output_domain("hand")
        class TableHandSaga(Saga):
            name = "saga-table-hand"

            @handles(HandStarted)
            def handle_hand_started(
                self, event: HandStarted, destinations: Destinations
            ) -> DealCards:
                cmd = DealCards(table_root=event.hand_root, ...)
                destinations.stamp_command(cmd, "hand")
                return cmd

    """

    name: str
    _domain: str = None  # Set by @domain decorator
    _output_domain: str = None  # Set by @output_domain decorator
    _dispatch_table: dict[str, tuple[str, type]] = {}
    _validated: bool = False

    def __init__(self) -> None:
        """Initialize saga instance with empty event accumulator."""
        self._events: list[types.EventBook] = []

    def emit_event(self, event: types.EventBook) -> None:
        """Emit a fact (event to inject to another aggregate).

        Args:
            event: EventBook containing the event to inject.
                   The Cover should specify target domain and root.
        """
        self._events.append(event)

    @property
    def input_domain(self) -> str:
        """Get input domain (from @domain decorator)."""
        return self._domain

    @classmethod
    def _get_output_domain(cls) -> str:
        """Get output domain (from @output_domain decorator)."""
        return cls._output_domain

    @classmethod
    def _ensure_configured(cls) -> None:
        """Validate configuration at first use (lazy validation)."""
        if cls._validated:
            return

        if getattr(cls, "_domain", None) is None:
            raise TypeError(f"{cls.__name__} must use @domain decorator")

        if getattr(cls, "_output_domain", None) is None:
            raise TypeError(f"{cls.__name__} must use @output_domain decorator")

        cls._validated = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract intermediate classes
        if inspect.isabstract(cls):
            return

        # Validate name attribute (required at definition time)
        if not getattr(cls, "name", None):
            raise TypeError(f"{cls.__name__} must define 'name' class attribute")

        # Build dispatch tables (decorators have run by now)
        cls._dispatch_table = cls._build_dispatch_table()
        cls._validated = False

    @classmethod
    def _build_dispatch_table(cls) -> dict[str, tuple[str, type]]:
        """Scan for @handles methods and build dispatch table."""
        table = {}
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)
            if callable(attr) and getattr(attr, "_is_handler", False):
                event_type = attr._event_type
                full_name = event_type.DESCRIPTOR.full_name
                if full_name in table:
                    raise TypeError(
                        f"{cls.__name__}: duplicate handler for {full_name}"
                    )
                table[full_name] = (attr_name, event_type)
        return table

    def dispatch(
        self,
        event_any: Any,
        root: bytes = None,
        correlation_id: str = "",
        destination_sequences: dict[str, int] = None,
    ) -> list[types.CommandBook]:
        """Dispatch event to matching @handles method.

        Args:
            event_any: Packed event as google.protobuf.Any
            root: Source aggregate root (passed to command cover)
            correlation_id: Correlation ID for the workflow
            destination_sequences: Map of domain to next sequence number

        Returns:
            List of CommandBooks to send.
        """
        type_url = event_any.type_url
        destinations = Destinations(destination_sequences or {})

        for full_name, (method_name, event_type) in self._dispatch_table.items():
            if type_url == TYPE_URL_PREFIX + full_name:
                # Unpack event
                event = event_type()
                event_any.Unpack(event)

                # Check if handler accepts destinations parameter
                method = getattr(self, method_name)
                sig = inspect.signature(method)
                params = list(sig.parameters.keys())

                # Call handler with appropriate parameters
                if "destinations" in params:
                    result = method(event, destinations=destinations)
                else:
                    result = method(event)

                # Pack result into CommandBooks
                return self._pack_commands(result, root, correlation_id)

        # No handler found - return empty (saga may not care about all events)
        return []

    def _pack_commands(
        self,
        result,
        root: bytes = None,
        correlation_id: str = "",
    ) -> list[types.CommandBook]:
        """Pack command(s) into CommandBooks."""
        if result is None:
            return []

        # Handle pre-packed CommandBooks (advanced usage)
        if isinstance(result, types.CommandBook):
            return [result]
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], types.CommandBook)
        ):
            return result

        commands = result if isinstance(result, tuple) else (result,)
        books = []

        for cmd in commands:
            cmd_any = _pack_any(cmd)
            cover = types.Cover(
                domain=self._get_output_domain(),
                correlation_id=correlation_id,
            )
            if root:
                cover.root.value = root

            book = types.CommandBook(
                cover=cover,
                pages=[types.CommandPage(command=cmd_any)],
            )
            books.append(book)

        return books

    @classmethod
    def handle(
        cls,
        source: types.EventBook,
        destination_sequences: dict[str, int] = None,
    ) -> saga_pb2.SagaResponse:
        """Handle source events and produce commands.

        Creates a saga instance and dispatches each event.
        This is the entry point for gRPC integration.

        Args:
            source: EventBook containing events to process.
            destination_sequences: Map of domain to next sequence number for stamping.

        Returns:
            SagaResponse containing commands and events.
        """
        cls._ensure_configured()
        saga = cls()
        root = source.cover.root.value if source.HasField("cover") else None
        correlation_id = source.cover.correlation_id if source.HasField("cover") else ""

        commands = []
        for page in source.pages:
            event_any = page.event if page.HasField("event") else None
            if (
                event_any is None
                and hasattr(page, "payload")
                and page.HasField("payload")
            ):
                # New proto: event is in payload oneof
                if hasattr(page.payload, "event"):
                    event_any = page.payload.event
                elif hasattr(page, "GetEvent"):
                    event_any = page.GetEvent()
            if event_any:
                commands.extend(
                    saga.dispatch(
                        event_any, root, correlation_id, destination_sequences
                    )
                )

        return saga_pb2.SagaResponse(commands=commands, events=saga._events)

    @classmethod
    def execute(
        cls,
        source: types.EventBook,
        destination_sequences: dict[str, int] = None,
    ) -> saga_pb2.SagaResponse:
        """Deprecated: Use handle() instead.

        Kept for backwards compatibility.
        """
        return cls.handle(source, destination_sequences)
