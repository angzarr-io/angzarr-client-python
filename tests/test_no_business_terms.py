"""The package and its codegen templates carry no example or business
vocabulary (parity C-0496); documentation is not scanned."""

from __future__ import annotations

import pathlib

from tests.business_terms import (
    AMBIGUOUS_TERMS,
    BUSINESS_TERMS,
    business_terms_in,
    package_files,
)

CODEGEN = pathlib.Path(__file__).resolve().parents[1] / "codegen"


def test_package_sources_name_no_business_concept():
    files = package_files()
    assert len(files) > 20, "the scan found too few package files to be meaningful"
    hits = {str(f): business_terms_in(f) for f in files}
    assert {f: h for f, h in hits.items() if h} == {}


def test_codegen_templates_name_no_business_concept():
    files = sorted(CODEGEN.iterdir())
    assert files, "no codegen templates found"
    hits = {str(f): business_terms_in(f) for f in files}
    assert {f: h for f, h in hits.items() if h} == {}


def test_the_scan_finds_business_terms_in_identifiers(tmp_path):
    source = tmp_path / "x.py"
    source.write_text(
        "class LedgerQueryService:\n"
        "    player_id = 1\n"
        "BUY_IN_DOMAIN = 'table'  # Blackjack\n"
        "def topupHandler(): pass\n"
        "routing = 'tableau'\n"
        "# dispatch tables in declaration order; the core hands it back\n"
        "class OrderCreated: root = uuid_for('order-42')\n"
    )
    assert business_terms_in(source) == [
        (1, "ledger"),
        (2, "player"),
        (3, "blackjack"),
        (3, "table"),
        (4, "topup"),
        (7, "order"),
        (7, "order"),
    ]


def test_the_vocabulary_covers_the_examples():
    assert {"blackjack", "poker", "player", "ledger"} <= BUSINESS_TERMS
    assert {"table", "round", "hand", "order"} <= AMBIGUOUS_TERMS
