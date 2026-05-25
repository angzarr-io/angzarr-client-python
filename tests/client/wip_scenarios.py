"""Scenarios resolved by cucumber-cleanup-workstream TODO-stub matchers
and skipped until those matchers get real implementations.

Replaces the @wip gherkin tags previously baked into angzarr-project's
.feature files (see angzarr-project 20d53e6). The skip policy is a
statement about *this client's* implementation state and belongs here
in the harness, not in the spec.

Sourced from /tmp/client_python_broken.txt - the full failure set
behave + pytest-bdd surfaced after the cucumber business-vocab rewrite
landed. Keyed by (feature_basename, scenario_name) so a name re-used
across features stays unambiguous.

Un-skip a scenario by removing its entry once its step matchers
are no longer TODO stubs raising NotImplementedError("WIP: step
needs implementation").
"""

WIP_SCENARIOS: set[tuple[str, str]] = {
    ("aggregate_client.feature", "A command for an unknown domain is refused"),
    (
        "aggregate_client.feature",
        "A command missing required fields is refused with the field name",
    ),
    (
        "aggregate_client.feature",
        "A fire-and-forget command returns before downstream work runs",
    ),
    ("aggregate_client.feature", "A malformed command is refused as invalid"),
    ("aggregate_client.feature", "A multi-event command lands atomically"),
    ("aggregate_client.feature", "A single command can produce multiple events"),
    (
        "aggregate_client.feature",
        "Commands carry a correlation ID through to their events",
    ),
    ("aggregate_client.feature", "Executing a command on a brand-new aggregate"),
    ("aggregate_client.feature", "Executing a command on an existing aggregate"),
    ("aggregate_client.feature", "Only one of two concurrent writes can land"),
    (
        "aggregate_client.feature",
        "Retrying at the current sequence after a stale write succeeds",
    ),
    ("aggregate_client.feature", "Sending a command at the wrong sequence is refused"),
    ("aggregate_client.feature", "The client surfaces a connection failure"),
    (
        "aggregate_client.feature",
        "The client surfaces a timeout when the service is slow",
    ),
    ("aggregate_client.feature", "The first command on an aggregate creates it"),
    (
        "aggregate_client.feature",
        "The first command on an aggregate must start at sequence 0",
    ),
    ("aggregate_client.feature", "Waiting for projectors before returning"),
    ("aggregate_client.feature", "Waiting for the full saga chain before returning"),
    # C-0147 — framework gap: handlers can't set EventBook.cover.ext through
    # the current router/dispatch surface. Scenario stays WIP until a
    # public handler-cover API exists. Stubs live in test_command_handler.py.
    (
        "command_handler.feature",
        "Handler-set ext on the emitted EventBook is not overridden",
    ),
    (
        "edition_propagation.feature",
        "Coordinator always overrides handler-set edition with source edition",
    ),
    (
        "edition_propagation.feature",
        "Coordinator always overrides handler-set edition with trigger edition",
    ),
    (
        "edition_propagation.feature",
        "PM propagates trigger edition to every emitted process_events book",
    ),
    (
        "edition_propagation.feature",
        "PM propagates trigger edition to outgoing commands",
    ),
    (
        "edition_propagation.feature",
        "Saga propagates main-timeline (empty) edition unchanged",
    ),
    (
        "edition_propagation.feature",
        "Saga propagates source edition to outgoing commands",
    ),
    (
        "edition_propagation.feature",
        "Saga propagates source edition to outgoing events",
    ),
    (
        "edition_propagation.feature",
        "Saga propagation preserves source edition divergences",
    ),
}
