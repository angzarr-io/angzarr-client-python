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
