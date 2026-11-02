# English for angzarr-client's own codes (angzarr_client.error_codes.codes):
# each id is a code, its variables the code's details. A framework reason
# key's English is the spec's catalog (reasons.en-US.ftl, shipped with the
# generated protos; decision 14.60), never here. Rendered by
# angzarr_client.diagnostic.render where an error is shown; nothing on the
# wire uses it (decision 14.37).

## Validation

VALUE_NOT_POSITIVE = { $field } must be positive
VALUE_NOT_NON_NEGATIVE = { $field } must be non-negative
VALUE_EMPTY = { $field } must not be empty
COLLECTION_EMPTY = { $field } must not be empty
ENTITY_NOT_FOUND = entity not found
ENTITY_ALREADY_EXISTS = entity already exists
STATUS_MISMATCH = status does not match expected
STATUS_FORBIDDEN = status is the forbidden value

## Builder

COMMAND_TYPE_URL_MISSING = command type_url not set
COMMAND_PAYLOAD_MISSING = command payload not set

## Conversion

PROTO_UUID_INVALID = { $field } is not a UUID
TIMESTAMP_PARSE_FAILED = the timestamp is not RFC 3339 ({ $ext_err_txt })

## Identity

DOMAIN_CONTAINS_COLON = domain "{ $domain }" must not contain ':'

## Transport / connection

CONNECTION_FAILED = connection failed
TRANSPORT_ERROR = transport error
GRPC_ERROR = grpc error

## Component host

HOST_SERVICE_UNNAMED = an application service needs its full service name
HOST_ALREADY_STARTED = the host is already started
DESCRIPTOR_RESOLUTION_UNSPECIFIED = component "{ $component }" lists a conflict resolution of UNSPECIFIED

## Process-manager compensation

UNDO_TARGETS_COMMAND = the undo of step "{ $step }" becomes { $command }, a command its destination may refuse
