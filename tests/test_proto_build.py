"""Unit tests for scripts/proto_build.py: the generated protos and fixture
messages import under the packages they live in, and the build's
preconditions."""

from __future__ import annotations

import importlib.util
import pathlib
import re
import runpy

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "proto_build.py"
_spec = importlib.util.spec_from_file_location("proto_build", _SCRIPT)
proto_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proto_build)


def test_proto_generation_needs_the_submodule(monkeypatch, tmp_path):
    monkeypatch.setattr(proto_build, "PROJECT_PROTO", tmp_path / "missing")
    with pytest.raises(proto_build.ProtoBuildError, match="git submodule update"):
        proto_build.generate_protos(tmp_path / "out")


def test_fixture_generation_needs_the_submodule(monkeypatch, tmp_path):
    monkeypatch.setattr(proto_build, "FIXTURE_PROTO", tmp_path / "missing")
    with pytest.raises(proto_build.ProtoBuildError, match="git submodule update"):
        proto_build.generate_fixtures(tmp_path / "out")


def test_generated_protos_import_under_the_package(tmp_path):
    files = proto_build.generate_protos(tmp_path / "proto")
    names = {f.relative_to(tmp_path / "proto").as_posix() for f in files}
    assert {
        "io/angzarr/v1/types_pb2.py",
        "io/angzarr/v1/types_pb2.pyi",
        "io/angzarr/component/v1/command_handler_pb2_grpc.py",
        "io/angzarr/component/v1/policy_pb2_grpc.py",
        "io/angzarr/transport/embedded/v1/child_protocol_pb2.py",
        "io/angzarr/transport/distributed/v1/command_handler_pb2_grpc.py",
        "sererr/v1/sererr_pb2.py",
    } <= names
    assert not any(name.startswith("io/angzarr/status") for name in names)
    assert not any(name.startswith("io/angzarr/router") for name in names)
    text = (tmp_path / "proto" / "io/angzarr/v1/command_handler_pb2.py").read_text()
    assert "from angzarr_client.proto.io.angzarr.v1 import types_pb2" in text
    assert re.search(r"^from io\.", text, re.MULTILINE) is None


def test_fixture_messages_import_the_framework_from_the_package(tmp_path):
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "stale_pb2.py").write_text("")
    files = proto_build.generate_fixtures(tmp_path / "gen")
    assert {f.name for f in files} == {"counter_pb2.py", "order_pb2.py"}
    text = (tmp_path / "gen" / "counter_pb2.py").read_text()
    assert "from angzarr_client.proto.io.angzarr.v1 import options_pb2" in text


def test_a_failing_protoc_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(proto_build, "_protos", lambda root, trees: ["nope.proto"])
    with pytest.raises(proto_build.ProtoBuildError, match="protoc failed"):
        proto_build.generate_protos(tmp_path / "out")


def test_main_reports_usage(capsys):
    assert proto_build.main(["proto_build.py"]) == 2
    assert proto_build.main(["proto_build.py", "router-lib"]) == 2
    assert "usage" in capsys.readouterr().err


def test_main_generates_and_counts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(proto_build, "generate_fixtures", lambda: [tmp_path] * 3)
    assert proto_build.main(["proto_build.py", "fixtures"]) == 0
    assert capsys.readouterr().out == f"3 files under {proto_build.FIXTURE_OUT}\n"


def test_the_reason_args_and_the_catalog_are_generated_beside_the_reasons(tmp_path):
    files = proto_build.generate_protos(tmp_path / "proto")
    names = {f.relative_to(tmp_path / "proto").as_posix() for f in files}
    assert {"io/angzarr/v1/reason_args.py", "io/angzarr/v1/reasons.en-US.ftl"} <= names
    catalog = (tmp_path / "proto" / "io/angzarr/v1/reasons.en-US.ftl").read_text()
    assert (
        catalog
        == (proto_build.PROJECT_PROTO / "io/angzarr/v1/reasons.en-US.ftl").read_text()
    )
    namespace = runpy.run_path(str(tmp_path / "proto" / "io/angzarr/v1/reason_args.py"))
    assert namespace["ARGS"]["ROUTER_REASON_ANY_DECODE_FAILED"] == (
        "message_type",
        "ext_err_txt",
    )
    assert namespace["ARGS"]["ROUTER_REASON_HANDLER_PANICKED"] == ()
    assert "ROUTER_REASON_UNSPECIFIED" not in namespace["ARGS"]
    assert namespace["MESSAGE_TYPE"] == "message_type"
    assert namespace["REFUSED"] == "refused"


def test_a_live_key_without_its_option_fails_the_build():
    from google.protobuf import descriptor_pb2

    from angzarr_client.proto.io.angzarr.v1 import reasons_pb2

    file = descriptor_pb2.FileDescriptorProto(name="io/angzarr/v1/reasons.proto")
    enum = file.enum_type.add(name="RouterReason")
    enum.value.add(name="ROUTER_REASON_UNSPECIFIED", number=0)
    enum.value.add(name="ROUTER_REASON_LIVE", number=1)
    with pytest.raises(
        proto_build.ProtoBuildError, match="ROUTER_REASON_LIVE declares no args"
    ):
        proto_build._package_args(file, ("RouterReason",), reasons_pb2.reason)


def test_each_transport_has_its_own_reason_args_and_catalog(tmp_path):
    # Decision 14.64: each package with keys, its args and English beside
    # its reasons.proto; the child's keys are the local-process transport's.
    files = proto_build.generate_protos(tmp_path / "proto")
    names = {f.relative_to(tmp_path / "proto").as_posix() for f in files}
    for package, key, args in (
        (
            "io/angzarr/transport/embedded/v1",
            "HOST_REASON_HANDSHAKE_MALFORMED",
            ("line",),
        ),
        (
            "io/angzarr/transport/distributed/v1",
            "COORDINATOR_REASON_PAYLOAD_UNAVAILABLE",
            ("uri", "cause"),
        ),
    ):
        assert {f"{package}/reason_args.py", f"{package}/reasons.en-US.ftl"} <= names
        namespace = runpy.run_path(str(tmp_path / "proto" / package / "reason_args.py"))
        assert namespace["ARGS"][key] == args
    core = runpy.run_path(str(tmp_path / "proto" / "io/angzarr/v1/reason_args.py"))
    assert "HOST_REASON_HANDSHAKE_MALFORMED" not in core["ARGS"]
    assert "HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN" in core["ARGS"]


def test_each_keys_status_is_generated_beside_its_args(tmp_path):
    # Decision 14.65: a key's status is its (reason) option's, generated as
    # the google.rpc.Code number it travels with; None for a readiness key,
    # which never travels as a status.
    proto_build.generate_protos(tmp_path / "proto")
    core = runpy.run_path(str(tmp_path / "proto" / "io/angzarr/v1/reason_args.py"))
    assert core["STATUS"]["ROUTER_REASON_PERSISTED_EVENT_CORRUPT"] == 15
    assert core["STATUS"]["ROUTER_REASON_NO_COMPONENT_OF_KIND"] == 12
    assert core["STATUS"]["COORDINATOR_REASON_EDITION_NOT_FORKED"] == 9
    assert core["STATUS"]["COORDINATOR_REASON_MISSING_RULES_VERSION"] is None
    assert set(core["STATUS"]) == set(core["ARGS"])
    embedded = runpy.run_path(
        str(tmp_path / "proto" / "io/angzarr/transport/embedded/v1/reason_args.py")
    )
    assert embedded["STATUS"]["HOST_REASON_PROBE_INPUT"] == 3


def test_a_live_key_without_its_status_fails_the_build():
    from google.protobuf import descriptor_pb2

    from angzarr_client.proto.io.angzarr.v1 import reasons_pb2

    file = descriptor_pb2.FileDescriptorProto(name="io/angzarr/v1/reasons.proto")
    enum = file.enum_type.add(name="RouterReason")
    enum.value.add(name="ROUTER_REASON_UNSPECIFIED", number=0)
    live = enum.value.add(name="ROUTER_REASON_LIVE", number=1)
    live.options.Extensions[reasons_pb2.reason].args.append("limit")
    with pytest.raises(
        proto_build.ProtoBuildError, match="ROUTER_REASON_LIVE declares no status"
    ):
        proto_build._package_args(file, ("RouterReason",), reasons_pb2.reason)
