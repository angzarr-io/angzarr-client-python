"""Cross-language inventory of stable error codes, static messages, and
detail-map keys.

Audit finding #59 (structural error model). Three sub-modules:

- ``codes`` — SCREAMING_SNAKE event-type identifiers. Carried on the
  ``code`` attribute of every error.
- ``messages`` — static human-readable messages. Carried on the
  ``message`` attribute. Same constant name as the corresponding code.
- ``keys`` — detail-map key strings. Used at insertion sites and in
  ``details["..."]`` lookups.

Adding a new error: add the constant in all three sub-modules (with
the same name in ``codes`` and ``messages``) AND add the parallel
constants in ``client-rust/main/src/error_codes.rs``, then reference
them at the call site.
"""

from . import codes, keys, messages

__all__ = ["codes", "messages", "keys"]
