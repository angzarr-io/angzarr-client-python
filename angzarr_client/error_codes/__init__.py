"""Cross-language inventory of this package's stable error codes and
detail-map keys.

Audit finding #59 (structural error model). Two sub-modules:

- ``codes``: SCREAMING_SNAKE identifiers, carried on the ``code`` attribute
  of every error;
- ``keys``: the detail-map keys of the package's own codes, used at
  insertion sites and in ``details["..."]`` lookups.

English is never passed to an error (decision 14.37): it is rendered where
the error is shown, from a catalog keyed by the code
(:func:`angzarr_client.diagnostic.render`): the spec's for a framework
reason key, ``locales/en-US/angzarr-client.ftl`` for the package's own.
Adding a new error: add its code here and its message there.
"""

from . import codes, keys

__all__ = ["codes", "keys"]
