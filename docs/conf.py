"""Sphinx configuration for the angzarr-client Python reference docs.

Build with `just docs` (runs `sphinx-build -b html docs docs/_build/html`).
Reads docstrings directly from the installed package via sphinx-autoapi,
so edits to source docstrings flow into the generated pages on next build.
"""

from __future__ import annotations

from importlib.metadata import version as _pkg_version

project = "angzarr-client"
author = "Benjamin Abbitt"
copyright = "2026, Benjamin Abbitt"  # noqa: A001 (sphinx convention)

# Single source of truth; matches pyproject.toml dynamic version (VERSION file).
try:
    release = _pkg_version("angzarr-client")
except Exception:
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
autoapi_dirs = ["../angzarr_client"]
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
html_title = f"angzarr-client {release}"
# No static assets yet; omit html_static_path so Sphinx doesn't warn about
# the missing directory.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Link "Edit on GitHub" to the repo. Furo reads these via html_theme_options.
html_theme_options = {
    "source_repository": "https://github.com/angzarr-io/angzarr-client-python/",
    "source_branch": "main",
    "source_directory": "angzarr_client/",
}
