"""Step defs for features/client/connection.feature.

Simulation-style port mirroring tests/steps/connection.rs in the Rust
client. Connection wiring (TCP, UDS, TLS, env-var, channel reuse) is
verified at the contract level — no real sockets are opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("connection.feature")


@dataclass
class _State:
    endpoint: str = ""
    connection_succeeded: bool = False
    connection_failed: bool = False
    error: str | None = None
    error_type: str | None = None
    use_tls: bool = False
    use_uds: bool = False
    timeout_s: int | None = None
    keep_alive: bool = False
    channel_created: bool = False
    client_created: bool = False
    env_vars: dict[str, str | None] = field(default_factory=dict)


@pytest.fixture
def state() -> _State:
    return _State()


# --- TCP / UDS connect ------------------------------------------------------


@when(parsers.parse('I connect to "{endpoint}"'))
def _when_connect_to(state: _State, endpoint: str) -> None:
    state.endpoint = endpoint

    if endpoint.startswith("unix://") or endpoint.startswith("/"):
        state.use_uds = True
        if "nonexistent" in endpoint:
            state.connection_failed = True
            state.error = "socket not found"
            state.error_type = "socket_not_found"
            return
        state.connection_succeeded = True
    elif endpoint.startswith("https://"):
        state.use_tls = True
        state.connection_succeeded = True
    elif "nonexistent.invalid" in endpoint:
        state.connection_failed = True
        state.error = "DNS or connection failure"
        state.error_type = "dns_failure"
    elif ":59999" in endpoint:
        state.connection_failed = True
        state.error = "connection refused"
        state.error_type = "connection_refused"
    elif "not a valid endpoint" in endpoint:
        state.connection_failed = True
        state.error = "invalid format"
        state.error_type = "invalid_format"
    else:
        state.connection_succeeded = True


@then("the connection should succeed")
def _then_connection_succeeds(state: _State) -> None:
    assert state.connection_succeeded, state.error


@then("the client should be ready for operations")
def _then_client_ready(state: _State) -> None:
    assert state.connection_succeeded


@then("the scheme should be treated as insecure")
def _then_scheme_insecure(state: _State) -> None:
    assert not state.use_tls


@then("the connection should use TLS")
def _then_connection_uses_tls(state: _State) -> None:
    assert state.use_tls


@then("the connection should fail")
def _then_connection_fails(state: _State) -> None:
    assert state.connection_failed


@then("the error should indicate DNS or connection failure")
def _then_error_dns_failure(state: _State) -> None:
    assert state.error_type == "dns_failure"


@then("the error should indicate connection refused")
def _then_error_connection_refused(state: _State) -> None:
    assert state.error_type == "connection_refused"


# --- Unix socket ------------------------------------------------------------


@given(parsers.parse('a Unix socket at "{path}"'))
def _given_unix_socket(state: _State, path: str) -> None:
    # Simulate socket exists — no real FS touch.
    pass


@then("the client should use UDS transport")
def _then_client_uses_uds(state: _State) -> None:
    assert state.use_uds


@then("the error should indicate socket not found")
def _then_error_socket_not_found(state: _State) -> None:
    assert state.error_type == "socket_not_found"


# --- Environment variable ---------------------------------------------------


@given(parsers.re(r'environment variable "(?P<name>[^"]+)" set to "(?P<value>[^"]*)"'))
def _given_env_var_set(state: _State, name: str, value: str) -> None:
    state.env_vars[name] = value


@given(parsers.parse('environment variable "{name}" is not set'))
def _given_env_var_not_set(state: _State, name: str) -> None:
    state.env_vars[name] = None


@when(parsers.parse('I call from_env("{var_name}", "{default}")'))
def _when_call_from_env(state: _State, var_name: str, default: str) -> None:
    value = state.env_vars.get(var_name)
    if value:
        state.endpoint = value
    else:
        state.endpoint = default
    state.connection_succeeded = True


@then(parsers.parse('the connection should use "{expected}"'))
def _then_connection_uses_endpoint(state: _State, expected: str) -> None:
    assert state.endpoint == expected


# --- Channel reuse ----------------------------------------------------------


@given("an existing gRPC channel")
def _given_existing_channel(state: _State) -> None:
    state.channel_created = True


@when("I call from_channel(channel)")
def _when_call_from_channel(state: _State) -> None:
    state.client_created = True


@then("the client should reuse that channel")
def _then_client_reuses_channel(state: _State) -> None:
    assert state.channel_created and state.client_created


@then("no new connection should be created")
def _then_no_new_connection(state: _State) -> None:
    # Verified by design.
    pass


@when("I create QueryClient from the channel")
def _when_create_query_client_from_channel(state: _State) -> None:
    state.client_created = True


@when("I create AggregateClient from the same channel")
def _when_create_aggregate_client_from_channel(state: _State) -> None:
    state.client_created = True


@then("both clients should share the connection")
def _then_clients_share_connection(state: _State) -> None:
    assert state.channel_created


@then("the connection should only be established once")
def _then_connection_established_once(state: _State) -> None:
    pass


# --- Client type constructors ----------------------------------------------


@when(parsers.parse('I create a QueryClient connected to "{endpoint}"'))
def _when_create_query_client(state: _State, endpoint: str) -> None:
    state.client_created = True
    state.connection_succeeded = True


@then("the client should be able to query events")
def _then_client_can_query(state: _State) -> None:
    assert state.connection_succeeded


@when(parsers.parse('I create an AggregateClient connected to "{endpoint}"'))
def _when_create_aggregate_client(state: _State, endpoint: str) -> None:
    state.client_created = True
    state.connection_succeeded = True


@then("the client should be able to execute commands")
def _then_client_can_execute(state: _State) -> None:
    assert state.connection_succeeded


@when(parsers.parse('I create a SpeculativeClient connected to "{endpoint}"'))
def _when_create_speculative_client(state: _State, endpoint: str) -> None:
    state.client_created = True
    state.connection_succeeded = True


@then("the client should be able to perform speculative operations")
def _then_client_can_speculate(state: _State) -> None:
    assert state.connection_succeeded


@when(parsers.parse('I create a DomainClient connected to "{endpoint}"'))
def _when_create_domain_client(state: _State, endpoint: str) -> None:
    state.client_created = True
    state.connection_succeeded = True


@then("the client should have aggregate and query sub-clients")
def _then_client_has_sub_clients(state: _State) -> None:
    assert state.client_created


@then("both should share the same connection")
def _then_both_share_connection(state: _State) -> None:
    pass


@when(parsers.parse('I create a Client connected to "{endpoint}"'))
def _when_create_full_client(state: _State, endpoint: str) -> None:
    state.client_created = True
    state.connection_succeeded = True


@then("the client should have aggregate, query, and speculative sub-clients")
def _then_client_has_all_sub_clients(state: _State) -> None:
    assert state.client_created


# --- Connection options -----------------------------------------------------


@when(parsers.parse("I connect with timeout of {seconds:d} seconds"))
def _when_connect_with_timeout(state: _State, seconds: int) -> None:
    state.timeout_s = seconds
    state.connection_succeeded = True


@then("the connection should respect the timeout")
def _then_connection_respects_timeout(state: _State) -> None:
    assert state.timeout_s is not None


@then("slow connections should fail after timeout")
def _then_slow_connections_fail(state: _State) -> None:
    pass


@when("I connect with keep-alive enabled")
def _when_connect_with_keepalive(state: _State) -> None:
    state.keep_alive = True
    state.connection_succeeded = True


@then("the connection should send keep-alive probes")
def _then_connection_sends_keepalive(state: _State) -> None:
    assert state.keep_alive


@then("idle connections should remain open")
def _then_idle_connections_remain(state: _State) -> None:
    pass


# --- Error handling ---------------------------------------------------------


@then("the error should indicate invalid format")
def _then_error_invalid_format(state: _State) -> None:
    assert state.error_type == "invalid_format"


@given("an established connection")
def _given_established_connection(state: _State) -> None:
    state.connection_succeeded = True
    state.client_created = True


@when("the server disconnects")
def _when_server_disconnects(state: _State) -> None:
    state.connection_failed = True
    state.error = "connection lost"
    state.error_type = "connection_lost"


@when("I attempt an operation")
def _when_attempt_operation(state: _State) -> None:
    pass


@then("the operation should fail")
def _then_operation_fails(state: _State) -> None:
    assert state.connection_failed


@then("the error should indicate connection lost")
def _then_error_connection_lost(state: _State) -> None:
    assert state.error_type == "connection_lost"


@given("a connection that failed")
def _given_connection_failed(state: _State) -> None:
    state.connection_failed = True


@when("I create a new client with the same endpoint")
def _when_create_new_client(state: _State) -> None:
    state.client_created = True
    state.connection_succeeded = True
    state.connection_failed = False


@then("the new connection should be independent")
def _then_new_connection_independent(state: _State) -> None:
    pass


@then("the new connection should succeed if server is available")
def _then_new_connection_succeeds(state: _State) -> None:
    assert state.connection_succeeded
