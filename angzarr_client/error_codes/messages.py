"""Static human-readable messages — the value of ``message`` on every
error this client constructs. Constant names match the corresponding
``codes::`` constant.

Mirrors ``client-rust/main/src/error_codes.rs::messages`` — same
constant names, same string values.
"""

# Validation
VALUE_NOT_POSITIVE = "value must be positive"
VALUE_NOT_NON_NEGATIVE = "value must be non-negative"
VALUE_EMPTY = "value must not be empty"
COLLECTION_EMPTY = "collection must not be empty"
ENTITY_NOT_FOUND = "entity not found"
ENTITY_ALREADY_EXISTS = "entity already exists"
STATUS_MISMATCH = "status does not match expected"
STATUS_FORBIDDEN = "status is the forbidden value"

# Builder
COMMAND_TYPE_URL_MISSING = "command type_url not set"
COMMAND_PAYLOAD_MISSING = "command payload not set"
COMMAND_SEQUENCE_MISSING = "sequence not set (call with_sequence)"

# Conversion
ANY_TYPE_MISMATCH = "Any type_url does not match expected message type"
ANY_DECODE_FAILED = "failed to decode Any payload into expected message type"
PROTO_UUID_INVALID = "proto UUID bytes are not a valid 16-byte UUID"
TIMESTAMP_PARSE_FAILED = "failed to parse RFC3339 timestamp"

# Transport / connection
ENDPOINT_PARSE_FAILED = "failed to parse fallback endpoint"
ENDPOINT_INVALID_URI = "endpoint URI is invalid"
CONNECTION_FAILED = "connection failed"
CONNECTION_FAILED_MAX_RETRIES = "connection failed after max retries"
TRANSPORT_ERROR = "transport error"
GRPC_ERROR = "grpc error"
INVALID_TRANSPORT_MODE = "invalid transport mode env value"
INVALID_PORT = "invalid port env value"
STREAM_LIMIT_EXCEEDED = "server stream exceeded the configured maximum"

# Dispatch — common
HANDLER_WRONG_RESPONSE_KIND = "handler returned wrong response kind"
HANDLER_WRONG_REQUEST_KIND = "handler dispatched with wrong request kind"
NO_HANDLER_REGISTERED = "no handler registered for the given (domain, type_url)"
MISSING_COMMAND_BOOK = "missing command book"
MISSING_COMMAND_PAGE = "missing command page"
MISSING_COMMAND_PAYLOAD = "missing command payload"
NOTIFICATION_DECODE_FAILED = "failed to decode Notification payload"
REJECTION_NOTIFICATION_DECODE_FAILED = "failed to decode RejectionNotification payload"

# Dispatch — saga
MISSING_SAGA_SOURCE = "missing saga source"
EMPTY_SAGA_SOURCE = "empty saga source"
MISSING_SAGA_EVENT_PAYLOAD = "missing event payload"
SAGA_INVALID_TYPE_URL = "saga trigger has invalid type_url"
SAGA_HANDLER_UNSUPPORTED_RETURN_TYPE = "saga handler returned unsupported type"

# Dispatch — process manager
MISSING_PM_TRIGGER = "missing PM trigger"
EMPTY_PM_TRIGGER = "empty PM trigger"
MISSING_PM_EVENT_PAYLOAD = "missing event payload on PM trigger"
PM_INVALID_TYPE_URL = "PM trigger has invalid type_url"
PM_HANDLER_WRONG_RETURN_TYPE = "PM handler must return ProcessManagerResponse"

# Dispatch — upcaster
UPCASTER_WRONG_RESPONSE_KIND = "upcaster handler returned non-Upcaster response"

# Build-time validation
HANDLER_FIELD_EMPTY_STRING = "handler field must be a non-empty string"
HANDLER_FIELD_EMPTY_LIST = "handler field must be a non-empty list"
HANDLER_STATE_NOT_TYPE = "handler 'state' must be a type"
HANDLER_UNKNOWN_KIND = "unknown handler kind"
ROUTER_NO_HANDLERS = "no handlers registered on Router"
DUPLICATE_COMMAND_HANDLER = (
    "duplicate command handler registration for (domain, type_url)"
)
MIXED_HANDLER_KINDS = (
    "cannot mix handler kinds in one Router — all handlers must share a kind"
)

# Saga / PM destinations
MISSING_DESTINATION_SEQUENCE = "no sequence for destination domain"
