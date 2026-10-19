"""The angzarr framework message types and coordinator stubs this package
uses, gathered from the generated ``angzarr_client.proto.io.angzarr.v1``
modules."""

from .proto.io.angzarr.v1.command_handler_pb2 import (
    BusinessResponse,
    CommandResponse,
    SpeculateCommandHandlerRequest,
)
from .proto.io.angzarr.v1.command_handler_pb2_grpc import (
    CommandHandlerCoordinatorServiceStub,
)
from .proto.io.angzarr.v1.process_manager_pb2 import (
    ProcessManagerHandleRequest,
    ProcessManagerHandleResponse,
    SpeculatePmRequest,
)
from .proto.io.angzarr.v1.process_manager_pb2_grpc import (
    ProcessManagerCoordinatorServiceStub,
)
from .proto.io.angzarr.v1.projector_pb2 import SpeculateProjectorRequest
from .proto.io.angzarr.v1.projector_pb2_grpc import ProjectorCoordinatorServiceStub
from .proto.io.angzarr.v1.query_pb2_grpc import EventQueryServiceStub
from .proto.io.angzarr.v1.saga_pb2 import (
    SagaHandleRequest,
    SagaResponse,
    SpeculateSagaRequest,
)
from .proto.io.angzarr.v1.saga_pb2_grpc import SagaCoordinatorServiceStub
from .proto.io.angzarr.v1.types_pb2 import (
    UUID,
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
    MergeStrategy,
    PageHeader,
    PayloadReference,
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
    "UUID",
    "BusinessResponse",
    "CommandBook",
    "CommandHandlerCoordinatorServiceStub",
    "CommandPage",
    "CommandRequest",
    "CommandResponse",
    "ComponentDescriptor",
    "ContextualCommand",
    "Cover",
    "DomainDivergence",
    "Edition",
    "EventBook",
    "EventPage",
    "EventQueryServiceStub",
    "EventRequest",
    "MergeStrategy",
    "PageHeader",
    "PayloadReference",
    "ProcessManagerCoordinatorServiceStub",
    "ProcessManagerHandleRequest",
    "ProcessManagerHandleResponse",
    "Projection",
    "ProjectorCoordinatorServiceStub",
    "Query",
    "SagaCoordinatorServiceStub",
    "SagaHandleRequest",
    "SagaResponse",
    "SequenceRange",
    "SequenceSet",
    "Snapshot",
    "SpeculateCommandHandlerRequest",
    "SpeculatePmRequest",
    "SpeculateProjectorRequest",
    "SpeculateSagaRequest",
    "SyncMode",
    "Target",
    "TemporalQuery",
]
