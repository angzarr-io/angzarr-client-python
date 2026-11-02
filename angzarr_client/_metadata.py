"""The version of the distribution that installed a package, found from the
package's own name, so a rename is picked up.

It imports nothing of the package: the docs build (docs/conf.py) loads this
file alone, where the package itself does not import (its generated protos
are not built there).
"""

from __future__ import annotations

from importlib.metadata import packages_distributions, version


def installed_version(package: str) -> str:
    """The version of the distribution that installed ``package``.

    A wheel's installed metadata maps the package to its distribution; an
    editable install records only a .pth file, so the map has no entry, and
    there the package's name names its distribution (names compare
    normalized).
    """
    return version(packages_distributions().get(package, [package])[0])
