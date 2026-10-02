"""Client constructors: connect, from_channel, from_env, from_stub(s), from_clients."""

from unittest.mock import Mock

import grpc
import pytest

from angzarr_client._pb import (
    CommandBook,
    CommandRequest,
    Query,
    SpeculateCommandHandlerRequest,
    SpeculatePmRequest,
    SpeculateProjectorRequest,
    SpeculateSagaRequest,
)
from angzarr_client.client import (
    CommandHandlerClient,
    DomainClient,
    QueryClient,
    SpeculativeClient,
)
from angzarr_client.retry import RetryPolicy

CHANNEL_CLIENTS = [QueryClient, CommandHandlerClient, SpeculativeClient, DomainClient]


class SingleAttempt(RetryPolicy):
    """Runs the operation exactly once, recording that it ran."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, operation):
        self.calls += 1
        return operation()


@pytest.fixture
def channels(monkeypatch):
    """Replace grpc.insecure_channel with a factory of recorded mock channels."""
    made: list[tuple[str, Mock]] = []

    def factory(target):
        ch = Mock(spec=grpc.Channel)
        made.append((target, ch))
        return ch

    monkeypatch.setattr(grpc, "insecure_channel", factory)
    return made


@pytest.mark.parametrize("cls", CHANNEL_CLIENTS)
def test_connect_opens_channel_via_retry_policy_and_owns_it(cls, channels) -> None:
    policy = SingleAttempt()
    client = cls.connect("localhost:1310", retry=policy)

    assert policy.calls == 1
    assert [target for target, _ in channels] == ["localhost:1310"]
    client.close()
    channels[0][1].close.assert_called_once_with()


@pytest.mark.parametrize("cls", CHANNEL_CLIENTS)
def test_from_channel_leaves_channel_open_on_close(cls) -> None:
    channel = Mock(spec=grpc.Channel)
    with cls.from_channel(channel):
        pass
    channel.close.assert_not_called()


def test_domain_client_from_channel_wires_sub_clients_to_channel() -> None:
    channel = Mock(spec=grpc.Channel)
    client = DomainClient.from_channel(channel)

    assert isinstance(client.command_handler, CommandHandlerClient)
    assert isinstance(client.query, QueryClient)
    assert isinstance(client.speculative, SpeculativeClient)
    assert channel.unary_unary.called


def test_domain_client_init_builds_speculative_client() -> None:
    channel = Mock(spec=grpc.Channel)
    client = DomainClient(channel)
    assert isinstance(client.speculative, SpeculativeClient)


@pytest.mark.parametrize("cls", CHANNEL_CLIENTS)
def test_from_env_connects_to_env_value_or_default(cls, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        cls, "connect", classmethod(lambda _cls, endpoint: seen.append(endpoint))
    )
    monkeypatch.setenv("AZ_TEST_ENDPOINT", "envhost:1")
    cls.from_env("AZ_TEST_ENDPOINT", "fallback:2")
    monkeypatch.setenv("AZ_TEST_ENDPOINT", "")
    cls.from_env("AZ_TEST_ENDPOINT", "fallback:2")
    monkeypatch.delenv("AZ_TEST_ENDPOINT")
    cls.from_env("AZ_TEST_ENDPOINT", "fallback:3")

    assert seen == ["envhost:1", "fallback:2", "fallback:3"]


class RecordingRpc:
    """Callable stand-in for a stub method; records each invocation."""

    def __init__(self, response) -> None:
        self.response = response
        self.requests: list = []

    def __call__(self, request, *, timeout, metadata):
        self.requests.append(request)
        return self.response


def test_query_client_from_stub_routes_rpcs_to_stub() -> None:
    stub = Mock()
    stub.GetEventBook = RecordingRpc("book")
    client = QueryClient.from_stub(stub)

    assert isinstance(client, QueryClient)
    query = Query()
    assert client.get_event_book(query) == "book"
    assert stub.GetEventBook.requests == [query]
    client.close()


def test_command_handler_client_from_stub_close_is_noop() -> None:
    stub = Mock()
    stub.HandleCommand = RecordingRpc("resp")
    client = CommandHandlerClient.from_stub(stub)

    request = CommandRequest(command=CommandBook())
    assert client.handle_command(request) == "resp"
    client.close()


def test_speculative_client_from_stubs_routes_each_method_to_its_stub() -> None:
    ch = Mock(HandleSyncSpeculative=RecordingRpc("ch"))
    saga = Mock(ExecuteSpeculative=RecordingRpc("saga"))
    proj = Mock(HandleSpeculative=RecordingRpc("proj"))
    pm = Mock(HandleSpeculative=RecordingRpc("pm"))
    client = SpeculativeClient.from_stubs(ch, saga, proj, pm)

    assert isinstance(client, SpeculativeClient)
    assert client.command_handler(SpeculateCommandHandlerRequest()) == "ch"
    assert client.saga(SpeculateSagaRequest()) == "saga"
    assert client.projector(SpeculateProjectorRequest()) == "proj"
    assert client.process_manager(SpeculatePmRequest()) == "pm"
    client.close()


def test_domain_client_from_clients_exposes_given_clients() -> None:
    ch, q, spec = object(), object(), object()
    client = DomainClient.from_clients(ch, q, spec)

    assert isinstance(client, DomainClient)
    assert client.command_handler is ch
    assert client.query is q
    assert client.speculative is spec
    client.close()
