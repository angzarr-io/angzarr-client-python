"""Sphinx configuration for the Python reference docs.

Build with `just docs` (runs `sphinx-build -b html docs docs/_build/html`).
Reads docstrings directly from the installed package via sphinx-autoapi,
so edits to source docstrings flow into the generated pages on next build.
"""

from __future__ import annotations

import importlib.util
import pathlib

# Standard library from Python 3.11 (ruff, targeting the package's 3.10 floor,
# sorts it as third-party); the docs build runs on uv's newest Python.
import tomllib

# The project and its package as pyproject.toml names them, so a rename is
# picked up: the distribution, and the one package its wheel builds.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PYPROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text())
_PACKAGE = _PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"][0]


def _installed_version() -> str:
    """The package's version, found as the package finds its own
    (``<package>/_metadata.py``, loaded alone: the package does not import
    here, its generated protos are not built for the docs)."""
    spec = importlib.util.spec_from_file_location(
        "_metadata", _ROOT / _PACKAGE / "_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.installed_version(_PACKAGE)


project = _PYPROJECT["project"]["name"]
author = "Benjamin Abbitt"
copyright = "2026, Benjamin Abbitt"

# Single source of truth; matches pyproject.toml dynamic version (VERSION file).
try:
    release = _installed_version()
except Exception:  # noqa: BLE001 — docs build without an installed package
    release = "0.0.0"
version = release

# -- Sphinx extensions -------------------------------------------------------
extensions = [
    # Automatic API doc generation — walks the package and produces
    # reference pages without per-module directives.
    "autoapi.extension",
    # Google/NumPy-style docstrings; the codebase uses Google style.
    "sphinx.ext.napoleon",
    # Intersphinx cross-references to Python stdlib.
    "sphinx.ext.intersphinx",
    # Markdown source support for the landing page.
    "myst_parser",
]

# -- sphinx-autoapi ---------------------------------------------------------
autoapi_type = "python"
autoapi_dirs = [f"../{_PACKAGE}"]
autoapi_ignore = [
    # Skip generated proto stubs — they're proto-generated and noisy.
    "*/proto/*",
]
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]
autoapi_python_class_content = "both"  # class + __init__ docstrings
autoapi_keep_files = False
autoapi_add_toctree_entry = True

# -- Napoleon ---------------------------------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

# -- Intersphinx ------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# -- MyST --------------------------------------------------------------------
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# -- HTML output -------------------------------------------------------------
html_theme = "furo"
html_title = f"{project} {release}"
# No static assets yet; omit html_static_path so Sphinx doesn't warn about
# the missing directory.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Link "Edit on GitHub" to the repo. Furo reads these via html_theme_options.
html_theme_options = {
    "source_repository": "https://github.com/angzarr-io/angzarr-client-python/",
    "source_branch": "main",
    "source_directory": f"{_PACKAGE}/",
}
