"""Tests for angzarr_client._metadata: the version of the distribution that
installed a package, found from the package's name.

The suite runs against an editable install, whose metadata maps no package
to its distribution, so the installed map is injected here: one where it
names the distribution (a wheel install), one where it has no entry.
"""

from __future__ import annotations

import pytest

import angzarr_client
from angzarr_client import _metadata


@pytest.fixture
def versions(monkeypatch):
    """The installed versions, by distribution name, that the lookup sees."""
    known = {"wheel-dist": "1.2.3", "pkg": "4.5.6"}
    monkeypatch.setattr(_metadata, "version", known.__getitem__)
    return known


def test_a_package_the_metadata_maps_has_its_distributions_version(
    monkeypatch, versions
) -> None:
    monkeypatch.setattr(
        _metadata,
        "packages_distributions",
        lambda: {"other": ["pkg"], "pkg": ["wheel-dist"]},
    )
    assert _metadata.installed_version("pkg") == "1.2.3"


def test_a_package_the_metadata_does_not_map_is_its_own_distribution(
    monkeypatch, versions
) -> None:
    monkeypatch.setattr(
        _metadata, "packages_distributions", lambda: {"other": ["wheel-dist"]}
    )
    assert _metadata.installed_version("pkg") == "4.5.6"


def test_the_package_version_is_its_installed_distributions() -> None:
    assert angzarr_client.__version__ == _metadata.installed_version(
        angzarr_client.__name__
    )
