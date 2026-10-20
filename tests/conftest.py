"""Suite-wide guards, and how Gherkin tags become pytest marks.

A test must not leave a timer thread running or a signal handler installed:
a stray timer that signals the process (or a handler left behind) lands in
whichever test runs next — it stopped other tests' hosts, killed the run with
SIGTERM, or left it hung. Each test is checked on teardown; a leaked timer is
cancelled and the test fails naming it.
"""

from __future__ import annotations

import re
import signal
import threading

import pytest

# A spec scenario id (@C-0580, @EU-0012, @EA-0007): an identifier, not a
# category, so it is not turned into a pytest mark.
_SCENARIO_ID = re.compile(r"(?:C|EU|EA)-\d+")


# The attribute a scenario function carries its scenario ids in.
SCENARIO_IDS_ATTR = "angzarr_scenario_ids"


def pytest_bdd_apply_tag(tag, function):
    """Keep scenario-id tags off the pytest marks: record them on the
    scenario function (``SCENARIO_IDS_ATTR``) and tell pytest-bdd the tag is
    handled (True). Every other tag becomes the registered mark of that
    name."""
    if _SCENARIO_ID.fullmatch(tag):
        ids = getattr(function, SCENARIO_IDS_ATTR, frozenset())
        setattr(function, SCENARIO_IDS_ATTR, ids | {tag})
        return True
    return None


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
