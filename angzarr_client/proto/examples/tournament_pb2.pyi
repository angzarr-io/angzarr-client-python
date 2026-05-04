from angzarr_client.proto.examples import poker_types_pb2 as _poker_types_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TournamentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TOURNAMENT_STATUS_UNSPECIFIED: _ClassVar[TournamentStatus]
    TOURNAMENT_CREATED: _ClassVar[TournamentStatus]
    TOURNAMENT_REGISTRATION_OPEN: _ClassVar[TournamentStatus]
    TOURNAMENT_RUNNING: _ClassVar[TournamentStatus]
    TOURNAMENT_PAUSED: _ClassVar[TournamentStatus]
    TOURNAMENT_COMPLETED: _ClassVar[TournamentStatus]
    TOURNAMENT_CANCELLED: _ClassVar[TournamentStatus]
TOURNAMENT_STATUS_UNSPECIFIED: TournamentStatus
TOURNAMENT_CREATED: TournamentStatus
TOURNAMENT_REGISTRATION_OPEN: TournamentStatus
TOURNAMENT_RUNNING: TournamentStatus
TOURNAMENT_PAUSED: TournamentStatus
TOURNAMENT_COMPLETED: TournamentStatus
TOURNAMENT_CANCELLED: TournamentStatus

class RebuyConfig(_message.Message):
    __slots__ = ("enabled", "max_rebuys", "rebuy_level_cutoff", "stack_threshold", "rebuy_cost", "rebuy_chips")
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    MAX_REBUYS_FIELD_NUMBER: _ClassVar[int]
    REBUY_LEVEL_CUTOFF_FIELD_NUMBER: _ClassVar[int]
    STACK_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    REBUY_COST_FIELD_NUMBER: _ClassVar[int]
    REBUY_CHIPS_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    max_rebuys: int
    rebuy_level_cutoff: int
    stack_threshold: int
    rebuy_cost: int
    rebuy_chips: int
    def __init__(self, enabled: bool = ..., max_rebuys: _Optional[int] = ..., rebuy_level_cutoff: _Optional[int] = ..., stack_threshold: _Optional[int] = ..., rebuy_cost: _Optional[int] = ..., rebuy_chips: _Optional[int] = ...) -> None: ...

class AddonConfig(_message.Message):
    __slots__ = ("enabled", "addon_level", "addon_cost", "addon_chips")
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    ADDON_LEVEL_FIELD_NUMBER: _ClassVar[int]
    ADDON_COST_FIELD_NUMBER: _ClassVar[int]
    ADDON_CHIPS_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    addon_level: int
    addon_cost: int
    addon_chips: int
    def __init__(self, enabled: bool = ..., addon_level: _Optional[int] = ..., addon_cost: _Optional[int] = ..., addon_chips: _Optional[int] = ...) -> None: ...

class PayoutPosition(_message.Message):
    __slots__ = ("position", "percentage")
    POSITION_FIELD_NUMBER: _ClassVar[int]
    PERCENTAGE_FIELD_NUMBER: _ClassVar[int]
    position: int
    percentage: int
    def __init__(self, position: _Optional[int] = ..., percentage: _Optional[int] = ...) -> None: ...

class PlayerRegistration(_message.Message):
    __slots__ = ("player_root", "fee_paid", "starting_stack", "rebuys_used", "addon_taken", "table_assignment", "seat_assignment", "registered_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    FEE_PAID_FIELD_NUMBER: _ClassVar[int]
    STARTING_STACK_FIELD_NUMBER: _ClassVar[int]
    REBUYS_USED_FIELD_NUMBER: _ClassVar[int]
    ADDON_TAKEN_FIELD_NUMBER: _ClassVar[int]
    TABLE_ASSIGNMENT_FIELD_NUMBER: _ClassVar[int]
    SEAT_ASSIGNMENT_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    fee_paid: int
    starting_stack: int
    rebuys_used: int
    addon_taken: bool
    table_assignment: int
    seat_assignment: int
    registered_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., fee_paid: _Optional[int] = ..., starting_stack: _Optional[int] = ..., rebuys_used: _Optional[int] = ..., addon_taken: bool = ..., table_assignment: _Optional[int] = ..., seat_assignment: _Optional[int] = ..., registered_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CreateTournament(_message.Message):
    __slots__ = ("name", "game_variant", "buy_in", "starting_stack", "max_players", "min_players", "scheduled_start", "rebuy_config", "addon_config", "blind_structure", "registration_cutoff_level", "payout_structure")
    NAME_FIELD_NUMBER: _ClassVar[int]
    GAME_VARIANT_FIELD_NUMBER: _ClassVar[int]
    BUY_IN_FIELD_NUMBER: _ClassVar[int]
    STARTING_STACK_FIELD_NUMBER: _ClassVar[int]
    MAX_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    MIN_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    SCHEDULED_START_FIELD_NUMBER: _ClassVar[int]
    REBUY_CONFIG_FIELD_NUMBER: _ClassVar[int]
    ADDON_CONFIG_FIELD_NUMBER: _ClassVar[int]
    BLIND_STRUCTURE_FIELD_NUMBER: _ClassVar[int]
    REGISTRATION_CUTOFF_LEVEL_FIELD_NUMBER: _ClassVar[int]
    PAYOUT_STRUCTURE_FIELD_NUMBER: _ClassVar[int]
    name: str
    game_variant: _poker_types_pb2.GameVariant
    buy_in: int
    starting_stack: int
    max_players: int
    min_players: int
    scheduled_start: _timestamp_pb2.Timestamp
    rebuy_config: RebuyConfig
    addon_config: AddonConfig
    blind_structure: _containers.RepeatedCompositeFieldContainer[BlindLevel]
    registration_cutoff_level: int
    payout_structure: _containers.RepeatedCompositeFieldContainer[PayoutPosition]
    def __init__(self, name: _Optional[str] = ..., game_variant: _Optional[_Union[_poker_types_pb2.GameVariant, str]] = ..., buy_in: _Optional[int] = ..., starting_stack: _Optional[int] = ..., max_players: _Optional[int] = ..., min_players: _Optional[int] = ..., scheduled_start: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., rebuy_config: _Optional[_Union[RebuyConfig, _Mapping]] = ..., addon_config: _Optional[_Union[AddonConfig, _Mapping]] = ..., blind_structure: _Optional[_Iterable[_Union[BlindLevel, _Mapping]]] = ..., registration_cutoff_level: _Optional[int] = ..., payout_structure: _Optional[_Iterable[_Union[PayoutPosition, _Mapping]]] = ...) -> None: ...

class OpenRegistration(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CloseRegistration(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class EnrollPlayer(_message.Message):
    __slots__ = ("player_root", "reservation_id")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ...) -> None: ...

class UnregisterPlayer(_message.Message):
    __slots__ = ("player_root",)
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    def __init__(self, player_root: _Optional[bytes] = ...) -> None: ...

class StartTournament(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CompleteTournament(_message.Message):
    __slots__ = ("winner_root", "finishing_order")
    WINNER_ROOT_FIELD_NUMBER: _ClassVar[int]
    FINISHING_ORDER_FIELD_NUMBER: _ClassVar[int]
    winner_root: bytes
    finishing_order: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, winner_root: _Optional[bytes] = ..., finishing_order: _Optional[_Iterable[bytes]] = ...) -> None: ...

class ColorUp(_message.Message):
    __slots__ = ("retire_denomination", "new_denomination")
    RETIRE_DENOMINATION_FIELD_NUMBER: _ClassVar[int]
    NEW_DENOMINATION_FIELD_NUMBER: _ClassVar[int]
    retire_denomination: int
    new_denomination: int
    def __init__(self, retire_denomination: _Optional[int] = ..., new_denomination: _Optional[int] = ...) -> None: ...

class RebalanceTables(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class EnterHandForHand(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class NotifyHandComplete(_message.Message):
    __slots__ = ("table_root",)
    TABLE_ROOT_FIELD_NUMBER: _ClassVar[int]
    table_root: bytes
    def __init__(self, table_root: _Optional[bytes] = ...) -> None: ...

class ProcessRebuy(_message.Message):
    __slots__ = ("player_root", "reservation_id")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ...) -> None: ...

class ProcessAddon(_message.Message):
    __slots__ = ("player_root", "reservation_id")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ...) -> None: ...

class AdvanceBlindLevel(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class EliminatePlayer(_message.Message):
    __slots__ = ("player_root", "hand_root")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    HAND_ROOT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    hand_root: bytes
    def __init__(self, player_root: _Optional[bytes] = ..., hand_root: _Optional[bytes] = ...) -> None: ...

class PauseTournament(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class ResumeTournament(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class TournamentCreated(_message.Message):
    __slots__ = ("name", "game_variant", "buy_in", "starting_stack", "max_players", "min_players", "scheduled_start", "rebuy_config", "addon_config", "blind_structure", "created_at", "registration_cutoff_level", "payout_structure")
    NAME_FIELD_NUMBER: _ClassVar[int]
    GAME_VARIANT_FIELD_NUMBER: _ClassVar[int]
    BUY_IN_FIELD_NUMBER: _ClassVar[int]
    STARTING_STACK_FIELD_NUMBER: _ClassVar[int]
    MAX_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    MIN_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    SCHEDULED_START_FIELD_NUMBER: _ClassVar[int]
    REBUY_CONFIG_FIELD_NUMBER: _ClassVar[int]
    ADDON_CONFIG_FIELD_NUMBER: _ClassVar[int]
    BLIND_STRUCTURE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    REGISTRATION_CUTOFF_LEVEL_FIELD_NUMBER: _ClassVar[int]
    PAYOUT_STRUCTURE_FIELD_NUMBER: _ClassVar[int]
    name: str
    game_variant: _poker_types_pb2.GameVariant
    buy_in: int
    starting_stack: int
    max_players: int
    min_players: int
    scheduled_start: _timestamp_pb2.Timestamp
    rebuy_config: RebuyConfig
    addon_config: AddonConfig
    blind_structure: _containers.RepeatedCompositeFieldContainer[BlindLevel]
    created_at: _timestamp_pb2.Timestamp
    registration_cutoff_level: int
    payout_structure: _containers.RepeatedCompositeFieldContainer[PayoutPosition]
    def __init__(self, name: _Optional[str] = ..., game_variant: _Optional[_Union[_poker_types_pb2.GameVariant, str]] = ..., buy_in: _Optional[int] = ..., starting_stack: _Optional[int] = ..., max_players: _Optional[int] = ..., min_players: _Optional[int] = ..., scheduled_start: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., rebuy_config: _Optional[_Union[RebuyConfig, _Mapping]] = ..., addon_config: _Optional[_Union[AddonConfig, _Mapping]] = ..., blind_structure: _Optional[_Iterable[_Union[BlindLevel, _Mapping]]] = ..., created_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., registration_cutoff_level: _Optional[int] = ..., payout_structure: _Optional[_Iterable[_Union[PayoutPosition, _Mapping]]] = ...) -> None: ...

class RegistrationOpened(_message.Message):
    __slots__ = ("opened_at",)
    OPENED_AT_FIELD_NUMBER: _ClassVar[int]
    opened_at: _timestamp_pb2.Timestamp
    def __init__(self, opened_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RegistrationClosed(_message.Message):
    __slots__ = ("total_registrations", "closed_at")
    TOTAL_REGISTRATIONS_FIELD_NUMBER: _ClassVar[int]
    CLOSED_AT_FIELD_NUMBER: _ClassVar[int]
    total_registrations: int
    closed_at: _timestamp_pb2.Timestamp
    def __init__(self, total_registrations: _Optional[int] = ..., closed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentPlayerEnrolled(_message.Message):
    __slots__ = ("player_root", "reservation_id", "fee_paid", "starting_stack", "registration_number", "enrolled_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    FEE_PAID_FIELD_NUMBER: _ClassVar[int]
    STARTING_STACK_FIELD_NUMBER: _ClassVar[int]
    REGISTRATION_NUMBER_FIELD_NUMBER: _ClassVar[int]
    ENROLLED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    fee_paid: int
    starting_stack: int
    registration_number: int
    enrolled_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ..., fee_paid: _Optional[int] = ..., starting_stack: _Optional[int] = ..., registration_number: _Optional[int] = ..., enrolled_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentEnrollmentRejected(_message.Message):
    __slots__ = ("player_root", "reservation_id", "reason", "rejected_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REJECTED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    reason: str
    rejected_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ..., reason: _Optional[str] = ..., rejected_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class PlayerUnregistered(_message.Message):
    __slots__ = ("player_root", "refund_amount", "unregistered_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    REFUND_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    UNREGISTERED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    refund_amount: int
    unregistered_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., refund_amount: _Optional[int] = ..., unregistered_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentStarted(_message.Message):
    __slots__ = ("total_players", "tables_created", "total_prize_pool", "started_at")
    TOTAL_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    TABLES_CREATED_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PRIZE_POOL_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    total_players: int
    tables_created: int
    total_prize_pool: int
    started_at: _timestamp_pb2.Timestamp
    def __init__(self, total_players: _Optional[int] = ..., tables_created: _Optional[int] = ..., total_prize_pool: _Optional[int] = ..., started_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RebuyProcessed(_message.Message):
    __slots__ = ("player_root", "reservation_id", "rebuy_cost", "chips_added", "rebuy_count", "processed_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REBUY_COST_FIELD_NUMBER: _ClassVar[int]
    CHIPS_ADDED_FIELD_NUMBER: _ClassVar[int]
    REBUY_COUNT_FIELD_NUMBER: _ClassVar[int]
    PROCESSED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    rebuy_cost: int
    chips_added: int
    rebuy_count: int
    processed_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ..., rebuy_cost: _Optional[int] = ..., chips_added: _Optional[int] = ..., rebuy_count: _Optional[int] = ..., processed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RebuyDenied(_message.Message):
    __slots__ = ("player_root", "reservation_id", "reason", "denied_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    DENIED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    reason: str
    denied_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ..., reason: _Optional[str] = ..., denied_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AddonProcessed(_message.Message):
    __slots__ = ("player_root", "reservation_id", "addon_cost", "chips_added", "processed_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    ADDON_COST_FIELD_NUMBER: _ClassVar[int]
    CHIPS_ADDED_FIELD_NUMBER: _ClassVar[int]
    PROCESSED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    reservation_id: bytes
    addon_cost: int
    chips_added: int
    processed_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., reservation_id: _Optional[bytes] = ..., addon_cost: _Optional[int] = ..., chips_added: _Optional[int] = ..., processed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class BlindLevelAdvanced(_message.Message):
    __slots__ = ("level", "small_blind", "big_blind", "ante", "advanced_at")
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    SMALL_BLIND_FIELD_NUMBER: _ClassVar[int]
    BIG_BLIND_FIELD_NUMBER: _ClassVar[int]
    ANTE_FIELD_NUMBER: _ClassVar[int]
    ADVANCED_AT_FIELD_NUMBER: _ClassVar[int]
    level: int
    small_blind: int
    big_blind: int
    ante: int
    advanced_at: _timestamp_pb2.Timestamp
    def __init__(self, level: _Optional[int] = ..., small_blind: _Optional[int] = ..., big_blind: _Optional[int] = ..., ante: _Optional[int] = ..., advanced_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class PlayerEliminated(_message.Message):
    __slots__ = ("player_root", "finish_position", "hand_root", "payout", "eliminated_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    FINISH_POSITION_FIELD_NUMBER: _ClassVar[int]
    HAND_ROOT_FIELD_NUMBER: _ClassVar[int]
    PAYOUT_FIELD_NUMBER: _ClassVar[int]
    ELIMINATED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    finish_position: int
    hand_root: bytes
    payout: int
    eliminated_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., finish_position: _Optional[int] = ..., hand_root: _Optional[bytes] = ..., payout: _Optional[int] = ..., eliminated_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentPaused(_message.Message):
    __slots__ = ("reason", "paused_at")
    REASON_FIELD_NUMBER: _ClassVar[int]
    PAUSED_AT_FIELD_NUMBER: _ClassVar[int]
    reason: str
    paused_at: _timestamp_pb2.Timestamp
    def __init__(self, reason: _Optional[str] = ..., paused_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentResumed(_message.Message):
    __slots__ = ("resumed_at",)
    RESUMED_AT_FIELD_NUMBER: _ClassVar[int]
    resumed_at: _timestamp_pb2.Timestamp
    def __init__(self, resumed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentCompleted(_message.Message):
    __slots__ = ("winner_root", "total_prize_pool", "results", "completed_at")
    WINNER_ROOT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PRIZE_POOL_FIELD_NUMBER: _ClassVar[int]
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    winner_root: bytes
    total_prize_pool: int
    results: _containers.RepeatedCompositeFieldContainer[TournamentResult]
    completed_at: _timestamp_pb2.Timestamp
    def __init__(self, winner_root: _Optional[bytes] = ..., total_prize_pool: _Optional[int] = ..., results: _Optional[_Iterable[_Union[TournamentResult, _Mapping]]] = ..., completed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ColorUpCompleted(_message.Message):
    __slots__ = ("retired_denomination", "new_denomination", "completed_at")
    RETIRED_DENOMINATION_FIELD_NUMBER: _ClassVar[int]
    NEW_DENOMINATION_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    retired_denomination: int
    new_denomination: int
    completed_at: _timestamp_pb2.Timestamp
    def __init__(self, retired_denomination: _Optional[int] = ..., new_denomination: _Optional[int] = ..., completed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class PlayerMovedBetweenTables(_message.Message):
    __slots__ = ("player_root", "source_table_root", "destination_table_root", "destination_seat", "stack", "moved_at")
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TABLE_ROOT_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_TABLE_ROOT_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_SEAT_FIELD_NUMBER: _ClassVar[int]
    STACK_FIELD_NUMBER: _ClassVar[int]
    MOVED_AT_FIELD_NUMBER: _ClassVar[int]
    player_root: bytes
    source_table_root: bytes
    destination_table_root: bytes
    destination_seat: int
    stack: int
    moved_at: _timestamp_pb2.Timestamp
    def __init__(self, player_root: _Optional[bytes] = ..., source_table_root: _Optional[bytes] = ..., destination_table_root: _Optional[bytes] = ..., destination_seat: _Optional[int] = ..., stack: _Optional[int] = ..., moved_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class HandForHandStarted(_message.Message):
    __slots__ = ("started_at",)
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    started_at: _timestamp_pb2.Timestamp
    def __init__(self, started_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class HandForHandRoundComplete(_message.Message):
    __slots__ = ("round_number", "completed_at")
    ROUND_NUMBER_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    round_number: int
    completed_at: _timestamp_pb2.Timestamp
    def __init__(self, round_number: _Optional[int] = ..., completed_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class HandForHandEnded(_message.Message):
    __slots__ = ("ended_at",)
    ENDED_AT_FIELD_NUMBER: _ClassVar[int]
    ended_at: _timestamp_pb2.Timestamp
    def __init__(self, ended_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TournamentResult(_message.Message):
    __slots__ = ("position", "player_root", "payout")
    POSITION_FIELD_NUMBER: _ClassVar[int]
    PLAYER_ROOT_FIELD_NUMBER: _ClassVar[int]
    PAYOUT_FIELD_NUMBER: _ClassVar[int]
    position: int
    player_root: bytes
    payout: int
    def __init__(self, position: _Optional[int] = ..., player_root: _Optional[bytes] = ..., payout: _Optional[int] = ...) -> None: ...

class BlindLevel(_message.Message):
    __slots__ = ("level", "small_blind", "big_blind", "ante", "duration_minutes")
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    SMALL_BLIND_FIELD_NUMBER: _ClassVar[int]
    BIG_BLIND_FIELD_NUMBER: _ClassVar[int]
    ANTE_FIELD_NUMBER: _ClassVar[int]
    DURATION_MINUTES_FIELD_NUMBER: _ClassVar[int]
    level: int
    small_blind: int
    big_blind: int
    ante: int
    duration_minutes: int
    def __init__(self, level: _Optional[int] = ..., small_blind: _Optional[int] = ..., big_blind: _Optional[int] = ..., ante: _Optional[int] = ..., duration_minutes: _Optional[int] = ...) -> None: ...

class TournamentState(_message.Message):
    __slots__ = ("tournament_id", "name", "game_variant", "status", "buy_in", "starting_stack", "max_players", "min_players", "scheduled_start", "rebuy_config", "addon_config", "blind_structure", "current_level", "registered_players", "players_remaining", "total_prize_pool")
    class RegisteredPlayersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: PlayerRegistration
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[PlayerRegistration, _Mapping]] = ...) -> None: ...
    TOURNAMENT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    GAME_VARIANT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    BUY_IN_FIELD_NUMBER: _ClassVar[int]
    STARTING_STACK_FIELD_NUMBER: _ClassVar[int]
    MAX_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    MIN_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    SCHEDULED_START_FIELD_NUMBER: _ClassVar[int]
    REBUY_CONFIG_FIELD_NUMBER: _ClassVar[int]
    ADDON_CONFIG_FIELD_NUMBER: _ClassVar[int]
    BLIND_STRUCTURE_FIELD_NUMBER: _ClassVar[int]
    CURRENT_LEVEL_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_PLAYERS_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_REMAINING_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PRIZE_POOL_FIELD_NUMBER: _ClassVar[int]
    tournament_id: str
    name: str
    game_variant: _poker_types_pb2.GameVariant
    status: TournamentStatus
    buy_in: int
    starting_stack: int
    max_players: int
    min_players: int
    scheduled_start: _timestamp_pb2.Timestamp
    rebuy_config: RebuyConfig
    addon_config: AddonConfig
    blind_structure: _containers.RepeatedCompositeFieldContainer[BlindLevel]
    current_level: int
    registered_players: _containers.MessageMap[str, PlayerRegistration]
    players_remaining: int
    total_prize_pool: int
    def __init__(self, tournament_id: _Optional[str] = ..., name: _Optional[str] = ..., game_variant: _Optional[_Union[_poker_types_pb2.GameVariant, str]] = ..., status: _Optional[_Union[TournamentStatus, str]] = ..., buy_in: _Optional[int] = ..., starting_stack: _Optional[int] = ..., max_players: _Optional[int] = ..., min_players: _Optional[int] = ..., scheduled_start: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., rebuy_config: _Optional[_Union[RebuyConfig, _Mapping]] = ..., addon_config: _Optional[_Union[AddonConfig, _Mapping]] = ..., blind_structure: _Optional[_Iterable[_Union[BlindLevel, _Mapping]]] = ..., current_level: _Optional[int] = ..., registered_players: _Optional[_Mapping[str, PlayerRegistration]] = ..., players_remaining: _Optional[int] = ..., total_prize_pool: _Optional[int] = ...) -> None: ...
