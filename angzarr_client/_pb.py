"""The angzarr framework message types and coordinator stubs this package
uses, gathered from the generated ``angzarr_client.proto.io.angzarr.v1``
modules."""

from .proto.io.angzarr.v1.command_handler_pb2 import (
    BusinessResponse,
    CommandResponse,
    RevocationResponse,
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
    AngzarrDeferredSequence,
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
    MergeStrategy,
    Notification,
    PageHeader,
    PayloadReference,
    Projection,
    Query,
    RejectionNotification,
    SequenceRange,
    SequenceSet,
    Snapshot,
    SyncMode,
    Target,
    TemporalQuery,
)

__all__ = [
    "UUID",
    "AngzarrDeferredSequence",
    "BusinessResponse",
    "CascadeErrorMode",
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
    "Notification",
    "PageHeader",
    "PayloadReference",
    "ProcessManagerCoordinatorServiceStub",
    "ProcessManagerHandleRequest",
    "ProcessManagerHandleResponse",
    "Projection",
    "ProjectorCoordinatorServiceStub",
    "Query",
    "RejectionNotification",
    "RevocationResponse",
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
