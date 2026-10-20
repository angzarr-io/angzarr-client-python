"""Detail-map key constants — the keys used in the ``details`` mapping
on every error this client constructs. Use these at insertion sites
AND when reading ``details[key]`` in tests.

Mirrors ``client-rust/main/src/error_codes.rs::keys`` — same constant
names, same string values.
"""

FIELD = "field"
CONTEXT = "context"
CAUSE = "cause"
ENDPOINT = "endpoint"
INPUT = "input"
EXPECTED = "expected"
ACTUAL = "actual"
EXPECTED_KIND = "expected_kind"
DOMAIN = "domain"
TYPE_URL = "type_url"
ROUTER_NAME = "router_name"
HANDLER_CLASS = "handler_class"
HANDLER_KIND = "handler_kind"
ACTUAL_RETURN_TYPE = "actual_return_type"
ENV_VAR = "env_var"
