"""``describe``: a component's ``ComponentDescriptor``, built from its
dispatch table.

angzarr-cli generates every component's descriptor at build time (the
``_descriptor.py`` a scaffold writes, a protobuf held as hex). That file is
replaceable by hand: ``describe(dispatch, ...)`` builds the same descriptor
from the dispatch table a hand-written component registers, so code written
without the CLI needs no hex::

    from angzarr_client import ComponentHost, describe

    table = ProjectorDispatch("Totals", TotalsView).for_domains("alpha", "beta")
    host = ComponentHost(rules_version=__version__)
    host.add_projector(table, describe(table, rules_version=__version__))

The descriptor's fields, as the coordinator reads them (``types.proto``,
``ComponentDescriptor``):

- ``name`` and ``kind``: the table's ``name`` and ``kind``;
- ``rules_version``: the binary's build version, the one every answer
  reports (``ComponentHost(rules_version=...)``, which the host checks it
  against when the component is registered);
- ``inputs``: the domains it subscribes to: a policy's one
  ``input_domain``; a projector's source domains (``for_domains``), each
  named, as there is no wildcard domain (decision 14.53); none for an
  aggregate or a process manager;
- ``state`` (an aggregate or a process manager only): the state message's
  ``type_url``, ``"/"`` and its full name, and ``options.conflict_resolutions``,
  the full resolved list: STRICT and AGGREGATE_HANDLES, which every state
  supports, with whatever else it supports (COMMUTATIVE), deduplicated, in
  enum-number order;
- ``starting_types`` (a process manager only): the full NAMES (no type URL
  prefix) of the command and fact types that start an instance;
- ``output_domain`` (a policy only): the one domain its commands go to.

The coordinator refuses a descriptor that breaks a rule (decision 14.42,
angzarr-embedded-host's ``descriptor::validate``), in this order, each with
its ``CoordinatorReason`` key: an empty rules version
(``COORDINATOR_REASON_MISSING_RULES_VERSION``); starting types on a kind
other than a process manager (``..._STARTING_TYPES_ON_NON_PM``); no state on
an aggregate or a process manager (``..._MISSING_STATE_TYPE``), a state on a
policy or a projector (``..._STATE_ON_STATELESS_KIND``); a policy without
exactly one input (``..._POLICY_INPUT_COUNT``) or with no output domain
(``..._POLICY_NO_OUTPUT_DOMAIN``); an output domain on any other kind
(``..._OUTPUT_DOMAIN_ON_NON_POLICY``). ``describe`` never builds one: it
raises :class:`~angzarr_client.host.ConfigurationError` with that key as its
code instead. A projector naming no domain it reads is the host's refusal
(``HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN``, decision 14.55), raised after
the coordinator's.
"""

from __future__ import annotations

from collections.abc import Iterable

from .error_codes import codes
from .host import ConfigurationError
from .proto.io.angzarr.v1 import options_pb2, types_pb2
from .proto.io.angzarr.v1.reasons_pb2 import CoordinatorReason

_AGGREGATE = types_pb2.COMPONENT_KIND_AGGREGATE
_PROCESS_MANAGER = types_pb2.COMPONENT_KIND_PROCESS_MANAGER
_POLICY = types_pb2.COMPONENT_KIND_POLICY
_PROJECTOR = types_pb2.COMPONENT_KIND_PROJECTOR
_KINDS = (_AGGREGATE, _PROCESS_MANAGER, _POLICY, _PROJECTOR)
_STATEFUL = (_AGGREGATE, _PROCESS_MANAGER)

#: The resolutions every state supports (``ConflictResolution``'s implicit
#: base, types.proto): STRICT and AGGREGATE_HANDLES.
BASE_RESOLUTIONS = (
    types_pb2.CONFLICT_RESOLUTION_STRICT,
    types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
)

RESOLUTION_UNSPECIFIED = codes.DESCRIPTOR_RESOLUTION_UNSPECIFIED


def _reason(value: int) -> str:
    return CoordinatorReason.Name(value)


def _full_name(message) -> str:
    """A message class's (or a type name's, or a type URL's) full name."""
    if isinstance(message, str):
        return message.rpartition("/")[2]
    return message.DESCRIPTOR.full_name


def _declared_resolutions(state) -> list[int]:
    """What the state message's ``(io.angzarr.v1.state)`` option lists
    (nothing for a type given by name, or a message without the option)."""
    if isinstance(state, str):
        return []
    options = state.DESCRIPTOR.GetOptions()
    if not options.HasExtension(options_pb2.state):
        return []
    return list(options.Extensions[options_pb2.state].conflict_resolutions)


def _state_class(dispatch):
    """The state message class of an aggregate's or a process manager's
    table: what its rebuilder's factory makes."""
    return type(dispatch.rebuilder.factory())


def _inputs(dispatch, kind: int) -> list[str]:
    if kind == _POLICY:
        return [dispatch.input_domain] if dispatch.input_domain else []
    if kind == _PROJECTOR:
        return list(dispatch.domains)
    return []


def describe(
    dispatch,
    *,
    rules_version: str | None = None,
    state_type=None,
    resolutions: Iterable[int] | None = None,
    starting_types: Iterable = (),
) -> types_pb2.ComponentDescriptor:
    """The ``ComponentDescriptor`` of ``dispatch``'s component: what
    angzarr-cli would generate for it (the module docstring says field by
    field).

    - ``rules_version``: the build version; defaults to the table's own.
    - ``state_type`` (an aggregate or a process manager): the state message
      class, or its full name; defaults to the class the table's rebuilder
      makes.
    - ``resolutions``: the conflict resolutions the state supports beyond
      the base; defaults to its message's ``(io.angzarr.v1.state)`` option.
    - ``starting_types`` (a process manager): its starting command and fact
      types, as message classes or full names (a type URL is cut to its
      name).

    A descriptor the coordinator would refuse is not built: raises
    :class:`~angzarr_client.host.ConfigurationError`, its code the
    ``CoordinatorReason`` key; nor is a projector's naming no input domain,
    code ``HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN``. A table of no component kind raises
    ``TypeError``.
    """
    kind = getattr(dispatch, "kind", None)
    if kind not in _KINDS:
        raise TypeError(f"not a component's dispatch table: {dispatch!r}")
    name = dispatch.name
    descriptor = types_pb2.ComponentDescriptor(
        name=name,
        kind=kind,
        rules_version=(
            dispatch.rules_version if rules_version is None else rules_version
        ),
        inputs=[types_pb2.Target(domain=d) for d in _inputs(dispatch, kind)],
        starting_types=[_full_name(t) for t in starting_types],
        output_domain=dispatch.output_domain if kind == _POLICY else "",
    )
    if kind in _STATEFUL or state_type is not None or resolutions is not None:
        state = state_type
        if state is None and kind in _STATEFUL:
            state = _state_class(dispatch)
        if resolutions is None:
            listed = _declared_resolutions(state)
        else:
            listed = list(resolutions)
        if types_pb2.CONFLICT_RESOLUTION_UNSPECIFIED in listed:
            raise ConfigurationError(
                RESOLUTION_UNSPECIFIED,
                code=RESOLUTION_UNSPECIFIED,
                details={"component": name},
            )
        if state is not None:
            descriptor.state.type_url = "/" + _full_name(state)
        descriptor.state.options.conflict_resolutions.extend(
            sorted(set(BASE_RESOLUTIONS) | set(listed))
        )
    refused = _refusal(descriptor)
    if refused is not None:
        code, details = refused
        raise ConfigurationError(code, code=code, details=details)
    if kind == _PROJECTOR and not descriptor.inputs:
        code = codes.HOST_REASON_PROJECTOR_NO_INPUT_DOMAIN
        raise ConfigurationError(code, code=code, details={"component": name})
    return descriptor


def _refusal(descriptor: types_pb2.ComponentDescriptor):
    """The coordinator's first refusal of ``descriptor``, ``(reason key,
    details)``, in its order (angzarr-embedded-host ``descriptor::validate``),
    or None when it accepts it."""
    component = {"component": descriptor.name}
    kind = descriptor.kind
    with_kind = {**component, "component_kind": types_pb2.ComponentKind.Name(kind)}
    if not descriptor.rules_version:
        return _reason(CoordinatorReason.COORDINATOR_REASON_MISSING_RULES_VERSION), (
            component
        )
    if kind != _PROCESS_MANAGER and descriptor.starting_types:
        return (
            _reason(CoordinatorReason.COORDINATOR_REASON_STARTING_TYPES_ON_NON_PM),
            with_kind,
        )
    has_state = descriptor.HasField("state")
    if kind in _STATEFUL and not has_state:
        return (
            _reason(CoordinatorReason.COORDINATOR_REASON_MISSING_STATE_TYPE),
            with_kind,
        )
    if kind in (_POLICY, _PROJECTOR) and has_state:
        return (
            _reason(CoordinatorReason.COORDINATOR_REASON_STATE_ON_STATELESS_KIND),
            with_kind,
        )
    if kind == _POLICY:
        if len(descriptor.inputs) != 1:
            return _reason(CoordinatorReason.COORDINATOR_REASON_POLICY_INPUT_COUNT), {
                **component,
                "input_count": len(descriptor.inputs),
            }
        if not descriptor.output_domain:
            return (
                _reason(CoordinatorReason.COORDINATOR_REASON_POLICY_NO_OUTPUT_DOMAIN),
                component,
            )
    elif descriptor.output_domain:
        return (
            _reason(CoordinatorReason.COORDINATOR_REASON_OUTPUT_DOMAIN_ON_NON_POLICY),
            with_kind,
        )
    return None
