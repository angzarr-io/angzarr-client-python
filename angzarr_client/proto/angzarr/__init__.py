"""Angzarr proto definitions.

The .v1 subpackage is the canonical location for the generated bindings.
This module re-exports both the symbols and the submodules themselves so
that:

    from angzarr_client.proto.angzarr import Cover         # type re-export
    from angzarr_client.proto.angzarr import types_pb2     # submodule alias

both keep working. New code should prefer `from angzarr_client.proto.angzarr.v1 import ...`.
"""

# Re-export submodules so `from angzarr_client.proto.angzarr import X_pb2` works.
# `as X` aliases mark these as intentional re-exports for ruff (otherwise
# F401 flags them as unused).
from .v1 import cloudevents_pb2 as cloudevents_pb2
from .v1 import cloudevents_pb2_grpc as cloudevents_pb2_grpc
from .v1 import command_handler_pb2 as command_handler_pb2
from .v1 import command_handler_pb2_grpc as command_handler_pb2_grpc
from .v1 import meta_pb2 as meta_pb2
from .v1 import meta_pb2_grpc as meta_pb2_grpc
from .v1 import process_manager_pb2 as process_manager_pb2
from .v1 import process_manager_pb2_grpc as process_manager_pb2_grpc
from .v1 import projector_pb2 as projector_pb2
from .v1 import projector_pb2_grpc as projector_pb2_grpc
from .v1 import query_pb2 as query_pb2
from .v1 import query_pb2_grpc as query_pb2_grpc
from .v1 import saga_pb2 as saga_pb2
from .v1 import saga_pb2_grpc as saga_pb2_grpc
from .v1 import stream_pb2 as stream_pb2
from .v1 import stream_pb2_grpc as stream_pb2_grpc
from .v1 import types_pb2 as types_pb2
from .v1 import types_pb2_grpc as types_pb2_grpc
from .v1 import upcaster_pb2 as upcaster_pb2
from .v1 import upcaster_pb2_grpc as upcaster_pb2_grpc

from .v1.command_handler_pb2 import (
    BusinessResponse,
    CommandResponse,
    RevocationResponse,
    SpeculateCommandHandlerRequest,
)
from .v1.command_handler_pb2_grpc import CommandHandlerCoordinatorServiceStub
from .v1.process_manager_pb2 import ProcessManagerHandleResponse, SpeculatePmRequest
from .v1.process_manager_pb2_grpc import ProcessManagerCoordinatorServiceStub
from .v1.projector_pb2 import SpeculateProjectorRequest
from .v1.projector_pb2_grpc import ProjectorCoordinatorServiceStub
from .v1.query_pb2_grpc import EventQueryServiceStub
from .v1.saga_pb2 import (
    SagaHandleRequest,
    SagaResponse,
    SpeculateSagaRequest,
)
from .v1.saga_pb2_grpc import SagaCoordinatorServiceStub
from .v1.upcaster_pb2 import UpcastRequest, UpcastResponse
from .v1.upcaster_pb2_grpc import UpcasterServiceStub
from .v1.types_pb2 import (
    UUID,
    CascadeErrorMode,
    CommandBook,
    CommandPage,
    CommandRequest,
    ComponentDescriptor,
    ContextualCommand,
    Cover,
    DomainDivergence,
    Edition,
    EventBook,
    EventPage,
    EventRequest,
    GetDescriptorRequest,
    MergeStrategy,
    PageHeader,
    Projection,
    Query,
    SequenceRange,
    SequenceSet,
    Snapshot,
    SyncMode,
    Target,
    TemporalQuery,
)

__all__ = [
    # Types
    "UUID",
    "Cover",
    "Edition",
    "DomainDivergence",
    "EventPage",
    "EventBook",
    "PageHeader",
    "MergeStrategy",
    "Snapshot",
    "CommandPage",
    "CommandBook",
    "CommandRequest",
    "CommandResponse",
    "ComponentDescriptor",
    "EventRequest",
    "GetDescriptorRequest",
    "Query",
    "SequenceRange",
    "SequenceSet",
    "Target",
    "TemporalQuery",
    "Projection",
    "SyncMode",
    "CascadeErrorMode",
    "ContextualCommand",
    # Speculative
    "SpeculateCommandHandlerRequest",
    "SpeculateProjectorRequest",
    "SpeculateSagaRequest",
    "SpeculatePmRequest",
    # Stubs
    "CommandHandlerCoordinatorServiceStub",
    "SagaCoordinatorServiceStub",
    "ProjectorCoordinatorServiceStub",
    "ProcessManagerCoordinatorServiceStub",
    "EventQueryServiceStub",
    "UpcasterServiceStub",
    # Requests
    "SagaHandleRequest",
    # Responses
    "BusinessResponse",
    "RevocationResponse",
    "SagaResponse",
    "ProcessManagerHandleResponse",
    # Upcaster
    "UpcastRequest",
    "UpcastResponse",
]
