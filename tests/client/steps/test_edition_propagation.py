"""Step defs for features/coordinator-contract/edition_propagation.feature.

WIP HARNESS — the edition-propagation contract is coordinator-tier:
the coordinator stamps the source/trigger cover's edition onto every
outgoing CommandBook / EventBook before persistence (see
`core/main/src/proto_ext/cover.rs::Cover::propagate_edition_from`).
Authoritative coverage today lives in the Rust unit tests under
`core/main/src/orchestration/{saga,process_manager}/local/tests.rs`.

This file scaffolds the step matchers so the feature wiring exists
on the Python side. Each matcher raises NotImplementedError until a
coordinator-tier runner (or a client-side fixture that drives the
coordinator) is available.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../coordinator-contract/edition_propagation.feature")


# --- Saga: source event → outgoing commands / events ---------------------


@given(
    parsers.parse('a saga "{name}" translating from "{source}" to "{target}"'),
    target_fixture="_edition_saga",
)
def _given_saga(name: str, source: str, target: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given("the saga handles OrderCreated by emitting a ReserveStock command")
def _given_saga_emits_reserve_stock():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given("the saga handles OrderCreated by emitting an OrderObserved event")
def _given_saga_emits_order_observed():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given(parsers.parse('the source event has edition "{edition}"'))
def _given_source_event_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given(parsers.parse('the saga handler sets outgoing edition "{edition}"'))
def _given_saga_handler_sets_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given("the source event has no edition set")
def _given_source_event_no_edition():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given(
    parsers.parse(
        'the source event has edition "{edition}" with divergence at '
        '"{domain}"={sequence:d}'
    )
)
def _given_source_edition_with_divergence(edition: str, domain: str, sequence: int):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@when("an OrderCreated event is dispatched to the saga")
def _when_order_created_dispatched_to_saga():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then(parsers.parse('the emitted command\'s cover has edition "{edition}"'))
def _then_emitted_command_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then(parsers.parse('the emitted event\'s cover has edition "{edition}"'))
def _then_emitted_event_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then(parsers.parse('the persisted command\'s cover has edition "{edition}"'))
def _then_persisted_command_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then("the emitted command's cover has no edition set")
def _then_emitted_command_no_edition():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then(
    parsers.parse(
        'the emitted command\'s cover has edition "{edition}" with '
        'divergence at "{domain}"={sequence:d}'
    )
)
def _then_emitted_command_edition_with_divergence(
    edition: str, domain: str, sequence: int
):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


# --- Process Manager: trigger event → outgoing commands / process_events --


@given(
    parsers.parse(
        'a process manager "{name}" with sources "{sources}" and ' 'targets "{targets}"'
    )
)
def _given_process_manager(name: str, sources: str, targets: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given("the PM also emits an OrderTracked process_event on OrderCreated")
def _given_pm_emits_process_event():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given(parsers.parse('the trigger event has edition "{edition}"'))
def _given_trigger_event_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@given(parsers.parse('the PM handler sets outgoing edition "{edition}"'))
def _given_pm_handler_sets_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@when("an OrderCreated trigger is dispatched to the PM")
def _when_trigger_dispatched_to_pm():
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")


@then(
    parsers.parse('every emitted process_events book\'s cover has edition "{edition}"')
)
def _then_every_process_events_book_edition(edition: str):
    # TODO (WIP): Implement this step matcher properly.
    raise NotImplementedError("WIP: step needs implementation")
