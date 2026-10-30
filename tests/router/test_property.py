"""A property sweep over the router's fold and the counter's rejection
threshold, across a grid of (prior history, increase amount) the tier's
scenarios do not enumerate. The reference model is the obvious one: the
handler sees the prior count at that next sequence and emits ``amount``
events, or a VALUE_NOT_POSITIVE rejection at amount 0, so a divergence flags
an off-by-one in the fold or a wrong threshold."""

import pytest

from angzarr_client.router import CodedError, Router

from . import builders
from .contract import Observed, counter_aggregate

_BOUND = 13  # 13 x 13 = 169 (prior, amount) combinations


@pytest.fixture
def seen() -> list[Observed]:
    return []


@pytest.fixture
def bare_router(seen):
    r = Router()
    r.register_aggregate(counter_aggregate(seen))
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
            assert seen[-1].ctx.next_sequence == prior_n, where
