"""Detail-map key constants for this package's own codes: the keys used in
the ``details`` mapping of the errors it constructs under a code of its own
(:mod:`angzarr_client.error_codes.codes`), and the variables of their
messages in ``locales/en-US/angzarr-client.ftl``. Use these at insertion
sites AND when reading ``details[key]`` in tests.

A framework reason key's args are the spec's, generated from reasons.proto
(``angzarr_client.proto.io.angzarr.v1.reason_args``; decision 14.60), never
typed here.
"""

FIELD = "field"
CONTEXT = "context"
INPUT = "input"
DOMAIN = "domain"
