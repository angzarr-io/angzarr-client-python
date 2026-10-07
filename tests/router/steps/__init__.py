"""Step definitions running angzarr-router's conformance features (read in
place from the pinned angzarr-router submodule) against the binding."""

import pathlib

CONFORMANCE_FEATURES = (
    pathlib.Path(__file__).resolve().parents[3]
    / "angzarr-router"
    / "conformance"
    / "features"
)
