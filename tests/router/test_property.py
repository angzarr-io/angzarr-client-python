"""P5 property sweep — the binding's breadth signal. Exercises the core's
rebuild and rejection threshold across a broad grid of (prior history,
increase amount) the named scenarios don't enumerate. The expected outcome is
the obvious reference model — the handler sees the prior count at that next
sequence and emits ``amount`` events, or a VALUE_NOT_POSITIVE rejection at
amount 0 — so a divergence flags an off-by-one in the rebuild or a wrong
threshold. No old client is linked; the shared
conformance features remain the cross-language behavior contract."""

import pytest

from angzarr_client.router import CodedError, Router

from . import builders
from .fixture import CounterAggregate, Observation
from .gen.test.counter import counter_aggregate_angzarr

_BOUND = 13  # 13 x 13 = 169 (prior, amount) combinations


@pytest.fixture
def seen() -> list[Observation]:
    return []


@pytest.fixture
def bare_router(seen):
    r = Router()
    counter_aggregate_angzarr.register_counter_aggregate(r, CounterAggregate(seen))
    yield r
    r.close()


def test_property_sweep(bare_router, seen):
    for prior_n in range(_BOUND):
        for amount in range(_BOUND):
            cmd = builders.increase_command(amount)
            prior = builders.prior_increases(prior_n)
            if prior is not None:
                cmd.events.CopyFrom(prior)

            if amount == 0:
                with pytest.raises(CodedError) as exc:
                    bare_router.dispatch(cmd)
                assert exc.value.code == "VALUE_NOT_POSITIVE", f"prior={prior_n}"
                continue

            resp = bare_router.dispatch(cmd)
            where = f"prior={prior_n} amount={amount}"
            assert len(resp.events.pages) == amount, where
            assert seen[-1].count == prior_n, where
            assert seen[-1].cctx.next_sequence == prior_n, where
