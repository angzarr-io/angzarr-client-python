"""Suite-wide guards.

A test must not leave a timer thread running or a signal handler installed:
a stray timer that signals the process (or a handler left behind) lands in
whichever test runs next — it stopped other tests' hosts, killed the run with
SIGTERM, or left it hung. Each test is checked on teardown; a leaked timer is
cancelled and the test fails naming it.
"""

from __future__ import annotations

import signal
import threading

import pytest

_SIGNALS = (signal.SIGTERM, signal.SIGINT)


@pytest.fixture(autouse=True)
def _no_leaked_timers_or_signal_handlers():
    handlers = {sig: signal.getsignal(sig) for sig in _SIGNALS}
    before = set(threading.enumerate())
    yield
    leaked = [
        t
        for t in threading.enumerate()
        if t not in before and isinstance(t, threading.Timer) and t.is_alive()
    ]
    for timer in leaked:
        timer.cancel()
        timer.join(5)
    assert (
        not leaked
    ), f"the test left timer threads running: {[t.name for t in leaked]}"
    changed = {sig.name for sig in _SIGNALS if signal.getsignal(sig) != handlers[sig]}
    for sig in _SIGNALS:
        signal.signal(sig, handlers[sig])
    assert not changed, f"the test left signal handlers installed: {sorted(changed)}"
