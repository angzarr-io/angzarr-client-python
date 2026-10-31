"""Angzarr for Python.

- ``angzarr_client.router``: the router, native Python, that dispatches to a
  component's business code; ``angzarr codegen python`` renders this
  repository's codegen/ templates against it. ``UnclassifiedError``, the
  error business code raises for a failure it does not classify, is also
  here at the top level (decision 14.42).
- ``ComponentHost`` hosts one component; ``describe`` builds a hand-written
  component's ``ComponentDescriptor`` from its dispatch table
  (``angzarr_client.descriptor``).
- The coordinator clients (``CommandHandlerClient``, ``QueryClient``,
  ``DomainClient``) and their builders.
- ``angzarr_client.testing``: test helpers, imported from that module
  explicitly (they are not part of this top-level surface).
"""

from importlib.metadata import version as _version

__version__ = _version("angzarr-client")

from ._pb import ConflictResolution
from .builder import CommandBuilder, QueryBuilder
from .client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    TransportMode,
    resolve_ch_endpoint,
)
from .descriptor import describe
from .errors import (
    ClientError,
    CommandRejectedError,
    ConnectionError,
    GRPCError,
    InvalidArgumentError,
    InvalidTimestampError,
    TransportError,
)
from .helpers import (
    CORRELATION_ID_HEADER,
    DEFAULT_EDITION,
    META_ANGZARR_DOMAIN,
    PROJECTION_DOMAIN_PREFIX,
    PROJECTION_TYPE_URL,
    TYPE_URL_PREFIX,
    UNKNOWN_DOMAIN,
    WILDCARD_DOMAIN,
    correlated_metadata,
    full_type_url,
    full_type_url_for,
    now,
    parse_timestamp,
    proto_to_uuid,
    proto_uuid_to_hex,
    type_name_from_url,
    type_url,
    type_url_matches,
    type_url_matches_exact,
    uuid_to_proto,
)
from .host import ComponentHost, ConfigurationError
from .identity import compute_root, to_proto_bytes
from .retry import (
    ExponentialBackoffRetry,
    RetryPolicy,
    default_retry_policy,
)
from .router.errors import UnclassifiedError
from .server import (
    DEFAULT_BIND_HOST,
    ENV_BIND_ADDRESS,
    configure_logging,
    get_transport_config,
    resolve_bind_address,
)
from .validation import (
    require_exists,
    require_non_negative,
    require_not_empty,
    require_not_empty_str,
    require_not_exists,
    require_positive,
    require_status,
    require_status_not,
)
from .wrappers import (
    CommandBook,
    CommandPage,
    CommandResponse,
    Cover,
    CoverBearer,
    EventBook,
    EventPage,
    Query,
    Wrapped,
)

__all__ = [
    "CORRELATION_ID_HEADER",
    "DEFAULT_BIND_HOST",
    "DEFAULT_EDITION",
    "ENV_BIND_ADDRESS",
    "META_ANGZARR_DOMAIN",
    "PROJECTION_DOMAIN_PREFIX",
    "PROJECTION_TYPE_URL",
    "TYPE_URL_PREFIX",
    # Constants
    "UNKNOWN_DOMAIN",
    "WILDCARD_DOMAIN",
    # Errors
    "ClientError",
    "CommandBook",
    # Builders
    "CommandBuilder",
    # Clients
    "CommandHandlerClient",
    "CommandPage",
    "CommandRejectedError",
    "CommandResponse",
    # Component host
    "ComponentHost",
    "ConfigurationError",
    "ConflictResolution",
    "ConnectionError",
    "Cover",
    "CoverBearer",
    "DomainClient",
    "EventBook",
    "EventPage",
    "ExponentialBackoffRetry",
    "GRPCError",
    "InvalidArgumentError",
    "InvalidTimestampError",
    "Query",
    "QueryBuilder",
    "QueryClient",
    # Retry
    "RetryPolicy",
    "TransportError",
    "TransportMode",
    "UnclassifiedError",
    # Wrappers (user-facing object surface for framework protos)
    "Wrapped",
    # Identity
    "compute_root",
    "configure_logging",
    # Pure utilities (non-accessor — accessors live on wrapper classes)
    "correlated_metadata",
    "default_retry_policy",
    "describe",
    "full_type_url",
    "full_type_url_for",
    "get_transport_config",
    "now",
    "parse_timestamp",
    "proto_to_uuid",
    "proto_uuid_to_hex",
    # Validation
    "require_exists",
    "require_non_negative",
    "require_not_empty",
    "require_not_empty_str",
    "require_not_exists",
    "require_positive",
    "require_status",
    "require_status_not",
    "resolve_bind_address",
    "resolve_ch_endpoint",
    "to_proto_bytes",
    "type_name_from_url",
    "type_url",
    "type_url_matches",
    "type_url_matches_exact",
    "uuid_to_proto",
]
