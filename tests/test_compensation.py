"""The compensation tooling's edges (angzarr_client.compensation, decision
14.74), beside the handler-contract tier's compensation.feature
(tests/router/test_handler_contract.py), as angzarr-embedded's
compensation.test.rs has them: a reply of no step's, an undo no step
becomes, a target with no (command) declaration, the warning logged and as
a code and its args."""

from __future__ import annotations

import logging

from angzarr_client import Compensation, UndoTargetsCommand
from angzarr_client.error_codes import codes
from tests.router.gen import compensation_pb2 as c


def order() -> Compensation:
    """Two steps: "reserve", undone by a fact (ReleaseStock), and "charge",
    undone by a fact (RefundPayment)."""
    return (
        Compensation(
            # Each built from the state too: the PM's events carry its data.
            failed=lambda state, reason: ("failed", reason, state.charge_id),
            rolled_back=lambda state: state.failed,
            compensation_failed=lambda state, step: (
                "compensation-failed",
                step,
                state.reservation_id,
            ),
        )
        .step(
            "reserve",
            completed_by=c.StockReserved,
            undoes=c.ReleaseStock,
            recorded=lambda s: c.StockReserved() if s.reservation_id else None,
            undo=lambda _: "release-due",
        )
        .step(
            "charge",
            completed_by=c.PaymentCharged,
            undoes=c.RefundPayment,
            recorded=lambda s: c.PaymentCharged() if s.charge_id else None,
            undo=lambda _: "refund-due",
        )
    )


def test_a_rollback_undoes_the_recorded_steps_newest_first_after_the_failure():
    both = c.ShipmentState(reservation_id="r", charge_id="c")
    assert order().rollback(both, "no") == [
        ("failed", "no", "c"),
        "refund-due",
        "release-due",
    ]
    charged = c.ShipmentState(charge_id="c")
    assert order().rollback(charged, "no") == [("failed", "no", "c"), "refund-due"]


def test_a_reply_is_undone_only_once_rolled_back_and_only_for_its_step():
    charged = c.PaymentCharged(charge_id="c")
    assert order().reply(c.ShipmentState(), charged) == [charged]
    failed = c.ShipmentState(failed=True)
    assert order().reply(failed, charged) == [charged, "refund-due"]
    reserved = c.StockReserved()
    assert order().reply(failed, reserved) == [reserved, "release-due"]
    # No step is recorded by it: the reply alone.
    unrelated = c.RefundDue()
    assert order().reply(failed, unrelated) == [unrelated]


def test_a_refused_undo_is_its_steps_compensation_failure():
    state = c.ShipmentState(reservation_id="r")
    assert order().undo_refused(state, c.RefundPayment) == (
        "compensation-failed",
        "charge",
        "r",
    )
    assert order().undo_refused(state, "test.compensation.ReleaseStock") == (
        "compensation-failed",
        "reserve",
        "r",
    )
    assert order().undo_refused(state, c.RestockItems) is None


def test_registering_warns_of_each_undo_that_becomes_a_command_and_logs_it(caplog):
    assert order().warnings == ()
    with caplog.at_level(logging.WARNING, logger="angzarr_client.compensation"):
        shipping = (
            order()
            .step(
                "ship",
                completed_by=c.RefundDue,
                undoes=c.RestockItems,
                recorded=lambda s: None,
                undo=lambda _: None,
            )
            .step(
                "ship again",
                completed_by=c.RefundDue,
                undoes=c.RestockItems,
                recorded=lambda s: None,
                undo=lambda _: None,
            )
        )
    assert [(w.step, w.command) for w in shipping.warnings] == [
        ("ship", "test.compensation.RestockItems"),
        ("ship again", "test.compensation.RestockItems"),
    ]
    assert [r.getMessage() for r in caplog.records] == [
        str(w) for w in shipping.warnings
    ]
    # Logged lazily: the warning itself is the record's argument, its text
    # made only where a handler shows it.
    assert [r.args for r in caplog.records] == [(w,) for w in shipping.warnings]


def test_an_undo_to_a_message_no_command_entry_declares_is_not_warned_of():
    # ShipmentFailed has no (command) entry: nothing declares it refusable.
    lone = order().step(
        "lone",
        completed_by=c.RefundDue,
        undoes=c.ShipmentFailed.DESCRIPTOR,
        recorded=lambda s: None,
        undo=lambda _: None,
    )
    assert lone.warnings == ()


def test_the_warning_is_a_code_and_its_args_never_english():
    warning = UndoTargetsCommand(step="ship", command="test.compensation.RestockItems")
    assert warning.code == codes.UNDO_TARGETS_COMMAND == "UNDO_TARGETS_COMMAND"
    assert warning.args() == {
        "step": "ship",
        "command": "test.compensation.RestockItems",
    }
    assert (
        str(warning)
        == "UNDO_TARGETS_COMMAND step=ship command=test.compensation.RestockItems"
    )
