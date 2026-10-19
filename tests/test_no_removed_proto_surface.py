"""The client exposes nothing that only served proto the latest spec removed:
deferred / destination sequences, rejection notifications, revocation,
cascade error modes."""

from __future__ import annotations

import pytest

import angzarr_client
from angzarr_client import _pb, helpers, wrappers
from angzarr_client.error_codes import codes, messages


@pytest.mark.parametrize("cls", [wrappers.EventPage, wrappers.CommandPage])
@pytest.mark.parametrize("name", ["header", "is_deferred"])
def test_pages_expose_no_header_or_deferred_accessor(cls, name):
    assert not hasattr(cls, name)


@pytest.mark.parametrize("name", ["idempotency_key", "destination_map"])
def test_no_deferred_or_destination_helpers(name):
    assert not hasattr(helpers, name)
    assert not hasattr(angzarr_client, name)
    assert name not in angzarr_client.__all__


@pytest.mark.parametrize(
    "name",
    [
        "Notification",
        "RejectionNotification",
        "RevocationResponse",
        "AngzarrDeferredSequence",
        "CascadeErrorMode",
    ],
)
def test_pb_does_not_reexport_removed_messages(name):
    assert not hasattr(_pb, name)
    assert name not in _pb.__all__


@pytest.mark.parametrize(
    "name",
    [
        "NOTIFICATION_DECODE_FAILED",
        "REJECTION_NOTIFICATION_DECODE_FAILED",
        "MISSING_DESTINATION_SEQUENCE",
    ],
)
def test_no_error_codes_for_removed_features(name):
    assert not hasattr(codes, name)
    assert not hasattr(messages, name)
