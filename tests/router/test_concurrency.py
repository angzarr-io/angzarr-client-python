"""Concurrent dispatch: parallel dispatches from several threads against
one router stay isolated. The router keeps no state between deliveries, so
the state a handler sees reflects only its own delivery's history, never
another thread's."""

import threading

from angzarr_client.proto.io.angzarr.v1 import types_pb2
from angzarr_client.router import AggregateDispatch, Rebuilder, Router, pack

from . import builders
from .gen import counter_pb2

_THREADS = 8
_ITERATIONS = 50


def _echo_count(_cmd, state, _cctx):
    """Answer one event carrying the count the handler saw."""
    book = types_pb2.EventBook()
    book.pages.add().payload.CopyFrom(pack(state))
    return book


def _count_increases(state, _event):
    state.count += 1


def test_concurrent_dispatches_isolate_state():
    router = Router()
    router.register_aggregate(
        AggregateDispatch(
            "Counter",
            "counter",
            Rebuilder(factory=counter_pb2.CounterState).apply(
                builders.FQ_INCREASED, _count_increases
            ),
        ).on_command(builders.FQ_INCREASE_BY, _echo_count)
    )
    errors: list[Exception] = []

    def worker(prior_n: int) -> None:
        try:
            prior = builders.prior_increases(prior_n)
            for _ in range(_ITERATIONS):
                cmd = builders.increase_command(1)
                if prior is not None:
                    cmd.events.CopyFrom(prior)
                resp = router.dispatch(cmd)
                seen = counter_pb2.CounterState.FromString(
                    resp.events.pages[0].payload.value
                ).count
                if seen != prior_n:
                    raise AssertionError(f"thread prior={prior_n} saw count {seen}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    router.close()

    assert not errors, errors
