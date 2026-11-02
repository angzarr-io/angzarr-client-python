"""SCREAMING_SNAKE event-type identifiers — the value of ``code`` on
every error this client constructs.

Each local code's English is this package's catalog,
``angzarr_client/locales/en-US/angzarr-client.ftl``. The component host's
codes are framework reason keys, each named by the generated reason enum
value it is, its English the spec's catalog (decision 14.60).
"""

from ..proto.io.angzarr.transport.embedded.v1.reasons_pb2 import (
    HostReason as ChildReason,
)
from ..proto.io.angzarr.v1.reasons_pb2 import HostReason, RouterReason

# Validation
VALUE_NOT_POSITIVE = "VALUE_NOT_POSITIVE"
VALUE_NOT_NON_NEGATIVE = "VALUE_NOT_NON_NEGATIVE"
VALUE_EMPTY = "VALUE_EMPTY"
COLLECTION_EMPTY = "COLLECTION_EMPTY"
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ENTITY_ALREADY_EXISTS = "ENTITY_ALREADY_EXISTS"
STATUS_MISMATCH = "STATUS_MISMATCH"
STATUS_FORBIDDEN = "STATUS_FORBIDDEN"

# Builder
COMMAND_TYPE_URL_MISSING = "COMMAND_TYPE_URL_MISSING"
COMMAND_PAYLOAD_MISSING = "COMMAND_PAYLOAD_MISSING"

# Conversion
PROTO_UUID_INVALID = "PROTO_UUID_INVALID"
TIMESTAMP_PARSE_FAILED = "TIMESTAMP_PARSE_FAILED"

# Identity
DOMAIN_CONTAINS_COLON = "DOMAIN_CONTAINS_COLON"

# Transport / connection
CONNECTION_FAILED = "CONNECTION_FAILED"
TRANSPORT_ERROR = "TRANSPORT_ERROR"
GRPC_ERROR = "GRPC_ERROR"

# Component host: framework reason keys (angzarr-project
# proto/io/angzarr/v1/reasons.proto, decision 14.44), each its enum value's
# full name, the prefix included.
# An error escaped a handler, or it failed as a bug; its cause names why
# (decision 14.37).
ROUTER_REASON_UNHANDLED_HANDLER_ERROR = RouterReason.Name(
    RouterReason.ROUTER_REASON_UNHANDLED_HANDLER_ERROR
)
# The cause of a ROUTER_REASON_UNHANDLED_HANDLER_ERROR when an exception
# escaped.
ROUTER_REASON_HANDLER_PANICKED = RouterReason.Name(
    RouterReason.ROUTER_REASON_HANDLER_PANICKED
)
HOST_REASON_NO_COMPONENTS_REGISTERED = HostReason.Name(
    HostReason.HOST_REASON_NO_COMPONENTS_REGISTERED
)
# An upcaster is its own sidecar host (decision 14.39): a host given a
# component and an upcaster is refused (details: component).
HOST_REASON_UPCASTER_WITH_COMPONENT = HostReason.Name(
    HostReason.HOST_REASON_UPCASTER_WITH_COMPONENT
)
# A host serves exactly one component; a second registration is refused
# (details: registered, refused).
ROUTER_REASON_DUPLICATE_COMPONENT = RouterReason.Name(
    RouterReason.ROUTER_REASON_DUPLICATE_COMPONENT
)

# Child process (angzarr_client.child)
# A line whose first word is angzarr-handshake but which is not a v1
# handshake naming an absolute UTF-8 path (details: line, its line ending
# removed, escaped when it is not UTF-8; C-0756, C-0760, decisions 14.59
# and 14.62). run_child refuses a socket path that is not UTF-8 under it.
# A child's keys are the local-process transport's (decision 14.64).
HOST_REASON_HANDSHAKE_MALFORMED = ChildReason.Name(
    ChildReason.HOST_REASON_HANDSHAKE_MALFORMED
)

# Component descriptors (angzarr_client.descriptor, ComponentHost)
# A descriptor whose rules_version is not the version the component's
# answers report (details: component, described, reported; C-0743).
HOST_REASON_DESCRIPTOR_RULES_VERSION_MISMATCH = HostReason.Name(
    HostReason.HOST_REASON_DESCRIPTOR_RULES_VERSION_MISMATCH
)
# A projector naming no domain it reads: its table's domains or its
# descriptor's inputs empty (details: component; C-0747, decision 14.55).
HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN = HostReason.Name(
    HostReason.HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN
)
# The component host's own refusals to start.
HOST_SERVICE_UNNAMED = "HOST_SERVICE_UNNAMED"
HOST_ALREADY_STARTED = "HOST_ALREADY_STARTED"
# A conflict resolution of UNSPECIFIED given to describe (details: component).
DESCRIPTOR_RESOLUTION_UNSPECIFIED = "DESCRIPTOR_RESOLUTION_UNSPECIFIED"

# Process-manager compensation (angzarr_client.compensation, decision 14.74)
# A step's undo becomes a command, which its destination may refuse: a
# warning when the step is registered (details: step, command).
UNDO_TARGETS_COMMAND = "UNDO_TARGETS_COMMAND"
