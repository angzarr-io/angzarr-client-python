"""Deterministic aggregate identity.

``compute_root(domain, business_key)`` is
``uuid5(ANGZARR_ROOT_NAMESPACE, domain + ":" + business_key)`` (decision 9.2;
the ``derived_ids`` region of types.proto): the same (domain, key) pair
yields the same root in every language, service and restart. A domain never
contains ``':'``, so the first ``':'`` splits the name unambiguously whatever
the key holds; a domain containing one is refused. It is the only identity
derivation the client provides; domain-specific wrappers belong in the
applications that need them.
"""

from __future__ import annotations

import uuid

from .error_codes import codes, keys, messages
from .errors import InvalidArgumentError

# The fixed namespace for framework-derived roots (types.proto, derived_ids).
ANGZARR_ROOT_NAMESPACE = uuid.UUID("d4741b1a-52cd-43cf-add1-3dbb9615510b")


def compute_root(domain: str, business_key: str) -> uuid.UUID:
    """The deterministic root of the aggregate keyed by ``business_key`` in
    ``domain``. Raises :class:`InvalidArgumentError`
    (``DOMAIN_CONTAINS_COLON``) when ``domain`` contains ``':'``."""
    if ":" in domain:
        raise InvalidArgumentError(
            messages.DOMAIN_CONTAINS_COLON,
            code=codes.DOMAIN_CONTAINS_COLON,
            details={keys.DOMAIN: domain},
        )
    return uuid.uuid5(ANGZARR_ROOT_NAMESPACE, f"{domain}:{business_key}")


def to_proto_bytes(id: uuid.UUID) -> bytes:
    """The UUID's 16-byte proto representation."""
    return id.bytes
