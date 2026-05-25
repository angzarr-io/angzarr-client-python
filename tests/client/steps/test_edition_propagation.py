"""Step defs for features/coordinator-contract/edition_propagation.feature.

DEFERRED PER WIP_CLEANUP_PLAN.md R9 — the edition-propagation contract is
coordinator-tier (Rust sidecar), not client-tier. The Python router
dispatch path explicitly omits propagation per the audit-#86 reversion
(see angzarr_client/router/dispatch.py:644-645 — "edition propagation
moved to coordinator-contract; outgoing covers ride out as-is").

Authoritative coverage today lives in the Rust unit tests at
``core/main/src/orchestration/{saga,process_manager}/local/tests.rs``.

These matchers stay as NotImplementedError stubs (with explanatory
messages) and all 7 edition_propagation.feature scenarios stay in
WIP_SCENARIOS until one of the following lands:

1. A fake coordinator simulator in the Python test harness that mimics
   ``propagate_edition_from`` on the way to its in-memory persistence.
2. A real coordinator-tier integration runner that boots the Rust
   sidecar against the Python router and observes outgoing covers
   post-coordinator.

Either approach is a separate engineering initiative, not a mechanical
un-stubbing pass.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../coordinator-contract/edition_propagation.feature")


_GAP = (
    "edition-propagation is a coordinator-tier contract; the Python "
    "router dispatch path explicitly does not implement it (see "
    "dispatch.py:644-645 audit-#86 reversion). Defer to follow-up; "
    "see test_edition_propagation.py module docstring."
)


# --- Saga: source event → outgoing commands / events ---------------------


@given(
    parsers.parse('a saga "{name}" translating from "{source}" to "{target}"'),
    target_fixture="_edition_saga",
)
def _given_saga(name: str, source: str, target: str):
    raise NotImplementedError(_GAP)


@given("the saga handles OrderCreated by emitting a ReserveStock command")
def _given_saga_emits_reserve_stock():
    raise NotImplementedError(_GAP)


@given("the saga handles OrderCreated by emitting an OrderObserved event")
def _given_saga_emits_order_observed():
    raise NotImplementedError(_GAP)


@given(parsers.parse('the source event has edition "{edition}"'))
def _given_source_event_edition(edition: str):
    raise NotImplementedError(_GAP)


@given(parsers.parse('the saga handler sets outgoing edition "{edition}"'))
def _given_saga_handler_sets_edition(edition: str):
    raise NotImplementedError(_GAP)


@given("the source event has no edition set")
def _given_source_event_no_edition():
    raise NotImplementedError(_GAP)


@given(
    parsers.parse(
        'the source event has edition "{edition}" with divergence at '
        '"{domain}"={sequence:d}'
    )
)
def _given_source_edition_with_divergence(edition: str, domain: str, sequence: int):
    raise NotImplementedError(_GAP)


@when("an OrderCreated event is dispatched to the saga")
def _when_order_created_dispatched_to_saga():
    raise NotImplementedError(_GAP)


@then(parsers.parse('the emitted command\'s cover has edition "{edition}"'))
def _then_emitted_command_edition(edition: str):
    raise NotImplementedError(_GAP)


@then(parsers.parse('the emitted event\'s cover has edition "{edition}"'))
def _then_emitted_event_edition(edition: str):
    raise NotImplementedError(_GAP)


@then(parsers.parse('the persisted command\'s cover has edition "{edition}"'))
def _then_persisted_command_edition(edition: str):
    raise NotImplementedError(_GAP)


@then("the emitted command's cover has no edition set")
def _then_emitted_command_no_edition():
    raise NotImplementedError(_GAP)


@then(
    parsers.parse(
        'the emitted command\'s cover has edition "{edition}" with '
        'divergence at "{domain}"={sequence:d}'
    )
)
def _then_emitted_command_edition_with_divergence(
    edition: str, domain: str, sequence: int
):
    raise NotImplementedError(_GAP)


# --- Process Manager: trigger event → outgoing commands / process_events --


@given(
    parsers.parse(
        'a process manager "{name}" with sources "{sources}" and ' 'targets "{targets}"'
    )
)
def _given_process_manager(name: str, sources: str, targets: str):
    raise NotImplementedError(_GAP)


@given("the PM also emits an OrderTracked process_event on OrderCreated")
def _given_pm_emits_process_event():
    raise NotImplementedError(_GAP)


@given(parsers.parse('the trigger event has edition "{edition}"'))
def _given_trigger_event_edition(edition: str):
    raise NotImplementedError(_GAP)


@given(parsers.parse('the PM handler sets outgoing edition "{edition}"'))
def _given_pm_handler_sets_edition(edition: str):
    raise NotImplementedError(_GAP)


@when("an OrderCreated trigger is dispatched to the PM")
def _when_trigger_dispatched_to_pm():
    raise NotImplementedError(_GAP)


@then(
    parsers.parse('every emitted process_events book\'s cover has edition "{edition}"')
)
def _then_every_process_events_book_edition(edition: str):
    raise NotImplementedError(_GAP)
