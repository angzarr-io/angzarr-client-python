"""SagaContext for saga handlers with destination sequence support.

SagaContext provides type-safe access to destination sequences for command
stamping in saga handlers.

Design Philosophy:
    Sagas are stateless domain translators. They should NOT rebuild destination
    state to make decisions. The framework provides only destination sequences
    for command stamping. Business logic belongs in aggregates.

Example usage:
    @handles(OrderCreated)
    def handle_order(self, event: OrderCreated, ctx: SagaContext):
        cmd = ReserveInventory(order_id=event.order_id, quantity=event.quantity)
        ctx.stamp_command(cmd, "inventory")
        return cmd
"""

from __future__ import annotations

from .destinations import Destinations
from .proto.angzarr import types_pb2 as types

__all__ = ["SagaContext"]


class SagaContext:
    """Context for saga handlers providing source events and destination sequences.

    Sagas receive source events and destination sequences (not full EventBooks)
    from the framework. This class provides access to the source and a
    `stamp_command()` helper for setting correct sequence numbers.

    Why sequences only?
    -------------------
    Sagas are stateless translators - they should NOT make business decisions
    based on destination state. If you need external information:
    1. Inject it as a fact
    2. Let aggregates decide (they validate commands)
    3. Use sync mode for immediate feedback on command results

    Anti-pattern example (DON'T do this):
        # BAD: Saga rebuilds state and makes decisions
        def handle_order(self, event, destinations):
            inventory = rebuild_state(destinations["inventory"])
            if inventory.stock < event.quantity:  # Decision in saga!
                return RejectCommand(...)
            return CreateReservation(...)

    Correct pattern:
        # GOOD: Saga translates, aggregate decides
        def handle_order(self, event, ctx):
            cmd = CreateReservation(order_id=event.order_id, quantity=event.quantity)
            ctx.stamp_command(cmd, "inventory")
            return cmd
            # Inventory aggregate will reject if insufficient stock
            # Saga handles rejection via @rejected decorator
    """

    def __init__(
        self,
        source: types.EventBook,
        destination_sequences: dict[str, int],
    ) -> None:
        """Create a SagaContext from source events and destination sequences.

        Args:
            source: EventBook containing source events to process.
            destination_sequences: Dict mapping domain name to next sequence number.
        """
        self._source = source
        self._destinations = Destinations(destination_sequences)

    @property
    def source(self) -> types.EventBook:
        """Get the source EventBook."""
        return self._source

    @property
    def destinations(self) -> Destinations:
        """Get the Destinations context for command stamping."""
        return self._destinations

    def sequence_for(self, domain: str) -> int | None:
        """Get the next sequence number for a destination domain.

        Args:
            domain: The target domain name.

        Returns:
            The next sequence number, or None if domain not found.
        """
        return self._destinations.sequence_for(domain)

    def stamp_command(
        self,
        cmd: types.CommandBook,
        domain: str,
    ) -> types.CommandBook:
        """Stamp a command with the correct sequence for its destination domain.

        Convenience method that delegates to destinations.stamp_command().

        Args:
            cmd: The CommandBook to stamp.
            domain: The target domain (must be in destination_sequences).

        Returns:
            The same CommandBook (for chaining).

        Raises:
            ValueError: If domain is not in destination_sequences.
        """
        return self._destinations.stamp_command(cmd, domain)

    def has_destination(self, domain: str) -> bool:
        """Check if a destination domain is available.

        Args:
            domain: The domain name to check.

        Returns:
            True if the domain has a sequence entry.
        """
        return self._destinations.has_domain(domain)
