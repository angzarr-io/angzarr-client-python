"""Destinations context for sagas and process managers.

Provides type-safe access to destination sequences for command stamping.
Sagas and PMs receive destination_sequences from the framework and use this
class to stamp commands with the correct sequence numbers.

Design Philosophy:
    Sagas and PMs should NOT rebuild destination state to make decisions.
    They receive only sequences for command stamping. Business logic belongs
    in aggregates - sagas/PMs just translate and coordinate.

Example usage:
    @handles(OrderCreated)
    def handle_order(self, event: OrderCreated, destinations: Destinations):
        cmd = ReserveInventory(order_id=event.order_id, quantity=event.quantity)
        destinations.stamp_command(cmd, "inventory")
        return cmd
"""

from __future__ import annotations

from .proto.angzarr import types_pb2 as types

__all__ = ["Destinations"]


class Destinations:
    """Context for saga/PM handlers providing access to destination sequences.

    Sagas and PMs receive destination sequences (not full EventBooks) from the
    framework. This class provides a `stamp_command()` helper to set the correct
    sequence number on commands before they're sent.

    Why sequences only?
    -------------------
    Sagas and PMs are translators/coordinators - they should NOT make business
    decisions based on destination state. If you need external information:
    1. Inject it as a fact
    2. Let aggregates decide (they validate commands)
    3. Use sync mode for immediate feedback on command results

    Attributes:
        sequences: Map from domain name to next sequence number.
    """

    def __init__(self, sequences: dict[str, int]) -> None:
        """Create a Destinations context from a sequence map.

        Args:
            sequences: Dict mapping domain name to next sequence number.
                       Typically from proto's `destination_sequences` field.
        """
        self._sequences = sequences

    @classmethod
    def from_proto(cls, destination_sequences: dict[str, int]) -> Destinations:
        """Create from proto's destination_sequences map.

        Args:
            destination_sequences: Map from ProcessManagerHandleRequest or
                                   SagaHandleRequest proto.

        Returns:
            Destinations instance with the sequences.
        """
        return cls(dict(destination_sequences))

    def sequence_for(self, domain: str) -> int | None:
        """Get the next sequence number for a destination domain.

        Args:
            domain: The target domain name.

        Returns:
            The next sequence number, or None if domain not found.
        """
        return self._sequences.get(domain)

    def stamp_command(
        self,
        cmd: types.CommandBook,
        domain: str,
    ) -> types.CommandBook:
        """Stamp a command with the correct sequence for its destination domain.

        Modifies the command's page headers in-place with the sequence number.

        Args:
            cmd: The CommandBook to stamp.
            domain: The target domain (must be in destination_sequences).

        Returns:
            The same CommandBook (for chaining).

        Raises:
            InvalidArgumentError: If domain is not in destination_sequences.
                        Check your output_domains config if you get this error.
        """
        from .error_codes import codes, keys, messages
        from .errors import InvalidArgumentError

        seq = self._sequences.get(domain)
        if seq is None:
            raise InvalidArgumentError(
                messages.NO_HANDLER_REGISTERED,  # placeholder; better message below
                code=codes.NO_HANDLER_REGISTERED,
                details={keys.DOMAIN: domain},
            )
        for page in cmd.pages:
            if not page.HasField("header"):
                page.header.SetInParent()
            page.header.sequence = seq
        return cmd

    @staticmethod
    def deferred_header(
        source_cover: types.Cover,
        source_seq: int,
    ) -> types.PageHeader:
        """Build a PageHeader carrying an ``AngzarrDeferredSequence``.

        Use this on saga-produced commands so the framework can dedupe
        on ``(source.root, source_seq, target.root)``. AMQP at-least-once
        redelivery of the trigger event then becomes a no-op at the
        destination aggregate's pipeline (cached events returned without
        re-invoking business logic), instead of relying on a business
        guard that surfaces as an idempotent-failure-shaped retry storm.
        """
        return types.PageHeader(
            angzarr_deferred=types.AngzarrDeferredSequence(
                source=source_cover,
                source_seq=source_seq,
            ),
        )

    def has_domain(self, domain: str) -> bool:
        """Check if a destination domain is available.

        Args:
            domain: The domain name to check.

        Returns:
            True if the domain has a sequence entry.
        """
        return domain in self._sequences

    @property
    def domains(self) -> list[str]:
        """Get list of available destination domains."""
        return list(self._sequences.keys())
