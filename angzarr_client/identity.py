"""Deterministic aggregate identity.

``compute_root(domain, business_key)`` is ``uuid5(NAMESPACE_OID,
domain + ":" + business_key)``: the same (domain, key) pair yields the same
root in every language, service and restart, and the separator keeps
(``"ab"``, ``"c"``) distinct from (``"a"``, ``"bc"``) — domain names contain
no ``':'``. It is the only identity derivation the client provides;
domain-specific wrappers belong in the applications that need them.
"""

from __future__ import annotations

import uuid


def compute_root(domain: str, business_key: str) -> uuid.UUID:
    """The deterministic root of the aggregate keyed by ``business_key`` in
    ``domain``."""
    return uuid.uuid5(uuid.NAMESPACE_OID, f"{domain}:{business_key}")


def to_proto_bytes(id: uuid.UUID) -> bytes:
    """The UUID's 16-byte proto representation."""
    return id.bytes
