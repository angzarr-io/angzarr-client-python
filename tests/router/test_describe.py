"""``describe``: a hand-written component's ComponentDescriptor, built from
its dispatch table, so a component written without angzarr-cli needs no
generated hex. What it builds is what the coordinator accepts (the rules of
angzarr-embedded-host's ``descriptor::validate``, decision 14.42) and what
angzarr-cli generates for the same component (angzarr-codegen's
``descriptor.rs``)."""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from angzarr_client import ConfigurationError, describe
from angzarr_client.descriptor import _refusal
from angzarr_client.proto.io.angzarr.v1 import options_pb2, types_pb2
from angzarr_client.router import ProjectorDispatch, Rebuilder

from .contract import counter_aggregate, counter_projector, fulfillment, order_policy
from .gen import counter_pb2, order_pb2

VERSION = "1.4.2+build.7"
STRICT = types_pb2.CONFLICT_RESOLUTION_STRICT
COMMUTATIVE = types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE
AGGREGATE_HANDLES = types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES


def _state(type_url: str, *resolutions: int) -> types_pb2.StateDescriptor:
    state = types_pb2.StateDescriptor(type_url=type_url)
    state.options.conflict_resolutions.extend(resolutions)
    return state


# --- each kind -----------------------------------------------------------------


def test_an_aggregate_is_described_by_its_name_kind_version_and_state():
    assert describe(counter_aggregate([]), rules_version=VERSION) == (
        types_pb2.ComponentDescriptor(
            name="CounterAggregate",
            kind=types_pb2.COMPONENT_KIND_AGGREGATE,
            rules_version=VERSION,
            state=_state("/test.counter.CounterState", STRICT, AGGREGATE_HANDLES),
        )
    )


def test_a_process_manager_lists_its_starting_types_by_full_name():
    described = describe(
        fulfillment("", False, []),
        rules_version=VERSION,
        starting_types=[order_pb2.StartFulfillment, "orders.v1.ShipOrder"],
    )
    assert described == types_pb2.ComponentDescriptor(
        name="Fulfillment",
        kind=types_pb2.COMPONENT_KIND_PROCESS_MANAGER,
        rules_version=VERSION,
        state=_state("/orders.v1.FulfillmentState", STRICT, AGGREGATE_HANDLES),
        starting_types=["orders.v1.StartFulfillment", "orders.v1.ShipOrder"],
    )


def test_a_starting_type_given_as_a_type_url_is_listed_by_its_name():
    described = describe(
        fulfillment("", False, []),
        rules_version=VERSION,
        starting_types=["type.googleapis.com/orders.v1.StartFulfillment"],
    )
    assert list(described.starting_types) == ["orders.v1.StartFulfillment"]


def test_a_type_url_with_a_path_is_cut_to_the_name_after_its_last_slash():
    described = describe(
        fulfillment("", False, []),
        rules_version=VERSION,
        starting_types=["example.com/types/v1/orders.v1.StartFulfillment"],
    )
    assert list(described.starting_types) == ["orders.v1.StartFulfillment"]


def test_a_policy_has_its_one_input_and_its_output_domain_and_no_state():
    assert describe(order_policy("inventory", []), rules_version=VERSION) == (
        types_pb2.ComponentDescriptor(
            name="OrderPolicy",
            kind=types_pb2.COMPONENT_KIND_POLICY,
            rules_version=VERSION,
            inputs=[types_pb2.Target(domain="order")],
            output_domain="inventory",
        )
    )


def test_a_projector_has_an_input_per_source_domain_and_no_state():
    projector = counter_projector([], "counter", "ledger", "order")
    assert describe(projector, rules_version=VERSION) == (
        types_pb2.ComponentDescriptor(
            name="CounterProjector",
            kind=types_pb2.COMPONENT_KIND_PROJECTOR,
            rules_version=VERSION,
            inputs=[
                types_pb2.Target(domain="counter"),
                types_pb2.Target(domain="ledger"),
                types_pb2.Target(domain="order"),
            ],
        )
    )


def test_a_projector_of_every_domain_names_no_input():
    projector = ProjectorDispatch("All", counter_pb2.CounterProjectorState)
    assert list(describe(projector, rules_version=VERSION).inputs) == []


# --- the rules version ---------------------------------------------------------


def test_the_rules_version_defaults_to_the_tables_own():
    table = counter_aggregate([]).with_rules_version("9.9.9")
    assert describe(table).rules_version == "9.9.9"


def test_without_a_rules_version_nothing_is_described():
    with pytest.raises(ConfigurationError) as info:
        describe(counter_aggregate([]))
    assert info.value.code == "COORDINATOR_REASON_MISSING_RULES_VERSION"
    assert str(info.value) == "COORDINATOR_REASON_MISSING_RULES_VERSION"
    assert info.value.details == {"component": "CounterAggregate"}


# --- the state -----------------------------------------------------------------


def test_the_state_type_may_be_named_by_class_or_by_full_name():
    by_class = describe(
        counter_aggregate([]), rules_version=VERSION, state_type=order_pb2.OrderState
    )
    by_name = describe(
        counter_aggregate([]), rules_version=VERSION, state_type="orders.v1.OrderState"
    )
    assert by_class.state.type_url == by_name.state.type_url == "/orders.v1.OrderState"


def test_resolutions_given_are_added_to_the_base_deduplicated_in_enum_order():
    described = describe(
        counter_aggregate([]),
        rules_version=VERSION,
        resolutions=[STRICT, COMMUTATIVE, COMMUTATIVE],
    )
    assert list(described.state.options.conflict_resolutions) == [
        COMMUTATIVE,
        STRICT,
        AGGREGATE_HANDLES,
    ]


def test_unspecified_is_not_a_resolution():
    with pytest.raises(ConfigurationError) as info:
        describe(
            counter_aggregate([]),
            rules_version=VERSION,
            resolutions=[types_pb2.CONFLICT_RESOLUTION_UNSPECIFIED],
        )
    assert info.value.code == "DESCRIPTOR_RESOLUTION_UNSPECIFIED"
    assert str(info.value) == "DESCRIPTOR_RESOLUTION_UNSPECIFIED"
    assert info.value.details == {"component": "CounterAggregate"}


def _commutative_state():
    """A state message whose proto declares ``(io.angzarr.v1.state)`` with
    COMMUTATIVE, built at run time in the default pool."""
    file = descriptor_pb2.FileDescriptorProto(
        name="test/describe/commutative.proto",
        package="test.describe",
        syntax="proto3",
        dependency=["io/angzarr/v1/options.proto"],
    )
    message = file.message_type.add(name="ListState")
    message.options.Extensions[options_pb2.state].conflict_resolutions.append(
        COMMUTATIVE
    )
    pool = descriptor_pool.Default()
    try:
        found = pool.FindMessageTypeByName("test.describe.ListState")
    except KeyError:
        found = pool.Add(file).message_types_by_name["ListState"]
    return message_factory.GetMessageClass(found)


def test_the_resolutions_default_to_the_state_messages_state_option():
    state_class = _commutative_state()
    table = counter_aggregate([])
    table.rebuilder = Rebuilder(state_class)
    described = describe(table, rules_version=VERSION)
    assert described.state == _state(
        "/test.describe.ListState", COMMUTATIVE, STRICT, AGGREGATE_HANDLES
    )


# --- what the coordinator refuses is never built -------------------------------


def test_starting_types_on_another_kind_are_refused():
    with pytest.raises(ConfigurationError) as info:
        describe(
            counter_aggregate([]),
            rules_version=VERSION,
            starting_types=["test.counter.IncreaseBy"],
        )
    assert info.value.code == "COORDINATOR_REASON_STARTING_TYPES_ON_NON_PM"
    assert info.value.details == {
        "component": "CounterAggregate",
        "component_kind": "COMPONENT_KIND_AGGREGATE",
    }


@pytest.mark.parametrize(
    "table",
    [order_policy("inventory", []), counter_projector([])],
    ids=["policy", "projector"],
)
def test_a_state_on_a_stateless_kind_is_refused(table):
    with pytest.raises(ConfigurationError) as info:
        describe(table, rules_version=VERSION, state_type=counter_pb2.CounterState)
    assert info.value.code == "COORDINATOR_REASON_STATE_ON_STATELESS_KIND"


@pytest.mark.parametrize(
    "table",
    [order_policy("inventory", []), counter_projector([])],
    ids=["policy", "projector"],
)
def test_resolutions_on_a_stateless_kind_are_refused(table):
    with pytest.raises(ConfigurationError) as info:
        describe(table, rules_version=VERSION, resolutions=[COMMUTATIVE])
    assert info.value.code == "COORDINATOR_REASON_STATE_ON_STATELESS_KIND"
    assert info.value.details == {
        "component": table.name,
        "component_kind": types_pb2.ComponentKind.Name(table.kind),
    }


def test_a_policy_with_no_output_domain_is_refused():
    with pytest.raises(ConfigurationError) as info:
        describe(order_policy("", []), rules_version=VERSION)
    assert info.value.code == "COORDINATOR_REASON_POLICY_NO_OUTPUT_DOMAIN"
    assert info.value.details == {"component": "OrderPolicy"}


def test_a_policy_with_no_input_domain_is_refused():
    table = order_policy("inventory", [])
    table.input_domain = ""
    with pytest.raises(ConfigurationError) as info:
        describe(table, rules_version=VERSION)
    assert info.value.code == "COORDINATOR_REASON_POLICY_INPUT_COUNT"
    assert info.value.details == {"component": "OrderPolicy", "input_count": "0"}


def test_a_table_of_no_known_kind_is_not_described():
    with pytest.raises(TypeError, match="not a component's dispatch table"):
        describe(object(), rules_version=VERSION)


# --- the rules no describe() output can break, each as the coordinator keeps it


def test_no_state_on_a_stateful_kind_is_refused():
    descriptor = types_pb2.ComponentDescriptor(
        name="Fulfillment",
        kind=types_pb2.COMPONENT_KIND_PROCESS_MANAGER,
        rules_version=VERSION,
    )
    assert _refusal(descriptor) == (
        "COORDINATOR_REASON_MISSING_STATE_TYPE",
        {
            "component": "Fulfillment",
            "component_kind": "COMPONENT_KIND_PROCESS_MANAGER",
        },
    )


def test_an_output_domain_on_another_kind_is_refused():
    descriptor = types_pb2.ComponentDescriptor(
        name="CounterProjector",
        kind=types_pb2.COMPONENT_KIND_PROJECTOR,
        rules_version=VERSION,
        output_domain="inventory",
    )
    assert _refusal(descriptor) == (
        "COORDINATOR_REASON_OUTPUT_DOMAIN_ON_NON_POLICY",
        {"component": "CounterProjector", "component_kind": "COMPONENT_KIND_PROJECTOR"},
    )


def test_the_rules_are_checked_in_the_coordinators_order():
    # Every rule broken at once: the first, the rules version, is the one.
    descriptor = types_pb2.ComponentDescriptor(
        name="P",
        kind=types_pb2.COMPONENT_KIND_POLICY,
        starting_types=["x.Y"],
        state=types_pb2.StateDescriptor(type_url="/x.S"),
    )
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_MISSING_RULES_VERSION"
    descriptor.rules_version = VERSION
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_STARTING_TYPES_ON_NON_PM"
    del descriptor.starting_types[:]
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_STATE_ON_STATELESS_KIND"
    descriptor.ClearField("state")
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_POLICY_INPUT_COUNT"
    descriptor.inputs.add(domain="a")
    descriptor.inputs.add(domain="b")
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_POLICY_INPUT_COUNT"
    del descriptor.inputs[1:]
    assert _refusal(descriptor)[0] == "COORDINATOR_REASON_POLICY_NO_OUTPUT_DOMAIN"
    descriptor.output_domain = "b"
    assert _refusal(descriptor) is None


# --- what angzarr-cli generates decodes equal to the hand construction --------

# The CounterAggregate's descriptor as angzarr-cli generates it (the hex of
# the `_descriptor.py` that `angzarr scaffold python` writes), at the
# justfile's CLI_REV, for the handler-contract fixture, rules version 0.1.0:
#
#     angzarr scaffold python -I angzarr-project/features/handler-contract/fixture \
#         --domain counter --no-lock --src <dir> counter.proto
#
# (buf may fail afterwards for want of options.proto; _descriptor.py is
# written first). Regenerate it when the CLI changes what it generates.
GENERATED_COUNTER_AGGREGATE = (
    "0a10436f756e7465724167677265676174652205302e312e30280132220a1a2f"
    "746573742e636f756e7465722e436f756e746572537461746512040a020203"
)


def test_a_generated_descriptor_decodes_equal_to_the_documented_construction():
    generated = types_pb2.ComponentDescriptor.FromString(
        bytes.fromhex(GENERATED_COUNTER_AGGREGATE)
    )
    # The README's table, field by field, for an aggregate.
    by_hand = types_pb2.ComponentDescriptor(
        name="CounterAggregate",
        kind=types_pb2.COMPONENT_KIND_AGGREGATE,
        rules_version="0.1.0",
        state=_state("/test.counter.CounterState", STRICT, AGGREGATE_HANDLES),
    )
    assert generated == by_hand
    assert describe(counter_aggregate([]), rules_version="0.1.0") == generated
    # Byte for byte, too: the same fields, in the same order.
    assert by_hand.SerializeToString() == generated.SerializeToString()
