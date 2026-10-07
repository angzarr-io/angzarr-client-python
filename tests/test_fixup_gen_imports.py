"""Unit tests for scripts/fixup_gen_imports.py (the generated-import reroot)."""

from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fixup_gen_imports.py"
)
_spec = importlib.util.spec_from_file_location("fixup_gen_imports", _SCRIPT)
fixup_gen_imports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixup_gen_imports)


def _write(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_longest_prefix_wins_and_google_is_untouched(tmp_path):
    pb2 = _write(
        tmp_path,
        "io/angzarr/examples/v1/player_pb2.py",
        "from google.protobuf import any_pb2 as a\n"
        "from io.angzarr.v1 import types_pb2 as t\n"
        "from io.angzarr.examples.v1 import cards_pb2 as c\n"
        "import sererr.v1.sererr_pb2\n",
    )
    changed = fixup_gen_imports.fixup(
        tmp_path,
        {
            "io.angzarr.v1": "angzarr_client.proto.io.angzarr.v1",
            "io.angzarr.examples": "gen.io.angzarr.examples",
            "sererr": "angzarr_client.proto.sererr",
        },
    )
    assert changed == 1
    assert pb2.read_text() == (
        "from google.protobuf import any_pb2 as a\n"
        "from angzarr_client.proto.io.angzarr.v1 import types_pb2 as t\n"
        "from gen.io.angzarr.examples.v1 import cards_pb2 as c\n"
        "import angzarr_client.proto.sererr.v1.sererr_pb2\n"
    )


def test_prefix_matches_whole_module_components_only(tmp_path):
    grpc = _write(
        tmp_path, "x_pb2_grpc.py", "from iox.thing import a_pb2\nfrom io import b_pb2\n"
    )
    fixup_gen_imports.fixup(tmp_path, {"io": "pkg.io"})
    assert grpc.read_text() == "from iox.thing import a_pb2\nfrom pkg.io import b_pb2\n"


def test_only_generated_files_are_rewritten(tmp_path):
    wiring = _write(
        tmp_path, "order_angzarr.py", "from io.angzarr.v1 import types_pb2\n"
    )
    stub = _write(tmp_path, "types_pb2.pyi", "from io.angzarr.v1 import meta_pb2\n")
    changed = fixup_gen_imports.fixup(tmp_path, {"io": "pkg.io"})
    assert changed == 1
    assert wiring.read_text() == "from io.angzarr.v1 import types_pb2\n"
    assert stub.read_text() == "from pkg.io.angzarr.v1 import meta_pb2\n"


def test_main_refuses_malformed_arguments(tmp_path, capsys):
    assert fixup_gen_imports.main(["fixup", str(tmp_path)]) == 2
    assert fixup_gen_imports.main(["fixup", str(tmp_path), "no-equals"]) == 2
    assert fixup_gen_imports.main(["fixup", str(tmp_path / "missing"), "a=b"]) == 1
    assert fixup_gen_imports.main(["fixup", str(tmp_path), "a=b"]) == 0
    assert "rerooted imports in 0 file(s)" in capsys.readouterr().out
