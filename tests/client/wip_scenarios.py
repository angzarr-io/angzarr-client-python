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
    ("builder.feature", "A single saga handler builds a saga-routing router"),
    ("builder.feature", "An unmarked component cannot be registered as a handler"),
    (
        "builder.feature",
        "Command handlers for distinct domains build a command-handling router",
    ),
    (
        "builder.feature",
        "Each registered handler is introspected at most once at build time",
    ),
    ("builder.feature", "Empty configuration fails to build"),
    ("builder.feature", "Mixed handler kinds are rejected"),
    (
        "builder.feature",
        "Two command handlers for the same domain and command pair are rejected at build time",
    ),
    # C-0147 — framework gap: handlers can't set EventBook.cover.ext through
    # the current router/dispatch surface. Scenario stays WIP until a
    # public handler-cover API exists. Stubs live in test_command_handler.py.
    (
        "command_handler.feature",
        "Handler-set ext on the emitted EventBook is not overridden",
    ),
    ("compensation.feature", "Build context from rejected command"),
    ("compensation.feature", "Build notification command book for routing"),
    ("compensation.feature", "Build notification wrapper for rejection"),
    ("compensation.feature", "Build rejection notification from context"),
    ("compensation.feature", "Command book targets triggering aggregate"),
    ("compensation.feature", "Complex rejection reason"),
    ("compensation.feature", "Context preserves correlation ID"),
    ("compensation.feature", "Context preserves saga origin details"),
    ("compensation.feature", "Notification has sent_at timestamp"),
    (
        "compensation.feature",
        "Process manager rejections produce a compensation notification",
    ),
    ("compensation.feature", "Rejected command is preserved for debugging"),
    ("compensation.feature", "Rejection notification has correct issuer name"),
    ("compensation.feature", "Rejection notification includes source aggregate"),
    ("compensation.feature", "Rejection reason is preserved exactly"),
    ("compensation.feature", "Saga origin chain is maintained"),
    ("compensation.feature", "Saga rejections produce a compensation notification"),
    ("domain-client.feature", "Closing the client severs both read and write paths"),
    (
        "domain-client.feature",
        "Connecting to a domain exposes both query and command access",
    ),
    ("domain-client.feature", "Connecting via environment variable"),
    ("domain-client.feature", "Fetching events through the fluent query builder"),
    ("domain-client.feature", "Reads and writes share the underlying connection"),
    ("domain-client.feature", "Sending a command through the fluent builder"),
    ("process_manager.feature", "PM receives a trigger and emits a command"),
    ("process_manager.feature", "PM skips events from domains outside its sources"),
    ("process_manager.feature", "PM state is rebuilt from its own process events"),
    (
        "query_client.feature",
        "A correlation query gathers events across every aggregate it touched",
    ),
    ("query_client.feature", "A correlation query with no matches returns nothing"),
    ("query_client.feature", "A query against an unknown aggregate returns nothing"),
    (
        "query_client.feature",
        "A query as of a sequence returns history up to that point",
    ),
    (
        "query_client.feature",
        "A query as of a timestamp returns history up to that moment",
    ),
    ("query_client.feature", "A query fails clearly when the backend is unreachable"),
    (
        "query_client.feature",
        "A query in a named edition returns only that edition's history",
    ),
    ("query_client.feature", "A query preserves event types and payloads exactly"),
    ("query_client.feature", "A query returns the full history of an aggregate"),
    (
        "query_client.feature",
        "A query surfaces the latest snapshot alongside the history",
    ),
    ("query_client.feature", "A query with an empty domain is refused"),
    (
        "query_client.feature",
        "A range query starting at a sequence returns the tail of history",
    ),
    (
        "query_client.feature",
        "A range query starting past the end of history returns nothing",
    ),
    (
        "query_client.feature",
        "A range query with an upper bound includes both endpoints",
    ),
    ("query_client.feature", "An edition's history is isolated from the main timeline"),
    (
        "rejected_compensation.feature",
        "Compensation events are appended in sequence after prior history",
    ),
    (
        "rejected_compensation.feature",
        "Compensation handlers route by (source_domain, command) pair",
    ),
    (
        "rejected_compensation.feature",
        "Multiple compensation handlers on one class do not cross-fire",
    ),
    (
        "rejected_compensation.feature",
        "No matching compensation handler yields empty compensation",
    ),
    (
        "rejected_compensation.feature",
        "State is rebuilt before the compensation handler runs",
    ),
    ("rejection.feature", "A matching rejection fires the compensation"),
    ("rejection.feature", "A rejection Payment does not compensate is ignored"),
    ("rejection.feature", "Multiple compensators for the same rejection all run"),
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
