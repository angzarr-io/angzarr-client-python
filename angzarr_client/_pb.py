"""The angzarr framework message types and coordinator stubs this package
uses, gathered from the generated ``angzarr_client.proto.io.angzarr.v1``
modules."""

from .proto.io.angzarr.v1.command_handler_pb2 import CommandResponse
from .proto.io.angzarr.v1.command_handler_pb2_grpc import (
    CommandHandlerCoordinatorServiceStub,
)
from .proto.io.angzarr.v1.query_pb2_grpc import EventQueryServiceStub
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
    Query,
    SequenceRange,
    Snapshot,
    TemporalQuery,
)

__all__ = [
    "UUID",
    "Command",
    "CommandBook",
    "CommandHandlerCoordinatorServiceStub",
    "CommandPage",
    "CommandResponse",
    "ConflictResolution",
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
