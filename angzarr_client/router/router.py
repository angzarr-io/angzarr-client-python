"""The router: one business process hosts one component (decision 14.19),
and the router hands each delivery to it, refusing a delivery for a kind it
does not host."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ..proto.io.angzarr.v1 import types_pb2
from .errors import (
    DUPLICATE_COMPONENT,
    NO_COMPONENT_OF_KIND,
    NO_HANDLERS_REGISTERED,
    CodedError,
    GrpcCode,
)

if TYPE_CHECKING:
    from typing_extensions import Self

_AGGREGATE = types_pb2.COMPONENT_KIND_AGGREGATE
_PROCESS_MANAGER = types_pb2.COMPONENT_KIND_PROCESS_MANAGER
_POLICY = types_pb2.COMPONENT_KIND_POLICY
_PROJECTOR = types_pb2.COMPONENT_KIND_PROJECTOR
_STRATEGIES = (
    types_pb2.CONFLICT_RESOLUTION_STRICT,
    types_pb2.CONFLICT_RESOLUTION_AGGREGATE_HANDLES,
)


def _kind_name(kind: int) -> str:
    return types_pb2.ComponentKind.Name(kind).removeprefix("COMPONENT_KIND_")


class Router:
    """Hosts one component's dispatch table: an aggregate, a process
    manager, a policy or a projector. Registration is not safe for
    concurrent use; dispatch is (it keeps no state between deliveries)."""

    def __init__(self) -> None:
        self._component = None
        self._lock = threading.Lock()

    def close(self) -> None:
        """Drop the hosted component: a closed router hosts nothing."""
        self._component = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _register(self, dispatch) -> None:
        with self._lock:
            held = self._component
            if held is not None:
                raise CodedError(
                    DUPLICATE_COMPONENT,
                    extras={
                        "component": held.name,
                        "component_kind": _kind_name(held.kind),
                    },
                )
            self._component = dispatch

    register_aggregate = _register
    register_process_manager = _register
    register_policy = _register
    register_projector = _register

    def _hosted(self, *kinds: int):
        component = self._component
        if component is None:
            raise CodedError(NO_HANDLERS_REGISTERED)
        if component.kind not in kinds:
            raise CodedError(NO_COMPONENT_OF_KIND, GrpcCode.UNIMPLEMENTED)
        return component

    def dispatch(self, request: types_pb2.ContextualCommand):
        """``CommandHandlerService.Handle``: a command or a fact for the
        hosted aggregate or process manager."""
        return self._hosted(_AGGREGATE, _PROCESS_MANAGER).handle(request)

    def dispatch_policy(self, source: types_pb2.EventBook):
        """``PolicyService.Handle``: a source book for the hosted policy."""
        return self._hosted(_POLICY).handle(source)

    def dispatch_refusal(self, refusal):
        """``PolicyService.OnRefused``: a refusal of the hosted policy's
        command."""
        return self._hosted(_POLICY).refused(refusal)

    def dispatch_projector(self, book: types_pb2.EventBook):
        """``ProjectorService.Handle``: a book for the hosted projector."""
        return self._hosted(_PROJECTOR).handle(book)

    def component_descriptor(self) -> types_pb2.ComponentDescriptor:
        """What the hosted component says of itself (its name, kind, build
        version, inputs and, for an aggregate or a process manager, its state
        type and strategies), answered unjudged: the coordinator validates
        it (decision 14.42)."""
        c = self._hosted(_AGGREGATE, _PROCESS_MANAGER, _POLICY, _PROJECTOR)
        desc = types_pb2.ComponentDescriptor(
            name=c.name, kind=c.kind, rules_version=c.rules_version
        )
        if c.kind == _POLICY:
            desc.inputs.add(domain=c.input_domain)
            desc.output_domain = c.output_domain
        elif c.kind == _PROJECTOR:
            desc.inputs.extend(types_pb2.Target(domain=d) for d in c.domains or ["*"])
        else:
            desc.state.type_url = "/" + c.rebuilder.factory().DESCRIPTOR.full_name
            commutative = (
                [types_pb2.CONFLICT_RESOLUTION_COMMUTATIVE] if c.changed else []
            )
            desc.state.options.conflict_resolutions.extend([*_STRATEGIES, *commutative])
        return desc
