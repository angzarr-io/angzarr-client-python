"""Example and business vocabulary that must never appear in the
angzarr_client package (parity C-0496): the package is generic framework code;
application types, services and domains belong to applications."""

from __future__ import annotations

import pathlib
import re

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "angzarr_client"

# Words that never name a framework concept: flagged anywhere, as a word or
# as part of an identifier.
BUSINESS_TERMS = frozenset(
    {
        "blackjack",
        "poker",
        "player",
        "players",
        "ledger",
        "dealer",
        "wager",
        "wagers",
        "bet",
        "bets",
        "chips",
        "card",
        "cards",
        "deck",
        "tournament",
        "loyalty",
        "buyin",
        "cashout",
        "topup",
        "payment",
        "payments",
        "cart",
        "customer",
        "customers",
        "shipping",
        "shipment",
        "fulfillment",
        "invoice",
    }
)

# Business words that are also plain English ("declaration order", "dispatch
# tables", "hands it back"): flagged as part of an identifier (OrderCreated,
# table_id) or as a quoted name ("order", "table-1"), not in prose.
AMBIGUOUS_TERMS = frozenset(
    {
        "order",
        "orders",
        "table",
        "tables",
        "hand",
        "hands",
        "round",
        "rounds",
        "product",
        "products",
        "inventory",
        "account",
        "accounts",
    }
)

# Generated framework protos are the spec's own text (angzarr-project).
_EXCLUDED_DIRS = {"proto", "__pycache__", "_lib"}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_SPLIT = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_QUOTED = re.compile(
    r"""["'](""" + "|".join(sorted(AMBIGUOUS_TERMS)) + r""")(?=["'\-_.:/])""",
    re.IGNORECASE,
)


def _parts(token: str) -> list[str]:
    return [w.lower() for part in token.split("_") for w in _SPLIT.findall(part)]


def business_terms_in(path: pathlib.Path) -> list[tuple[int, str]]:
    """(line number, term) for every business term in a source file."""
    hits = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        for token in _WORD.findall(line):
            parts = _parts(token)
            for word in parts:
                if word in BUSINESS_TERMS or (
                    len(parts) > 1 and word in AMBIGUOUS_TERMS
                ):
                    hits.append((number, word))
        hits.extend((number, m.group(1).lower()) for m in _QUOTED.finditer(line))
    return hits


def package_files() -> list[pathlib.Path]:
    """Every source file of the package, generated protos excepted."""
    return sorted(
        p
        for p in PACKAGE.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".pyi", ".tmpl", ".yaml", ".md", ".txt"}
        and not (_EXCLUDED_DIRS & set(p.relative_to(PACKAGE).parts))
    )
