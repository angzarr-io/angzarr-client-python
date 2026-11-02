"""The angzarr framework message types and coordinator stubs this package
uses, gathered from the generated modules: core's messages
(``angzarr_client.proto.io.angzarr.v1``), and the distributed transport's
coordinator and query services with their messages
(``angzarr_client.proto.io.angzarr.transport.distributed.v1``; decision
14.64)."""

from .proto.io.angzarr.transport.distributed.v1.command_handler_pb2 import (
    CommandResponse,
)
from .proto.io.angzarr.transport.distributed.v1.command_handler_pb2_grpc import (
    CommandHandlerCoordinatorServiceStub,
)
from .proto.io.angzarr.transport.distributed.v1.query_pb2 import (
    CorrelationQuery,
    Query,
    SequenceRange,
    TemporalQuery,
)
from .proto.io.angzarr.transport.distributed.v1.query_pb2_grpc import (
    EventQueryServiceStub,
)
from .proto.io.angzarr.v1.types_pb2 import (
    UUID,
    Command,
    CommandBook,
    CommandPage,
    ConflictResolution,
    Cover,
    EventBook,
    EventMeta,
    EventPage,
    Projection,
    Snapshot,
)

__all__ = [
    "UUID",
    "Command",
    "CommandBook",
    "CommandHandlerCoordinatorServiceStub",
    "CommandPage",
    "CommandResponse",
    "ConflictResolution",
    "CorrelationQuery",
    "Cover",
    "EventBook",
    "EventMeta",
    "EventPage",
    "EventQueryServiceStub",
    "Projection",
    "Query",
    "SequenceRange",
    "Snapshot",
    "TemporalQuery",
]
